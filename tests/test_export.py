import json

import networkx as nx

from graph_layout_synth.export import export_graph_json


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
