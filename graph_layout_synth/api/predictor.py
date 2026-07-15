"""Testable next-room prediction and aggregation service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from graph_layout_synth.api.adapter import floorplan_to_graph
from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    aggregate_candidate_evidence_from_matching_nodes,
    build_suggestions_from_counts,
)
from graph_layout_synth.api.models import (
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.sampling import ExistingGeneratorSampler, GraphSampler
from graph_layout_synth.api.semantic_anchor_matching import extract_anchor_room_type
from graph_layout_synth.api.suggestion_debug_artifacts import (
    SuggestionArtifactWriter,
)


LOGGER = logging.getLogger(__name__)
PREDICTOR_VERSION = "graphlayoutsynth-v1"


@dataclass
class NextRoomPredictor:
    """Convert, sample, aggregate, and rank room-type suggestions."""

    sampler: GraphSampler = field(default_factory=ExistingGeneratorSampler)
    artifact_writer: SuggestionArtifactWriter = field(
        default_factory=SuggestionArtifactWriter
    )
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
        candidate_evidence = aggregate_candidate_evidence_from_matching_nodes(
            adapted.graph,
            anchor_node_id,
            generated_graphs,
        )
        anchor_type = extract_anchor_room_type(
            adapted.graph,
            anchor_node_id,
        )
        suggestions = build_suggestions_from_counts(
            candidate_evidence.room_type_counts,
            actual_sample_count,
            anchor_type,
            candidate_evidence.edge_type_counts_by_room_type,
            intended_edge_sample_counts=candidate_evidence.intended_edge_sample_counts,
            intended_edge_type_counts=candidate_evidence.intended_edge_type_counts,
        )
        response = SuggestNextRoomResponse(
            suggestions=suggestions,
            sample_count=actual_sample_count,
            predictor_version=self.predictor_version,
        )
        try:
            artifact_directory = self.artifact_writer.save_if_enabled(
                request,
                adapted.graph,
                anchor_node_id,
                generated_graphs,
                response,
                getattr(self.sampler, "config", None),
            )
        except Exception:
            LOGGER.warning(
                "Failed to save next-room suggestion debug artifacts.",
                exc_info=True,
            )
        else:
            if artifact_directory is not None:
                LOGGER.info(
                    "Saved next-room suggestion debug artifacts to %s.",
                    artifact_directory,
                )
        return response
