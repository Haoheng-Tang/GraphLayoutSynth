"""Best-effort debug artifacts for next-room suggestion requests."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Hashable
from uuid import uuid4

import networkx as nx

from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    subtract_neighbor_signature,
)
from graph_layout_synth.api.models import (
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.semantic_anchor_matching import (
    NeighborRelation,
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    extract_anchor_room_type,
    find_matching_anchor_nodes,
)
from graph_layout_synth.config import LayoutConfig
from graph_layout_synth.export import export_graph_json
from graph_layout_synth.visualize import visualize_graph


LOGGER = logging.getLogger(__name__)
DEFAULT_ARTIFACT_DIRECTORY = Path("outputs/nextroom_suggestions")
SAVE_ARTIFACTS_ENV = "GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS"
SAVE_PNGS_ENV = "GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_PNGS"
ARTIFACT_DIRECTORY_ENV = "GRAPHLAYOUTSYNTH_SUGGESTION_ARTIFACT_DIR"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_ENV_VALUES


@dataclass(frozen=True)
class SuggestionDebugSettings:
    """Resolved request and environment settings for one prediction."""

    enabled: bool
    include_visualizations: bool
    base_directory: Path

    @classmethod
    def from_request(
        cls,
        request: SuggestNextRoomRequest,
    ) -> "SuggestionDebugSettings":
        include_visualizations = (
            request.include_debug_visualizations
            or _environment_flag(SAVE_PNGS_ENV)
        )
        enabled = (
            request.include_debug_artifacts
            or _environment_flag(SAVE_ARTIFACTS_ENV)
            or include_visualizations
        )
        configured_directory = os.getenv(ARTIFACT_DIRECTORY_ENV)
        base_directory = (
            Path(configured_directory).expanduser()
            if configured_directory
            else DEFAULT_ARTIFACT_DIRECTORY
        )
        return cls(
            enabled=enabled,
            include_visualizations=include_visualizations,
            base_directory=base_directory,
        )


@dataclass
class SuggestionArtifactWriter:
    """Write inspectable artifacts without affecting suggestion semantics."""

    visualizer: Callable[..., Path] = visualize_graph
    visualization_config: LayoutConfig | None = None

    def save_if_enabled(
        self,
        request: SuggestNextRoomRequest,
        frontend_graph: nx.Graph,
        anchor_node_id: Hashable,
        generated_graphs: Sequence[nx.Graph],
        response: SuggestNextRoomResponse,
        visualization_config: LayoutConfig | None = None,
    ) -> Path | None:
        """Save one debug run when its request or environment gate is enabled."""
        settings = SuggestionDebugSettings.from_request(request)
        if not settings.enabled:
            return None

        timestamp = datetime.now(timezone.utc)
        run_directory = self._create_run_directory(
            settings.base_directory,
            timestamp,
        )
        self._write_json(
            run_directory / "request.json",
            request.model_dump(mode="json", by_alias=True),
        )

        for graph_index, generated_graph in enumerate(generated_graphs):
            export_graph_json(
                generated_graph,
                run_directory / f"generated_graph_{graph_index:03d}.json",
            )

        matching_report = self._build_matching_report(
            frontend_graph,
            anchor_node_id,
            generated_graphs,
        )
        self._write_json(
            run_directory / "matching_report.json",
            matching_report,
        )

        aggregation_report = self._build_aggregation_report(
            request,
            frontend_graph,
            anchor_node_id,
            response,
            matching_report,
        )
        self._write_json(
            run_directory / "aggregation_report.json",
            aggregation_report,
        )

        saved_visualizations = self._save_visualizations(
            run_directory,
            generated_graphs,
            settings.include_visualizations,
            visualization_config or self.visualization_config,
        )
        self._write_summary(
            run_directory / "README.md",
            timestamp,
            request,
            aggregation_report,
            saved_visualizations,
        )
        return run_directory

    @staticmethod
    def _create_run_directory(
        base_directory: Path,
        timestamp: datetime,
    ) -> Path:
        run_name = (
            f"{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}-"
            f"{uuid4().hex[:8]}"
        )
        run_directory = base_directory / run_name
        run_directory.mkdir(parents=True, exist_ok=False)
        return run_directory

    @staticmethod
    def _write_json(path: Path, data: Mapping[str, Any]) -> None:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def _build_matching_report(
        cls,
        frontend_graph: nx.Graph,
        anchor_node_id: Hashable,
        generated_graphs: Sequence[nx.Graph],
    ) -> dict[str, Any]:
        frontend_signature = build_anchor_neighbor_signature(
            frontend_graph,
            anchor_node_id,
        )
        graph_reports = []
        for graph_index, generated_graph in enumerate(generated_graphs):
            matching_node_ids = find_matching_anchor_nodes(
                frontend_graph,
                anchor_node_id,
                generated_graph,
            )
            matching_nodes = []
            graph_candidate_room_types: set[str] = set()
            for matching_node_id in matching_node_ids:
                neighbor_signature = build_candidate_neighbor_signature(
                    generated_graph,
                    matching_node_id,
                )
                extra_signature = subtract_neighbor_signature(
                    frontend_signature,
                    neighbor_signature,
                )
                candidate_room_types = sorted(
                    {
                        room_type
                        for (room_type, _edge_type), count in extra_signature.items()
                        if count > 0
                    }
                )
                graph_candidate_room_types.update(candidate_room_types)
                matching_nodes.append(
                    {
                        "nodeId": cls._json_safe_node_id(matching_node_id),
                        "roomType": generated_graph.nodes[matching_node_id].get(
                            "type"
                        ),
                        "neighborSignature": cls._signature_data(
                            neighbor_signature
                        ),
                        "extraNeighborSignature": cls._signature_data(
                            extra_signature
                        ),
                        "candidateRoomTypes": candidate_room_types,
                    }
                )

            graph_reports.append(
                {
                    "graphIndex": graph_index,
                    "frontendAnchorSignature": cls._signature_data(
                        frontend_signature
                    ),
                    "matchingNodeCount": len(matching_nodes),
                    "matchingNodes": matching_nodes,
                    "producedCandidates": bool(graph_candidate_room_types),
                    "candidateRoomTypes": sorted(graph_candidate_room_types),
                }
            )

        return {
            "frontendAnchorSignature": cls._signature_data(frontend_signature),
            "generatedGraphs": graph_reports,
        }

    @classmethod
    def _build_aggregation_report(
        cls,
        request: SuggestNextRoomRequest,
        frontend_graph: nx.Graph,
        anchor_node_id: Hashable,
        response: SuggestNextRoomResponse,
        matching_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        graph_reports = matching_report["generatedGraphs"]
        return {
            "frontendAnchorRoomId": request.anchor_room_id,
            "frontendAnchorRoomType": extract_anchor_room_type(
                frontend_graph,
                anchor_node_id,
            ),
            "frontendKnownNeighborSignature": matching_report[
                "frontendAnchorSignature"
            ],
            "generatedSampleCount": response.sample_count,
            "samplesWithMatches": sum(
                graph_report["matchingNodeCount"] > 0
                for graph_report in graph_reports
            ),
            "totalMatchingNodes": sum(
                graph_report["matchingNodeCount"]
                for graph_report in graph_reports
            ),
            "samplesWithCandidates": sum(
                graph_report["producedCandidates"]
                for graph_report in graph_reports
            ),
            "candidateCountsByRoomType": {
                suggestion.room_type: suggestion.sample_count
                for suggestion in response.suggestions
            },
            "finalSuggestions": [
                suggestion.model_dump(mode="json", by_alias=True)
                for suggestion in response.suggestions
            ],
            "predictorVersion": response.predictor_version,
        }

    def _save_visualizations(
        self,
        run_directory: Path,
        generated_graphs: Sequence[nx.Graph],
        include_visualizations: bool,
        visualization_config: LayoutConfig | None,
    ) -> list[str]:
        if not include_visualizations:
            return []

        saved_paths = []
        for graph_index, generated_graph in enumerate(generated_graphs):
            output_path = (
                run_directory / f"generated_graph_{graph_index:03d}.png"
            )
            try:
                visualizer_kwargs: dict[str, Any] = {
                    "title": f"Suggestion generated graph {graph_index:03d}",
                }
                if visualization_config is not None:
                    visualizer_kwargs["config"] = visualization_config
                self.visualizer(generated_graph, output_path, **visualizer_kwargs)
            except Exception:
                LOGGER.warning(
                    "Failed to save suggestion debug visualization %s.",
                    output_path,
                    exc_info=True,
                )
                continue
            saved_paths.append(output_path.name)
        return saved_paths

    @staticmethod
    def _write_summary(
        path: Path,
        timestamp: datetime,
        request: SuggestNextRoomRequest,
        aggregation_report: Mapping[str, Any],
        saved_visualizations: Sequence[str],
    ) -> None:
        suggestions = aggregation_report["finalSuggestions"]
        if suggestions:
            suggestion_lines = [
                "| Room type | Sample count | Sample share |",
                "| --- | ---: | ---: |",
                *(
                    f"| {suggestion['roomType']} | "
                    f"{suggestion['sampleCount']} | "
                    f"{suggestion['sampleShare']:.4f} |"
                    for suggestion in suggestions
                ),
            ]
        else:
            suggestion_lines = ["No suggestions were produced."]

        visualization_note = (
            f"- PNG visualizations: {len(saved_visualizations)} saved"
            if request.include_debug_visualizations
            or _environment_flag(SAVE_PNGS_ENV)
            else "- PNG visualizations: disabled"
        )
        lines = [
            "# Next-room suggestion debug run",
            "",
            f"- Timestamp: {timestamp.isoformat()}",
            f"- Anchor room ID: `{request.anchor_room_id}`",
            (
                "- Anchor room type: "
                f"`{aggregation_report['frontendAnchorRoomType']}`"
            ),
            f"- Requested sample count: {request.sample_count}",
            (
                "- Generated graphs: "
                f"{aggregation_report['generatedSampleCount']}"
            ),
            (
                "- Graphs with matching nodes: "
                f"{aggregation_report['samplesWithMatches']}"
            ),
            (
                "- Total matching nodes: "
                f"{aggregation_report['totalMatchingNodes']}"
            ),
            visualization_note,
            "",
            "## Final suggestions",
            "",
            *suggestion_lines,
            "",
            "## Files",
            "",
            "- `request.json`: validated request payload",
            "- `generated_graph_*.json`: raw generated graph samples",
            "- `matching_report.json`: per-graph semantic matches and extras",
            "- `aggregation_report.json`: support counts and returned suggestions",
            "- `generated_graph_*.png`: optional graph visualizations",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _signature_data(
        signature: Mapping[NeighborRelation, int],
    ) -> list[dict[str, Any]]:
        return [
            {
                "neighborRoomType": room_type,
                "edgeType": edge_type,
                "count": count,
            }
            for (room_type, edge_type), count in sorted(signature.items())
        ]

    @staticmethod
    def _json_safe_node_id(node_id: Hashable) -> Any:
        try:
            json.dumps(node_id)
        except (TypeError, ValueError):
            return repr(node_id)
        return node_id
