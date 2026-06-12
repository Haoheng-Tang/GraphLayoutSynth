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


def _room_nodes(graph: nx.Graph) -> list[str]:
    return [
        node
        for node, attrs in graph.nodes(data=True)
        if not attrs.get("is_abstract", False)
        and attrs.get("type") not in {"BuildingFloor", "Corridor", "Zone"}
    ]


def compute_candidate_metrics(
    G: nx.Graph,
    validation_report: ValidationResult | None = None,
) -> CandidateMetrics:
    """Compute transparent ranking metrics for one candidate graph."""
    if validation_report is None:
        validation_report = validate_graph(G)

    rooms = _room_nodes(G)
    rooms_with_access = sum(1 for node in rooms if room_has_corridor_access(G, node))
    corridor_access_ratio = rooms_with_access / len(rooms) if rooms else 1.0
    edge_types = [attrs.get("edge_type") for _, _, attrs in G.edges(data=True)]

    return CandidateMetrics(
        node_count=G.number_of_nodes(),
        edge_count=G.number_of_edges(),
        room_count=len(rooms),
        corridor_count=sum(1 for _, attrs in G.nodes(data=True) if attrs.get("type") == "Corridor"),
        door_edge_count=sum(1 for edge_type in edge_types if edge_type == "door"),
        wall_edge_count=sum(1 for edge_type in edge_types if edge_type == "wall"),
        connected_graph=1 if is_connected(G) else 0,
        corridor_access_ratio=round(corridor_access_ratio, 4),
        abstract_node_count=len(abstract_nodes(G)),
        invalid_edge_type_count=len(invalid_edge_types(G)),
        validation_passed=1 if validation_report.is_valid else 0,
    )


def score_candidate(metrics: CandidateMetrics | dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Score candidate metrics using transparent additive weights."""
    metric_values = asdict(metrics) if isinstance(metrics, CandidateMetrics) else metrics
    weights = weights or DEFAULT_WEIGHTS
    score = 0.0
    score += weights.get("validation_passed", 0.0) * metric_values["validation_passed"]
    score += weights.get("connected_graph", 0.0) * metric_values["connected_graph"]
    score += weights.get("corridor_access_ratio", 0.0) * metric_values["corridor_access_ratio"]
    score += weights.get("abstract_node_count", 0.0) * metric_values["abstract_node_count"]
    score += weights.get("invalid_edge_type_count", 0.0) * metric_values["invalid_edge_type_count"]
    if not metric_values["connected_graph"]:
        score += weights.get("disconnected_graph", 0.0)
    return round(score, 4)


def _candidate_value(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def rank_candidates(
    candidates: list[Any],
    weights: dict[str, float] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates by deterministic metric score."""
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
        ranking_score = score_candidate(metrics, weights)
        ranked.append(
            {
                "candidate_id": candidate_id,
                "graph": graph,
                "validation_report": validation_report,
                "metrics": asdict(metrics),
                "ranking_score": ranking_score,
                "export_paths": _candidate_value(candidate, "export_paths", {}),
            }
        )

    ranked.sort(
        key=lambda item: (
            -item["ranking_score"],
            -item["metrics"]["validation_passed"],
            -item["metrics"]["corridor_access_ratio"],
            item["metrics"]["invalid_edge_type_count"],
            item["metrics"]["abstract_node_count"],
            item["candidate_id"],
        )
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked[:top_k] if top_k is not None else ranked
