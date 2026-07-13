"""Deterministic JSON-schema export for GAUNTLET contracts."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from gauntlet.config.models import GauntletConfig
from gauntlet.core.models import (
    BenchmarkPackManifest,
    EvaluationRun,
    Evidence,
    Finding,
    GauntletModel,
    Scenario,
    ScenarioResult,
    ScoreCard,
)

SCHEMA_MODELS: Final[Mapping[str, type[GauntletModel]]] = {
    "evaluation-run": EvaluationRun,
    "scenario": Scenario,
    "scenario-result": ScenarioResult,
    "evidence": Evidence,
    "finding": Finding,
    "score-card": ScoreCard,
    "benchmark-pack-manifest": BenchmarkPackManifest,
    "gauntlet-config": GauntletConfig,
}


def get_json_schemas() -> dict[str, dict[str, object]]:
    """Return validation-mode JSON schemas for every public model."""
    return {
        name: model.model_json_schema(mode="validation") for name, model in SCHEMA_MODELS.items()
    }


def export_json_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write stable, human-readable schema documents to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, schema in get_json_schemas().items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return tuple(paths)
