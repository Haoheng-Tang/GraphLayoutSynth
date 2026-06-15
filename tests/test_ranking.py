import networkx as nx

from graph_layout_synth.ranking import (
    compute_candidate_metrics,
    rank_candidates,
    score_candidate,
    score_candidate_breakdown,
)


def _valid_graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="PatientRoom", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")
    return graph


def test_metric_computation_on_simple_valid_graph():
    metrics = compute_candidate_metrics(_valid_graph())

    assert metrics.node_count == 2
    assert metrics.edge_count == 1
    assert metrics.room_count == 1
    assert metrics.corridor_count == 1
    assert metrics.door_edge_count == 1
    assert metrics.wall_edge_count == 0
    assert metrics.connected_graph == 1
    assert metrics.corridor_access_ratio == 1.0
    assert metrics.abstract_node_count == 0
    assert metrics.invalid_edge_type_count == 0
    assert metrics.validation_passed == 1
    assert metrics.edge_node_ratio == 0.5
    assert metrics.room_corridor_ratio == 1.0
    assert metrics.door_wall_ratio == 1.0
    assert metrics.corridor_fraction == 0.5
    assert metrics.dead_end_count == 1
    assert metrics.average_room_to_corridor_distance == 1.0
    assert metrics.max_room_to_corridor_distance == 1


def test_metric_computation_on_invalid_edge_type():
    graph = _valid_graph()
    graph.add_edge("corridor", "room", edge_type="stairs")

    metrics = compute_candidate_metrics(graph)

    assert metrics.invalid_edge_type_count == 1
    assert metrics.validation_passed == 0


def test_ranking_order_is_deterministic_for_controlled_candidates():
    valid_graph = _valid_graph()
    invalid_graph = _valid_graph()
    invalid_graph.add_node("orphan", type="PatientRoom", zone="zone_2", is_abstract=False)

    ranked = rank_candidates(
        [
            {"candidate_id": "bad", "graph": invalid_graph},
            {"candidate_id": "good", "graph": valid_graph},
        ]
    )

    assert ranked[0]["candidate_id"] == "good"
    assert ranked[0]["final_score"] == score_candidate(compute_candidate_metrics(valid_graph))
    assert ranked[0]["ranking_score"] == ranked[0]["final_score"]


def test_valid_candidates_with_different_topology_get_different_scores():
    compact = _valid_graph()
    denser = _valid_graph()
    denser.add_node("support", type="ClinicalSupport", zone="zone_1", is_abstract=False)
    denser.add_edge("corridor", "support", edge_type="door")
    denser.add_edge("room", "support", edge_type="wall")

    compact_score = score_candidate(compute_candidate_metrics(compact))
    denser_score = score_candidate(compute_candidate_metrics(denser))

    assert compact_score != denser_score


def test_score_breakdown_sums_to_final_score():
    metrics = compute_candidate_metrics(_valid_graph())
    breakdown = score_candidate_breakdown(metrics)

    assert round(sum(breakdown.values()), 4) == score_candidate(metrics)
    assert {"validation", "connectivity", "corridor_access", "edge_density"}.issubset(breakdown)


def test_ranking_targets_can_change_score_components():
    metrics = compute_candidate_metrics(_valid_graph())

    default_breakdown = score_candidate_breakdown(metrics)
    tuned_breakdown = score_candidate_breakdown(
        metrics,
        targets={"edge_node_ratio": metrics.edge_node_ratio},
    )

    assert tuned_breakdown["edge_density"] > default_breakdown["edge_density"]


def test_deterministic_tie_breaking_uses_candidate_id_fallback():
    graph_a = _valid_graph()
    graph_b = _valid_graph()

    ranked = rank_candidates(
        [
            {"candidate_id": "candidate_b", "graph": graph_b},
            {"candidate_id": "candidate_a", "graph": graph_a},
        ]
    )

    assert ranked[0]["candidate_id"] == "candidate_a"
    assert ranked[0]["tie_break_keys"]["candidate_id_asc"] == "candidate_a"
