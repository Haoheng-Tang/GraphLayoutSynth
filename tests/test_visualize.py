import networkx as nx

from graph_layout_synth.visualize import visualize_graph


def test_visualization_creates_png_file(tmp_path):
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")

    output_path = visualize_graph(graph, tmp_path / "graph.png", title="Test graph")

    assert output_path.exists()
    assert output_path.suffix == ".png"
    assert output_path.stat().st_size > 0
