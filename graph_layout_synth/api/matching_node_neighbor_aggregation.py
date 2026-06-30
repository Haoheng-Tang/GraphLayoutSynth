"""Aggregate extra neighbors from all semantic anchor matches."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Hashable

import networkx as nx

from graph_layout_synth.api.models import NextRoomTypeSuggestion
from graph_layout_synth.api.semantic_anchor_matching import (
    NeighborRelation,
    NeighborSignature,
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    find_matching_anchor_nodes,
    is_semantic_anchor_match,
)


def subtract_neighbor_signature(
    required: Mapping[NeighborRelation, int],
    candidate: Mapping[NeighborRelation, int],
) -> NeighborSignature:
    """Subtract known relation counts and keep only positive extras."""
    extras: NeighborSignature = Counter()
    for relation, candidate_count in candidate.items():
        remaining_count = candidate_count - required.get(relation, 0)
        if remaining_count > 0:
            extras[relation] = remaining_count
    return extras


def extract_extra_neighbor_candidates(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
    candidate_node_id: Hashable,
) -> NeighborSignature:
    """Return extra relations for one valid semantic match, else empty."""
    if not is_semantic_anchor_match(
        frontend_graph,
        anchor_node_id,
        generated_graph,
        candidate_node_id,
    ):
        return Counter()

    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )
    candidate_signature = build_candidate_neighbor_signature(
        generated_graph,
        candidate_node_id,
    )
    return subtract_neighbor_signature(required_signature, candidate_signature)


def candidate_room_types_for_generated_graph(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
) -> set[str]:
    """Return de-duplicated extra room types from all matches in one graph."""
    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )
    room_types: set[str] = set()
    for matching_node_id in find_matching_anchor_nodes(
        frontend_graph,
        anchor_node_id,
        generated_graph,
    ):
        candidate_signature = build_candidate_neighbor_signature(
            generated_graph,
            matching_node_id,
        )
        extra_signature = subtract_neighbor_signature(
            required_signature,
            candidate_signature,
        )
        room_types.update(
            neighbor_room_type
            for (neighbor_room_type, _edge_type), count in extra_signature.items()
            if count > 0
        )
    return room_types


def aggregate_candidates_from_matching_nodes(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graphs: Sequence[nx.Graph],
) -> Counter[str]:
    """Count generated graph samples supporting each extra room type."""
    counts: Counter[str] = Counter()
    for generated_graph in generated_graphs:
        counts.update(
            candidate_room_types_for_generated_graph(
                frontend_graph,
                anchor_node_id,
                generated_graph,
            )
        )
    return counts


def build_suggestions_from_counts(
    counts: Mapping[str, int],
    sample_count: int,
    anchor_room_type: str,
) -> list[NextRoomTypeSuggestion]:
    """Build deterministic API suggestions from per-sample support counts."""
    if sample_count <= 0:
        return []

    suggestions = []
    for room_type, count in sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        if count <= 0:
            continue
        sample_share = count / sample_count
        suggestions.append(
            NextRoomTypeSuggestion(
                room_type=room_type,
                sample_count=count,
                sample_share=sample_share,
                confidence=sample_share,
                reason=(
                    "Appeared as an extra neighbor of a semantically matched "
                    f"{anchor_room_type} in {count} of {sample_count} generated "
                    "graph samples."
                ),
            )
        )
    return suggestions
