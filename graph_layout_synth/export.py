"""JSON export utilities for generated graphs."""

from __future__ import annotations

import json
from inspect import signature
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph


def graph_to_node_link_data(graph: nx.Graph) -> dict:
    """Convert a graph to a simple NetworkX node-link dictionary."""
    parameters = signature(json_graph.node_link_data).parameters
    if "edges" in parameters:
        return json_graph.node_link_data(graph, edges="links")
    return json_graph.node_link_data(graph, link="links")


def export_graph_json(graph: nx.Graph, output_path: str | Path) -> Path:
    """Write a graph to JSON and return the output path."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = graph_to_node_link_data(graph)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
