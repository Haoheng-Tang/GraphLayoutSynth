"""Compact candidate review summaries for human and RAG-style inspection."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import networkx as nx


ROOM_LIKE_EXCLUDED_TYPES = {"BuildingFloor", "Corridor", "Zone"}
DEFAULT_TYPED_ACCESSIBILITY_PAIRS = (("PatientRoom", "ClinicalSupport"),)


def _sorted_counts(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _histogram(values: list[int]) -> dict[str, int]:
    return {str(value): count for value, count in sorted(Counter(values).items())}


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _range(values: list[int]) -> dict[str, int | None]:
    return {
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _room_like_nodes(graph: nx.Graph) -> list[str]:
    """Return concrete room-like nodes for review metrics.

    This intentionally excludes abstract nodes and non-room scaffolding types.
    Without geometry, these are only graph-layout proxies.
    """
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if not attrs.get("is_abstract", False)
        and attrs.get("type") not in ROOM_LIKE_EXCLUDED_TYPES
    ]


def degree_summary(graph: nx.Graph) -> dict[str, Any]:
    """Return compact graph degree statistics."""
    degrees = [degree for _, degree in graph.degree()]
    return {
        "degree_min": min(degrees) if degrees else 0,
        "degree_mean": _mean(degrees),
        "degree_max": max(degrees) if degrees else 0,
    }


def wall_adjacency_summary(graph: nx.Graph) -> dict[str, Any]:
    """Summarize wall-edge degree over concrete room-like nodes."""
    rooms = _room_like_nodes(graph)
    room_wall_degrees = []
    for node in rooms:
        wall_degree = sum(
            1
            for neighbor in graph.neighbors(node)
            if graph.edges[node, neighbor].get("edge_type") == "wall"
        )
        room_wall_degrees.append(
            {
                "node_id": str(node),
                "node_type": graph.nodes[node].get("type", "unknown"),
                "wall_degree": wall_degree,
                **({"zone": graph.nodes[node]["zone"]} if "zone" in graph.nodes[node] else {}),
            }
        )

    counted_room_count = len(rooms)
    wall_degrees = [entry["wall_degree"] for entry in room_wall_degrees]
    isolated_wall_nodes = [entry for entry in room_wall_degrees if entry["wall_degree"] == 0]
    low_wall_adjacency_nodes = [entry for entry in room_wall_degrees if entry["wall_degree"] < 2]
    interior_count = sum(1 for value in wall_degrees if value >= 2)
    return {
        "counted_room_count": counted_room_count,
        "room_wall_degree_min": min(wall_degrees) if wall_degrees else 0,
        "room_wall_degree_mean": _mean(wall_degrees),
        "room_wall_degree_max": max(wall_degrees) if wall_degrees else 0,
        "room_wall_degree_histogram": _histogram(wall_degrees),
        "isolated_wall_room_count": len(isolated_wall_nodes),
        "isolated_wall_nodes": isolated_wall_nodes,
        "low_wall_adjacency_room_count": len(low_wall_adjacency_nodes),
        "low_wall_adjacency_nodes": low_wall_adjacency_nodes,
        "low_wall_adjacency_room_ratio": round(len(low_wall_adjacency_nodes) / counted_room_count, 4) if counted_room_count else 0.0,
        "interior_wall_adjacency_room_count": interior_count,
        "interior_wall_adjacency_ratio": round(interior_count / counted_room_count, 4) if counted_room_count else 0.0,
    }


def support_type_summary(graph: nx.Graph) -> dict[str, Any]:
    """Return support-room counts by concrete node type, without combining them."""
    room_like_nodes = _room_like_nodes(graph)
    support_type_counts = Counter(
        graph.nodes[node].get("type", "unknown")
        for node in room_like_nodes
        if "support" in str(graph.nodes[node].get("type", "")).lower()
    )
    counted_room_count = len(room_like_nodes)
    return {
        "support_type_counts": _sorted_counts(support_type_counts),
        "support_type_ratios": {
            node_type: round(count / counted_room_count, 4) if counted_room_count else 0.0
            for node_type, count in sorted(support_type_counts.items())
        },
    }


def _nodes_of_type(graph: nx.Graph, node_type: str) -> list[str]:
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs.get("type") == node_type
        and not attrs.get("is_abstract", False)
    ]


def _travel_graph(graph: nx.Graph, edge_type: str | None) -> nx.Graph:
    if edge_type is None:
        return graph
    travel_graph = nx.Graph()
    travel_graph.add_nodes_from(graph.nodes(data=True))
    travel_graph.add_edges_from(
        (left, right, attrs)
        for left, right, attrs in graph.edges(data=True)
        if attrs.get("edge_type") == edge_type
    )
    return travel_graph


def _empty_accessibility_pair_summary(source_type: str, target_type: str, source_count: int, target_count: int) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "target_type": target_type,
        "source_count": source_count,
        "target_count": target_count,
        "reachable_count": 0,
        "unreachable_count": source_count,
        "distance_min": None,
        "distance_mean": None,
        "distance_median": None,
        "distance_max": None,
        "distance_histogram": {},
        "far_source_nodes": [],
    }


def typed_accessibility_summary(
    graph: nx.Graph,
    type_pairs: list[tuple[str, str]] | None = None,
    edge_type: str | None = "door",
) -> dict[str, Any]:
    """Summarize nearest-target travel distances between typed node pairs."""
    type_pairs = type_pairs or list(DEFAULT_TYPED_ACCESSIBILITY_PAIRS)
    travel_graph = _travel_graph(graph, edge_type)
    pair_summaries = []

    for source_type, target_type in type_pairs:
        source_nodes = _nodes_of_type(graph, source_type)
        target_nodes = _nodes_of_type(graph, target_type)
        if not source_nodes or not target_nodes:
            pair_summaries.append(
                _empty_accessibility_pair_summary(
                    source_type,
                    target_type,
                    len(source_nodes),
                    len(target_nodes),
                )
            )
            continue

        reachable = []
        unreachable_count = 0
        for source in source_nodes:
            distances = []
            for target in target_nodes:
                try:
                    distances.append((nx.shortest_path_length(travel_graph, source, target), target))
                except nx.NetworkXNoPath:
                    continue
            if not distances:
                unreachable_count += 1
                continue
            distance, nearest_target = min(distances, key=lambda item: (item[0], str(item[1])))
            reachable.append(
                {
                    "node_id": str(source),
                    "node_type": source_type,
                    "nearest_target_id": str(nearest_target),
                    "distance": distance,
                }
            )

        distance_values = [entry["distance"] for entry in reachable]
        max_distance = max(distance_values) if distance_values else None
        far_source_nodes = [
            entry
            for entry in reachable
            if entry["distance"] == max_distance
        ]
        far_source_nodes.sort(key=lambda item: (str(item["node_id"]), str(item["nearest_target_id"])))
        pair_summaries.append(
            {
                "source_type": source_type,
                "target_type": target_type,
                "source_count": len(source_nodes),
                "target_count": len(target_nodes),
                "reachable_count": len(reachable),
                "unreachable_count": unreachable_count,
                "distance_min": min(distance_values) if distance_values else None,
                "distance_mean": _mean(distance_values) if distance_values else None,
                "distance_median": median(distance_values) if distance_values else None,
                "distance_max": max_distance,
                "distance_histogram": _histogram(distance_values),
                "far_source_nodes": far_source_nodes,
            }
        )

    return {
        "edge_type": edge_type,
        "pairs": pair_summaries,
    }


def _validity_status(candidate_report: dict | None, ranking_entry: dict | None) -> dict[str, Any]:
    if candidate_report is not None:
        return {
            "is_valid": bool(candidate_report.get("is_valid")),
            "validation_errors": candidate_report.get("validation_errors", []),
        }
    metrics = (ranking_entry or {}).get("metrics", {})
    if "validation_passed" in metrics:
        return {
            "is_valid": bool(metrics["validation_passed"]),
            "validation_errors": [],
        }
    return {"is_valid": None, "validation_errors": []}


def _first_available(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _trace_metadata(candidate_report: dict | None, ranking_entry: dict | None) -> dict[str, Any]:
    source = {}
    if ranking_entry is not None:
        source.update(ranking_entry)
    if candidate_report is not None:
        source.update(candidate_report)
    keys = {"trace_path", "trace_length", "applied_rule_names", "applied_rule_counts"}
    return {key: source[key] for key in keys if key in source}


def build_candidate_review_summary(
    candidate_id: str,
    graph: nx.Graph,
    candidate_report: dict | None = None,
    ranking_entry: dict | None = None,
    artifact_paths: dict | None = None,
) -> dict[str, Any]:
    """Build a compact JSON-serializable summary for one candidate."""
    artifact_paths = artifact_paths or {}
    metrics = (ranking_entry or {}).get("metrics") or (candidate_report or {}).get("metrics", {})
    node_type_counts = _sorted_counts(
        Counter(attrs.get("type", "unknown") for _, attrs in graph.nodes(data=True))
    )
    edge_type_counts = _sorted_counts(
        Counter(attrs.get("edge_type", "unknown") for _, _, attrs in graph.edges(data=True))
    )
    graph_degree_summary = degree_summary(graph)
    wall_summary = wall_adjacency_summary(graph)
    support_summary = support_type_summary(graph)
    accessibility_summary = typed_accessibility_summary(graph)

    return {
        "candidate_id": candidate_id,
        "validity_status": _validity_status(candidate_report, ranking_entry),
        "final_score": _first_available(
            (ranking_entry or {}).get("final_score"),
            (candidate_report or {}).get("final_score"),
            (candidate_report or {}).get("ranking_score"),
        ),
        "score_breakdown": _first_available(
            (ranking_entry or {}).get("score_breakdown"),
            (candidate_report or {}).get("score_breakdown"),
        ),
        "key_metrics": {
            key: metrics[key]
            for key in (
                "corridor_access_ratio",
                "dead_end_count",
                "edge_node_ratio",
                "room_corridor_ratio",
                "door_wall_ratio",
                "corridor_fraction",
                "abstract_node_count",
                "invalid_edge_type_count",
            )
            if key in metrics
        },
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "node_type_counts": node_type_counts,
        "edge_type_counts": edge_type_counts,
        "degree_summary": graph_degree_summary,
        "degree_histogram": _histogram([degree for _, degree in graph.degree()]),
        "dead_end_count": metrics.get("dead_end_count", sum(1 for _, degree in graph.degree() if degree <= 1)),
        "corridor_access_ratio": metrics.get("corridor_access_ratio"),
        "support_type_counts": support_summary["support_type_counts"],
        "support_type_ratios": support_summary["support_type_ratios"],
        "wall_adjacency_summary": wall_summary,
        "typed_accessibility_summary": accessibility_summary,
        "trace_metadata": _trace_metadata(candidate_report, ranking_entry),
        "artifact_paths": {
            "graph_path": artifact_paths.get("graph_path"),
            "report_path": artifact_paths.get("report_path"),
            "trace_path": artifact_paths.get("trace_path"),
            "image_path": artifact_paths.get("image_path"),
            "review_summary_path": artifact_paths.get("review_summary_path"),
        },
    }


def build_candidate_pool_summary(candidate_summaries: list[dict]) -> dict[str, Any]:
    """Build a lightweight pool-level summary from candidate summaries."""
    scores = [
        summary["final_score"]
        for summary in candidate_summaries
        if summary.get("final_score") is not None
    ]
    node_counts = [summary["node_count"] for summary in candidate_summaries]
    edge_counts = [summary["edge_count"] for summary in candidate_summaries]
    node_type_counts = Counter()
    edge_type_counts = Counter()
    failure_modes = Counter()
    low_wall_ratios = []
    interior_wall_ratios = []
    isolated_wall_counts = []

    for summary in candidate_summaries:
        node_type_counts.update(summary.get("node_type_counts", {}))
        edge_type_counts.update(summary.get("edge_type_counts", {}))
        for error in summary.get("validity_status", {}).get("validation_errors", []):
            failure_modes[str(error)] += 1
        wall_summary = summary.get("wall_adjacency_summary", {})
        low_wall_ratios.append(wall_summary.get("low_wall_adjacency_room_ratio", 0.0))
        interior_wall_ratios.append(wall_summary.get("interior_wall_adjacency_ratio", 0.0))
        isolated_wall_counts.append(wall_summary.get("isolated_wall_room_count", 0))

    return {
        "num_candidates": len(candidate_summaries),
        "num_valid": sum(
            1
            for summary in candidate_summaries
            if summary.get("validity_status", {}).get("is_valid") is True
        ),
        "score_min": min(scores) if scores else None,
        "score_median": median(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "node_count_range": _range(node_counts),
        "edge_count_range": _range(edge_counts),
        "common_node_types": dict(node_type_counts.most_common()),
        "common_edge_types": dict(edge_type_counts.most_common()),
        "common_failure_modes": dict(failure_modes.most_common()),
        "wall_adjacency_pool_summary": {
            "low_wall_adjacency_room_ratio_min": min(low_wall_ratios) if low_wall_ratios else 0.0,
            "low_wall_adjacency_room_ratio_median": median(low_wall_ratios) if low_wall_ratios else 0.0,
            "low_wall_adjacency_room_ratio_max": max(low_wall_ratios) if low_wall_ratios else 0.0,
            "interior_wall_adjacency_ratio_min": min(interior_wall_ratios) if interior_wall_ratios else 0.0,
            "interior_wall_adjacency_ratio_median": median(interior_wall_ratios) if interior_wall_ratios else 0.0,
            "interior_wall_adjacency_ratio_max": max(interior_wall_ratios) if interior_wall_ratios else 0.0,
            "isolated_wall_room_count_min": min(isolated_wall_counts) if isolated_wall_counts else 0,
            "isolated_wall_room_count_median": median(isolated_wall_counts) if isolated_wall_counts else 0,
            "isolated_wall_room_count_max": max(isolated_wall_counts) if isolated_wall_counts else 0,
        },
    }


def export_review_summary_json(data: dict, output_path: str | Path) -> Path:
    """Write review summary data to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
