"""Aggregate extra neighbors from all semantic anchor matches."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Hashable

import networkx as nx

from graph_layout_synth.api.models import NextRoomTypeSuggestion
from graph_layout_synth.api.semantic_anchor_matching import (
    NeighborRelation,
    NeighborSignature,
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    is_semantic_anchor_match,
)

SUGGESTION_EDGE_TYPES = ("door", "wall")


@dataclass(frozen=True)
class CandidateAggregation:
    """Graph-sample support counts for suggested room and edge types."""

    room_type_counts: Counter[str] = field(default_factory=Counter)
    edge_type_counts_by_room_type: dict[str, Counter[str]] = field(
        default_factory=dict
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


def _normalize_suggestion_edge_type(edge_type: object) -> str | None:
    if edge_type in SUGGESTION_EDGE_TYPES:
        return str(edge_type)
    return None


def _dominant_edge_type(edge_counts: Mapping[str, int] | None) -> str | None:
    if not edge_counts:
        return None

    door_count = edge_counts.get("door", 0)
    wall_count = edge_counts.get("wall", 0)
    if door_count <= 0 and wall_count <= 0:
        return None
    if door_count >= wall_count:
        return "door"
    return "wall"


def candidate_relations_for_generated_graph(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
) -> set[NeighborRelation]:
    """Return de-duplicated extra room/edge relations from one graph."""
    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )
    relations: set[NeighborRelation] = set()

    for matching_node_id in generated_graph.nodes:
        try:
            if not is_semantic_anchor_match(
                frontend_graph,
                anchor_node_id,
                generated_graph,
                matching_node_id,
            ):
                continue
            candidate_signature = build_candidate_neighbor_signature(
                generated_graph,
                matching_node_id,
            )
        except ValueError:
            continue
        extra_signature = subtract_neighbor_signature(
            required_signature,
            candidate_signature,
        )
        relations.update(
            relation for relation, count in extra_signature.items() if count > 0
        )
    return relations


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
    return {
        neighbor_room_type
        for neighbor_room_type, _edge_type in candidate_relations_for_generated_graph(
            frontend_graph,
            anchor_node_id,
            generated_graph,
        )
    }


def aggregate_candidate_evidence_from_matching_nodes(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graphs: Sequence[nx.Graph],
) -> CandidateAggregation:
    """Count graph samples supporting each extra room type and edge type."""
    room_type_counts: Counter[str] = Counter()
    edge_type_counts_by_room_type: dict[str, Counter[str]] = {}

    for generated_graph in generated_graphs:
        relations = candidate_relations_for_generated_graph(
            frontend_graph,
            anchor_node_id,
            generated_graph,
        )
        room_type_counts.update({room_type for room_type, _edge_type in relations})
        for room_type, edge_type in relations:
            normalized_edge_type = _normalize_suggestion_edge_type(edge_type)
            if normalized_edge_type is None:
                continue
            edge_counts = edge_type_counts_by_room_type.setdefault(
                room_type,
                Counter(),
            )
            edge_counts[normalized_edge_type] += 1

    return CandidateAggregation(
        room_type_counts=room_type_counts,
        edge_type_counts_by_room_type=edge_type_counts_by_room_type,
    )


def aggregate_candidates_from_matching_nodes(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graphs: Sequence[nx.Graph],
) -> Counter[str]:
    """Count generated graph samples supporting each extra room type."""
    return aggregate_candidate_evidence_from_matching_nodes(
        frontend_graph,
        anchor_node_id,
        generated_graphs,
    ).room_type_counts


def build_suggestions_from_counts(
    counts: Mapping[str, int],
    sample_count: int,
    anchor_room_type: str,
    edge_type_counts: Mapping[str, Mapping[str, int]] | None = None,
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
        room_edge_counts = {
            edge_type: edge_count
            for edge_type, edge_count in (
                (edge_type_counts or {}).get(room_type, {})
            ).items()
            if edge_type in SUGGESTION_EDGE_TYPES and edge_count > 0
        }
        dominant_edge_type = _dominant_edge_type(room_edge_counts)
        reason = (
            "Appeared as an extra neighbor of a semantically matched "
            f"{anchor_room_type} in {count} of {sample_count} generated "
            "graph samples."
        )
        if dominant_edge_type is not None:
            reason += f" Dominant connection type: {dominant_edge_type}."
        suggestions.append(
            NextRoomTypeSuggestion(
                room_type=room_type,
                sample_count=count,
                sample_share=sample_share,
                confidence=sample_share,
                reason=reason,
                edge_type=dominant_edge_type,
                edge_type_counts=room_edge_counts or None,
            )
        )
    return suggestions
