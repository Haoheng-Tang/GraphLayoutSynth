"""Basic validators for generated layout graphs."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config
from graph_layout_synth.config_contract import is_corridor_node_type
from graph_layout_synth.grammar import VALID_EDGE_TYPES


@dataclass(frozen=True)
class ValidationResult:
    """Validation status and human-readable error messages."""

    is_valid: bool
    errors: list[str]


def is_connected(graph: nx.Graph) -> bool:
    """Return whether the graph is non-empty and connected."""
    return graph.number_of_nodes() > 0 and nx.is_connected(graph)


def room_has_corridor_access(
    graph: nx.Graph,
    node: str,
    corridor_types: set[str] | None = None,
) -> bool:
    """Return whether a non-corridor room has a door edge to a corridor.

    ``corridor_types`` is the config's declared circulation group when
    available; without it the shared token fallback applies.
    """
    for neighbor in graph.neighbors(node):
        edge_type = graph.edges[node, neighbor].get("edge_type")
        neighbor_type = graph.nodes[neighbor].get("type")
        if edge_type == "door" and is_corridor_node_type(neighbor_type, corridor_types):
            return True
    return False


def rooms_have_corridor_access(
    graph: nx.Graph,
    corridor_types: set[str] | None = None,
) -> bool:
    """Return whether every non-corridor concrete room has corridor access."""
    for node, attrs in graph.nodes(data=True):
        node_type = attrs.get("type")
        is_abstract = attrs.get("is_abstract", False)
        if (
            not is_abstract
            and node_type != "Zone"
            and not is_corridor_node_type(node_type, corridor_types)
        ):
            if not room_has_corridor_access(graph, node, corridor_types):
                return False
    return True


def invalid_edge_types(
    graph: nx.Graph,
    allowed_edge_types: set[str] | None = None,
) -> list[str]:
    """Return edge labels for edges with missing or unsupported edge types."""
    allowed_edge_types = allowed_edge_types or VALID_EDGE_TYPES
    invalid = []
    for left, right, attrs in graph.edges(data=True):
        edge_type = attrs.get("edge_type")
        if edge_type not in allowed_edge_types:
            invalid.append(f"{left}-{right}: {edge_type}")
    return invalid


def abstract_nodes(graph: nx.Graph) -> list[str]:
    """Return nodes still marked abstract."""
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs.get("is_abstract", False)
    ]


def validate_graph(graph: nx.Graph, config: LayoutConfig | None = None) -> ValidationResult:
    """Run all Milestone 1 validation checks."""
    config = config or load_config()
    errors = []
    corridor_types = set(config.corridor_node_types) or None

    if config.validation.require_connected_graph and not is_connected(graph):
        errors.append("Graph is not connected.")

    if config.validation.require_corridor_access and not rooms_have_corridor_access(
        graph, corridor_types
    ):
        errors.append("At least one room does not have door access to a corridor.")

    bad_edges = invalid_edge_types(graph, set(config.allowed_edge_types))
    if bad_edges:
        errors.append(f"Invalid edge types: {', '.join(bad_edges)}.")

    remaining_abstract_nodes = abstract_nodes(graph)
    if remaining_abstract_nodes and not config.validation.allow_abstract_nodes_final:
        errors.append(
            "Abstract nodes remain: "
            + ", ".join(str(node) for node in remaining_abstract_nodes)
            + "."
        )

    return ValidationResult(is_valid=not errors, errors=errors)
