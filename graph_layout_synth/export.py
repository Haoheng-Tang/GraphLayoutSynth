"""JSON export utilities for generated graphs."""

from __future__ import annotations

import json
import csv
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
    metrics: dict | None = None,
    ranking_score: float | None = None,
    final_score: float | None = None,
    score_breakdown: dict | None = None,
    trace_metadata: dict | None = None,
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
    report = {
        "is_valid": is_valid,
        "validation_errors": validation_errors,
        "score": score,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "type_counts": dict(sorted(type_counts.items())),
        "edge_type_counts": dict(sorted(edge_type_counts.items())),
    }
    if metrics is not None:
        report["metrics"] = metrics
    resolved_final_score = final_score if final_score is not None else ranking_score
    if resolved_final_score is not None:
        report["final_score"] = resolved_final_score
        report["ranking_score"] = resolved_final_score
    if score_breakdown is not None:
        report["score_breakdown"] = score_breakdown
    if trace_metadata is not None:
        report.update(trace_metadata)
    return report


def export_report_json(
    graph: nx.Graph,
    output_path: str | Path,
    score: float,
    is_valid: bool,
    validation_errors: list[str],
    metrics: dict | None = None,
    ranking_score: float | None = None,
    final_score: float | None = None,
    score_breakdown: dict | None = None,
    trace_metadata: dict | None = None,
) -> Path:
    """Write validation, score, and count metadata to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = graph_report_data(
        graph,
        score,
        is_valid,
        validation_errors,
        metrics,
        ranking_score,
        final_score,
        score_breakdown,
        trace_metadata,
    )
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def export_ranking_report_json(ranked_candidates: list[dict], output_path: str | Path) -> Path:
    """Write a ranking report without embedding graph objects."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_ranking_report_json_row(candidate) for candidate in ranked_candidates]
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path


def export_ranking_report_csv(ranked_candidates: list[dict], output_path: str | Path) -> Path:
    """Write a compact CSV ranking report."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_ranking_report_csv_row(candidate) for candidate in ranked_candidates]
    fieldnames = list(rows[0].keys()) if rows else ["rank", "candidate_id", "final_score"]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _ranking_report_json_row(candidate: dict) -> dict:
    row = {
        "rank": candidate["rank"],
        "candidate_id": candidate["candidate_id"],
        "final_score": candidate["final_score"],
        "ranking_score": candidate["final_score"],
        "score_breakdown": candidate["score_breakdown"],
        "metrics": candidate["metrics"],
        "tie_break_keys": candidate["tie_break_keys"],
    }
    row.update(candidate.get("trace_metadata", {}))
    review_summary_path = candidate.get("export_paths", {}).get("review_summary_path")
    if review_summary_path is not None:
        row["review_summary_path"] = review_summary_path
    return row


def _ranking_report_csv_row(candidate: dict) -> dict:
    metrics = candidate["metrics"]
    tie_break_keys = candidate["tie_break_keys"]
    trace_metadata = candidate.get("trace_metadata", {})
    export_paths = candidate.get("export_paths", {})
    return {
        "rank": candidate["rank"],
        "candidate_id": candidate["candidate_id"],
        "final_score": candidate["final_score"],
        "ranking_score": candidate["final_score"],
        "validation_passed": metrics["validation_passed"],
        "node_count": metrics["node_count"],
        "edge_count": metrics["edge_count"],
        "room_count": metrics["room_count"],
        "corridor_count": metrics["corridor_count"],
        "door_edge_count": metrics["door_edge_count"],
        "wall_edge_count": metrics["wall_edge_count"],
        "connected_graph": metrics["connected_graph"],
        "corridor_access_ratio": metrics["corridor_access_ratio"],
        "edge_node_ratio": metrics["edge_node_ratio"],
        "room_corridor_ratio": metrics["room_corridor_ratio"],
        "door_wall_ratio": metrics["door_wall_ratio"],
        "corridor_fraction": metrics["corridor_fraction"],
        "dead_end_count": metrics["dead_end_count"],
        "average_room_to_corridor_distance": metrics["average_room_to_corridor_distance"],
        "max_room_to_corridor_distance": metrics["max_room_to_corridor_distance"],
        "support_room_count": metrics["support_room_count"],
        "support_room_ratio": metrics["support_room_ratio"],
        "abstract_node_count": metrics["abstract_node_count"],
        "invalid_edge_type_count": metrics["invalid_edge_type_count"],
        "tie_validation_passed_desc": tie_break_keys["validation_passed_desc"],
        "tie_corridor_access_ratio_desc": tie_break_keys["corridor_access_ratio_desc"],
        "tie_invalid_edge_type_count_asc": tie_break_keys["invalid_edge_type_count_asc"],
        "tie_abstract_node_count_asc": tie_break_keys["abstract_node_count_asc"],
        "tie_dead_end_count_asc": tie_break_keys["dead_end_count_asc"],
        "tie_edge_node_ratio_desc": tie_break_keys["edge_node_ratio_desc"],
        "tie_candidate_id_asc": tie_break_keys["candidate_id_asc"],
        "trace_path": trace_metadata.get("trace_path", ""),
        "trace_length": trace_metadata.get("trace_length", 0),
        "applied_rule_names": ";".join(trace_metadata.get("applied_rule_names", [])),
        "applied_rule_counts": json.dumps(trace_metadata.get("applied_rule_counts", {}), sort_keys=True),
        "review_summary_path": export_paths.get("review_summary_path", ""),
    }
