from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import networkx as nx

import graph_layout_synth.api.sampling as sampling_module
from graph_layout_synth.api.matching_node_neighbor_aggregation import (
    aggregate_candidate_evidence_from_matching_nodes,
    aggregate_candidates_from_matching_nodes,
    build_suggestions_from_counts,
    candidate_room_types_for_generated_graph,
    extract_extra_neighbor_candidates,
    subtract_neighbor_signature,
)
from graph_layout_synth.api.sampling import (
    ExistingGeneratorSampler,
    GRAMMAR_MODE_ENV,
    SUGGESTION_CONFIG_PATH_ENV,
)


def _add_neighbor(
    graph: nx.Graph,
    anchor_node: str,
    neighbor_node: str,
    room_type: str,
    edge_type: str,
) -> None:
    graph.add_node(neighbor_node, type=room_type)
    graph.add_edge(anchor_node, neighbor_node, edge_type=edge_type)


def _frontend_anchor(
    neighbors: list[tuple[str, str]] | None = None,
) -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("frontend-anchor", type="PatientRoom")
    for index, (room_type, edge_type) in enumerate(neighbors or []):
        _add_neighbor(
            graph,
            "frontend-anchor",
            f"frontend-neighbor-{index}",
            room_type,
            edge_type,
        )
    return graph


def _add_candidate(
    graph: nx.Graph,
    candidate_node: str,
    neighbors: list[tuple[str, str]],
    room_type: str = "PatientRoom",
) -> None:
    graph.add_node(candidate_node, type=room_type)
    for index, (neighbor_type, edge_type) in enumerate(neighbors):
        _add_neighbor(
            graph,
            candidate_node,
            f"{candidate_node}-neighbor-{index}",
            neighbor_type,
            edge_type,
        )


def test_matching_node_extras_are_candidates_and_known_neighbors_are_subtracted() -> None:
    frontend = _frontend_anchor(
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
        ]
    )
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match",
        [
            ("Corridor", "door"),
            ("PatientRoom", "wall"),
            ("Bathroom", "door"),
            ("StaffSupport", "door"),
        ],
    )

    extras = extract_extra_neighbor_candidates(
        frontend,
        "frontend-anchor",
        generated,
        "match",
    )

    assert extras == Counter(
        {
            ("Bathroom", "door"): 1,
            ("StaffSupport", "door"): 1,
        }
    )
    assert ("Corridor", "door") not in extras
    assert ("PatientRoom", "wall") not in extras


def test_wrong_edge_type_does_not_create_misleading_candidates() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "not-a-match",
        [
            ("Corridor", "wall"),
            ("Bathroom", "door"),
        ],
    )

    assert extract_extra_neighbor_candidates(
        frontend,
        "frontend-anchor",
        generated,
        "not-a-match",
    ) == Counter()
    assert candidate_room_types_for_generated_graph(
        frontend,
        "frontend-anchor",
        generated,
    ) == set()


def test_multiset_subtraction_preserves_extra_multiplicity() -> None:
    required = Counter({("PatientRoom", "wall"): 1})
    candidate = Counter(
        {
            ("PatientRoom", "wall"): 3,
            ("StaffSupport", "door"): 1,
        }
    )

    assert subtract_neighbor_signature(required, candidate) == Counter(
        {
            ("PatientRoom", "wall"): 2,
            ("StaffSupport", "door"): 1,
        }
    )


def test_all_matching_nodes_in_one_graph_contribute_candidates() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match-a",
        [
            ("Corridor", "door"),
            ("Bathroom", "door"),
        ],
    )
    _add_candidate(
        generated,
        "match-b",
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ],
    )

    assert candidate_room_types_for_generated_graph(
        frontend,
        "frontend-anchor",
        generated,
    ) == {"Bathroom", "StaffSupport"}


def test_same_room_type_from_multiple_matches_counts_once_per_graph() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match-a",
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ],
    )
    _add_candidate(
        generated,
        "match-b",
        [
            ("Corridor", "door"),
            ("StaffSupport", "wall"),
        ],
    )

    assert aggregate_candidates_from_matching_nodes(
        frontend,
        "frontend-anchor",
        [generated],
    ) == Counter({"StaffSupport": 1})


def test_edge_type_counts_use_graph_sample_support_semantics() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match-a",
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ],
    )
    _add_candidate(
        generated,
        "match-b",
        [
            ("Corridor", "door"),
            ("StaffSupport", "wall"),
            ("ClinicalSupport", "door"),
        ],
    )

    evidence = aggregate_candidate_evidence_from_matching_nodes(
        frontend,
        "frontend-anchor",
        [generated],
    )

    assert evidence.room_type_counts == Counter(
        {"StaffSupport": 1, "ClinicalSupport": 1}
    )
    assert evidence.edge_type_counts_by_room_type == {
        "StaffSupport": Counter({"door": 1, "wall": 1}),
        "ClinicalSupport": Counter({"door": 1}),
    }


def test_same_room_type_across_graph_samples_increments_sample_count() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated_graphs = []
    for graph_index in range(2):
        generated = nx.Graph()
        _add_candidate(
            generated,
            f"match-{graph_index}",
            [
                ("Corridor", "door"),
                ("StaffSupport", "door"),
            ],
        )
        generated_graphs.append(generated)

    assert aggregate_candidates_from_matching_nodes(
        frontend,
        "frontend-anchor",
        generated_graphs,
    ) == Counter({"StaffSupport": 2})


def test_suggestions_use_graph_sample_share_and_deterministic_sorting() -> None:
    suggestions = build_suggestions_from_counts(
        Counter(
            {
                "PatientRoom": 3,
                "ClinicalSupport": 2,
                "Bathroom": 2,
            }
        ),
        sample_count=4,
        anchor_room_type="Corridor",
    )

    assert [suggestion.room_type for suggestion in suggestions] == [
        "PatientRoom",
        "Bathroom",
        "ClinicalSupport",
    ]
    assert [suggestion.sample_count for suggestion in suggestions] == [3, 2, 2]
    assert [suggestion.sample_share for suggestion in suggestions] == [
        0.75,
        0.5,
        0.5,
    ]
    assert suggestions[0].confidence == 0.75
    assert suggestions[0].reason == (
        "Appeared as an extra neighbor of a semantically matched Corridor "
        "in 3 of 4 generated graph samples."
    )


def test_suggestions_include_dominant_edge_type_and_counts() -> None:
    suggestions = build_suggestions_from_counts(
        Counter({"StaffSupport": 3}),
        sample_count=4,
        anchor_room_type="Corridor",
        edge_type_counts={
            "StaffSupport": Counter({"door": 2, "wall": 1}),
        },
    )

    assert suggestions[0].sample_count == 3
    assert suggestions[0].sample_share == 0.75
    assert suggestions[0].confidence == 0.75
    assert suggestions[0].edge_type == "door"
    assert suggestions[0].edge_type_counts == {"door": 2, "wall": 1}
    assert suggestions[0].reason == (
        "Appeared as an extra neighbor of a semantically matched Corridor "
        "in 3 of 4 generated graph samples. Dominant connection type: door."
    )


def test_dominant_edge_type_prefers_door_on_tie() -> None:
    suggestions = build_suggestions_from_counts(
        Counter({"StaffSupport": 2}),
        sample_count=2,
        anchor_room_type="PatientRoom",
        edge_type_counts={
            "StaffSupport": Counter({"door": 1, "wall": 1}),
        },
    )

    assert suggestions[0].edge_type == "door"
    assert suggestions[0].edge_type_counts == {"door": 1, "wall": 1}


def test_unknown_edge_type_is_ignored_for_edge_type_counts() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match",
        [
            ("Corridor", "door"),
            ("StaffSupport", "adjacency"),
        ],
    )

    evidence = aggregate_candidate_evidence_from_matching_nodes(
        frontend,
        "frontend-anchor",
        [generated],
    )
    suggestions = build_suggestions_from_counts(
        evidence.room_type_counts,
        sample_count=1,
        anchor_room_type="PatientRoom",
        edge_type_counts=evidence.edge_type_counts_by_room_type,
    )

    assert evidence.room_type_counts == Counter({"StaffSupport": 1})
    assert evidence.edge_type_counts_by_room_type == {}
    assert suggestions[0].edge_type is None
    assert suggestions[0].edge_type_counts is None


def test_missing_edge_type_does_not_crash_aggregation() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    generated.add_node("match", type="PatientRoom")
    generated.add_node("known", type="Corridor")
    generated.add_node("extra", type="StaffSupport")
    generated.add_edge("match", "known", edge_type="door")
    generated.add_edge("match", "extra")

    evidence = aggregate_candidate_evidence_from_matching_nodes(
        frontend,
        "frontend-anchor",
        [generated],
    )

    assert evidence.room_type_counts == Counter()
    assert evidence.edge_type_counts_by_room_type == {}


def test_graph_with_no_matching_nodes_contributes_no_candidates() -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "not-a-match",
        [
            ("Corridor", "wall"),
            ("Bathroom", "door"),
        ],
    )

    counts = aggregate_candidates_from_matching_nodes(
        frontend,
        "frontend-anchor",
        [generated],
    )

    assert counts == Counter()
    assert build_suggestions_from_counts(
        counts,
        sample_count=1,
        anchor_room_type="PatientRoom",
    ) == []


def test_no_matching_nodes_returns_empty_suggestions_with_nonzero_sample_count() -> None:
    suggestions = build_suggestions_from_counts(
        Counter(),
        sample_count=5,
        anchor_room_type="PatientRoom",
    )

    assert suggestions == []


def test_zero_generated_samples_returns_empty_suggestions_without_division() -> None:
    suggestions = build_suggestions_from_counts(
        Counter({"StaffSupport": 1}),
        sample_count=0,
        anchor_room_type="PatientRoom",
    )

    assert suggestions == []


def test_sampler_returns_raw_generated_graphs_without_node_selection(
    monkeypatch,
) -> None:
    frontend = _frontend_anchor([("Corridor", "door")])
    generated = nx.Graph()
    _add_candidate(
        generated,
        "match-a",
        [
            ("Corridor", "door"),
            ("Bathroom", "door"),
        ],
    )
    _add_candidate(
        generated,
        "match-b",
        [
            ("Corridor", "door"),
            ("StaffSupport", "door"),
        ],
        )

    monkeypatch.delenv(GRAMMAR_MODE_ENV, raising=False)
    monkeypatch.delenv(SUGGESTION_CONFIG_PATH_ENV, raising=False)
    monkeypatch.setattr(sampling_module, "load_config", lambda: object())
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=generated)],
    )

    samples = ExistingGeneratorSampler().sample(
        frontend,
        "frontend-anchor",
        sample_count=1,
    )

    assert samples == [generated]
    assert samples[0] is generated
    assert {"match-a", "match-b"} <= set(samples[0].nodes)


def test_sampler_can_use_config_path_from_environment(
    monkeypatch,
    tmp_path,
) -> None:
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    generated = nx.Graph()
    generated.add_node("match", type="Corridor")
    config_path = tmp_path / "api-config.yaml"
    expected_config = object()
    loaded_paths = []

    monkeypatch.setenv(GRAMMAR_MODE_ENV, "env_config")
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setattr(
        sampling_module,
        "load_config",
        lambda path: loaded_paths.append(path) or expected_config,
    )
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=generated)],
    )

    sampler = ExistingGeneratorSampler()
    samples = sampler.sample(
        frontend,
        "frontend-anchor",
        sample_count=1,
    )

    assert samples == [generated]
    assert loaded_paths == [config_path]
    assert sampler.config is expected_config
