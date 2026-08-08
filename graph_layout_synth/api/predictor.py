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
    SuggestionConfigSourceInfo,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.sampling import (
    ExistingGeneratorSampler,
    GraphSampler,
    SuggestionConfigSource,
)
from graph_layout_synth.api.semantic_anchor_matching import extract_anchor_room_type
from graph_layout_synth.api.suggestion_debug_artifacts import (
    SuggestionArtifactWriter,
)


LOGGER = logging.getLogger(__name__)
PREDICTOR_VERSION = "graphlayoutsynth-v1"


def _config_source_info(
    config_source: SuggestionConfigSource | None,
) -> SuggestionConfigSourceInfo | None:
    """Convert the sampler's resolved config source into its API shape.

    Mocked samplers in tests expose no config source, so this stays optional
    rather than inventing one; there is no second resolution path.
    """
    if config_source is None:
        return None
    return SuggestionConfigSourceInfo(
        mode=config_source.mode,
        config_path=(
            str(config_source.config_path)
            if config_source.config_path is not None
            else None
        ),
        variant_id=config_source.variant_id,
    )


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
        config_source = getattr(self.sampler, "last_config_source", None)
        if config_source is not None:
            LOGGER.info(
                "Suggestion sampler config source: mode=%s configPath=%s variantId=%s",
                config_source.mode,
                config_source.config_path,
                config_source.variant_id,
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
            matched_sample_count=candidate_evidence.matched_sample_count,
            samples_with_candidates=candidate_evidence.samples_with_candidates,
            config_source=_config_source_info(config_source),
        )
        try:
            artifact_directory = self.artifact_writer.save_if_enabled(
                request,
                adapted.graph,
                anchor_node_id,
                generated_graphs,
                response,
                getattr(self.sampler, "last_resolved_config", None)
                or getattr(self.sampler, "config", None),
                config_source=config_source,
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
