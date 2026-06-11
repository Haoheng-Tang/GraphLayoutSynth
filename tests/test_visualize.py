import networkx as nx

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config
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


def test_config_room_types_have_defined_colors():
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.visualization.node_colors["PatientRoom"]
    assert config.visualization.node_colors["ClinicalSupport"]
    assert config.visualization.node_colors["StaffSupport"]


def test_visualization_uses_config_colors_for_config_room_types(tmp_path):
    config = load_config(DEFAULT_CONFIG_PATH)
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("patient", type="PatientRoom", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "patient", edge_type="door")

    output_path = visualize_graph(graph, tmp_path / "configured.png", config=config)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
