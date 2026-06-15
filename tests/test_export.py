import json

import networkx as nx

from graph_layout_synth.export import export_graph_json, export_ranking_report_json


def test_json_export_creates_file(tmp_path):
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")

    output_path = export_graph_json(graph, tmp_path / "graph.json")

    assert output_path.exists()
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert "nodes" in data
    assert "links" in data


def test_ranking_report_contains_refined_score_fields(tmp_path):
    ranked_candidates = [
        {
            "rank": 1,
            "candidate_id": "candidate_1",
            "final_score": 141.0,
            "ranking_score": 141.0,
            "score_breakdown": {"validation": 100.0, "connectivity": 20.0, "edge_density": 21.0},
            "metrics": {"validation_passed": 1, "corridor_access_ratio": 1.0},
            "tie_break_keys": {"candidate_id_asc": "candidate_1"},
        }
    ]

    output_path = export_ranking_report_json(ranked_candidates, tmp_path / "ranking_report.json")
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert data[0]["final_score"] == 141.0
    assert data[0]["score_breakdown"]["validation"] == 100.0
    assert data[0]["metrics"]["validation_passed"] == 1
    assert data[0]["tie_break_keys"]["candidate_id_asc"] == "candidate_1"
