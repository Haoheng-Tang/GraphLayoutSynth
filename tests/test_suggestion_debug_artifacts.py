from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Hashable

import networkx as nx
import pytest

from graph_layout_synth.api.models import SuggestNextRoomRequest
from graph_layout_synth.api.predictor import NextRoomPredictor
from graph_layout_synth.api.suggestion_debug_artifacts import (
    ARTIFACT_DIRECTORY_ENV,
    SAVE_ARTIFACTS_ENV,
    SAVE_PNGS_ENV,
    SuggestionArtifactWriter,
)


class DebugSampler:
    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        generated = nx.Graph(candidate_id="debug-candidate")
        generated.add_node("match", type="Corridor", zone="patient")
        generated.add_node("known", type="PatientRoom")
        generated.add_node("extra", type="StaffSupport")
        generated.add_edge("match", "known", edge_type="door")
        generated.add_edge("match", "extra", edge_type="wall")
        return [generated]


class ConfiguredDebugSampler(DebugSampler):
    config = object()


@pytest.fixture(autouse=True)
def clear_debug_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SAVE_ARTIFACTS_ENV, raising=False)
    monkeypatch.delenv(SAVE_PNGS_ENV, raising=False)
    monkeypatch.delenv(ARTIFACT_DIRECTORY_ENV, raising=False)


def _request(
    *,
    include_debug_artifacts: bool = False,
    include_debug_visualizations: bool = False,
) -> SuggestNextRoomRequest:
    return SuggestNextRoomRequest.model_validate(
        {
            "floorplan": {
                "schemaVersion": 1,
                "rooms": [
                    {
                        "id": "corridor-1",
                        "type": "Corridor",
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                    {
                        "id": "patient-1",
                        "type": "PatientRoom",
                        "x": 100,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                ],
                "edges": [
                    {
                        "id": "known-door",
                        "sourceRoomId": "corridor-1",
                        "targetRoomId": "patient-1",
                        "edgeType": "door",
                    }
                ],
                "selectedRoomId": "corridor-1",
            },
            "anchorRoomId": "corridor-1",
            "sampleCount": 1,
            "includeDebugArtifacts": include_debug_artifacts,
            "includeDebugVisualizations": include_debug_visualizations,
        }
    )


def _only_run_directory(base_directory: Path) -> Path:
    run_directories = list(base_directory.iterdir())
    assert len(run_directories) == 1
    assert run_directories[0].is_dir()
    return run_directories[0]


def test_artifacts_are_not_saved_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_directory = tmp_path / "suggestion-artifacts"
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(base_directory))

    response = NextRoomPredictor(sampler=DebugSampler()).suggest(_request())

    assert response.suggestions[0].room_type == "StaffSupport"
    assert not base_directory.exists()


def test_request_flag_saves_complete_debug_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_directory = tmp_path / "suggestion-artifacts"
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(base_directory))

    response = NextRoomPredictor(sampler=DebugSampler()).suggest(
        _request(include_debug_artifacts=True)
    )

    assert response.suggestions[0].room_type == "StaffSupport"
    run_directory = _only_run_directory(base_directory)
    assert {
        "README.md",
        "aggregation_report.json",
        "generated_graph_000.json",
        "matching_report.json",
        "request.json",
    } <= {path.name for path in run_directory.iterdir()}

    request_snapshot = json.loads(
        (run_directory / "request.json").read_text(encoding="utf-8")
    )
    assert request_snapshot["anchorRoomId"] == "corridor-1"
    assert request_snapshot["sampleCount"] == 1
    assert request_snapshot["includeDebugArtifacts"] is True
    assert request_snapshot["includeDebugVisualizations"] is False

    graph_data = json.loads(
        (run_directory / "generated_graph_000.json").read_text(
            encoding="utf-8"
        )
    )
    assert graph_data["graph"]["candidate_id"] == "debug-candidate"
    assert {node["type"] for node in graph_data["nodes"]} == {
        "Corridor",
        "PatientRoom",
        "StaffSupport",
    }
    assert {edge["edge_type"] for edge in graph_data["links"]} == {
        "door",
        "wall",
    }

    matching_report = json.loads(
        (run_directory / "matching_report.json").read_text(encoding="utf-8")
    )
    graph_report = matching_report["generatedGraphs"][0]
    assert graph_report["matchingNodeCount"] == 1
    assert graph_report["matchingNodes"][0]["nodeId"] == "match"
    assert graph_report["matchingNodes"][0]["roomType"] == "Corridor"
    assert graph_report["producedCandidates"] is True
    assert graph_report["candidateRoomTypes"] == ["StaffSupport"]
    assert graph_report["matchingNodes"][0]["neighborSignature"] == [
        {
            "neighborRoomType": "PatientRoom",
            "edgeType": "door",
            "count": 1,
        },
        {
            "neighborRoomType": "StaffSupport",
            "edgeType": "wall",
            "count": 1,
        },
    ]

    aggregation_report = json.loads(
        (run_directory / "aggregation_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert aggregation_report == {
        "frontendAnchorRoomId": "corridor-1",
        "frontendAnchorRoomType": "Corridor",
        "frontendKnownNeighborSignature": [
            {
                "neighborRoomType": "PatientRoom",
                "edgeType": "door",
                "count": 1,
            }
        ],
        "generatedSampleCount": 1,
        "samplesWithMatches": 1,
        "totalMatchingNodes": 1,
        "samplesWithCandidates": 1,
        "candidateCountsByRoomType": {"StaffSupport": 1},
        "finalSuggestions": [
            {
                "roomType": "StaffSupport",
                "sampleCount": 1,
                "sampleShare": 1.0,
                "confidence": 1.0,
                "reason": (
                    "Appeared as an extra neighbor of a semantically matched "
                    "Corridor in 1 of 1 generated graph samples. "
                    "Dominant connection type: wall."
                ),
                "edgeType": "wall",
                "edgeTypeCounts": {"wall": 1},
                "intendedEdges": None,
            }
        ],
        "predictorVersion": "graphlayoutsynth-v1",
    }

    summary = (run_directory / "README.md").read_text(encoding="utf-8")
    assert "Anchor room ID: `corridor-1`" in summary
    assert "Generated graphs: 1" in summary
    assert "Graphs with matching nodes: 1" in summary
    assert "Total matching nodes: 1" in summary
    assert "| StaffSupport | 1 | 1.0000 |" in summary


def test_environment_flag_enables_artifact_saving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_directory = tmp_path / "environment-artifacts"
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(base_directory))
    monkeypatch.setenv(SAVE_ARTIFACTS_ENV, "true")

    NextRoomPredictor(sampler=DebugSampler()).suggest(_request())

    run_directory = _only_run_directory(base_directory)
    assert (run_directory / "request.json").is_file()


def test_visualization_flag_saves_png_and_enables_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_directory = tmp_path / "visualized-artifacts"
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(base_directory))

    def fake_visualizer(
        graph: nx.Graph,
        output_path: str | Path,
        title: str | None = None,
        config: object | None = None,
    ) -> Path:
        assert graph.graph["candidate_id"] == "debug-candidate"
        assert title == "Suggestion generated graph 000"
        assert config is ConfiguredDebugSampler.config
        path = Path(output_path)
        path.write_bytes(b"fake png")
        return path

    predictor = NextRoomPredictor(
        sampler=ConfiguredDebugSampler(),
        artifact_writer=SuggestionArtifactWriter(
            visualizer=fake_visualizer
        ),
    )
    predictor.suggest(_request(include_debug_visualizations=True))

    run_directory = _only_run_directory(base_directory)
    assert (run_directory / "generated_graph_000.png").read_bytes() == b"fake png"
    assert (run_directory / "generated_graph_000.json").is_file()


def test_visualization_failure_warns_and_does_not_fail_prediction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_directory = tmp_path / "visualization-failure"
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(base_directory))

    def failing_visualizer(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("visualizer failed")

    predictor = NextRoomPredictor(
        sampler=DebugSampler(),
        artifact_writer=SuggestionArtifactWriter(
            visualizer=failing_visualizer
        ),
    )

    with caplog.at_level(logging.WARNING):
        response = predictor.suggest(
            _request(include_debug_visualizations=True)
        )

    assert response.suggestions[0].room_type == "StaffSupport"
    run_directory = _only_run_directory(base_directory)
    assert (run_directory / "generated_graph_000.json").is_file()
    assert (run_directory / "aggregation_report.json").is_file()
    assert not (run_directory / "generated_graph_000.png").exists()
    assert "Failed to save suggestion debug visualization" in caplog.text


def test_artifact_save_failure_warns_and_does_not_break_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    invalid_base_directory = tmp_path / "not-a-directory"
    invalid_base_directory.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv(
        ARTIFACT_DIRECTORY_ENV,
        str(invalid_base_directory),
    )

    with caplog.at_level(logging.WARNING):
        response = NextRoomPredictor(sampler=DebugSampler()).suggest(
            _request(include_debug_artifacts=True)
        )

    assert response.suggestions[0].room_type == "StaffSupport"
    assert "Failed to save next-room suggestion debug artifacts" in caplog.text
