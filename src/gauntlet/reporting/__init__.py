"""Evaluation report generation."""

from gauntlet.reporting.compare import (
    ComparisonArtifactError,
    ComparisonInputError,
    ContextChange,
    ContextChangeKind,
    NumericDelta,
    RegressionAssessment,
    RunComparison,
    RunComparisonService,
    format_run_comparison,
)
from gauntlet.reporting.generator import ReportGenerationError, ReportGenerator
from gauntlet.reporting.models import (
    BenchmarkProvenance,
    ExecutionMode,
    ReportArtifacts,
    ReportContext,
    RunSummary,
)
from gauntlet.reporting.pipeline import (
    EvaluationConfigurationError,
    EvaluationExecutionError,
    EvaluationExitCode,
    EvaluationPipeline,
    EvaluationPipelineError,
    EvaluationPipelineResult,
    EvaluationRequest,
    EvaluationSecurityError,
    IncompleteEvaluationError,
    exit_code_for_recommendation,
)

__all__ = [
    "BenchmarkProvenance",
    "ComparisonArtifactError",
    "ComparisonInputError",
    "ContextChange",
    "ContextChangeKind",
    "EvaluationConfigurationError",
    "EvaluationExecutionError",
    "EvaluationExitCode",
    "EvaluationPipeline",
    "EvaluationPipelineError",
    "EvaluationPipelineResult",
    "EvaluationRequest",
    "EvaluationSecurityError",
    "ExecutionMode",
    "IncompleteEvaluationError",
    "NumericDelta",
    "RegressionAssessment",
    "ReportArtifacts",
    "ReportContext",
    "ReportGenerationError",
    "ReportGenerator",
    "RunSummary",
    "RunComparison",
    "RunComparisonService",
    "exit_code_for_recommendation",
    "format_run_comparison",
]
