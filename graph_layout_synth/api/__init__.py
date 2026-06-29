"""HTTP API support for semantic next-room prediction."""

from graph_layout_synth.api.models import (
    DoorOrAdjacency,
    FloorplanState,
    NextRoomTypeSuggestion,
    Room,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
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
    "build_anchor_neighbor_signature",
    "build_candidate_neighbor_signature",
    "covers_neighbor_signature",
    "extract_anchor_room_type",
    "find_matching_anchor_nodes",
    "is_semantic_anchor_match",
]
