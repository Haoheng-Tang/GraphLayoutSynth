from __future__ import annotations

from collections.abc import Sequence
from typing import Hashable

import networkx as nx
from fastapi.testclient import TestClient

from graph_layout_synth.api.adapter import floorplan_to_graph
from graph_layout_synth.api.models import FloorplanState, SuggestNextRoomRequest
from graph_layout_synth.api.predictor import NextRoomPredictor
from graph_layout_synth.api.sampling import GraphSampler
from server.main import create_app


def _request_body(*, sample_count: int = 3, include_edge_side: bool = False) -> dict:
    edge = {
        "id": "edge-1",
        "sourceRoomId": "room-1",
        "targetRoomId": "room-2",
        "edgeType": "door",
    }
    if include_edge_side:
        edge["side"] = "east"
    return {
        "floorplan": {
            "schemaVersion": 1,
            "rooms": [
                {
                    "id": "room-1",
                    "type": "Corridor",
                    "x": 100,
                    "y": 100,
                    "width": 150,
                    "height": 80,
                },
                {
                    "id": "room-2",
                    "type": "PatientRoom",
                    "x": 250,
                    "y": 100,
                    "width": 150,
                    "height": 110,
                },
            ],
            "edges": [edge],
            "selectedRoomId": "room-1",
        },
        "anchorRoomId": "room-1",
        "sampleCount": sample_count,
    }


class FakeSampler:
    def __init__(self, new_neighbor_types: Sequence[Sequence[str]]) -> None:
        self.new_neighbor_types = new_neighbor_types
        self.received_anchor: Hashable | None = None
        self.received_graph: nx.Graph | None = None

    def sample(
        self,
        partial_graph: nx.Graph,
        anchor_node_id: Hashable,
        sample_count: int,
    ) -> list[nx.Graph]:
        self.received_anchor = anchor_node_id
        self.received_graph = partial_graph
        samples = []
        for sample_index, room_types in enumerate(self.new_neighbor_types[:sample_count]):
            sample = nx.Graph()
            generated_anchor = ("generated-anchor", sample_index)
            sample.add_node(
                generated_anchor,
                type=partial_graph.nodes[anchor_node_id]["type"],
            )
            for known_index, known_neighbor in enumerate(
                partial_graph.neighbors(anchor_node_id)
            ):
                known_node = ("known", sample_index, known_index)
                sample.add_node(
                    known_node,
                    type=partial_graph.nodes[known_neighbor]["type"],
                )
                sample.add_edge(
                    generated_anchor,
                    known_node,
                    edge_type=partial_graph.edges[
                        anchor_node_id,
                        known_neighbor,
                    ]["edge_type"],
                )
            for neighbor_index, room_type in enumerate(room_types):
                node_id = ("extra", sample_index, neighbor_index)
                sample.add_node(node_id, type=room_type)
                sample.add_edge(generated_anchor, node_id, edge_type="door")
            samples.append(sample)
        return samples


def _client(sampler: GraphSampler) -> TestClient:
    predictor = NextRoomPredictor(sampler=sampler)
    return TestClient(create_app(predictor))


def test_health_endpoint_returns_ok() -> None:
    response = _client(FakeSampler([])).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_local_frontend_origin_is_allowed_by_cors() -> None:
    response = _client(FakeSampler([])).options(
        "/suggest-next-room",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_valid_request_returns_ranked_camel_case_suggestions() -> None:
    client = _client(
        FakeSampler(
            [
                ["PatientRoom", "PatientRoom", "StaffSupport"],
                ["PatientRoom"],
                ["ClinicalSupport"],
            ]
        )
    )

    response = client.post("/suggest-next-room", json=_request_body())

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [
            {
                "roomType": "PatientRoom",
                "sampleCount": 2,
                "sampleShare": 2 / 3,
                "confidence": 2 / 3,
                "reason": (
                    "Appeared as an extra neighbor of a semantically matched Corridor "
                    "in 2 of 3 generated graph samples."
                ),
            },
            {
                "roomType": "ClinicalSupport",
                "sampleCount": 1,
                "sampleShare": 1 / 3,
                "confidence": 1 / 3,
                "reason": (
                    "Appeared as an extra neighbor of a semantically matched Corridor "
                    "in 1 of 3 generated graph samples."
                ),
            },
            {
                "roomType": "StaffSupport",
                "sampleCount": 1,
                "sampleShare": 1 / 3,
                "confidence": 1 / 3,
                "reason": (
                    "Appeared as an extra neighbor of a semantically matched Corridor "
                    "in 1 of 3 generated graph samples."
                ),
            },
        ],
        "sampleCount": 3,
        "predictorVersion": "graphlayoutsynth-v1",
    }


def test_request_does_not_require_side() -> None:
    response = _client(FakeSampler([[]])).post(
        "/suggest-next-room",
        json=_request_body(sample_count=1),
    )

    assert response.status_code == 200


def test_accidental_side_fields_are_accepted_and_ignored_by_prediction() -> None:
    body = _request_body(sample_count=1, include_edge_side=True)
    body["side"] = "north"

    response = _client(FakeSampler([["StaffSupport"]])).post(
        "/suggest-next-room",
        json=body,
    )

    assert response.status_code == 200
    assert response.json()["suggestions"][0]["roomType"] == "StaffSupport"


def test_invalid_anchor_room_id_returns_400() -> None:
    body = _request_body()
    body["anchorRoomId"] = "missing-room"

    response = _client(FakeSampler([])).post("/suggest-next-room", json=body)

    assert response.status_code == 400
    assert "anchorRoomId" in str(response.json()["detail"])


def test_invalid_edge_reference_returns_400() -> None:
    body = _request_body()
    body["floorplan"]["edges"][0]["targetRoomId"] = "missing-room"

    response = _client(FakeSampler([])).post("/suggest-next-room", json=body)

    assert response.status_code == 400
    assert "targetRoomId" in str(response.json()["detail"])


def test_missing_floorplan_returns_400() -> None:
    response = _client(FakeSampler([])).post(
        "/suggest-next-room",
        json={"anchorRoomId": "room-1", "sampleCount": 3},
    )

    assert response.status_code == 400


def test_out_of_range_sample_count_returns_400() -> None:
    response = _client(FakeSampler([])).post(
        "/suggest-next-room",
        json=_request_body(sample_count=201),
    )

    assert response.status_code == 400


def test_non_integer_sample_count_returns_400() -> None:
    body = _request_body()
    body["sampleCount"] = "3"

    response = _client(FakeSampler([])).post("/suggest-next-room", json=body)

    assert response.status_code == 400


def test_frontend_room_ids_map_to_hidden_internal_integer_ids() -> None:
    floorplan = FloorplanState.model_validate(
        _request_body(include_edge_side=True)["floorplan"]
    )

    adapted = floorplan_to_graph(floorplan)

    assert adapted.frontend_to_internal == {"room-1": 0, "room-2": 1}
    assert adapted.internal_to_frontend == {0: "room-1", 1: "room-2"}
    assert adapted.graph.nodes[0]["external_id"] == "room-1"
    assert adapted.graph.edges[0, 1]["edge_type"] == "door"
    assert adapted.graph.edges[0, 1]["side"] == "east"


def test_predictor_passes_mapped_anchor_to_sampler() -> None:
    sampler = FakeSampler([[]])
    request = SuggestNextRoomRequest.model_validate(_request_body(sample_count=1))

    NextRoomPredictor(sampler=sampler).suggest(request)

    assert sampler.received_anchor == 0
    assert sampler.received_graph is not None
    assert set(sampler.received_graph.nodes) == {0, 1}


def test_existing_neighbors_are_not_counted() -> None:
    sampler = FakeSampler([[], []])

    response = _client(sampler).post(
        "/suggest-next-room",
        json=_request_body(sample_count=2),
    )

    assert response.status_code == 200
    assert response.json()["suggestions"] == []


def test_endpoint_aggregates_all_semantic_matches_once_per_graph() -> None:
    class MultipleMatchSampler:
        def sample(
            self,
            partial_graph: nx.Graph,
            anchor_node_id: Hashable,
            sample_count: int,
        ) -> list[nx.Graph]:
            generated = nx.Graph()
            for match_name, extras in (
                ("match-a", [("StaffSupport", "door")]),
                (
                    "match-b",
                    [
                        ("StaffSupport", "wall"),
                        ("ClinicalSupport", "door"),
                    ],
                ),
            ):
                generated.add_node(match_name, type="Corridor")
                known_neighbor = f"{match_name}-known-patient"
                generated.add_node(known_neighbor, type="PatientRoom")
                generated.add_edge(
                    match_name,
                    known_neighbor,
                    edge_type="door",
                )
                for extra_index, (room_type, edge_type) in enumerate(extras):
                    extra_node = f"{match_name}-extra-{extra_index}"
                    generated.add_node(extra_node, type=room_type)
                    generated.add_edge(
                        match_name,
                        extra_node,
                        edge_type=edge_type,
                    )
            return [generated]

    response = _client(MultipleMatchSampler()).post(
        "/suggest-next-room",
        json=_request_body(sample_count=1),
    )

    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert [suggestion["roomType"] for suggestion in suggestions] == [
        "ClinicalSupport",
        "StaffSupport",
    ]
    assert all(suggestion["sampleCount"] == 1 for suggestion in suggestions)
    assert all(suggestion["sampleShare"] == 1.0 for suggestion in suggestions)


def test_empty_generator_result_returns_empty_suggestions_and_actual_count() -> None:
    response = _client(FakeSampler([])).post(
        "/suggest-next-room",
        json=_request_body(sample_count=3),
    )

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [],
        "sampleCount": 0,
        "predictorVersion": "graphlayoutsynth-v1",
    }


def test_generator_failure_returns_controlled_500() -> None:
    class FailingSampler:
        def sample(
            self,
            partial_graph: nx.Graph,
            anchor_node_id: Hashable,
            sample_count: int,
        ) -> list[nx.Graph]:
            raise RuntimeError("secret internal detail")

    client = TestClient(create_app(NextRoomPredictor(sampler=FailingSampler())))

    response = client.post("/suggest-next-room", json=_request_body())

    assert response.status_code == 500
    assert response.json() == {"detail": "Next-room prediction failed."}
    assert "secret internal detail" not in response.text
