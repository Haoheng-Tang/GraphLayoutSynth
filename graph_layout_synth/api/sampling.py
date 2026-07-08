"""Mockable graph-sampling boundary for next-room prediction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Hashable, Protocol

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config
from graph_layout_synth.generator import generate_candidates
from graph_layout_synth.grammar_variant_control_plane import (
    GrammarVariantControlPlaneError,
    active_variant_config_path,
)

GRAMMAR_MODE_ENV = "GRAPHLAYOUTSYNTH_GRAMMAR_MODE"
SUGGESTION_CONFIG_PATH_ENV = "GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG"
GRAMMAR_MODE_STATIC = "static"
GRAMMAR_MODE_ENV_CONFIG = "env_config"
GRAMMAR_MODE_ACTIVE_VARIANT = "active_variant"


class GraphSampler(Protocol):
    """Generate raw candidate graphs for semantic matching."""

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        """Return up to ``sample_count`` generated graph samples."""


@dataclass
class ExistingGeneratorSampler:
    """Expose existing seed-based generation behind the sampler boundary.

    The current grammar cannot expand arbitrary concrete partial graphs, so it
    generates ordinary candidates. Semantic matching and extra-neighbor
    aggregation happen after sampling and can consume every matching node.
    This boundary remains mockable for tests and replaceable by future true
    conditional generation.
    """

    config: LayoutConfig | None = None
    seed: int | None = None

    def resolved_config(self) -> LayoutConfig:
        """Return the config used for suggestion graph generation."""
        if self.config is not None:
            return self.config

        mode = os.getenv(GRAMMAR_MODE_ENV, "").strip().lower()
        configured_path = os.getenv(SUGGESTION_CONFIG_PATH_ENV)
        if not mode:
            mode = GRAMMAR_MODE_ENV_CONFIG if configured_path else GRAMMAR_MODE_STATIC

        if mode == GRAMMAR_MODE_STATIC:
            config = load_config()
        elif mode == GRAMMAR_MODE_ENV_CONFIG:
            if not configured_path:
                raise ValueError(
                    f"{SUGGESTION_CONFIG_PATH_ENV} must be set when "
                    f"{GRAMMAR_MODE_ENV}=env_config."
                )
            config = load_config(Path(configured_path).expanduser())
        elif mode == GRAMMAR_MODE_ACTIVE_VARIANT:
            try:
                config_path = active_variant_config_path()
            except GrammarVariantControlPlaneError as exc:
                raise ValueError(str(exc)) from exc
            config = load_config(config_path)
        else:
            raise ValueError(
                f"Unsupported {GRAMMAR_MODE_ENV} '{mode}'. Expected static, "
                "env_config, or active_variant."
            )
        self.config = config
        return config

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        if anchor_node_id not in partial_graph:
            raise ValueError(f"Anchor node '{anchor_node_id}' is not present in the graph.")

        config = self.resolved_config()
        results = generate_candidates(sample_count, seed=self.seed, config=config)
        return [result.graph for result in results]
