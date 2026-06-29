"""Testable next-room prediction and aggregation service."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Hashable

import networkx as nx

from graph_layout_synth.api.adapter import (
    existing_neighbor_ids,
    floorplan_to_graph,
)
from graph_layout_synth.api.models import (
    NextRoomTypeSuggestion,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.sampling import ExistingGeneratorSampler, GraphSampler


PREDICTOR_VERSION = "graphlayoutsynth-v1"


def aggregate_new_neighbor_types(
    samples: list[nx.Graph],
    anchor_node_id: Hashable,
    existing_neighbors: set[Hashable],
) -> Counter[str]:
    """Count each new neighbor room type at most once per graph sample."""
    counts: Counter[str] = Counter()
    for sample in samples:
        if anchor_node_id not in sample:
            continue
        room_types_in_sample = {
            sample.nodes[neighbor].get("type")
            for neighbor in sample.neighbors(anchor_node_id)
            if neighbor not in existing_neighbors
        }
        counts.update(
            room_type
            for room_type in room_types_in_sample
            if isinstance(room_type, str) and room_type
        )
    return counts


@dataclass
class NextRoomPredictor:
    """Convert, sample, aggregate, and rank room-type suggestions."""

    sampler: GraphSampler = field(default_factory=ExistingGeneratorSampler)
    predictor_version: str = PREDICTOR_VERSION

    def suggest(self, request: SuggestNextRoomRequest) -> SuggestNextRoomResponse:
        """Return ranked semantic suggestions for one frontend anchor room."""
        adapted = floorplan_to_graph(request.floorplan)
        anchor_node_id = adapted.internal_id(request.anchor_room_id)
        current_neighbors = existing_neighbor_ids(adapted.graph, anchor_node_id)
        samples = self.sampler.sample(
            adapted.graph.copy(),
            anchor_node_id,
            request.sample_count,
        )
        actual_sample_count = len(samples)
        counts = aggregate_new_neighbor_types(
            samples,
            anchor_node_id,
            current_neighbors,
        )
        anchor_type = adapted.graph.nodes[anchor_node_id]["type"]

        suggestions = [
            self._suggestion(
                room_type,
                count,
                actual_sample_count,
                anchor_type,
            )
            for room_type, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        return SuggestNextRoomResponse(
            suggestions=suggestions,
            sample_count=actual_sample_count,
            predictor_version=self.predictor_version,
        )

    @staticmethod
    def _suggestion(
        room_type: str,
        count: int,
        total_samples: int,
        anchor_type: str,
    ) -> NextRoomTypeSuggestion:
        sample_share = count / total_samples if total_samples else 0.0
        return NextRoomTypeSuggestion(
            room_type=room_type,
            sample_count=count,
            sample_share=sample_share,
            confidence=sample_share,
            reason=(
                f"Appeared as a new neighbor of the selected {anchor_type} "
                f"in {count} of {total_samples} generated graph samples."
            ),
        )
