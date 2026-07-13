"""Tests for the GAUNTLET command-line skeleton."""

from importlib import metadata

from typer.testing import CliRunner

from gauntlet import __version__
from gauntlet.cli import app

runner = CliRunner()


def test_version_option() -> None:
    """The eager root option reports the installed package version."""
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"gauntlet {__version__}\n"


def test_package_version_matches_distribution_metadata() -> None:
    """Package code and installed distribution expose one version source."""
    assert metadata.version("gauntlet") == __version__
