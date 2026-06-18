"""Diversity and novelty metrics over candidate review summaries."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any


ARCHIVE_VERSION = 1
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.05
DEFAULT_LOW_NOVELTY_THRESHOLD = 0.10
DEFAULT_ACCESS_SOURCE_TYPE = "PatientRoom"
DEFAULT_ACCESS_TARGET_TYPE = "ClinicalSupport"


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _nested_value(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for key in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _add_numeric_feature(features: dict[str, float], key: str, value: Any) -> None:
    features[key] = _number(value)


def _add_flat_count_features(features: dict[str, float], prefix: str, values: Any) -> None:
    if not isinstance(values, dict):
        return
    for key, value in sorted(values.items()):
        features[f"{prefix}.{key}"] = _number(value)


def _typed_accessibility_pair(summary: dict[str, Any], source_type: str, target_type: str) -> dict[str, Any] | None:
    access_summary = summary.get("typed_accessibility_summary", {})
    for pair in access_summary.get("pairs", []):
        if pair.get("source_type") == source_type and pair.get("target_type") == target_type:
            return pair
    return None


def extract_diversity_feature_vector(candidate_summary: dict) -> dict[str, float]:
    """Extract a numeric feature vector from a candidate review summary."""
    features: dict[str, float] = {}
    for key in (
        "node_count",
        "edge_count",
        "final_score",
        "dead_end_count",
        "corridor_access_ratio",
    ):
        _add_numeric_feature(features, key, candidate_summary.get(key))

    for source_key, feature_key in (
        ("key_metrics.corridor_access_ratio", "key_metrics.corridor_access_ratio"),
        ("key_metrics.dead_end_count", "key_metrics.dead_end_count"),
        ("key_metrics.support_room_count", "key_metrics.support_room_count"),
        ("key_metrics.support_room_ratio", "key_metrics.support_room_ratio"),
        ("key_metrics.abstract_node_count", "key_metrics.abstract_node_count"),
        ("key_metrics.invalid_edge_type_count", "key_metrics.invalid_edge_type_count"),
        ("key_metrics.edge_node_ratio", "edge_node_ratio"),
        ("key_metrics.room_corridor_ratio", "room_corridor_ratio"),
        ("key_metrics.door_wall_ratio", "door_wall_ratio"),
        ("key_metrics.corridor_fraction", "corridor_fraction"),
        ("wall_adjacency_summary.low_wall_adjacency_room_ratio", "wall_adjacency.low_wall_adjacency_room_ratio"),
        ("wall_adjacency_summary.interior_wall_adjacency_ratio", "wall_adjacency.interior_wall_adjacency_ratio"),
        ("wall_adjacency_summary.isolated_wall_room_count", "wall_adjacency.isolated_wall_room_count"),
        ("wall_adjacency_summary.low_wall_adjacency_room_count", "wall_adjacency.low_wall_adjacency_room_count"),
        ("degree_summary.degree_min", "degree_summary.degree_min"),
        ("degree_summary.degree_mean", "degree_summary.degree_mean"),
        ("degree_summary.degree_max", "degree_summary.degree_max"),
        ("trace_metadata.trace_length", "trace_metadata.trace_length"),
    ):
        _add_numeric_feature(features, feature_key, _nested_value(candidate_summary, source_key))

    _add_flat_count_features(features, "node_type_count", candidate_summary.get("node_type_counts", {}))
    _add_flat_count_features(features, "edge_type_count", candidate_summary.get("edge_type_counts", {}))
    _add_flat_count_features(features, "degree_histogram", candidate_summary.get("degree_histogram", {}))
    _add_flat_count_features(features, "support_type_ratio", candidate_summary.get("support_type_ratios", {}))
    _add_flat_count_features(features, "rule_count", _nested_value(candidate_summary, "trace_metadata.applied_rule_counts") or {})

    pair = _typed_accessibility_pair(
        candidate_summary,
        DEFAULT_ACCESS_SOURCE_TYPE,
        DEFAULT_ACCESS_TARGET_TYPE,
    )
    access_prefix = f"typed_access.{DEFAULT_ACCESS_SOURCE_TYPE}_to_{DEFAULT_ACCESS_TARGET_TYPE}"
    if pair:
        for key in (
            "source_count",
            "target_count",
            "reachable_count",
            "unreachable_count",
            "distance_min",
            "distance_mean",
            "distance_median",
            "distance_max",
        ):
            _add_numeric_feature(features, f"{access_prefix}.{key}", pair.get(key))
        _add_flat_count_features(features, f"{access_prefix}.distance_histogram", pair.get("distance_histogram", {}))
    else:
        _add_numeric_feature(features, f"{access_prefix}.source_count", 0.0)
        _add_numeric_feature(features, f"{access_prefix}.target_count", 0.0)
        _add_numeric_feature(features, f"{access_prefix}.reachable_count", 0.0)
        _add_numeric_feature(features, f"{access_prefix}.unreachable_count", 0.0)

    return {key: float(value) for key, value in sorted(features.items())}


def build_feature_matrix(feature_vectors: list[dict[str, float]]) -> tuple[list[str], list[list[float]]]:
    """Align feature dictionaries into a stable matrix."""
    feature_keys = sorted({key for vector in feature_vectors for key in vector})
    matrix = [
        [float(vector.get(key, 0.0)) for key in feature_keys]
        for vector in feature_vectors
    ]
    return feature_keys, matrix


def minmax_normalize_matrix(matrix: list[list[float]]) -> list[list[float]]:
    """Min-max normalize columns, using 0.0 for zero-range features."""
    if not matrix:
        return []
    column_count = len(matrix[0])
    mins = [min(row[index] for row in matrix) for index in range(column_count)]
    maxes = [max(row[index] for row in matrix) for index in range(column_count)]
    normalized = []
    for row in matrix:
        normalized_row = []
        for index, value in enumerate(row):
            value_range = maxes[index] - mins[index]
            normalized_row.append(0.0 if value_range == 0 else (value - mins[index]) / value_range)
        normalized.append(normalized_row)
    return normalized


def _weight_for_key(feature_key: str, feature_weights: dict[str, float] | None) -> float:
    if not feature_weights:
        return 1.0
    if feature_key in feature_weights:
        return feature_weights[feature_key]
    matching_prefixes = [
        prefix
        for prefix in feature_weights
        if feature_key.startswith(prefix)
    ]
    if not matching_prefixes:
        return 1.0
    return feature_weights[max(matching_prefixes, key=len)]


def _weighted_euclidean_distance(
    left: list[float],
    right: list[float],
    feature_keys: list[str],
    feature_weights: dict[str, float] | None = None,
) -> float:
    total = 0.0
    for index, feature_key in enumerate(feature_keys):
        weight = _weight_for_key(feature_key, feature_weights)
        total += weight * ((left[index] - right[index]) ** 2)
    return round(math.sqrt(total), 6)


def _max_weighted_euclidean_distance(
    feature_keys: list[str],
    feature_weights: dict[str, float] | None = None,
) -> float:
    """Return the maximum possible distance after per-feature min-max normalization."""
    total = 0.0
    for feature_key in feature_keys:
        weight = _weight_for_key(feature_key, feature_weights)
        if weight > 0:
            total += weight
    return round(math.sqrt(total), 6)


def weighted_distance(
    a: dict[str, float],
    b: dict[str, float],
    feature_weights: dict[str, float] | None = None,
) -> float:
    """Compute weighted Euclidean distance in normalized two-vector feature space."""
    feature_keys, matrix = build_feature_matrix([a, b])
    normalized = minmax_normalize_matrix(matrix)
    return _weighted_euclidean_distance(normalized[0], normalized[1], feature_keys, feature_weights)


def compute_pairwise_distances(
    feature_vectors: list[dict[str, float]],
    feature_weights: dict[str, float] | None = None,
) -> list[list[float]]:
    """Compute weighted Euclidean pairwise distances in normalized feature space."""
    feature_keys, matrix = build_feature_matrix(feature_vectors)
    normalized = minmax_normalize_matrix(matrix)
    distances = [[0.0 for _ in normalized] for _ in normalized]
    for left_index in range(len(normalized)):
        for right_index in range(left_index + 1, len(normalized)):
            distance = _weighted_euclidean_distance(
                normalized[left_index],
                normalized[right_index],
                feature_keys,
                feature_weights,
            )
            distances[left_index][right_index] = distance
            distances[right_index][left_index] = distance
    return distances


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return round(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 6)


def compute_diversity_metrics(
    candidate_summaries: list[dict],
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    feature_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute within-batch diversity metrics from candidate review summaries."""
    feature_vectors = [extract_diversity_feature_vector(summary) for summary in candidate_summaries]
    feature_keys, _ = build_feature_matrix(feature_vectors)
    distances = compute_pairwise_distances(feature_vectors, feature_weights)
    pair_distances = [
        distances[left][right]
        for left in range(len(distances))
        for right in range(left + 1, len(distances))
    ]
    near_duplicate_pair_count = sum(1 for distance in pair_distances if distance <= near_duplicate_threshold)
    unique_indices = []
    for index in range(len(candidate_summaries)):
        if not any(distances[index][earlier] <= near_duplicate_threshold for earlier in unique_indices):
            unique_indices.append(index)

    total_pairs = len(pair_distances)
    num_candidates = len(candidate_summaries)
    return {
        "num_candidates": num_candidates,
        "num_valid_candidates": sum(
            1
            for summary in candidate_summaries
            if summary.get("validity_status", {}).get("is_valid") is True
        ),
        "feature_count": len(feature_keys),
        "mean_pairwise_distance": round(sum(pair_distances) / total_pairs, 6) if total_pairs else 0.0,
        "median_pairwise_distance": median(pair_distances) if pair_distances else 0.0,
        "min_pairwise_distance": min(pair_distances) if pair_distances else 0.0,
        "max_pairwise_distance": max(pair_distances) if pair_distances else 0.0,
        "pairwise_distance_std": _std(pair_distances),
        "near_duplicate_threshold": near_duplicate_threshold,
        "near_duplicate_pair_count": near_duplicate_pair_count,
        "near_duplicate_rate": round(near_duplicate_pair_count / total_pairs, 6) if total_pairs else 0.0,
        "unique_candidate_count": len(unique_indices),
        "unique_rate": round(len(unique_indices) / num_candidates, 6) if num_candidates else 0.0,
    }


def load_final_output_archive(path: str | Path) -> dict[str, Any]:
    """Load a final-output archive, returning an empty archive if missing."""
    archive_path = Path(path)
    if not archive_path.exists():
        return {"version": ARCHIVE_VERSION, "outputs": []}
    return json.loads(archive_path.read_text(encoding="utf-8"))


def save_final_output_archive(archive: dict, path: str | Path) -> None:
    """Save a final-output archive."""
    archive_path = Path(path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(json.dumps(archive, indent=2), encoding="utf-8")


def _archive_feature_vector(output: dict[str, Any]) -> dict[str, float]:
    if isinstance(output.get("feature_vector"), dict):
        return {key: _number(value) for key, value in output["feature_vector"].items()}
    if isinstance(output.get("review_summary"), dict):
        return extract_diversity_feature_vector(output["review_summary"])
    return {}


def _normalized_current_archive_distances(
    current_vectors: list[dict[str, float]],
    archive_vectors: list[dict[str, float]],
    feature_weights: dict[str, float] | None,
) -> tuple[list[list[float]], float]:
    feature_keys, matrix = build_feature_matrix(current_vectors + archive_vectors)
    normalized = minmax_normalize_matrix(matrix)
    max_distance = _max_weighted_euclidean_distance(feature_keys, feature_weights)
    current_count = len(current_vectors)
    distances = []
    for current_index in range(current_count):
        row = []
        for archive_index in range(current_count, len(normalized)):
            row.append(
                _weighted_euclidean_distance(
                    normalized[current_index],
                    normalized[archive_index],
                    feature_keys,
                    feature_weights,
                )
            )
        distances.append(row)
    return distances, max_distance


def compute_novelty_against_archive(
    candidate_summaries: list[dict],
    archive: dict,
    feature_weights: dict[str, float] | None = None,
    low_novelty_threshold: float = DEFAULT_LOW_NOVELTY_THRESHOLD,
) -> dict[str, Any]:
    """Compute novelty as distance to nearest archived final output."""
    archive_outputs = archive.get("outputs", []) if isinstance(archive, dict) else []
    archive_vectors = [_archive_feature_vector(output) for output in archive_outputs]
    current_vectors = [extract_diversity_feature_vector(summary) for summary in candidate_summaries]
    if not archive_vectors:
        candidate_novelty = [
            {
                "candidate_id": summary.get("candidate_id"),
                "novelty_score": 1.0,
                "nearest_archive_output_id": None,
                "nearest_archive_distance": None,
                "nearest_archive_graph_path": None,
            }
            for summary in candidate_summaries
        ]
    else:
        distances, max_distance = _normalized_current_archive_distances(current_vectors, archive_vectors, feature_weights)
        candidate_novelty = []
        for index, summary in enumerate(candidate_summaries):
            nearest_archive_index, nearest_distance = min(
                enumerate(distances[index]),
                key=lambda item: item[1],
            )
            nearest_output = archive_outputs[nearest_archive_index]
            novelty_score = 0.0 if max_distance == 0.0 else nearest_distance / max_distance
            novelty_score = max(0.0, min(1.0, novelty_score))
            candidate_novelty.append(
                {
                    "candidate_id": summary.get("candidate_id"),
                    "novelty_score": round(novelty_score, 6),
                    "nearest_archive_output_id": nearest_output.get("output_id"),
                    "nearest_archive_distance": nearest_distance,
                    "nearest_archive_graph_path": nearest_output.get("graph_path"),
                }
            )

    novelty_scores = [entry["novelty_score"] for entry in candidate_novelty]
    low_novelty_count = sum(1 for score in novelty_scores if score <= low_novelty_threshold)
    return {
        "archive_size": len(archive_outputs),
        "mean_novelty_score": round(sum(novelty_scores) / len(novelty_scores), 6) if novelty_scores else 0.0,
        "median_novelty_score": median(novelty_scores) if novelty_scores else 0.0,
        "min_novelty_score": min(novelty_scores) if novelty_scores else 0.0,
        "max_novelty_score": max(novelty_scores) if novelty_scores else 0.0,
        "low_novelty_threshold": low_novelty_threshold,
        "low_novelty_candidate_count": low_novelty_count,
        "low_novelty_rate": round(low_novelty_count / len(novelty_scores), 6) if novelty_scores else 0.0,
        "candidate_novelty": candidate_novelty,
    }


DEFAULT_BIN_CONFIG = {
    "corridor_fraction": [0.2, 0.35],
    "support_type_ratio.ClinicalSupport": [0.2, 0.4],
    "support_type_ratio.StaffSupport": [0.1, 0.3],
    "wall_adjacency.low_wall_adjacency_room_ratio": [0.25, 0.5],
    "edge_node_ratio": [1.0, 1.5],
    "typed_access.PatientRoom_to_ClinicalSupport.distance_mean": [2.0, 3.0],
}


def _feature_bin(value: float, thresholds: list[float]) -> int:
    for index, threshold in enumerate(thresholds):
        if value <= threshold:
            return index
    return len(thresholds)


def compute_feature_bin_coverage(
    candidate_summaries: list[dict],
    bin_config: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Compute lightweight behavior-bin coverage from diversity features."""
    bin_config = bin_config or DEFAULT_BIN_CONFIG
    feature_vectors = [extract_diversity_feature_vector(summary) for summary in candidate_summaries]
    available_dimensions = [
        key
        for key in sorted(bin_config)
        if any(key in vector for vector in feature_vectors)
    ]
    occupied_bins = set()
    for vector in feature_vectors:
        if not available_dimensions:
            continue
        bin_values = []
        for key in available_dimensions:
            bin_values.append(str(_feature_bin(vector.get(key, 0.0), bin_config[key])))
        occupied_bins.add("|".join(f"{key}:{value}" for key, value in zip(available_dimensions, bin_values)))

    total_possible = 1
    for key in available_dimensions:
        total_possible *= len(bin_config[key]) + 1
    if not available_dimensions:
        total_possible = 0
    max_possible_occupied_bins = min(len(candidate_summaries), total_possible)
    return {
        "num_candidates": len(candidate_summaries),
        "num_dimensions": len(available_dimensions),
        "occupied_bin_count": len(occupied_bins),
        "total_possible_bin_count": total_possible,
        "coverage_rate": round(len(occupied_bins) / total_possible, 6) if total_possible else 0.0,
        "max_possible_occupied_bins": max_possible_occupied_bins,
        "sample_normalized_coverage_rate": round(len(occupied_bins) / max_possible_occupied_bins, 6) if max_possible_occupied_bins else 0.0,
        "occupied_bins": sorted(occupied_bins),
    }


def build_diversity_report(
    candidate_summaries: list[dict],
    archive: dict | None = None,
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    low_novelty_threshold: float = DEFAULT_LOW_NOVELTY_THRESHOLD,
    feature_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the complete diversity report payload."""
    archive = archive or {"version": ARCHIVE_VERSION, "outputs": []}
    return {
        "diversity_metrics": compute_diversity_metrics(
            candidate_summaries,
            near_duplicate_threshold=near_duplicate_threshold,
            feature_weights=feature_weights,
        ),
        "feature_bin_coverage": compute_feature_bin_coverage(candidate_summaries),
        "novelty_against_archive": compute_novelty_against_archive(
            candidate_summaries,
            archive,
            feature_weights=feature_weights,
            low_novelty_threshold=low_novelty_threshold,
        ),
    }


def export_diversity_report_json(report: dict[str, Any], output_path: str | Path) -> Path:
    """Write diversity report data to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
