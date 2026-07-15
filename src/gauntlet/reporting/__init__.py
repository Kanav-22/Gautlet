"""Evaluation report generation."""

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
    "ReportArtifacts",
    "ReportContext",
    "ReportGenerationError",
    "ReportGenerator",
    "RunSummary",
    "exit_code_for_recommendation",
]
