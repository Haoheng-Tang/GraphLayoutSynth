"""JSON export utilities for generated graphs."""

from __future__ import annotations

import json
from collections import Counter
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


def graph_report_data(
    graph: nx.Graph,
    score: float,
    is_valid: bool,
    validation_errors: list[str],
) -> dict:
    """Build a compact JSON-serializable report for a generated graph."""
    type_counts = Counter(
        attrs.get("type", "unknown")
        for _, attrs in graph.nodes(data=True)
    )
    edge_type_counts = Counter(
        attrs.get("edge_type", "unknown")
        for _, _, attrs in graph.edges(data=True)
    )
    return {
        "is_valid": is_valid,
        "validation_errors": validation_errors,
        "score": score,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "type_counts": dict(sorted(type_counts.items())),
        "edge_type_counts": dict(sorted(edge_type_counts.items())),
    }


def export_report_json(
    graph: nx.Graph,
    output_path: str | Path,
    score: float,
    is_valid: bool,
    validation_errors: list[str],
) -> Path:
    """Write validation, score, and count metadata to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = graph_report_data(graph, score, is_valid, validation_errors)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
