"""Benchmark-pack loader and validation CLI tests."""

from pathlib import Path
from shutil import copytree

import pytest
from typer.testing import CliRunner

from gauntlet.benchmarks import (
    BenchmarkCapabilityError,
    BenchmarkPackError,
    BenchmarkPackIdentity,
    load_benchmark_pack,
)
from gauntlet.cli import app

runner = CliRunner()
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "benchmarks"
VALID_PACK = FIXTURE_ROOT / "minimal_valid"
INVALID_MANIFEST = FIXTURE_ROOT / "invalid_manifest"


def copy_valid_pack(tmp_path: Path) -> Path:
    """Copy the valid static pack so an individual test can mutate it."""
    return copytree(VALID_PACK, tmp_path / "benchmark")


def test_load_valid_pack_preserves_identity_order_and_paths() -> None:
    loaded = load_benchmark_pack(
        VALID_PACK,
        available_capabilities={"trace_tool_calls", "invoke", "unused"},
    )

    assert loaded.identity == BenchmarkPackIdentity(
        id="gauntlet.test.minimal",
        version="0.1.0",
        schema_version=1,
    )
    assert loaded.root == VALID_PACK.resolve()
    assert loaded.manifest_path == (VALID_PACK / "manifest.yaml").resolve()
    assert loaded.manifest.scenarios == ["scenarios/basic.yaml"]
    assert tuple(scenario.id for scenario in loaded.scenarios) == ("test.basic",)
    assert loaded.scenario_paths == ((VALID_PACK / "scenarios" / "basic.yaml").resolve(),)
    assert loaded.scoring_policy_path == (VALID_PACK / "scoring.yaml").resolve()


def test_load_accepts_explicit_manifest_path() -> None:
    loaded = load_benchmark_pack(VALID_PACK / "manifest.yaml")

    assert loaded.identity.id == "gauntlet.test.minimal"
    assert loaded.root == VALID_PACK.resolve()


def test_load_reports_missing_adapter_capabilities() -> None:
    with pytest.raises(BenchmarkCapabilityError) as caught:
        load_benchmark_pack(VALID_PACK, available_capabilities={"invoke"})

    message = str(caught.value)
    assert "gauntlet.test.minimal" in message
    assert "trace_tool_calls" in message
    assert "available: invoke" in message


@pytest.mark.parametrize(
    ("manifest_text", "expected"),
    [
        ("[unterminated", "Invalid YAML"),
        ("- not\n- a\n- mapping\n", "must contain a YAML mapping"),
    ],
)
def test_load_normalizes_malformed_and_nonmapping_yaml_errors(
    tmp_path: Path,
    manifest_text: str,
    expected: str,
) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(manifest_text, encoding="utf-8")

    with pytest.raises(BenchmarkPackError, match=expected):
        load_benchmark_pack(tmp_path)


def test_load_reports_manifest_schema_errors() -> None:
    with pytest.raises(BenchmarkPackError) as caught:
        load_benchmark_pack(INVALID_MANIFEST)

    message = str(caught.value)
    assert "Benchmark manifest schema validation failed" in message
    assert "title" in message
    assert "Field required" in message


def test_load_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    pack = copy_valid_pack(tmp_path)
    manifest = pack / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 99"),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkPackError, match="unsupported schema_version 99"):
        load_benchmark_pack(pack)


def test_load_rejects_scenario_path_escape(tmp_path: Path) -> None:
    pack = copy_valid_pack(tmp_path)
    manifest = pack / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  - scenarios/basic.yaml",
            "  - ../outside.yaml",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkPackError, match="escapes the benchmark directory"):
        load_benchmark_pack(pack)


def test_load_rejects_duplicate_scenario_path(tmp_path: Path) -> None:
    pack = copy_valid_pack(tmp_path)
    manifest = pack / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  - scenarios/basic.yaml",
            "  - scenarios/basic.yaml\n  - scenarios/basic.yaml",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkPackError, match="lists scenario .* twice"):
        load_benchmark_pack(pack)


def test_load_rejects_duplicate_scenario_id(tmp_path: Path) -> None:
    pack = copy_valid_pack(tmp_path)
    duplicate = pack / "scenarios" / "duplicate.yaml"
    duplicate.write_text(
        (pack / "scenarios" / "basic.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = pack / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  - scenarios/basic.yaml",
            "  - scenarios/basic.yaml\n  - scenarios/duplicate.yaml",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkPackError, match="duplicate scenario id 'test.basic'"):
        load_benchmark_pack(pack)


def test_load_rejects_scenario_capability_undeclared_by_manifest(tmp_path: Path) -> None:
    pack = copy_valid_pack(tmp_path)
    scenario = pack / "scenarios" / "basic.yaml"
    scenario.write_text(
        scenario.read_text(encoding="utf-8").replace(
            "  - trace_tool_calls\ninput:",
            "  - trace_tool_calls\n  - browse_network\ninput:",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkCapabilityError) as caught:
        load_benchmark_pack(pack)

    message = str(caught.value)
    assert "test.basic" in message
    assert "browse_network" in message
    assert "gauntlet.test.minimal" in message


def test_benchmark_validate_cli_accepts_valid_pack() -> None:
    result = runner.invoke(app, ["benchmark", "validate", str(VALID_PACK)])

    assert result.exit_code == 0, result.output
    assert "Valid benchmark gauntlet.test.minimal version 0.1.0" in result.output
    assert "schema 1, 1 scenarios" in result.output


def test_benchmark_validate_cli_rejects_invalid_pack_without_traceback() -> None:
    result = runner.invoke(app, ["benchmark", "validate", str(INVALID_MANIFEST)])

    assert result.exit_code == 2
    assert "Error:" in result.output
    assert "title" in result.output
    assert "Traceback" not in result.output
