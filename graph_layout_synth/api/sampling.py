"""Mockable graph-sampling boundary for next-room prediction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Protocol

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config
from graph_layout_synth.generator import generate_candidates


class GraphSampler(Protocol):
    """Generate graph samples that preserve the supplied anchor node."""

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        """Return up to ``sample_count`` predicted graph samples."""


@dataclass
class ExistingGeneratorSampler:
    """Adapt the seed-based generator to one-hop partial-graph prediction.

    The current grammar cannot expand arbitrary concrete partial graphs. This
    adapter therefore generates ordinary candidates, finds a generated node
    with the anchor's room type, and projects that node's neighborhood onto a
    copy of the partial graph. It is intentionally isolated so true
    conditional generation can replace it without changing the API service.
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
        anchor_type = partial_graph.nodes[anchor_node_id].get("type")
        return [
            self._project_generated_neighborhood(
                partial_graph,
                anchor_node_id,
                anchor_type,
                result.graph,
                sample_index,
            )
            for sample_index, result in enumerate(results)
        ]

    @staticmethod
    def _project_generated_neighborhood(
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        anchor_type: str | None,
        generated_graph: nx.Graph,
        sample_index: int,
    ) -> nx.Graph:
        sample = partial_graph.copy()
        matching_nodes = sorted(
            (
                node
                for node, attributes in generated_graph.nodes(data=True)
                if attributes.get("type") == anchor_type
            ),
            key=str,
        )
        if not matching_nodes:
            return sample

        generated_anchor = matching_nodes[sample_index % len(matching_nodes)]
        for neighbor_index, generated_neighbor in enumerate(
            generated_graph.neighbors(generated_anchor)
        ):
            projected_id = ("predicted", sample_index, neighbor_index)
            node_attributes = dict(generated_graph.nodes[generated_neighbor])
            node_attributes.pop("external_id", None)
            node_attributes["is_predicted"] = True
            sample.add_node(projected_id, **node_attributes)
            sample.add_edge(
                anchor_node_id,
                projected_id,
                **dict(generated_graph.edges[generated_anchor, generated_neighbor]),
            )
        return sample
