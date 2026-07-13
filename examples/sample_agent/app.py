"""Canonical entry point for the correct sample agent."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .variants.correct import run as run_correct


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Run the canonical lookup-then-save workflow."""

    return run_correct(payload, tools=tools)
