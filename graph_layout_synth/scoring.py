"""Simple rule-based scoring for generated graphs."""

from __future__ import annotations

import networkx as nx

from graph_layout_synth.validators import (
    ValidationResult,
    abstract_nodes,
    invalid_edge_types,
    room_has_corridor_access,
)


def score_graph(graph: nx.Graph, validation: ValidationResult | None = None) -> float:
    """Score a graph using simple rewards and penalties."""
    score = 0.0

    if validation and validation.is_valid:
        score += 100.0

    for node, attrs in graph.nodes(data=True):
        if attrs.get("type") != "Corridor" and not attrs.get("is_abstract", False):
            if room_has_corridor_access(graph, node):
                score += 5.0

    score -= 20.0 * len(invalid_edge_types(graph))
    score -= 25.0 * len(abstract_nodes(graph))

    return score
