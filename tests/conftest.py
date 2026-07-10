"""Shared test fixtures for GraphLayoutSynth."""

from __future__ import annotations

import pytest


# Service behavior must never depend on the developer's real shell
# environment or `.env.local` during tests. `load_llm_environment` writes
# `.env.local` keys directly into `os.environ`, so a test that loads it (for
# example through the grammar-variant proposal flow with the default
# `env_path`) would otherwise leak variables such as
# `GRAPHLAYOUTSYNTH_GRAMMAR_MODE` into every later test in the process.
SERVICE_ENVIRONMENT_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS",
    "GRAPHLAYOUTSYNTH_LLM_VARIANT_DIR",
    "GRAPHLAYOUTSYNTH_GRAMMAR_MODE",
    "GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG",
    "GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS",
    "GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_PNGS",
    "GRAPHLAYOUTSYNTH_SUGGESTION_ARTIFACT_DIR",
    "NEXT_ROOM_ALLOWED_ORIGINS",
)


@pytest.fixture(autouse=True)
def isolate_service_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear service environment variables before every test."""
    for name in SERVICE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
