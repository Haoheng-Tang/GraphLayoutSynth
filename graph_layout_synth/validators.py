"""Basic validators for generated layout graphs."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from graph_layout_synth.grammar import VALID_EDGE_TYPES


@dataclass(frozen=True)
class ValidationResult:
    """Validation status and human-readable error messages."""

    is_valid: bool
    errors: list[str]


def is_connected(graph: nx.Graph) -> bool:
    """Return whether the graph is non-empty and connected."""
    return graph.number_of_nodes() > 0 and nx.is_connected(graph)


def room_has_corridor_access(graph: nx.Graph, node: str) -> bool:
    """Return whether a non-corridor room has a door edge to a corridor."""
    for neighbor in graph.neighbors(node):
        edge_type = graph.edges[node, neighbor].get("edge_type")
        neighbor_type = graph.nodes[neighbor].get("type")
        if edge_type == "door" and neighbor_type == "Corridor":
            return True
    return False


def rooms_have_corridor_access(graph: nx.Graph) -> bool:
    """Return whether every non-corridor concrete room has corridor access."""
    for node, attrs in graph.nodes(data=True):
        node_type = attrs.get("type")
        is_abstract = attrs.get("is_abstract", False)
        if not is_abstract and node_type not in {"Corridor", "Zone"}:
            if not room_has_corridor_access(graph, node):
                return False
    return True


def invalid_edge_types(graph: nx.Graph) -> list[str]:
    """Return edge labels for edges with missing or unsupported edge types."""
    invalid = []
    for left, right, attrs in graph.edges(data=True):
        edge_type = attrs.get("edge_type")
        if edge_type not in VALID_EDGE_TYPES:
            invalid.append(f"{left}-{right}: {edge_type}")
    return invalid


def abstract_nodes(graph: nx.Graph) -> list[str]:
    """Return nodes still marked abstract."""
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs.get("is_abstract", False)
    ]


def validate_graph(graph: nx.Graph) -> ValidationResult:
    """Run all Milestone 1 validation checks."""
    errors = []

    if not is_connected(graph):
        errors.append("Graph is not connected.")

    if not rooms_have_corridor_access(graph):
        errors.append("At least one room does not have door access to a corridor.")

    bad_edges = invalid_edge_types(graph)
    if bad_edges:
        errors.append(f"Invalid edge types: {', '.join(bad_edges)}.")

    remaining_abstract_nodes = abstract_nodes(graph)
    if remaining_abstract_nodes:
        errors.append(
            "Abstract nodes remain: "
            + ", ".join(str(node) for node in remaining_abstract_nodes)
            + "."
        )

    return ValidationResult(is_valid=not errors, errors=errors)
