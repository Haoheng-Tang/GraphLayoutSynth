import networkx as nx

from graph_layout_synth.generator import generate_candidate
from graph_layout_synth.validators import abstract_nodes


def test_generation_returns_networkx_graph():
    result = generate_candidate(seed=123)

    assert isinstance(result.graph, nx.Graph)


def test_generated_complete_graph_has_no_abstract_nodes():
    result = generate_candidate(seed=123)

    assert abstract_nodes(result.graph) == []
