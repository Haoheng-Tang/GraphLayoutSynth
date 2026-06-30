"""HTTP API support for semantic next-room prediction."""

from graph_layout_synth.api.models import (
    DoorOrAdjacency,
    FloorplanState,
    NextRoomTypeSuggestion,
    Room,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    aggregate_candidates_from_matching_nodes,
    build_suggestions_from_counts,
    candidate_room_types_for_generated_graph,
    extract_extra_neighbor_candidates,
    subtract_neighbor_signature,
)
from graph_layout_synth.api.predictor import NextRoomPredictor
from graph_layout_synth.api.semantic_anchor_matching import (
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    covers_neighbor_signature,
    extract_anchor_room_type,
    find_matching_anchor_nodes,
    is_semantic_anchor_match,
)

__all__ = [
    "DoorOrAdjacency",
    "FloorplanState",
    "NextRoomPredictor",
    "NextRoomTypeSuggestion",
    "Room",
    "SuggestNextRoomRequest",
    "SuggestNextRoomResponse",
    "aggregate_candidates_from_matching_nodes",
    "build_anchor_neighbor_signature",
    "build_candidate_neighbor_signature",
    "build_suggestions_from_counts",
    "candidate_room_types_for_generated_graph",
    "covers_neighbor_signature",
    "extract_anchor_room_type",
    "extract_extra_neighbor_candidates",
    "find_matching_anchor_nodes",
    "is_semantic_anchor_match",
    "subtract_neighbor_signature",
]
