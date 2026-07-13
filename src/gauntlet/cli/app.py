"""GAUNTLET command-line application."""

from typing import Annotated

import typer

from gauntlet import __version__

app = typer.Typer(
    help="Evaluate agentic AI systems with reproducible evidence.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    """Print the installed version for the eager root option."""
    if value:
        typer.echo(f"gauntlet {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed GAUNTLET version and exit.",
        ),
    ] = None,
) -> None:
    """Evaluate agentic AI systems."""
