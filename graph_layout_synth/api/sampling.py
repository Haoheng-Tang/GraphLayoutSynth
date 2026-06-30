"""Mockable graph-sampling boundary for next-room prediction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Protocol

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config
from graph_layout_synth.generator import generate_candidates


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

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        if anchor_node_id not in partial_graph:
            raise ValueError(f"Anchor node '{anchor_node_id}' is not present in the graph.")

        config = self.config or load_config()
        results = generate_candidates(sample_count, seed=self.seed, config=config)
        return [result.graph for result in results]
