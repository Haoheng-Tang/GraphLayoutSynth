import networkx as nx

from graph_layout_synth.ranking import (
    compute_candidate_metrics,
    rank_candidates,
    score_candidate,
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
    assert ranked[0]["ranking_score"] == score_candidate(compute_candidate_metrics(valid_graph))
