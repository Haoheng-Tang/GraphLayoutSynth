import json

import networkx as nx

from graph_layout_synth.export import export_report_json


def test_report_export_creates_json_file(tmp_path):
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")

    output_path = export_report_json(
        graph,
        tmp_path / "report.json",
        score=105.0,
        is_valid=True,
        validation_errors=[],
        metrics={"validation_passed": 1},
        final_score=130.0,
        score_breakdown={"validation": 100.0, "connectivity": 30.0},
    )

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert output_path.exists()
    assert data["score"] == 105.0
    assert data["final_score"] == 130.0
    assert data["ranking_score"] == 130.0
    assert data["score_breakdown"]["validation"] == 100.0
    assert data["metrics"]["validation_passed"] == 1
    assert data["node_count"] == 2
    assert data["edge_type_counts"] == {"door": 1}
