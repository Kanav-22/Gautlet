"""Generate normalized JSON and a safe evidence-summary Markdown report."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar, cast

from pydantic import JsonValue

from gauntlet.core.models import Finding, FindingSeverity, GauntletModel, ScenarioResult
from gauntlet.evidence import RunArtifactStore, SecretRedactor
from gauntlet.reporting.models import ReportArtifacts, ReportContext
from gauntlet.scoring import ScoringOutcome

ModelT = TypeVar("ModelT", bound=GauntletModel)


class ReportGenerationError(RuntimeError):
    """Raised when report inputs cannot be published safely."""


class ReportGenerator:
    """Write the canonical machine artifacts and Markdown from one safe model set."""

    def __init__(self, run_store: RunArtifactStore) -> None:
        self.run_store = run_store

    def write(
        self,
        run_id: str,
        *,
        results: Sequence[ScenarioResult],
        scoring: ScoringOutcome,
        findings: Sequence[Finding],
        context: ReportContext,
        redactor: SecretRedactor,
    ) -> ReportArtifacts:
        """Redact every persisted representation and write fixed-name artifacts."""

        clean_results = tuple(
            _sanitized_model(result, ScenarioResult, redactor) for result in results
        )
        scenario_ids = [result.scenario_id for result in clean_results]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ReportGenerationError("Report results contain duplicate scenario IDs")
        clean_scoring = _sanitized_model(scoring, ScoringOutcome, redactor)
        clean_findings = tuple(_sanitized_model(finding, Finding, redactor) for finding in findings)
        clean_context = _sanitized_model(context, ReportContext, redactor)

        results_path = self.run_store.write_results(run_id, clean_results)
        scorecard_path = self.run_store.write_scorecard(
            run_id,
            clean_scoring.scorecard,
        )
        findings_path = self.run_store.write_findings(run_id, clean_findings)
        markdown = _render_markdown(
            clean_results,
            clean_scoring,
            clean_findings,
            clean_context,
        )
        markdown_redaction = redactor.redact(markdown)
        if not isinstance(markdown_redaction.value, str):  # pragma: no cover - type invariant
            raise ReportGenerationError("Markdown redaction did not return text")
        markdown_path = self.run_store.write_report(run_id, markdown_redaction.value)
        return ReportArtifacts(
            results=results_path,
            scorecard=scorecard_path,
            findings=findings_path,
            markdown=markdown_path,
        )


def _sanitized_model(
    model: ModelT,
    model_type: type[ModelT],
    redactor: SecretRedactor,
) -> ModelT:
    raw = cast(JsonValue, model.model_dump(mode="json"))
    redacted = redactor.redact(raw).value
    try:
        return model_type.model_validate(redacted)
    except ValueError as error:
        raise ReportGenerationError(
            f"Redaction made {model_type.__name__} invalid: {error}"
        ) from error


def _render_markdown(
    results: Sequence[ScenarioResult],
    scoring: ScoringOutcome,
    findings: Sequence[Finding],
    context: ReportContext,
) -> str:
    high_risk = [
        finding
        for finding in findings
        if finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
    ]
    lines = [
        "# GAUNTLET Evaluation Report",
        "",
        "## Executive summary",
        "",
        f"- Release recommendation: `{scoring.recommendation.value}`",
        f"- Overall score: {scoring.scorecard.overall:.2f}/100",
        f"- Confidence: {scoring.scorecard.confidence:.2f}",
        f"- Scoring policy: `{_table_text(scoring.scorecard.policy_id)}`",
        f"- Scenarios completed: {scoring.scenarios_completed}",
        f"- Critical risks: {len(high_risk)} high or critical finding(s)",
        "- Major regressions: not assessed (no comparison baseline)",
        "",
        "Scores summarize stored evidence; they do not replace it.",
        "",
        "## Policy rules",
        "",
        "| Rule | Triggered | Observed effect |",
        "|---|---:|---|",
    ]
    lines.extend(
        f"| {_table_text(rule.rule_id)} | {'yes' if rule.triggered else 'no'} | "
        f"{_table_text(rule.effect)} |"
        for rule in scoring.policy_rules
    )
    lines.extend(["", "## Findings", ""])
    if findings:
        lines.extend(
            [
                "| Severity | Dimension | Finding | Remediation |",
                "|---|---|---|---|",
            ]
        )
        lines.extend(
            f"| {finding.severity.value} | {finding.dimension.value} | "
            f"{_table_text(finding.title)} | "
            f"{_table_text(finding.remediation or 'not provided')} |"
            for finding in findings
        )
    else:
        lines.append("No findings were supplied for this evaluation.")

    lines.extend(["", "## Top remediation priorities", ""])
    remediation_findings = [finding for finding in findings if finding.remediation]
    if remediation_findings:
        lines.extend(
            f"- **{finding.severity.value}** {_table_text(finding.title)}: "
            f"{_table_text(finding.remediation or '')}"
            for finding in remediation_findings[:5]
        )
    else:
        lines.append("No remediation priorities were supplied.")

    lines.extend(
        [
            "",
            "## Scenario results",
            "",
            "| Scenario | Status | Latency (ms) | Task success | Tool calls | Retries | Steps | Token/cost usage |",
            "|---|---|---:|---|---:|---:|---:|---|",
        ]
    )
    for result in results:
        usage = result.metrics.get("observed_usage")
        usage_text = _usage_text(usage)
        lines.append(
            f"| {_table_text(result.scenario_id)} | {result.status.value} | "
            f"{result.duration_ms} | {_metric_text(result.metrics.get('task_success'))} | "
            f"{_metric_text(result.metrics.get('tool_calls'))} | "
            f"{_metric_text(result.metrics.get('retries'))} | "
            f"{_metric_text(result.metrics.get('steps'))} | {_table_text(usage_text)} |"
        )

    lines.extend(
        [
            "",
            "## Environment and provenance",
            "",
            f"- Execution mode: `{context.execution_mode.value}`",
            f"- Isolation level: `{_table_text(context.isolation_level)}`",
            f"- Seed: `{context.seed if context.seed is not None else 'not set'}`",
            f"- Environment fingerprint: `{_table_text(context.environment_fingerprint)}`",
            f"- Configuration fingerprint: `{_table_text(context.config_fingerprint)}`",
            f"- GAUNTLET version: `{_table_text(context.gauntlet_version)}`",
            f"- Python: `{_table_text(context.python_version)}`",
            f"- Platform: `{_table_text(context.platform)}`",
            "- Benchmark packs: "
            + ", ".join(
                f"`{_table_text(pack.id)}@{_table_text(pack.version)}` "
                f"(schema {pack.schema_version})"
                for pack in context.benchmark_packs
            ),
            "",
            "Subprocess isolation provides process separation for the MVP; it is not a hardened sandbox for malicious code.",
            "",
            "Raw outputs, fixtures, hidden expected values, and evidence contents are intentionally omitted from this report.",
            "",
        ]
    )
    return "\n".join(lines)


def _metric_text(value: JsonValue | None) -> str:
    if value is None:
        return "not reported"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (str, int, float)):
        return _table_text(str(value))
    return "not reported"


def _usage_text(value: JsonValue | None) -> str:
    if not isinstance(value, dict) or not value:
        return "not reported"
    parts = [
        f"{name}={counter}"
        for name, counter in sorted(value.items())
        if isinstance(counter, (int, float)) and not isinstance(counter, bool)
    ]
    return ", ".join(parts) if parts else "not reported"


def _table_text(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace("|", "\\|").replace("`", "\\`")
