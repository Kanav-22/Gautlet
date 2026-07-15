"""Content-addressed, pre-persistence-redacted evaluation evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from gauntlet.core.models import Evidence, EvidenceType, Scenario, ScenarioResult
from gauntlet.evidence.store import (
    ArtifactStoreError,
    RunArtifactStore,
    _atomic_write_text,
    _filesystem_path,
    _json_text,
)

if TYPE_CHECKING:
    from gauntlet.execution.executor import ScenarioExecution

REDACTION_MARKER = "[REDACTED]"
_EVIDENCE_SCHEMA_VERSION = 1
_HASH_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_SECRET_NAME_PATTERN = re.compile(
    r"(?:secret|token|password|passwd|api[_-]?key|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)
_ENVELOPE_KEYS = {"schema_version", "type", "payload", "metadata", "redacted"}


class EvidenceStoreError(ArtifactStoreError):
    """Base failure for content-addressed evidence operations."""


class EvidenceCorruptionError(EvidenceStoreError):
    """Raised when stored evidence no longer matches its immutable metadata."""


class RedactionError(EvidenceStoreError):
    """Raised when a redaction policy cannot safely transform JSON evidence."""


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """A deep-copied JSON value and whether at least one replacement occurred."""

    value: JsonValue
    redacted: bool


@dataclass(frozen=True, slots=True)
class ScenarioEvidenceBundle:
    """A scenario execution linked to persisted evidence by semantic role."""

    execution: ScenarioExecution
    evidence: tuple[Evidence, ...]
    refs_by_role: dict[str, tuple[str, ...]]


class SecretRedactor:
    """Recursively replace configured literals and patterns in JSON values."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        secret_names: Sequence[str] = (),
        patterns: Sequence[str] = (),
    ) -> None:
        source = dict(os.environ if environment is None else environment)
        explicit_names = set(secret_names)
        unknown_names = sorted(explicit_names - source.keys())
        if unknown_names:
            raise RedactionError(
                "Explicit secret environment names are missing: " + ", ".join(unknown_names)
            )
        values = {
            value
            for name, value in source.items()
            if value and (_SECRET_NAME_PATTERN.search(name) is not None or name in explicit_names)
        }
        self._literals = tuple(sorted(values, key=lambda value: (-len(value), value)))
        compiled: list[re.Pattern[str]] = []
        for index, pattern in enumerate(patterns):
            if not isinstance(pattern, str) or not pattern:
                raise RedactionError(f"Redaction pattern #{index + 1} must be a non-empty string")
            try:
                expression = re.compile(pattern)
            except re.error as error:
                raise RedactionError(
                    f"Redaction pattern #{index + 1} is invalid: {error}"
                ) from error
            if expression.search("") is not None:
                raise RedactionError(
                    f"Redaction pattern #{index + 1} must not match the empty string"
                )
            compiled.append(expression)
        self._patterns = tuple(compiled)

    def redact(self, value: JsonValue) -> RedactionResult:
        """Return a redacted deep copy without mutating caller-owned data."""

        redacted_value, changed = self._redact_value(copy.deepcopy(value))
        return RedactionResult(redacted_value, changed)

    def _redact_text(self, value: str) -> tuple[str, bool]:
        result = value
        for literal in self._literals:
            result = result.replace(literal, REDACTION_MARKER)
        for expression in self._patterns:
            result = expression.sub(REDACTION_MARKER, result)
        return result, result != value

    def _redact_value(self, value: JsonValue) -> tuple[JsonValue, bool]:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            result_list: list[JsonValue] = []
            changed = False
            for item in value:
                redacted_item, item_changed = self._redact_value(item)
                result_list.append(redacted_item)
                changed = changed or item_changed
            return result_list, changed
        if isinstance(value, dict):
            result_dict: dict[str, JsonValue] = {}
            changed = False
            for key, item in value.items():
                redacted_key, key_changed = self._redact_text(key)
                if redacted_key in result_dict:
                    raise RedactionError(
                        "Redaction produced colliding object keys; evidence was not persisted"
                    )
                redacted_item, item_changed = self._redact_value(item)
                result_dict[redacted_key] = redacted_item
                changed = changed or key_changed or item_changed
            return result_dict, changed
        return value, False


class EvidenceStore:
    """Persist immutable evidence envelopes beneath an existing run directory."""

    def __init__(
        self,
        run_store: RunArtifactStore,
        *,
        environment: Mapping[str, str] | None = None,
        secret_names: Sequence[str] = (),
        redaction_patterns: Sequence[str] = (),
    ) -> None:
        self.run_store = run_store
        self.redactor = SecretRedactor(
            environment=environment,
            secret_names=secret_names,
            patterns=redaction_patterns,
        )

    def persist(
        self,
        run_id: str,
        evidence_type: EvidenceType,
        payload: JsonValue,
        *,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> Evidence:
        """Redact, hash, and atomically persist one canonical evidence envelope."""

        manifest = self.run_store.load_manifest(run_id)
        del manifest
        payload_result = self.redactor.redact(payload)
        metadata_result = self.redactor.redact(cast(JsonValue, dict(metadata or {})))
        assert isinstance(metadata_result.value, dict)
        was_redacted = payload_result.redacted or metadata_result.redacted
        envelope: dict[str, JsonValue] = {
            "schema_version": _EVIDENCE_SCHEMA_VERSION,
            "type": evidence_type.value,
            "payload": payload_result.value,
            "metadata": metadata_result.value,
            "redacted": was_redacted,
        }
        content = _json_text(envelope)
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        relative_path = Path("evidence") / f"{digest}.json"
        run_dir = self.run_store.run_dir(run_id)
        evidence_dir = run_dir / "evidence"
        destination = run_dir / relative_path
        physical_destination = _filesystem_path(destination)
        if evidence_dir.is_symlink() or physical_destination.is_symlink():
            raise EvidenceCorruptionError(
                "Symlinked evidence directories and files are not allowed"
            )
        if not evidence_dir.is_dir():
            raise EvidenceCorruptionError(f"Evidence directory is missing for run {run_id}")
        if physical_destination.exists():
            if not physical_destination.is_file() or physical_destination.read_bytes() != encoded:
                raise EvidenceCorruptionError(
                    f"Existing content-addressed evidence does not match {digest}"
                )
        else:
            _atomic_write_text(destination, content)
        return Evidence(
            id=f"evidence_{digest}",
            type=evidence_type,
            path=relative_path.as_posix(),
            content_hash=f"sha256:{digest}",
            redacted=was_redacted,
            metadata=metadata_result.value,
        )

    def load(self, run_id: str, evidence: Evidence) -> dict[str, JsonValue]:
        """Load and hash-verify a canonical evidence envelope."""

        self.run_store.load_manifest(run_id)
        match = _HASH_PATTERN.fullmatch(evidence.content_hash)
        if match is None:
            raise EvidenceCorruptionError("Evidence content hash is not canonical sha256")
        digest = match.group(1)
        expected_path = f"evidence/{digest}.json"
        if evidence.id != f"evidence_{digest}" or evidence.path != expected_path:
            raise EvidenceCorruptionError("Evidence ID, path, and content hash do not agree")
        path = self.path_for(run_id, evidence)
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise EvidenceCorruptionError("Evidence path is missing, unsafe, or not a file")
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != digest:
            raise EvidenceCorruptionError("Evidence content hash verification failed")
        try:
            raw = json.loads(encoded, parse_constant=self._reject_json_constant)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise EvidenceCorruptionError(f"Evidence JSON is invalid: {error}") from error
        if not isinstance(raw, dict) or set(raw) != _ENVELOPE_KEYS:
            raise EvidenceCorruptionError("Evidence envelope fields are invalid")
        if raw.get("schema_version") != _EVIDENCE_SCHEMA_VERSION:
            raise EvidenceCorruptionError("Evidence schema version is unsupported")
        if raw.get("type") != evidence.type.value:
            raise EvidenceCorruptionError("Evidence type does not match its metadata")
        if raw.get("redacted") is not evidence.redacted:
            raise EvidenceCorruptionError("Evidence redaction flag does not match its metadata")
        if raw.get("metadata") != evidence.metadata:
            raise EvidenceCorruptionError("Evidence metadata does not match its envelope")
        return cast(dict[str, JsonValue], raw)

    def path_for(self, run_id: str, evidence: Evidence) -> Path:
        """Return a platform-safe absolute path for a canonical evidence reference."""

        match = _HASH_PATTERN.fullmatch(evidence.content_hash)
        if match is None:
            raise EvidenceCorruptionError("Evidence content hash is not canonical sha256")
        digest = match.group(1)
        expected_path = f"evidence/{digest}.json"
        if evidence.id != f"evidence_{digest}" or evidence.path != expected_path:
            raise EvidenceCorruptionError("Evidence ID, path, and content hash do not agree")
        return _filesystem_path(self.run_store.run_dir(run_id) / Path(evidence.path))

    def record_execution(
        self,
        run_id: str,
        scenario: Scenario,
        execution: ScenarioExecution,
    ) -> ScenarioEvidenceBundle:
        """Persist all assertion-relevant facts from one finalized scenario execution."""

        if execution.result.scenario_id != scenario.id:
            raise EvidenceStoreError("Scenario and execution IDs do not match")
        final_attempt = execution.attempts[-1]
        common: dict[str, JsonValue] = {"scenario_id": scenario.id}
        by_role: dict[str, list[str]] = {}
        evidence_items: list[Evidence] = []

        def add(role: str, item: Evidence) -> None:
            evidence_items.append(item)
            by_role.setdefault(role, []).append(item.id)

        add(
            "output",
            self.persist(
                run_id,
                EvidenceType.ARTIFACT,
                cast(JsonValue, copy.deepcopy(execution.result.output)),
                metadata={**common, "kind": "output"},
            ),
        )
        trace_payload = cast(JsonValue, [copy.deepcopy(event) for event in final_attempt.trace])
        add(
            "trace",
            self.persist(
                run_id,
                EvidenceType.TRACE,
                trace_payload,
                metadata={**common, "kind": "full_trace"},
            ),
        )
        add(
            "fixtures",
            self.persist(
                run_id,
                EvidenceType.ARTIFACT,
                cast(JsonValue, copy.deepcopy(scenario.fixtures)),
                metadata={**common, "kind": "scenario_fixtures"},
            ),
        )
        execution_payload: dict[str, JsonValue] = {
            "status": execution.result.status.value,
            "lifecycle": [state.value for state in execution.lifecycle],
            "started_at": execution.result.started_at.isoformat(),
            "finished_at": execution.result.finished_at.isoformat(),
            "duration_ms": execution.result.duration_ms,
            "seed": execution.seed,
            "network_policy": execution.network_policy.value,
            "attempts": len(execution.attempts),
            "completed_normally": execution.result.status.value == "passed",
        }
        add(
            "execution",
            self.persist(
                run_id,
                EvidenceType.METRIC,
                execution_payload,
                metadata={**common, "kind": "execution_lifecycle"},
            ),
        )

        for attempt in execution.attempts:
            attempt_metadata: dict[str, JsonValue] = {
                **common,
                "attempt": attempt.attempt_number,
            }
            if attempt.stderr:
                add(
                    "stderr",
                    self.persist(
                        run_id,
                        EvidenceType.STDERR,
                        attempt.stderr,
                        metadata={**attempt_metadata, "kind": "adapter_stderr"},
                    ),
                )
            if attempt.error is not None:
                add(
                    "exception",
                    self.persist(
                        run_id,
                        EvidenceType.EXCEPTION,
                        cast(JsonValue, copy.deepcopy(attempt.error)),
                        metadata={**attempt_metadata, "kind": "execution_error"},
                    ),
                )
            for event_index, event in enumerate(attempt.trace):
                if event.get("type") != "tool_call":
                    continue
                add(
                    "tool_call",
                    self.persist(
                        run_id,
                        EvidenceType.TOOL_CALL,
                        cast(JsonValue, copy.deepcopy(event)),
                        metadata={
                            **attempt_metadata,
                            "kind": "tool_call",
                            "event_index": event_index,
                        },
                    ),
                )

        evidence_refs = list(dict.fromkeys(item.id for item in evidence_items))
        result_data = execution.result.model_dump(mode="json")
        result_data["evidence_refs"] = evidence_refs
        linked_result = ScenarioResult.model_validate(result_data)
        linked_execution = replace(execution, result=linked_result)
        return ScenarioEvidenceBundle(
            execution=linked_execution,
            evidence=tuple(evidence_items),
            refs_by_role={role: tuple(refs) for role, refs in by_role.items()},
        )

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant is not allowed: {value}")
