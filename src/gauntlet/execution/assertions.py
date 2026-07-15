"""Deterministic, evidence-linked MVP assertion evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from pydantic import (
    Field,
    JsonValue,
    NonNegativeInt,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from gauntlet.adapters import JsonObject
from gauntlet.core.models import GauntletModel, Scenario, ScenarioResult, ScenarioResultStatus
from gauntlet.evidence import ScenarioEvidenceBundle
from gauntlet.execution.executor import ScenarioExecution, ScenarioLifecycleState


class AssertionConfigurationError(ValueError):
    """Raised when a benchmark assertion definition is invalid."""


class AssertionEvaluationError(RuntimeError):
    """Raised when required execution facts or evidence links are unavailable."""


class AssertionType(StrEnum):
    """The nine deterministic assertion types required by the MVP."""

    TOOL_CALLED = "tool_called"
    MAX_TOOL_CALLS = "max_tool_calls"
    OUTPUT_CONTAINS = "output_contains"
    OUTPUT_FIELD_EQUALS = "output_field_equals"
    SCHEMA_VALID = "schema_valid"
    NO_FORBIDDEN_CALLS = "no_forbidden_calls"
    MAX_STEPS = "max_steps"
    NO_HALLUCINATED_SUCCESS = "no_hallucinated_success"
    COMPLETED_BEFORE_TIMEOUT = "completed_before_timeout"


StrictNonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _AssertionDefinition(GauntletModel):
    pass


class ToolCalledDefinition(_AssertionDefinition):
    type: Literal[AssertionType.TOOL_CALLED]
    tool: StrictStr

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool must be non-blank")
        return value


class MaxToolCallsDefinition(_AssertionDefinition):
    type: Literal[AssertionType.MAX_TOOL_CALLS]
    value: StrictNonNegativeInt


class OutputContainsDefinition(_AssertionDefinition):
    type: Literal[AssertionType.OUTPUT_CONTAINS]
    value: JsonValue


class OutputFieldEqualsDefinition(_AssertionDefinition):
    type: Literal[AssertionType.OUTPUT_FIELD_EQUALS]
    field: StrictStr
    value: JsonValue

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be non-blank")
        return value


class SchemaValidDefinition(_AssertionDefinition):
    type: Literal[AssertionType.SCHEMA_VALID]
    schema_: JsonObject = Field(alias="schema", serialization_alias="schema")


class NoForbiddenCallsDefinition(_AssertionDefinition):
    type: Literal[AssertionType.NO_FORBIDDEN_CALLS]
    tools: list[StrictStr]

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("tools must contain at least one name")
        if any(not tool.strip() for tool in value):
            raise ValueError("tools must contain only non-blank names")
        if len(set(value)) != len(value):
            raise ValueError("tools must contain unique names")
        return value


class MaxStepsDefinition(_AssertionDefinition):
    type: Literal[AssertionType.MAX_STEPS]
    value: StrictNonNegativeInt


class NoHallucinatedSuccessDefinition(_AssertionDefinition):
    type: Literal[AssertionType.NO_HALLUCINATED_SUCCESS]


class CompletedBeforeTimeoutDefinition(_AssertionDefinition):
    type: Literal[AssertionType.COMPLETED_BEFORE_TIMEOUT]


AssertionDefinition: TypeAlias = Annotated[
    ToolCalledDefinition
    | MaxToolCallsDefinition
    | OutputContainsDefinition
    | OutputFieldEqualsDefinition
    | SchemaValidDefinition
    | NoForbiddenCallsDefinition
    | MaxStepsDefinition
    | NoHallucinatedSuccessDefinition
    | CompletedBeforeTimeoutDefinition,
    Field(discriminator="type"),
]
_DEFINITION_ADAPTER: TypeAdapter[AssertionDefinition] = TypeAdapter(AssertionDefinition)


class AssertionResult(GauntletModel):
    """One deterministic assertion outcome linked to its exact evidence inputs."""

    assertion_index: NonNegativeInt
    type: AssertionType
    passed: bool
    message: str
    details: JsonObject
    evidence_refs: list[str]

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be non-blank")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("evidence_refs must contain non-blank IDs")
        if len(set(value)) != len(value):
            raise ValueError("evidence_refs must contain unique IDs")
        return value


@dataclass(frozen=True, slots=True)
class AssertionEvaluation:
    """A scenario execution with ordered assertion outcomes applied to status."""

    execution: ScenarioExecution
    results: tuple[AssertionResult, ...]


@dataclass(frozen=True, slots=True)
class _EvaluationContext:
    scenario: Scenario
    execution: ScenarioExecution
    output: JsonObject | None
    trace: tuple[JsonObject, ...]
    refs_by_role: Mapping[str, tuple[str, ...]]
    known_evidence_ids: frozenset[str]


class AssertionEngine:
    """Validate and evaluate every declared deterministic assertion in order."""

    def evaluate(
        self,
        scenario: Scenario,
        bundle: ScenarioEvidenceBundle,
    ) -> AssertionEvaluation:
        """Evaluate all assertions and apply ordinary failures to scenario status."""

        definitions = self._definitions(scenario.assertions)
        execution = bundle.execution
        if execution.result.scenario_id != scenario.id:
            raise AssertionEvaluationError("Scenario and evidence bundle IDs do not match")
        if not execution.attempts:
            raise AssertionEvaluationError("Scenario execution has no attempt records")
        context = _EvaluationContext(
            scenario=scenario,
            execution=execution,
            output=execution.result.output,
            trace=execution.attempts[-1].trace,
            refs_by_role=bundle.refs_by_role,
            known_evidence_ids=frozenset(item.id for item in bundle.evidence),
        )
        results = tuple(
            self._evaluate_one(index, definition, context)
            for index, definition in enumerate(definitions)
        )
        if execution.result.status is ScenarioResultStatus.PASSED and any(
            not result.passed for result in results
        ):
            result_data = execution.result.model_dump(mode="json")
            result_data["status"] = ScenarioResultStatus.FAILED.value
            failed_result = ScenarioResult.model_validate(result_data)
            lifecycle = list(execution.lifecycle)
            if len(lifecycle) < 2 or lifecycle[-2] is not ScenarioLifecycleState.PASSED:
                raise AssertionEvaluationError(
                    "Passed scenario lifecycle is missing its passed terminal state"
                )
            lifecycle[-2] = ScenarioLifecycleState.FAILED
            execution = replace(execution, result=failed_result, lifecycle=tuple(lifecycle))
        return AssertionEvaluation(execution=execution, results=results)

    def _definitions(
        self,
        raw_definitions: Sequence[JsonObject],
    ) -> tuple[AssertionDefinition, ...]:
        definitions: list[AssertionDefinition] = []
        for index, raw in enumerate(raw_definitions):
            try:
                definition = _DEFINITION_ADAPTER.validate_python(raw, strict=True)
            except ValidationError as error:
                raise AssertionConfigurationError(
                    f"Assertion #{index + 1} is invalid: {error.errors(include_url=False)}"
                ) from error
            if isinstance(definition, SchemaValidDefinition):
                self._validate_schema(definition.schema_, index)
            definitions.append(definition)
        return tuple(definitions)

    def _evaluate_one(
        self,
        index: int,
        definition: AssertionDefinition,
        context: _EvaluationContext,
    ) -> AssertionResult:
        if isinstance(definition, ToolCalledDefinition):
            events = self._tool_events(context.trace)
            count = sum(event["tool"] == definition.tool for event in events)
            return self._result(
                index,
                definition.type,
                count > 0,
                "Required tool call observed" if count else "Required tool call was not observed",
                {"tool": definition.tool, "matching_calls": count},
                self._refs(context, "trace"),
            )
        if isinstance(definition, MaxToolCallsDefinition):
            count = len(self._tool_events(context.trace))
            passed = count <= definition.value
            return self._result(
                index,
                definition.type,
                passed,
                "Tool-call limit satisfied" if passed else "Tool-call limit exceeded",
                {"actual_count": count, "maximum": definition.value},
                self._refs(context, "trace"),
            )
        if isinstance(definition, OutputContainsDefinition):
            passed = self._contains(context.output, definition.value)
            return self._result(
                index,
                definition.type,
                passed,
                "Expected output value observed"
                if passed
                else "Expected output value was not observed",
                {},
                self._refs(context, "output"),
            )
        if isinstance(definition, OutputFieldEqualsDefinition):
            output = context.output
            present = output is not None and definition.field in output
            passed = (
                present
                and output is not None
                and self._json_equal(output[definition.field], definition.value)
            )
            return self._result(
                index,
                definition.type,
                passed,
                "Output field matched" if passed else "Output field was missing or did not match",
                {"field": definition.field, "field_present": present},
                self._refs(context, "output"),
            )
        if isinstance(definition, SchemaValidDefinition):
            validator = Draft202012Validator(definition.schema_, format_checker=FormatChecker())
            errors = sorted(
                validator.iter_errors(context.output), key=lambda error: list(error.path)
            )
            details: JsonObject = {"error_count": len(errors)}
            if errors:
                first = errors[0]
                details.update(
                    {
                        "first_error_path": "/".join(str(part) for part in first.path),
                        "first_error_keyword": str(first.validator),
                    }
                )
            return self._result(
                index,
                definition.type,
                not errors,
                "Output schema matched" if not errors else "Output schema did not match",
                details,
                self._refs(context, "output"),
            )
        if isinstance(definition, NoForbiddenCallsDefinition):
            forbidden = set(definition.tools)
            matches = [
                event for event in self._tool_events(context.trace) if event["tool"] in forbidden
            ]
            return self._result(
                index,
                definition.type,
                not matches,
                "No forbidden tool call observed"
                if not matches
                else "Forbidden tool call observed",
                {"forbidden_call_count": len(matches)},
                self._refs(context, "trace"),
            )
        if isinstance(definition, MaxStepsDefinition):
            count = len(context.trace)
            passed = count <= definition.value
            return self._result(
                index,
                definition.type,
                passed,
                "Step limit satisfied" if passed else "Step limit exceeded",
                {"actual_count": count, "maximum": definition.value},
                self._refs(context, "trace"),
            )
        if isinstance(definition, NoHallucinatedSuccessDefinition):
            passed, consumed, expected = self._fixture_success_supported(context)
            return self._result(
                index,
                definition.type,
                passed,
                "Success is supported by fixture evidence"
                if passed
                else "Claimed success is not supported by consumed fixtures",
                {"consumed_fixtures": consumed, "expected_fixtures": expected},
                self._refs(context, "output", "trace", "fixtures"),
            )
        if isinstance(definition, CompletedBeforeTimeoutDefinition):
            completed = context.execution.result.status in {
                ScenarioResultStatus.PASSED,
                ScenarioResultStatus.FAILED,
            }
            timed_out = context.execution.result.status is ScenarioResultStatus.TIMED_OUT
            roles = ["execution"]
            if timed_out and "exception" in context.refs_by_role:
                roles.append("exception")
            return self._result(
                index,
                definition.type,
                completed and not timed_out,
                "Execution completed before timeout"
                if completed and not timed_out
                else "Execution did not complete before timeout",
                {"completed_normally": completed, "timed_out": timed_out},
                self._refs(context, *roles),
            )
        raise AssertionEvaluationError(
            f"Unsupported assertion definition: {type(definition).__name__}"
        )

    @staticmethod
    def _result(
        index: int,
        assertion_type: AssertionType,
        passed: bool,
        message: str,
        details: JsonObject,
        evidence_refs: list[str],
    ) -> AssertionResult:
        return AssertionResult(
            assertion_index=index,
            type=assertion_type,
            passed=passed,
            message=message,
            details=details,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _refs(context: _EvaluationContext, *roles: str) -> list[str]:
        refs: list[str] = []
        for role in roles:
            role_refs = context.refs_by_role.get(role)
            if not role_refs:
                raise AssertionEvaluationError(f"Assertion requires missing {role!r} evidence")
            refs.extend(role_refs)
        unique_refs = list(dict.fromkeys(refs))
        unknown = sorted(set(unique_refs) - context.known_evidence_ids)
        if unknown:
            raise AssertionEvaluationError(
                "Assertion references unknown evidence IDs: " + ", ".join(unknown)
            )
        return unique_refs

    @staticmethod
    def _tool_events(trace: Sequence[JsonObject]) -> list[JsonObject]:
        events: list[JsonObject] = []
        for event in trace:
            if event.get("type") != "tool_call":
                continue
            tool = event.get("tool")
            if not isinstance(tool, str) or not tool:
                raise AssertionEvaluationError("Tool-call trace event is missing a valid tool name")
            events.append(event)
        return events

    @classmethod
    def _contains(cls, candidate: JsonValue, expected: JsonValue) -> bool:
        if cls._json_equal(candidate, expected):
            return True
        if isinstance(candidate, str) and isinstance(expected, str):
            return expected in candidate
        if isinstance(candidate, dict):
            return any(cls._contains(value, expected) for value in candidate.values())
        if isinstance(candidate, list):
            return any(cls._contains(value, expected) for value in candidate)
        return False

    @classmethod
    def _json_equal(cls, left: JsonValue, right: JsonValue) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return isinstance(left, bool) and isinstance(right, bool) and left == right
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left == right
        if type(left) is not type(right):
            return False
        if isinstance(left, dict) and isinstance(right, dict):
            return left.keys() == right.keys() and all(
                cls._json_equal(left[key], right[key]) for key in left
            )
        if isinstance(left, list) and isinstance(right, list):
            return len(left) == len(right) and all(
                cls._json_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        return left == right

    @classmethod
    def _fixture_success_supported(cls, context: _EvaluationContext) -> tuple[bool, int, int]:
        if context.output is None or context.output.get("completed") is not True:
            return True, 0, 0
        raw_sequence = context.scenario.fixtures.get("tool_sequence", [])
        if not isinstance(raw_sequence, list) or not all(
            isinstance(item, dict) for item in raw_sequence
        ):
            raise AssertionEvaluationError("fixtures.tool_sequence is not a JSON object list")
        sequence = cast(list[JsonObject], raw_sequence)
        expected_index = 0
        for event in cls._tool_events(context.trace):
            if event.get("policy_result") != "allowed" or expected_index >= len(sequence):
                continue
            fixture = sequence[expected_index]
            if event.get("fixture_index") == expected_index and event.get("tool") == fixture.get(
                "tool"
            ):
                expected_index += 1
        return expected_index == len(sequence), expected_index, len(sequence)

    @classmethod
    def _validate_schema(cls, schema: JsonObject, index: int) -> None:
        external_ref = cls._external_ref(schema)
        if external_ref is not None:
            raise AssertionConfigurationError(
                f"Assertion #{index + 1} schema uses a non-local $ref: {external_ref}"
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise AssertionConfigurationError(
                f"Assertion #{index + 1} contains an invalid JSON Schema: {error.message}"
            ) from error

    @classmethod
    def _external_ref(cls, value: JsonValue) -> str | None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and not reference.startswith("#"):
                return reference
            for nested in value.values():
                result = cls._external_ref(nested)
                if result is not None:
                    return result
        elif isinstance(value, list):
            for nested in value:
                result = cls._external_ref(nested)
                if result is not None:
                    return result
        return None
