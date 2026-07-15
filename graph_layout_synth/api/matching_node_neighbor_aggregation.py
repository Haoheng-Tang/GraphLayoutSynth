"""Aggregate extra neighbors from all semantic anchor matches."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Hashable

import networkx as nx

from graph_layout_synth.api.models import (
    NextRoomTypeSuggestion,
    SuggestedIntendedEdge,
)
from graph_layout_synth.api.semantic_anchor_matching import (
    NeighborRelation,
    NeighborSignature,
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    is_semantic_anchor_match,
)

SUGGESTION_EDGE_TYPES = ("door", "wall")

# (target frontend room ID or None when ambiguous, target room type)
IntendedEdgeTarget = tuple[str | None, str]


@dataclass(frozen=True)
class CandidateAggregation:
    """Graph-sample support counts for suggested room and edge types."""

    room_type_counts: Counter[str] = field(default_factory=Counter)
    edge_type_counts_by_room_type: dict[str, Counter[str]] = field(
        default_factory=dict
    )
    intended_edge_sample_counts: dict[str, Counter[IntendedEdgeTarget]] = field(
        default_factory=dict
    )
    intended_edge_type_counts: dict[
        str, dict[IntendedEdgeTarget, Counter[str]]
    ] = field(default_factory=dict)


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


def _node_room_type(graph: nx.Graph, node_id: Hashable) -> str | None:
    room_type = graph.nodes[node_id].get("type")
    if isinstance(room_type, str) and room_type:
        return room_type
    return None


def _edge_type_between(graph: nx.Graph, left: Hashable, right: Hashable) -> str | None:
    edge_type = graph.edges[left, right].get("edge_type")
    if isinstance(edge_type, str) and edge_type:
        return edge_type
    return None


def _frontend_room_id(frontend_graph: nx.Graph, node_id: Hashable) -> str:
    external_id = frontend_graph.nodes[node_id].get("external_id")
    if isinstance(external_id, str) and external_id:
        return external_id
    return str(node_id)


def known_frontend_neighbor_targets(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
) -> dict[NeighborRelation, IntendedEdgeTarget]:
    """Map each known anchor relation to an intended-edge target.

    The target frontend room ID is kept only when exactly one existing room
    carries that (room type, anchor edge type) relation; with several
    identical known relations the generated evidence cannot name one room, so
    the ID is omitted and only the room type is reported.
    """
    frontend_ids_by_relation: dict[NeighborRelation, list[str]] = {}
    for neighbor_id in frontend_graph.neighbors(anchor_node_id):
        neighbor_type = _node_room_type(frontend_graph, neighbor_id)
        edge_type = _edge_type_between(frontend_graph, anchor_node_id, neighbor_id)
        if neighbor_type is None or edge_type is None:
            continue
        frontend_ids_by_relation.setdefault((neighbor_type, edge_type), []).append(
            _frontend_room_id(frontend_graph, neighbor_id)
        )

    return {
        relation: (
            frontend_ids[0] if len(frontend_ids) == 1 else None,
            relation[0],
        )
        for relation, frontend_ids in frontend_ids_by_relation.items()
    }


def intended_edge_details_for_generated_graph(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
) -> list[dict]:
    """Return per-matching-node secondary-edge evidence from one graph.

    For each semantic anchor match, the matched node's generated neighbors
    are partitioned deterministically (ascending ``str(node_id)`` order) into
    known-neighbor correspondents — consuming one slot per known frontend
    (room type, edge type) relation — and extra candidate suggested nodes. A
    secondary intended edge is recorded only when the generated graph itself
    contains a door/wall edge between a candidate node and a known-neighbor
    correspondent; nothing is inferred from room-type rules or geometry.
    """
    required_signature = build_anchor_neighbor_signature(
        frontend_graph,
        anchor_node_id,
    )
    known_targets = known_frontend_neighbor_targets(frontend_graph, anchor_node_id)

    details: list[dict] = []
    for matching_node_id in generated_graph.nodes:
        try:
            if not is_semantic_anchor_match(
                frontend_graph,
                anchor_node_id,
                generated_graph,
                matching_node_id,
            ):
                continue
        except ValueError:
            continue

        remaining_known_slots: Counter[NeighborRelation] = Counter(required_signature)
        known_correspondents: list[dict] = []
        candidate_nodes: list[dict] = []
        for neighbor_id in sorted(
            generated_graph.neighbors(matching_node_id),
            key=str,
        ):
            neighbor_type = _node_room_type(generated_graph, neighbor_id)
            edge_type = _edge_type_between(
                generated_graph,
                matching_node_id,
                neighbor_id,
            )
            if neighbor_type is None or edge_type is None:
                continue
            relation = (neighbor_type, edge_type)
            if remaining_known_slots.get(relation, 0) > 0:
                remaining_known_slots[relation] -= 1
                target_room_id, target_room_type = known_targets[relation]
                known_correspondents.append(
                    {
                        "node_id": neighbor_id,
                        "room_type": neighbor_type,
                        "anchor_edge_type": edge_type,
                        "target_room_id": target_room_id,
                        "target_room_type": target_room_type,
                    }
                )
            else:
                candidate_nodes.append(
                    {
                        "node_id": neighbor_id,
                        "room_type": neighbor_type,
                        "anchor_edge_type": edge_type,
                    }
                )

        secondary_edges: list[dict] = []
        for candidate in candidate_nodes:
            for known in known_correspondents:
                if not generated_graph.has_edge(
                    candidate["node_id"],
                    known["node_id"],
                ):
                    continue
                secondary_edge_type = _edge_type_between(
                    generated_graph,
                    candidate["node_id"],
                    known["node_id"],
                )
                if secondary_edge_type not in SUGGESTION_EDGE_TYPES:
                    continue
                secondary_edges.append(
                    {
                        "candidate_node_id": candidate["node_id"],
                        "suggested_room_type": candidate["room_type"],
                        "target_node_id": known["node_id"],
                        "target_room_id": known["target_room_id"],
                        "target_room_type": known["target_room_type"],
                        "edge_type": secondary_edge_type,
                    }
                )

        details.append(
            {
                "matching_node_id": matching_node_id,
                "known_neighbor_nodes": known_correspondents,
                "candidate_nodes": candidate_nodes,
                "secondary_edges": secondary_edges,
            }
        )
    return details


def intended_edge_relations_for_generated_graph(
    frontend_graph: nx.Graph,
    anchor_node_id: Hashable,
    generated_graph: nx.Graph,
) -> set[tuple[str, str | None, str, str]]:
    """De-duplicated (suggested type, target ID, target type, edge type) tuples."""
    relations: set[tuple[str, str | None, str, str]] = set()
    for match_detail in intended_edge_details_for_generated_graph(
        frontend_graph,
        anchor_node_id,
        generated_graph,
    ):
        for secondary_edge in match_detail["secondary_edges"]:
            relations.add(
                (
                    secondary_edge["suggested_room_type"],
                    secondary_edge["target_room_id"],
                    secondary_edge["target_room_type"],
                    secondary_edge["edge_type"],
                )
            )
    return relations


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
    intended_edge_sample_counts: dict[str, Counter[IntendedEdgeTarget]] = {}
    intended_edge_type_counts: dict[str, dict[IntendedEdgeTarget, Counter[str]]] = {}

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

        intended_relations = intended_edge_relations_for_generated_graph(
            frontend_graph,
            anchor_node_id,
            generated_graph,
        )
        seen_targets: set[tuple[str, IntendedEdgeTarget]] = set()
        for suggested_type, target_id, target_type, edge_type in intended_relations:
            target: IntendedEdgeTarget = (target_id, target_type)
            if (suggested_type, target) not in seen_targets:
                seen_targets.add((suggested_type, target))
                intended_edge_sample_counts.setdefault(suggested_type, Counter())[
                    target
                ] += 1
            intended_edge_type_counts.setdefault(suggested_type, {}).setdefault(
                target,
                Counter(),
            )[edge_type] += 1

    return CandidateAggregation(
        room_type_counts=room_type_counts,
        edge_type_counts_by_room_type=edge_type_counts_by_room_type,
        intended_edge_sample_counts=intended_edge_sample_counts,
        intended_edge_type_counts=intended_edge_type_counts,
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


def _build_intended_edges(
    room_type: str,
    sample_count: int,
    intended_sample_counts: Mapping[str, Mapping[IntendedEdgeTarget, int]] | None,
    intended_edge_type_counts: (
        Mapping[str, Mapping[IntendedEdgeTarget, Mapping[str, int]]] | None
    ),
) -> list[SuggestedIntendedEdge] | None:
    """Build one suggestion's deterministic secondary intended-edge list.

    Targets are ordered by descending sample support, then room type, then
    frontend room ID, so responses stay stable across runs. Dominant edge
    types follow the existing convention: ``door`` wins ties.
    """
    target_counts = (intended_sample_counts or {}).get(room_type)
    if not target_counts:
        return None

    intended_edges = []
    for target, target_sample_count in sorted(
        target_counts.items(),
        key=lambda item: (-item[1], item[0][1], item[0][0] or ""),
    ):
        if target_sample_count <= 0:
            continue
        target_room_id, target_room_type = target
        edge_counts = {
            edge_type: edge_count
            for edge_type, edge_count in (
                (intended_edge_type_counts or {}).get(room_type, {}).get(target, {})
            ).items()
            if edge_type in SUGGESTION_EDGE_TYPES and edge_count > 0
        }
        dominant_edge_type = _dominant_edge_type(edge_counts)
        if dominant_edge_type is None:
            continue
        intended_edges.append(
            SuggestedIntendedEdge(
                target_existing_room_id=target_room_id,
                target_room_type=target_room_type,
                edge_type=dominant_edge_type,
                edge_type_counts=edge_counts or None,
                confidence=target_sample_count / sample_count,
                sample_count=target_sample_count,
            )
        )
    return intended_edges or None


def build_suggestions_from_counts(
    counts: Mapping[str, int],
    sample_count: int,
    anchor_room_type: str,
    edge_type_counts: Mapping[str, Mapping[str, int]] | None = None,
    intended_edge_sample_counts: (
        Mapping[str, Mapping[IntendedEdgeTarget, int]] | None
    ) = None,
    intended_edge_type_counts: (
        Mapping[str, Mapping[IntendedEdgeTarget, Mapping[str, int]]] | None
    ) = None,
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
                intended_edges=_build_intended_edges(
                    room_type,
                    sample_count,
                    intended_edge_sample_counts,
                    intended_edge_type_counts,
                ),
            )
        )
    return suggestions
