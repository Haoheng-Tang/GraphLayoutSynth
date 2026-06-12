import networkx as nx

from graph_layout_synth.validators import validate_graph


def test_validators_accept_expected_valid_graph():
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")

    result = validate_graph(graph)

    assert result.is_valid
    assert result.errors == []


def test_validators_reject_room_without_corridor_access():
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="wall")

    result = validate_graph(graph)

    assert not result.is_valid
    assert any("corridor" in error for error in result.errors)


def test_validators_reject_invalid_edge_type_and_abstract_node():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", zone="zone_1", is_abstract=True)
    graph.add_node("room", type="Room", zone="zone_1", is_abstract=False)
    graph.add_edge("zone", "room", edge_type="stairs")

    result = validate_graph(graph)

    assert not result.is_valid
    assert any("Invalid edge types" in error for error in result.errors)
    assert any("Abstract nodes remain" in error for error in result.errors)
