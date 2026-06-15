"""Deterministic metric-based ranking for generated graph candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import networkx as nx

from graph_layout_synth.validators import (
    ValidationResult,
    abstract_nodes,
    invalid_edge_types,
    is_connected,
    room_has_corridor_access,
    validate_graph,
)


DEFAULT_WEIGHTS = {
    "validation_passed": 100.0,
    "connected_graph": 20.0,
    "corridor_access_ratio": 30.0,
    "abstract_node_count": -20.0,
    "invalid_edge_type_count": -15.0,
    "disconnected_graph": -20.0,
    "edge_density": 10.0,
    "corridor_efficiency": 10.0,
    "door_wall_balance": 5.0,
    "distance_efficiency": 6.0,
    "dead_end_count": -2.0,
    "support_mix": 5.0,
}

DEFAULT_TARGETS = {
    "edge_node_ratio": 1.25,
    "corridor_fraction": 0.25,
    "door_wall_ratio": 4.0,
    "room_to_corridor_distance": 1.0,
    "max_room_to_corridor_distance_penalty_window": 4.0,
    "support_room_ratio": 0.25,
}


@dataclass(frozen=True)
class CandidateMetrics:
    node_count: int
    edge_count: int
    room_count: int
    corridor_count: int
    door_edge_count: int
    wall_edge_count: int
    connected_graph: int
    corridor_access_ratio: float
    abstract_node_count: int
    invalid_edge_type_count: int
    validation_passed: int
    edge_node_ratio: float
    room_corridor_ratio: float
    door_wall_ratio: float
    corridor_fraction: float
    dead_end_count: int
    average_room_to_corridor_distance: float
    max_room_to_corridor_distance: float
    support_room_count: int
    support_room_ratio: float


def _room_nodes(graph: nx.Graph) -> list[str]:
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if not attrs.get("is_abstract", False)
        and attrs.get("type") not in {"BuildingFloor", "Corridor", "Zone"}
    ]


def _corridor_nodes(graph: nx.Graph) -> list[str]:
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs.get("type") == "Corridor"
    ]


def _support_room_count(graph: nx.Graph, rooms: list[str]) -> int:
    support_types = {"SupportRoom", "ServiceRoom", "ClinicalSupport", "StaffSupport"}
    return sum(
        1
        for node in rooms
        if graph.nodes[node].get("type") in support_types
        or "support" in str(graph.nodes[node].get("type", "")).lower()
    )


def _room_to_corridor_distances(graph: nx.Graph, rooms: list[str], corridors: list[str]) -> list[int]:
    if not rooms:
        return []
    if not corridors:
        return [graph.number_of_nodes() + 1 for _ in rooms]

    distances = []
    for room in rooms:
        room_distances = []
        for corridor in corridors:
            try:
                room_distances.append(nx.shortest_path_length(graph, room, corridor))
            except nx.NetworkXNoPath:
                continue
        distances.append(min(room_distances) if room_distances else graph.number_of_nodes() + 1)
    return distances


def compute_candidate_metrics(
    G: nx.Graph,
    validation_report: ValidationResult | None = None,
) -> CandidateMetrics:
    """Compute transparent ranking metrics for one candidate graph."""
    if validation_report is None:
        validation_report = validate_graph(G)

    rooms = _room_nodes(G)
    corridors = _corridor_nodes(G)
    rooms_with_access = sum(1 for node in rooms if room_has_corridor_access(G, node))
    corridor_access_ratio = rooms_with_access / len(rooms) if rooms else 1.0
    edge_types = [attrs.get("edge_type") for _, _, attrs in G.edges(data=True)]
    node_count = G.number_of_nodes()
    edge_count = G.number_of_edges()
    corridor_count = len(corridors)
    door_edge_count = sum(1 for edge_type in edge_types if edge_type == "door")
    wall_edge_count = sum(1 for edge_type in edge_types if edge_type == "wall")
    distances = _room_to_corridor_distances(G, rooms, corridors)
    support_room_count = _support_room_count(G, rooms)

    return CandidateMetrics(
        node_count=node_count,
        edge_count=edge_count,
        room_count=len(rooms),
        corridor_count=corridor_count,
        door_edge_count=door_edge_count,
        wall_edge_count=wall_edge_count,
        connected_graph=1 if is_connected(G) else 0,
        corridor_access_ratio=round(corridor_access_ratio, 4),
        abstract_node_count=len(abstract_nodes(G)),
        invalid_edge_type_count=len(invalid_edge_types(G)),
        validation_passed=1 if validation_report.is_valid else 0,
        edge_node_ratio=round(edge_count / node_count, 4) if node_count else 0.0,
        room_corridor_ratio=round(len(rooms) / corridor_count, 4) if corridor_count else 0.0,
        door_wall_ratio=round(door_edge_count / wall_edge_count, 4) if wall_edge_count else float(door_edge_count),
        corridor_fraction=round(corridor_count / node_count, 4) if node_count else 0.0,
        dead_end_count=sum(1 for node in rooms if G.degree[node] <= 1),
        average_room_to_corridor_distance=round(sum(distances) / len(distances), 4) if distances else 0.0,
        max_room_to_corridor_distance=max(distances) if distances else 0.0,
        support_room_count=support_room_count,
        support_room_ratio=round(support_room_count / len(rooms), 4) if rooms else 0.0,
    )


def score_candidate_breakdown(
    metrics: CandidateMetrics | dict[str, Any],
    weights: dict[str, float] | None = None,
    targets: dict[str, float] | None = None,
) -> dict[str, float]:
    """Return transparent additive score components for candidate metrics."""
    metric_values = asdict(metrics) if isinstance(metrics, CandidateMetrics) else metrics
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    targets = {**DEFAULT_TARGETS, **(targets or {})}

    edge_target = targets["edge_node_ratio"]
    corridor_target = targets["corridor_fraction"]
    door_wall_target = targets["door_wall_ratio"]
    distance_target = targets["room_to_corridor_distance"]
    distance_window = targets["max_room_to_corridor_distance_penalty_window"]
    support_target = targets["support_room_ratio"]

    edge_density_fit = max(0.0, 1.0 - abs(metric_values["edge_node_ratio"] - edge_target) / edge_target) if edge_target else 0.0
    corridor_fit = max(0.0, 1.0 - abs(metric_values["corridor_fraction"] - corridor_target) / corridor_target) if corridor_target else 0.0
    door_wall_fit = min(metric_values["door_wall_ratio"], door_wall_target) / door_wall_target if metric_values["door_wall_ratio"] and door_wall_target else 0.0
    avg_distance_penalty = max(0.0, metric_values["average_room_to_corridor_distance"] - distance_target)
    max_distance_penalty = max(0.0, metric_values["max_room_to_corridor_distance"] - distance_target)
    distance_fit = max(0.0, 1.0 - ((avg_distance_penalty + max_distance_penalty) / distance_window)) if distance_window else 0.0
    support_ratio = metric_values["support_room_ratio"]
    support_fit = max(0.0, 1.0 - abs(support_ratio - support_target) / support_target) if support_ratio and support_target else 0.0

    connectivity = weights.get("connected_graph", 0.0) if metric_values["connected_graph"] else weights.get("disconnected_graph", 0.0)
    breakdown = {
        "validation": weights.get("validation_passed", 0.0) * metric_values["validation_passed"],
        "connectivity": connectivity,
        "corridor_access": weights.get("corridor_access_ratio", 0.0) * metric_values["corridor_access_ratio"],
        "edge_density": weights.get("edge_density", 0.0) * edge_density_fit,
        "corridor_efficiency": weights.get("corridor_efficiency", 0.0) * corridor_fit,
        "door_wall_balance": weights.get("door_wall_balance", 0.0) * door_wall_fit,
        "distance_efficiency": weights.get("distance_efficiency", 0.0) * distance_fit,
        "support_mix": weights.get("support_mix", 0.0) * support_fit,
        "dead_end_penalty": weights.get("dead_end_count", 0.0) * metric_values["dead_end_count"],
        "invalid_edge_penalty": weights.get("invalid_edge_type_count", 0.0) * metric_values["invalid_edge_type_count"],
        "abstract_node_penalty": weights.get("abstract_node_count", 0.0) * metric_values["abstract_node_count"],
    }
    return {key: round(value, 4) for key, value in breakdown.items()}


def score_candidate(
    metrics: CandidateMetrics | dict[str, Any],
    weights: dict[str, float] | None = None,
    targets: dict[str, float] | None = None,
) -> float:
    """Score candidate metrics using transparent additive weights."""
    breakdown = score_candidate_breakdown(metrics, weights, targets)
    return round(sum(breakdown.values()), 4)


def tie_break_keys(metrics: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """Return deterministic secondary sort fields exposed in reports."""
    return {
        "validation_passed_desc": metrics["validation_passed"],
        "corridor_access_ratio_desc": metrics["corridor_access_ratio"],
        "invalid_edge_type_count_asc": metrics["invalid_edge_type_count"],
        "abstract_node_count_asc": metrics["abstract_node_count"],
        "dead_end_count_asc": metrics["dead_end_count"],
        "edge_node_ratio_desc": metrics["edge_node_ratio"],
        "candidate_id_asc": candidate_id,
    }


def _candidate_value(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _split_ranking_settings(
    weights: Any,
    targets: dict[str, float] | None,
) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    if hasattr(weights, "weights") and hasattr(weights, "targets"):
        return weights.weights, targets or weights.targets
    return weights, targets


def rank_candidates(
    candidates: list[Any],
    weights: dict[str, float] | None = None,
    targets: dict[str, float] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates by deterministic metric score."""
    weights, targets = _split_ranking_settings(weights, targets)
    ranked = []
    for index, candidate in enumerate(candidates, start=1):
        graph = _candidate_value(candidate, "graph")
        if graph is None:
            raise ValueError("Each candidate must include a graph.")
        candidate_id = _candidate_value(candidate, "candidate_id", f"candidate_{index}")
        validation_report = _candidate_value(candidate, "validation_report")
        if validation_report is None and hasattr(candidate, "is_valid"):
            validation_report = ValidationResult(
                is_valid=candidate.is_valid,
                errors=candidate.validation_errors,
            )
        metrics = compute_candidate_metrics(graph, validation_report)
        metrics_dict = asdict(metrics)
        ranking_settings = _candidate_value(candidate, "ranking_settings")
        candidate_weights = weights
        candidate_targets = targets
        if ranking_settings is not None:
            candidate_weights = candidate_weights or getattr(ranking_settings, "weights", None)
            candidate_targets = candidate_targets or getattr(ranking_settings, "targets", None)
        score_breakdown = score_candidate_breakdown(metrics_dict, candidate_weights, candidate_targets)
        final_score = round(sum(score_breakdown.values()), 4)
        tie_keys = tie_break_keys(metrics_dict, candidate_id)
        ranked.append(
            {
                "candidate_id": candidate_id,
                "graph": graph,
                "validation_report": validation_report,
                "metrics": metrics_dict,
                "score_breakdown": score_breakdown,
                "final_score": final_score,
                "ranking_score": final_score,
                "tie_break_keys": tie_keys,
                "export_paths": _candidate_value(candidate, "export_paths", {}),
            }
        )

    ranked.sort(
        key=lambda item: (
            -item["final_score"],
            -item["tie_break_keys"]["validation_passed_desc"],
            -item["tie_break_keys"]["corridor_access_ratio_desc"],
            item["tie_break_keys"]["invalid_edge_type_count_asc"],
            item["tie_break_keys"]["abstract_node_count_asc"],
            item["tie_break_keys"]["dead_end_count_asc"],
            -item["tie_break_keys"]["edge_node_ratio_desc"],
            item["tie_break_keys"]["candidate_id_asc"],
        )
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked[:top_k] if top_k is not None else ranked
