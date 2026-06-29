from __future__ import annotations

from collections import Counter

import networkx as nx

from graph_layout_synth.api.sampling import ExistingGeneratorSampler
from graph_layout_synth.api.semantic_anchor_matching import (
    build_anchor_neighbor_signature,
    build_candidate_neighbor_signature,
    covers_neighbor_signature,
    extract_anchor_room_type,
    find_matching_anchor_nodes,
    is_semantic_anchor_match,
)


def _add_neighbor(
    graph: nx.Graph,
    anchor_node: str,
    neighbor_node: str,
    neighbor_type: str,
    edge_type: str,
) -> None:
    graph.add_node(neighbor_node, type=neighbor_type)
    graph.add_edge(anchor_node, neighbor_node, edge_type=edge_type)


def _frontend_anchor(
    neighbors: list[tuple[str, str]] | None = None,
    room_type: str = "PatientRoom",
) -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("frontend-anchor", type=room_type)
    for index, (neighbor_type, edge_type) in enumerate(neighbors or []):
        _add_neighbor(
            graph,
            "frontend-anchor",
            f"frontend-neighbor-{index}",
            neighbor_type,
            edge_type,
        )
    return graph


def _generated_candidate(
    neighbors: list[tuple[str, str]],
    room_type: str = "PatientRoom",
    candidate_node: str = "candidate",
) -> nx.Graph:
    graph = nx.Graph()
    graph.add_node(candidate_node, type=room_type)
    for index, (neighbor_type, edge_type) in enumerate(neighbors):
        _add_neighbor(
            graph,
            candidate_node,
            f"{candidate_node}-neighbor-{index}",
            neighbor_type,
            edge_type,
        )
    return graph


def test_signature_helpers_extract_room_type_and_relation_counts() -> None:
    frontend = _frontend_anchor(
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
            ("PatientRoom", "wall"),
        ]
    )

    assert extract_anchor_room_type(frontend, "frontend-anchor") == "PatientRoom"
    assert build_anchor_neighbor_signature(frontend, "frontend-anchor") == Counter(
        {
            ("PatientRoom", "wall"): 2,
            ("Corridor", "door"): 1,
        }
    )
    assert build_candidate_neighbor_signature(
        frontend,
        "frontend-anchor",
    ) == build_anchor_neighbor_signature(frontend, "frontend-anchor")


def test_same_room_type_with_empty_frontend_signature_matches() -> None:
    frontend = _frontend_anchor()
    generated = _generated_candidate(
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ]
    )

    assert is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_different_room_type_does_not_match_even_when_neighbors_cover() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = _generated_candidate(
        [("Corridor", "door")],
        room_type="ClinicalSupport",
    )

    assert not is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_exact_one_hop_signature_coverage_matches() -> None:
    required = [
        ("Corridor", "door"),
        ("PatientRoom", "wall"),
    ]
    frontend = _frontend_anchor(required)
    generated = _generated_candidate(required)

    assert is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_coverage_helper_is_one_way_and_count_aware() -> None:
    required = Counter(
        {
            ("Corridor", "door"): 1,
            ("PatientRoom", "wall"): 2,
        }
    )

    assert covers_neighbor_signature(
        required,
        Counter(
            {
                ("Corridor", "door"): 1,
                ("PatientRoom", "wall"): 2,
                ("StaffSupport", "door"): 4,
            }
        ),
    )
    assert not covers_neighbor_signature(
        required,
        Counter(
            {
                ("Corridor", "door"): 1,
                ("PatientRoom", "wall"): 1,
            }
        ),
    )


def test_additional_neighbors_and_higher_degree_still_match() -> None:
    frontend = _frontend_anchor(
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
        ]
    )
    generated = _generated_candidate(
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
            ("Bathroom", "door"),
            ("StaffSupport", "door"),
            ("NurseStation", "wall"),
        ]
    )

    assert frontend.degree["frontend-anchor"] == 2
    assert generated.degree["candidate"] == 5
    assert is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_missing_required_neighbor_fails() -> None:
    frontend = _frontend_anchor(
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
        ]
    )
    generated = _generated_candidate([("Corridor", "door")])

    assert not is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_wrong_edge_type_fails() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = _generated_candidate([("Corridor", "wall")])

    assert not is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_multiset_count_shortfall_fails() -> None:
    frontend = _frontend_anchor(
        [
            ("PatientRoom", "wall"),
            ("PatientRoom", "wall"),
        ]
    )
    generated = _generated_candidate([("PatientRoom", "wall")])

    assert not is_semantic_anchor_match(
        frontend,
        "frontend-anchor",
        generated,
        "candidate",
    )


def test_multiset_equal_or_greater_count_passes() -> None:
    frontend = _frontend_anchor(
        [
            ("PatientRoom", "wall"),
            ("PatientRoom", "wall"),
        ]
    )
    for generated_count in (2, 3):
        generated = _generated_candidate(
            [("PatientRoom", "wall")] * generated_count
        )
        assert is_semantic_anchor_match(
            frontend,
            "frontend-anchor",
            generated,
            "candidate",
        )


def test_one_generated_graph_returns_all_matching_nodes() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    for candidate_node in ("match-a", "match-b"):
        generated.add_node(candidate_node, type="PatientRoom")
        _add_neighbor(
            generated,
            candidate_node,
            f"{candidate_node}-corridor",
            "Corridor",
            "door",
        )
    generated.add_node("wrong-type", type="StaffSupport")
    _add_neighbor(
        generated,
        "wrong-type",
        "wrong-type-corridor",
        "Corridor",
        "door",
    )

    assert set(
        find_matching_anchor_nodes(
            frontend,
            "frontend-anchor",
            generated,
        )
    ) == {"match-a", "match-b"}


def test_one_generated_graph_can_return_zero_matching_nodes() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = _generated_candidate([("Corridor", "wall")])

    assert (
        find_matching_anchor_nodes(
            frontend,
            "frontend-anchor",
            generated,
        )
        == []
    )


def test_matching_membership_does_not_depend_on_node_insertion_order() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])

    def generated_with_order(order: tuple[str, ...]) -> nx.Graph:
        graph = nx.Graph()
        for candidate_node in order:
            graph.add_node(candidate_node, type="PatientRoom")
            edge_type = "wall" if candidate_node == "not-a-match" else "door"
            _add_neighbor(
                graph,
                candidate_node,
                f"{candidate_node}-corridor",
                "Corridor",
                edge_type,
            )
        return graph

    forward = generated_with_order(("match-a", "not-a-match", "match-b"))
    reverse = generated_with_order(("match-b", "not-a-match", "match-a"))

    assert set(
        find_matching_anchor_nodes(frontend, "frontend-anchor", forward)
    ) == {"match-a", "match-b"}
    assert set(
        find_matching_anchor_nodes(frontend, "frontend-anchor", reverse)
    ) == {"match-a", "match-b"}


def test_matching_returns_all_nodes_without_random_or_modulo_selection() -> None:
    frontend = _frontend_anchor()
    generated = nx.Graph()
    for candidate_node in ("patient-1", "patient-2", "patient-3"):
        generated.add_node(candidate_node, type="PatientRoom")

    first_call = find_matching_anchor_nodes(
        frontend,
        "frontend-anchor",
        generated,
    )
    second_call = find_matching_anchor_nodes(
        frontend,
        "frontend-anchor",
        generated,
    )

    assert set(first_call) == {"patient-1", "patient-2", "patient-3"}
    assert set(second_call) == set(first_call)
    assert len(first_call) == len(second_call) == 3


def test_sampler_does_not_arbitrarily_project_when_multiple_nodes_match() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    for candidate_node in ("match-a", "match-b"):
        generated.add_node(candidate_node, type="PatientRoom")
        _add_neighbor(
            generated,
            candidate_node,
            f"{candidate_node}-corridor",
            "Corridor",
            "door",
        )

    projected = ExistingGeneratorSampler._project_unique_semantic_match(
        frontend,
        "frontend-anchor",
        generated,
        sample_index=0,
    )

    assert set(projected.nodes) == set(frontend.nodes)
    assert set(projected.edges) == set(frontend.edges)


def test_sampler_projects_neighborhood_for_one_semantic_match() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = _generated_candidate(
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ]
    )
    generated.add_node("non-match", type="PatientRoom")
    _add_neighbor(
        generated,
        "non-match",
        "non-match-corridor",
        "Corridor",
        "wall",
    )

    projected = ExistingGeneratorSampler._project_unique_semantic_match(
        frontend,
        "frontend-anchor",
        generated,
        sample_index=7,
    )

    predicted_types = {
        attributes["type"]
        for _, attributes in projected.nodes(data=True)
        if attributes.get("is_predicted")
    }
    assert predicted_types == {"Corridor", "StaffSupport"}
