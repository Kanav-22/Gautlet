"""Scenario lifecycle orchestration over the framework-neutral adapter boundary."""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias, cast, runtime_checkable

from pydantic import JsonValue

from gauntlet.adapters import (
    MAX_MESSAGE_BYTES,
    AdapterChildError,
    AdapterError,
    AdapterTimeoutError,
    JsonObject,
    PythonCallableAdapter,
    SystemAdapter,
)
from gauntlet.config.models import NetworkPolicy
from gauntlet.core.models import Scenario, ScenarioResult, ScenarioResultStatus


class ExecutionPolicyError(ValueError):
    """Raised before execution when a scenario policy cannot be honored."""


class ScenarioLifecycleState(StrEnum):
    """Observable states in the documented scenario lifecycle."""

    LOADED = "loaded"
    VALIDATED = "validated"
    PREPARED = "prepared"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMED_OUT = "timed_out"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class ScenarioAttemptContext:
    """Immutable, scenario-specific inputs used to construct one adapter attempt."""

    scenario: Scenario
    attempt_number: int
    timeout_seconds: float
    seed: int | None
    tool_sequence: tuple[JsonObject, ...]
    network_policy: NetworkPolicy


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Raw execution facts retained for later evidence persistence."""

    attempt_number: int
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    output: JsonObject | None
    error: JsonObject | None
    trace: tuple[JsonObject, ...]
    usage: JsonObject
    stderr: str
    isolation_level: str
    timed_out: bool
    retryable: bool


@dataclass(frozen=True, slots=True)
class ScenarioExecution:
    """Normalized result plus the raw facts needed by evidence and assertions."""

    result: ScenarioResult
    lifecycle: tuple[ScenarioLifecycleState, ...]
    attempts: tuple[AttemptRecord, ...]
    seed: int | None
    network_policy: NetworkPolicy


@runtime_checkable
class _DiagnosticAdapter(Protocol):
    @property
    def stderr(self) -> str: ...


AdapterFactory: TypeAlias = Callable[[ScenarioAttemptContext], SystemAdapter]
Clock: TypeAlias = Callable[[], datetime]
MonotonicClock: TypeAlias = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PythonCallableAdapterFactory:
    """Create an isolated built-in adapter for each scenario attempt."""

    target: str
    project_root: Path
    max_message_bytes: int = MAX_MESSAGE_BYTES

    def __call__(self, context: ScenarioAttemptContext) -> PythonCallableAdapter:
        return PythonCallableAdapter(
            self.target,
            project_root=self.project_root,
            timeout_seconds=context.timeout_seconds,
            tool_sequence=context.tool_sequence,
            seed=context.seed,
            max_message_bytes=self.max_message_bytes,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_copy(value: JsonObject) -> JsonObject:
    return copy.deepcopy(value)


def _error_payload(error_type: str, message: str, **details: JsonValue) -> JsonObject:
    return {"type": error_type, "message": message, **details}


def _adapter_diagnostics(adapter: SystemAdapter | None) -> str:
    if isinstance(adapter, _DiagnosticAdapter):
        return adapter.stderr
    return ""


class ScenarioExecutor:
    """Execute validated scenarios with deterministic retries and bounded concurrency."""

    def __init__(
        self,
        adapter_factory: AdapterFactory,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 0,
        seed: int | None = None,
        network_policy: NetworkPolicy = NetworkPolicy.DISABLED,
        max_concurrency: int = 1,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._timeout_seconds = self._positive_number("timeout_seconds", timeout_seconds)
        self._max_retries = self._non_negative_integer("max_retries", max_retries)
        self._seed = self._optional_integer("seed", seed)
        self._network_policy = NetworkPolicy(network_policy)
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise ValueError("max_concurrency must be a positive integer")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        self._max_concurrency = max_concurrency
        self._clock = clock or _utc_now
        self._monotonic_clock = monotonic_clock or time.monotonic

    def execute(self, scenario: Scenario) -> ScenarioExecution:
        """Execute one scenario through its full lifecycle and contain adapter failures."""

        lifecycle = [ScenarioLifecycleState.LOADED]
        timeout_seconds, max_retries, seed, network_policy = self._resolve_policy(scenario)
        tool_sequence = self._tool_sequence(scenario)
        lifecycle.extend((ScenarioLifecycleState.VALIDATED, ScenarioLifecycleState.PREPARED))

        started_at = self._clock()
        started_monotonic = self._monotonic_clock()
        attempts: list[AttemptRecord] = []
        lifecycle.append(ScenarioLifecycleState.RUNNING)

        for attempt_number in range(1, max_retries + 2):
            context = ScenarioAttemptContext(
                scenario=scenario,
                attempt_number=attempt_number,
                timeout_seconds=timeout_seconds,
                seed=seed,
                tool_sequence=copy.deepcopy(tool_sequence),
                network_policy=network_policy,
            )
            attempt = self._execute_attempt(context)
            attempts.append(attempt)
            if attempt.error is None:
                status = ScenarioResultStatus.PASSED
                break
            if attempt.timed_out:
                status = ScenarioResultStatus.TIMED_OUT
                break
            if not attempt.retryable or attempt_number > max_retries:
                status = ScenarioResultStatus.ERROR
                break
        else:  # pragma: no cover - the bounded range always reaches a terminal outcome
            raise AssertionError("scenario attempt loop did not produce a terminal outcome")

        terminal_state = {
            ScenarioResultStatus.PASSED: ScenarioLifecycleState.PASSED,
            ScenarioResultStatus.ERROR: ScenarioLifecycleState.ERROR,
            ScenarioResultStatus.TIMED_OUT: ScenarioLifecycleState.TIMED_OUT,
        }[status]
        lifecycle.extend((terminal_state, ScenarioLifecycleState.FINALIZED))

        finished_at = self._clock()
        duration_ms = max(0, round((self._monotonic_clock() - started_monotonic) * 1000))
        final_attempt = attempts[-1]
        result = ScenarioResult(
            scenario_id=scenario.id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            output=_json_copy(final_attempt.output) if final_attempt.output is not None else None,
            error=_json_copy(final_attempt.error) if final_attempt.error is not None else None,
            metrics={
                "attempts": len(attempts),
                "retries": len(attempts) - 1,
                "steps": len(final_attempt.trace),
                "usage": cast(JsonValue, _json_copy(final_attempt.usage)),
            },
            evidence_refs=[],
            findings=[],
        )
        return ScenarioExecution(
            result=result,
            lifecycle=tuple(lifecycle),
            attempts=tuple(attempts),
            seed=seed,
            network_policy=network_policy,
        )

    def execute_many(self, scenarios: Sequence[Scenario]) -> tuple[ScenarioExecution, ...]:
        """Execute scenarios concurrently without exceeding the configured local bound."""

        scenario_list = list(scenarios)
        if not scenario_list:
            return ()
        worker_count = min(self._max_concurrency, len(scenario_list))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="gauntlet-scenario",
        ) as pool:
            futures = [pool.submit(self.execute, scenario) for scenario in scenario_list]
            return tuple(future.result() for future in futures)

    def _execute_attempt(self, context: ScenarioAttemptContext) -> AttemptRecord:
        adapter: SystemAdapter | None = None
        output: JsonObject | None = None
        error_payload: JsonObject | None = None
        trace: tuple[JsonObject, ...] = ()
        usage: JsonObject = {}
        timed_out = False
        retryable = False
        isolation_level = "unknown"
        started_at = self._clock()
        started_monotonic = self._monotonic_clock()

        try:
            adapter = self._adapter_factory(context)
            isolation_level = str(getattr(adapter, "isolation_level", "unknown"))
            adapter.reset()
            output = _json_copy(adapter.invoke(_json_copy(context.scenario.input)))
            trace = tuple(_json_copy(event) for event in adapter.trace())
            usage = _json_copy(adapter.usage())
        except AdapterTimeoutError as error:
            timed_out = True
            error_payload = _error_payload(
                "timeout",
                str(error),
                operation=error.operation,
                timeout_seconds=error.timeout_seconds,
            )
        except AdapterChildError as error:
            retryable = error.retryable
            error_payload = _error_payload(
                "adapter_child_error",
                str(error),
                code=error.code,
                details=cast(JsonValue, copy.deepcopy(error.details)),
                retryable=error.retryable,
            )
            if adapter is not None:
                try:
                    trace = tuple(_json_copy(event) for event in adapter.trace())
                    usage = _json_copy(adapter.usage())
                except AdapterError:
                    pass
        except AdapterError as error:
            error_payload = _error_payload(
                "adapter_error",
                str(error),
                adapter_error_type=type(error).__name__,
            )
        except Exception as error:
            error_payload = _error_payload(
                "execution_error",
                str(error),
                exception_type=type(error).__name__,
            )
        finally:
            stderr = _adapter_diagnostics(adapter)
            if adapter is not None:
                try:
                    adapter.close()
                except Exception as close_error:
                    close_payload: JsonObject = {
                        "type": "cleanup_error",
                        "message": str(close_error),
                        "exception_type": type(close_error).__name__,
                    }
                    if error_payload is None:
                        output = None
                        error_payload = close_payload
                    else:
                        error_payload["cleanup_error"] = cast(JsonValue, close_payload)
                    retryable = False

        finished_at = self._clock()
        duration_ms = max(0, round((self._monotonic_clock() - started_monotonic) * 1000))
        return AttemptRecord(
            attempt_number=context.attempt_number,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            output=output,
            error=error_payload,
            trace=trace,
            usage=usage,
            stderr=stderr,
            isolation_level=isolation_level,
            timed_out=timed_out,
            retryable=retryable,
        )

    def _resolve_policy(self, scenario: Scenario) -> tuple[float, int, int | None, NetworkPolicy]:
        policy = scenario.execution_policy
        timeout = self._positive_number(
            "execution_policy.timeout_seconds",
            policy.get("timeout_seconds", self._timeout_seconds),
        )
        retries = self._non_negative_integer(
            "execution_policy.max_retries",
            policy.get("max_retries", self._max_retries),
        )
        seed = self._optional_integer(
            "execution_policy.seed",
            policy.get("seed", self._seed),
        )
        network_raw = policy.get("network", self._network_policy.value)
        if not isinstance(network_raw, str):
            raise ExecutionPolicyError("execution_policy.network must be enabled or disabled")
        try:
            network = NetworkPolicy(network_raw)
        except ValueError as error:
            raise ExecutionPolicyError(
                "execution_policy.network must be enabled or disabled"
            ) from error
        return timeout, retries, seed, network

    @staticmethod
    def _tool_sequence(scenario: Scenario) -> tuple[JsonObject, ...]:
        value = scenario.fixtures.get("tool_sequence", [])
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ExecutionPolicyError("fixtures.tool_sequence must be a list of JSON objects")
        return tuple(copy.deepcopy(cast(list[JsonObject], value)))

    @staticmethod
    def _positive_number(name: str, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExecutionPolicyError(f"{name} must be a finite positive number")
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ExecutionPolicyError(f"{name} must be a finite positive number")
        return result

    @staticmethod
    def _non_negative_integer(name: str, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExecutionPolicyError(f"{name} must be a non-negative integer")
        return value

    @staticmethod
    def _optional_integer(name: str, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExecutionPolicyError(f"{name} must be an integer or null")
        return value
