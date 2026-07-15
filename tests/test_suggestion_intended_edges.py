from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Hashable

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    aggregate_candidate_evidence_from_matching_nodes,
    build_suggestions_from_counts,
    intended_edge_relations_for_generated_graph,
    known_frontend_neighbor_targets,
)
from graph_layout_synth.api.models import (
    DoorOrAdjacency,
    FloorplanState,
    Room,
    SuggestNextRoomRequest,
)
from graph_layout_synth.api.predictor import NextRoomPredictor
from graph_layout_synth.api.suggestion_debug_artifacts import ARTIFACT_DIRECTORY_ENV
from server.main import create_app


def _frontend_graph(extra_corridor: bool = False) -> nx.Graph:
    """Frontend anchor PatientRoom with a known door-connected Corridor."""
    graph = nx.Graph()
    graph.add_node(0, type="PatientRoom", external_id="patient-1")
    graph.add_node(1, type="Corridor", external_id="corridor-1")
    graph.add_edge(0, 1, edge_type="door")
    if extra_corridor:
        graph.add_node(2, type="Corridor", external_id="corridor-2")
        graph.add_edge(0, 2, edge_type="door")
    return graph


def _generated_graph(
    secondary_edge_type: str | None,
    *,
    corridor_count: int = 1,
) -> nx.Graph:
    """Generated sample: matched anchor, known Corridor(s), extra PatientRoom."""
    graph = nx.Graph()
    graph.add_node("anchor", type="PatientRoom")
    graph.add_node("candidate", type="PatientRoom")
    graph.add_edge("anchor", "candidate", edge_type="wall")
    for index in range(corridor_count):
        corridor_id = f"corridor-{index}"
        graph.add_node(corridor_id, type="Corridor")
        graph.add_edge("anchor", corridor_id, edge_type="door")
    if secondary_edge_type is not None:
        graph.add_edge("candidate", "corridor-0", edge_type=secondary_edge_type)
    return graph


class FixedSampler:
    """Sampler returning pre-built generated graphs for endpoint tests."""

    def __init__(self, graphs: Sequence[nx.Graph]) -> None:
        self.graphs = list(graphs)

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        return self.graphs[:sample_count]


def _request_body(sample_count: int = 1) -> dict:
    return {
        "floorplan": {
            "schemaVersion": 1,
            "rooms": [
                {
                    "id": "patient-1",
                    "type": "PatientRoom",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                },
                {
                    "id": "corridor-1",
                    "type": "Corridor",
                    "x": 100,
                    "y": 0,
                    "width": 200,
                    "height": 60,
                },
            ],
            "edges": [
                {
                    "id": "edge-1",
                    "sourceRoomId": "patient-1",
                    "targetRoomId": "corridor-1",
                    "edgeType": "door",
                }
            ],
        },
        "anchorRoomId": "patient-1",
        "sampleCount": sample_count,
    }


def _suggest(graphs: Sequence[nx.Graph], sample_count: int) -> dict:
    client = TestClient(create_app(NextRoomPredictor(sampler=FixedSampler(graphs))))
    response = client.post("/suggest-next-room", json=_request_body(sample_count))
    assert response.status_code == 200
    return response.json()


def test_known_frontend_neighbor_targets_keep_unambiguous_room_id() -> None:
    targets = known_frontend_neighbor_targets(_frontend_graph(), 0)

    assert targets == {("Corridor", "door"): ("corridor-1", "Corridor")}


def test_secondary_intended_edge_found_with_door_evidence() -> None:
    relations = intended_edge_relations_for_generated_graph(
        _frontend_graph(),
        0,
        _generated_graph("door"),
    )

    assert relations == {("PatientRoom", "corridor-1", "Corridor", "door")}


def test_no_hard_coded_patient_corridor_door_rule() -> None:
    relations = intended_edge_relations_for_generated_graph(
        _frontend_graph(),
        0,
        _generated_graph("wall"),
    )

    assert relations == {("PatientRoom", "corridor-1", "Corridor", "wall")}


def test_no_secondary_edge_when_generated_graph_lacks_it() -> None:
    relations = intended_edge_relations_for_generated_graph(
        _frontend_graph(),
        0,
        _generated_graph(None),
    )

    assert relations == set()


def test_endpoint_returns_intended_edge_with_anchor_edge_type_preserved() -> None:
    payload = _suggest([_generated_graph("door")], sample_count=1)

    suggestion = payload["suggestions"][0]
    assert suggestion["roomType"] == "PatientRoom"
    assert suggestion["edgeType"] == "wall"
    assert suggestion["intendedEdges"] == [
        {
            "targetExistingRoomId": "corridor-1",
            "targetRoomType": "Corridor",
            "edgeType": "door",
            "edgeTypeCounts": {"door": 1},
            "confidence": 1.0,
            "sampleCount": 1,
        }
    ]


def test_endpoint_backward_compatible_fields_and_null_intended_edges() -> None:
    payload = _suggest([_generated_graph(None)], sample_count=1)

    assert set(payload) == {"suggestions", "sampleCount", "predictorVersion"}
    suggestion = payload["suggestions"][0]
    for existing_field in (
        "roomType",
        "sampleCount",
        "sampleShare",
        "confidence",
        "reason",
        "edgeType",
        "edgeTypeCounts",
    ):
        assert existing_field in suggestion
    assert suggestion["edgeType"] == "wall"
    # Absent evidence yields an omitted (or null) field, never an invented edge.
    assert suggestion.get("intendedEdges") is None


def test_multiple_samples_aggregate_counts_and_dominant_edge_type() -> None:
    graphs = [
        _generated_graph("door"),
        _generated_graph("door"),
        _generated_graph("wall"),
        _generated_graph(None),
    ]

    payload = _suggest(graphs, sample_count=4)

    suggestion = payload["suggestions"][0]
    assert suggestion["sampleCount"] == 4
    intended = suggestion["intendedEdges"][0]
    assert intended["targetExistingRoomId"] == "corridor-1"
    assert intended["edgeTypeCounts"] == {"door": 2, "wall": 1}
    assert intended["edgeType"] == "door"
    assert intended["sampleCount"] == 3
    assert intended["confidence"] == pytest.approx(3 / 4)


def test_dominant_intended_edge_type_prefers_door_on_ties() -> None:
    graphs = [_generated_graph("door"), _generated_graph("wall")]

    payload = _suggest(graphs, sample_count=2)

    intended = payload["suggestions"][0]["intendedEdges"][0]
    assert intended["edgeTypeCounts"] == {"door": 1, "wall": 1}
    assert intended["edgeType"] == "door"


def test_ambiguous_known_corridors_omit_target_room_id() -> None:
    frontend = _frontend_graph(extra_corridor=True)
    generated = _generated_graph("door", corridor_count=2)

    evidence = aggregate_candidate_evidence_from_matching_nodes(
        frontend,
        0,
        [generated],
    )
    suggestions = build_suggestions_from_counts(
        evidence.room_type_counts,
        1,
        "PatientRoom",
        evidence.edge_type_counts_by_room_type,
        intended_edge_sample_counts=evidence.intended_edge_sample_counts,
        intended_edge_type_counts=evidence.intended_edge_type_counts,
    )

    intended = suggestions[0].intended_edges
    assert intended is not None
    assert len(intended) == 1
    assert intended[0].target_existing_room_id is None
    assert intended[0].target_room_type == "Corridor"
    assert intended[0].edge_type == "door"


def test_candidate_edge_to_extra_generated_room_is_not_an_intended_edge() -> None:
    """Edges to generated-only rooms are suggestions, not intended-edge targets.

    The StaffSupport candidate connects only to an *extra* generated corridor,
    not to the corridor that corresponds to the known frontend neighbor, so no
    secondary intended edge may be reported.
    """
    frontend = _frontend_graph()
    generated = nx.Graph()
    generated.add_node("anchor", type="PatientRoom")
    generated.add_node("corridor-0", type="Corridor")
    generated.add_node("extra-corridor", type="Corridor")
    generated.add_node("candidate", type="StaffSupport")
    generated.add_edge("anchor", "corridor-0", edge_type="door")
    generated.add_edge("anchor", "extra-corridor", edge_type="door")
    generated.add_edge("anchor", "candidate", edge_type="wall")
    generated.add_edge("candidate", "extra-corridor", edge_type="door")

    relations = intended_edge_relations_for_generated_graph(frontend, 0, generated)

    assert relations == set()


def test_debug_artifacts_include_intended_edge_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ARTIFACT_DIRECTORY_ENV, str(tmp_path))
    frontend = _frontend_graph()
    generated = _generated_graph("door")
    request = SuggestNextRoomRequest(
        floorplan=FloorplanState(
            schema_version=1,
            rooms=[
                Room(id="patient-1", type="PatientRoom", x=0, y=0, width=100, height=100),
                Room(id="corridor-1", type="Corridor", x=100, y=0, width=200, height=60),
            ],
            edges=[
                DoorOrAdjacency(
                    id="edge-1",
                    source_room_id="patient-1",
                    target_room_id="corridor-1",
                    edge_type="door",
                )
            ],
        ),
        anchor_room_id="patient-1",
        sample_count=1,
        include_debug_artifacts=True,
    )
    predictor = NextRoomPredictor(sampler=FixedSampler([generated]))

    response = predictor.suggest(request)

    assert response.suggestions[0].intended_edges is not None
    run_directories = list(tmp_path.iterdir())
    assert len(run_directories) == 1
    matching_report = json.loads(
        (run_directories[0] / "matching_report.json").read_text(encoding="utf-8")
    )
    assert matching_report["knownFrontendNeighborTargets"] == [
        {
            "neighborRoomType": "Corridor",
            "anchorEdgeType": "door",
            "targetRoomId": "corridor-1",
            "targetRoomType": "Corridor",
        }
    ]
    evidence = matching_report["generatedGraphs"][0]["intendedEdgeEvidence"]
    secondary_edges = [
        edge
        for match_detail in evidence
        for edge in match_detail["secondaryEdges"]
    ]
    assert {
        (edge["suggestedRoomType"], edge["targetRoomId"], edge["edgeType"])
        for edge in secondary_edges
    } == {("PatientRoom", "corridor-1", "door")}
    aggregation_report = json.loads(
        (run_directories[0] / "aggregation_report.json").read_text(encoding="utf-8")
    )
    final_suggestion = aggregation_report["finalSuggestions"][0]
    assert final_suggestion["intendedEdges"][0]["targetExistingRoomId"] == "corridor-1"
