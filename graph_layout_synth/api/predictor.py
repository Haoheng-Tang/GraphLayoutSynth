"""Testable next-room prediction and aggregation service."""

from __future__ import annotations

from dataclasses import dataclass, field

from graph_layout_synth.api.adapter import floorplan_to_graph
from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    aggregate_candidates_from_matching_nodes,
    build_suggestions_from_counts,
)
from graph_layout_synth.api.models import (
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.sampling import ExistingGeneratorSampler, GraphSampler
from graph_layout_synth.api.semantic_anchor_matching import extract_anchor_room_type


PREDICTOR_VERSION = "graphlayoutsynth-v1"


@dataclass
class NextRoomPredictor:
    """Convert, sample, aggregate, and rank room-type suggestions."""

    sampler: GraphSampler = field(default_factory=ExistingGeneratorSampler)
    predictor_version: str = PREDICTOR_VERSION

    def suggest(self, request: SuggestNextRoomRequest) -> SuggestNextRoomResponse:
        """Return ranked semantic suggestions for one frontend anchor room."""
        adapted = floorplan_to_graph(request.floorplan)
        anchor_node_id = adapted.internal_id(request.anchor_room_id)
        generated_graphs = self.sampler.sample(
            adapted.graph.copy(),
            anchor_node_id,
            request.sample_count,
        )
        actual_sample_count = len(generated_graphs)
        counts = aggregate_candidates_from_matching_nodes(
            adapted.graph,
            anchor_node_id,
            generated_graphs,
        )
        anchor_type = extract_anchor_room_type(
            adapted.graph,
            anchor_node_id,
        )
        suggestions = build_suggestions_from_counts(
            counts,
            actual_sample_count,
            anchor_type,
        )
        return SuggestNextRoomResponse(
            suggestions=suggestions,
            sample_count=actual_sample_count,
            predictor_version=self.predictor_version,
        )
