import json

import networkx as nx

from graph_layout_synth.review_summary import (
    build_candidate_pool_summary,
    build_candidate_review_summary,
    export_review_summary_json,
    wall_adjacency_summary,
)


def _known_graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", is_abstract=False)
    graph.add_node("room_a", type="PatientRoom", is_abstract=False)
    graph.add_node("room_b", type="PatientRoom", is_abstract=False)
    graph.add_node("clinical", type="ClinicalSupport", is_abstract=False)
    graph.add_node("staff", type="StaffSupport", is_abstract=False)
    graph.add_edge("corridor", "room_a", edge_type="door")
    graph.add_edge("corridor", "room_b", edge_type="door")
    graph.add_edge("corridor", "clinical", edge_type="door")
    graph.add_edge("corridor", "staff", edge_type="door")
    graph.add_edge("room_a", "room_b", edge_type="wall")
    graph.add_edge("room_a", "clinical", edge_type="wall")
    graph.add_edge("room_b", "staff", edge_type="wall")
    return graph


def test_review_summary_is_json_serializable_and_counts_types():
    graph = _known_graph()
    summary = build_candidate_review_summary(
        "candidate_1",
        graph,
        candidate_report={"is_valid": True, "validation_errors": []},
        ranking_entry={
            "final_score": 123.0,
            "metrics": {"corridor_access_ratio": 1.0, "dead_end_count": 0},
            "score_breakdown": {"validation": 100.0},
        },
        artifact_paths={"graph_path": "outputs/candidate_1.json"},
    )

    json.dumps(summary)
    assert summary["node_type_counts"] == {
        "ClinicalSupport": 1,
        "Corridor": 1,
        "PatientRoom": 2,
        "StaffSupport": 1,
    }
    assert summary["edge_type_counts"] == {"door": 4, "wall": 3}
    assert summary["final_score"] == 123.0
    assert summary["artifact_paths"]["graph_path"] == "outputs/candidate_1.json"
    assert summary["support_type_counts"] == {"ClinicalSupport": 1, "StaffSupport": 1}
    assert summary["support_type_ratios"] == {"ClinicalSupport": 0.25, "StaffSupport": 0.25}
    assert "support_room_count" not in summary
    assert "support_room_ratio" not in summary
    assert "support_room_count" not in summary["key_metrics"]
    assert "support_room_ratio" not in summary["key_metrics"]


def test_degree_summary_and_histogram_for_known_graph():
    summary = build_candidate_review_summary("candidate_1", _known_graph())

    assert summary["degree_summary"] == {
        "degree_min": 2,
        "degree_mean": 2.8,
        "degree_max": 4,
    }
    assert summary["degree_histogram"] == {"2": 2, "3": 2, "4": 1}


def test_wall_adjacency_summary_for_known_wall_degrees():
    summary = wall_adjacency_summary(_known_graph())

    assert summary["counted_room_count"] == 4
    assert summary["room_wall_degree_min"] == 1
    assert summary["room_wall_degree_mean"] == 1.5
    assert summary["room_wall_degree_max"] == 2
    assert summary["room_wall_degree_histogram"] == {"1": 2, "2": 2}
    assert summary["isolated_wall_room_count"] == 0
    assert summary["isolated_wall_nodes"] == []
    assert summary["low_wall_adjacency_room_count"] == 2
    assert summary["low_wall_adjacency_nodes"] == [
        {"node_id": "clinical", "node_type": "ClinicalSupport", "wall_degree": 1},
        {"node_id": "staff", "node_type": "StaffSupport", "wall_degree": 1},
    ]
    assert summary["low_wall_adjacency_room_ratio"] == 0.5
    assert summary["interior_wall_adjacency_room_count"] == 2
    assert summary["interior_wall_adjacency_ratio"] == 0.5


def test_wall_adjacency_summary_handles_zero_counted_rooms():
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", is_abstract=False)

    summary = wall_adjacency_summary(graph)

    assert summary["counted_room_count"] == 0
    assert summary["room_wall_degree_min"] == 0
    assert summary["room_wall_degree_mean"] == 0.0
    assert summary["room_wall_degree_max"] == 0
    assert summary["isolated_wall_nodes"] == []
    assert summary["low_wall_adjacency_nodes"] == []
    assert summary["low_wall_adjacency_room_ratio"] == 0.0
    assert summary["interior_wall_adjacency_ratio"] == 0.0


def test_pool_summary_computes_counts_and_score_range():
    first = build_candidate_review_summary(
        "candidate_1",
        _known_graph(),
        candidate_report={"is_valid": True, "validation_errors": []},
        ranking_entry={"final_score": 10.0, "metrics": {}},
    )
    second = build_candidate_review_summary(
        "candidate_2",
        _known_graph(),
        candidate_report={"is_valid": False, "validation_errors": ["Graph is not connected."]},
        ranking_entry={"final_score": 20.0, "metrics": {}},
    )

    pool = build_candidate_pool_summary([first, second])

    assert pool["num_candidates"] == 2
    assert pool["num_valid"] == 1
    assert pool["score_min"] == 10.0
    assert pool["score_median"] == 15.0
    assert pool["score_max"] == 20.0
    assert pool["node_count_range"] == {"min": 5, "max": 5}
    assert pool["common_failure_modes"] == {"Graph is not connected.": 1}


def test_export_review_summary_json_creates_file(tmp_path):
    data = {"pool_summary": {"num_candidates": 0}, "candidate_summaries": []}

    output_path = export_review_summary_json(data, tmp_path / "review_summary.json")

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8")) == data
