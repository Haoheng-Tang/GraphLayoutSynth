"""Mockable graph-sampling boundary for next-room prediction."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Hashable, Protocol

import networkx as nx

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, LayoutConfig, load_config
from graph_layout_synth.generator import generate_candidates
from graph_layout_synth.grammar_variant_control_plane import (
    GrammarVariantControlPlaneError,
    active_variant_pointer,
)

GRAMMAR_MODE_ENV = "GRAPHLAYOUTSYNTH_GRAMMAR_MODE"
SUGGESTION_CONFIG_PATH_ENV = "GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG"
GRAMMAR_MODE_STATIC = "static"
GRAMMAR_MODE_ENV_CONFIG = "env_config"
GRAMMAR_MODE_ACTIVE_VARIANT = "active_variant"
CONFIG_SOURCE_INJECTED = "injected"


@dataclass(frozen=True)
class SuggestionConfigSource:
    """Where the suggestion sampler's config comes from for one request."""

    mode: str
    config_path: Path | None
    variant_id: str | None = None

    def as_report_dict(self) -> dict[str, str | None]:
        """JSON-safe form for debug artifacts and logs."""
        return {
            "mode": self.mode,
            "configPath": str(self.config_path) if self.config_path else None,
            "variantId": self.variant_id,
        }


def resolve_suggestion_config_source() -> SuggestionConfigSource:
    """Resolve the current grammar mode into a concrete config source.

    This is re-evaluated on every suggestion request (and shared with the
    room-type catalog), so activating a different variant takes effect
    immediately without a server restart. In ``active_variant`` mode a
    missing or broken pointer raises instead of falling back to the base
    config, so it is always explicit whether variants are actually used.
    """
    mode = os.getenv(GRAMMAR_MODE_ENV, "").strip().lower()
    configured_path = os.getenv(SUGGESTION_CONFIG_PATH_ENV)
    if not mode:
        mode = GRAMMAR_MODE_ENV_CONFIG if configured_path else GRAMMAR_MODE_STATIC

    if mode == GRAMMAR_MODE_STATIC:
        return SuggestionConfigSource(
            mode=GRAMMAR_MODE_STATIC,
            config_path=Path(DEFAULT_CONFIG_PATH),
        )
    if mode == GRAMMAR_MODE_ENV_CONFIG:
        if not configured_path:
            raise ValueError(
                f"{SUGGESTION_CONFIG_PATH_ENV} must be set when "
                f"{GRAMMAR_MODE_ENV}=env_config."
            )
        return SuggestionConfigSource(
            mode=GRAMMAR_MODE_ENV_CONFIG,
            config_path=Path(configured_path).expanduser(),
        )
    if mode == GRAMMAR_MODE_ACTIVE_VARIANT:
        try:
            pointer = active_variant_pointer()
        except GrammarVariantControlPlaneError as exc:
            raise ValueError(str(exc)) from exc
        config_path = Path(pointer["validatedConfigPath"])
        if not config_path.is_file():
            raise ValueError(
                "Active grammar variant config file does not exist: "
                f"{config_path}"
            )
        variant_id = pointer.get("variantId")
        return SuggestionConfigSource(
            mode=GRAMMAR_MODE_ACTIVE_VARIANT,
            config_path=config_path,
            variant_id=variant_id if isinstance(variant_id, str) else None,
        )
    raise ValueError(
        f"Unsupported {GRAMMAR_MODE_ENV} '{mode}'. Expected static, "
        "env_config, or active_variant."
    )


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

    ``config`` is an explicit injected override (tests, embedding callers) and
    always wins. Otherwise the config source is re-resolved on every call so
    that activating a grammar variant mid-process takes effect immediately;
    the parsed config is only reused while the resolved source (mode, path,
    variant) is unchanged, because one sampler instance lives in
    ``app.state.predictor`` for the whole server process.
    """

    config: LayoutConfig | None = None
    seed: int | None = None
    last_resolved_config: LayoutConfig | None = field(
        default=None, init=False, repr=False, compare=False
    )
    last_config_source: SuggestionConfigSource | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _cached_config: LayoutConfig | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _cached_source: SuggestionConfigSource | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def resolved_config(self) -> LayoutConfig:
        """Return the config used for suggestion graph generation."""
        if self.config is not None:
            self.last_config_source = SuggestionConfigSource(
                mode=CONFIG_SOURCE_INJECTED,
                config_path=None,
            )
            self.last_resolved_config = self.config
            return self.config

        source = resolve_suggestion_config_source()
        if self._cached_config is None or self._cached_source != source:
            self._cached_config = self._load_config_for_source(source)
            self._cached_source = source
        self.last_config_source = source
        self.last_resolved_config = self._cached_config
        return self._cached_config

    @staticmethod
    def _load_config_for_source(source: SuggestionConfigSource) -> LayoutConfig:
        if source.mode == GRAMMAR_MODE_STATIC:
            return load_config()
        return load_config(source.config_path)

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
