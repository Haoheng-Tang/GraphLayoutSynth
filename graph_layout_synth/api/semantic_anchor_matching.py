"""Pure one-hop semantic matching for frontend anchor rooms."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Hashable

import networkx as nx


NeighborRelation = tuple[str, str]
NeighborSignature = Counter[NeighborRelation]


def _require_node(graph: nx.Graph, node_id: Hashable, label: str) -> None:
    if node_id not in graph:
        raise ValueError(f"{label} node '{node_id}' is not present in the graph.")


def _room_type(graph: nx.Graph, node_id: Hashable, label: str) -> str:
    _require_node(graph, node_id, label)
    room_type = graph.nodes[node_id].get("type")
    if not isinstance(room_type, str) or not room_type:
        raise ValueError(f"{label} node '{node_id}' must have a non-empty string type.")
    return room_type


def extract_anchor_room_type(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
) -> str:
    """Return the selected frontend anchor's semantic room type."""
    return _room_type(frontend_graph, anchor_node_id, "Anchor")


def _build_neighbor_signature(
    graph: nx.Graph,
    node_id: Hashable,
    label: str,
) -> NeighborSignature:
    _require_node(graph, node_id, label)
    signature: NeighborSignature = Counter()
    for neighbor_id in graph.neighbors(node_id):
        neighbor_type = _room_type(graph, neighbor_id, f"{label} neighbor")
        edge_type = graph.edges[node_id, neighbor_id].get("edge_type")
        if not isinstance(edge_type, str) or not edge_type:
            raise ValueError(
                f"{label} edge '{node_id}'--'{neighbor_id}' must have a "
                "non-empty string edge_type."
            )
        signature[(neighbor_type, edge_type)] += 1
    return signature


def build_anchor_neighbor_signature(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
) -> NeighborSignature:
    """Build the selected frontend anchor's known one-hop multiset."""
    return _build_neighbor_signature(frontend_graph, anchor_node_id, "Anchor")


def build_candidate_neighbor_signature(
    generated_graph: nx.Graph,
    candidate_node_id: Hashable,
) -> NeighborSignature:
    """Build one generated candidate node's one-hop neighbor multiset."""
    return _build_neighbor_signature(
        generated_graph,
        candidate_node_id,
        "Generated candidate",
    )


def covers_neighbor_signature(
    required: Mapping[NeighborRelation, int],
    candidate: Mapping[NeighborRelation, int],
) -> bool:
    """Return whether ``candidate`` covers every required multiset count."""
    return all(
        candidate.get(relation, 0) >= required_count
        for relation, required_count in required.items()
    )


def is_semantic_anchor_match(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
    candidate_node_id: Hashable,
) -> bool:
    """Check strict one-way one-hop multiset coverage for one candidate."""
    anchor_room_type = extract_anchor_room_type(frontend_graph, anchor_node_id)
    candidate_room_type = _room_type(
        generated_graph,
        candidate_node_id,
        "Generated candidate",
    )
    if candidate_room_type != anchor_room_type:
        return False

    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )
    candidate_signature = build_candidate_neighbor_signature(
        generated_graph,
        candidate_node_id,
    )
    return covers_neighbor_signature(required_signature, candidate_signature)


def find_matching_anchor_nodes(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
) -> list[Hashable]:
    """Return every generated node satisfying semantic anchor coverage."""
    anchor_room_type = extract_anchor_room_type(frontend_graph, anchor_node_id)
    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )

    matching_nodes = []
    for candidate_node_id, attributes in generated_graph.nodes(data=True):
        if attributes.get("type") != anchor_room_type:
            continue
        candidate_signature = build_candidate_neighbor_signature(
            generated_graph,
            candidate_node_id,
        )
        if covers_neighbor_signature(required_signature, candidate_signature):
            matching_nodes.append(candidate_node_id)
    return matching_nodes
