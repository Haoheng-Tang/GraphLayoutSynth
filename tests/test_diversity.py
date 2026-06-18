import json
import math

from graph_layout_synth.cli import main
from graph_layout_synth.diversity import (
    build_feature_matrix,
    compute_diversity_metrics,
    compute_feature_bin_coverage,
    compute_novelty_against_archive,
    compute_pairwise_distances,
    extract_diversity_feature_vector,
    load_final_output_archive,
    minmax_normalize_matrix,
    weighted_distance,
)


def _summary(candidate_id: str = "candidate_1", node_count: int = 5, final_score: float = 100.0) -> dict:
    return {
        "candidate_id": candidate_id,
        "validity_status": {"is_valid": True, "validation_errors": []},
        "final_score": final_score,
        "key_metrics": {
            "corridor_access_ratio": 1.0,
            "dead_end_count": 1,
            "edge_node_ratio": 1.4,
            "room_corridor_ratio": 3.0,
            "door_wall_ratio": 2.0,
            "corridor_fraction": 0.2,
        },
        "node_count": node_count,
        "edge_count": 7,
        "node_type_counts": {"Corridor": 1, "PatientRoom": 3, "ClinicalSupport": 1},
        "edge_type_counts": {"door": 4, "wall": 3},
        "degree_summary": {"degree_min": 1, "degree_mean": 2.0, "degree_max": 3},
        "degree_histogram": {"1": 1, "2": 3, "3": 1},
        "support_type_ratios": {"ClinicalSupport": 0.25},
        "wall_adjacency_summary": {
            "low_wall_adjacency_room_ratio": 0.5,
            "interior_wall_adjacency_ratio": 0.5,
            "isolated_wall_room_count": 0,
            "low_wall_adjacency_room_count": 2,
        },
        "trace_metadata": {
            "trace_length": 4,
            "applied_rule_counts": {
                "expand_floor_to_zones": 1,
                "expand_zone_to_room_cluster": 3,
            },
        },
        "typed_accessibility_summary": {
            "edge_type": "door",
            "pairs": [
                {
                    "source_type": "PatientRoom",
                    "target_type": "ClinicalSupport",
                    "source_count": 3,
                    "target_count": 1,
                    "reachable_count": 3,
                    "unreachable_count": 0,
                    "distance_min": 2,
                    "distance_mean": 2.3333,
                    "distance_median": 2,
                    "distance_max": 3,
                    "distance_histogram": {"2": 2, "3": 1},
                }
            ],
        },
    }


def test_feature_vector_flattens_counts_rules_and_typed_accessibility():
    features = extract_diversity_feature_vector(_summary())

    assert features["node_count"] == 5.0
    assert features["node_type_count.PatientRoom"] == 3.0
    assert features["edge_type_count.wall"] == 3.0
    assert features["rule_count.expand_zone_to_room_cluster"] == 3.0
    assert features["typed_access.PatientRoom_to_ClinicalSupport.distance_histogram.2"] == 2.0
    assert features["typed_access.PatientRoom_to_ClinicalSupport.distance_histogram.3"] == 1.0
    assert features["typed_access.PatientRoom_to_ClinicalSupport.distance_mean"] == 2.3333


def test_feature_matrix_alignment_and_zero_range_normalization():
    keys, matrix = build_feature_matrix([{"b": 2.0}, {"a": 1.0, "b": 2.0}])
    normalized = minmax_normalize_matrix(matrix)

    assert keys == ["a", "b"]
    assert matrix == [[0.0, 2.0], [1.0, 2.0]]
    assert normalized == [[0.0, 0.0], [1.0, 0.0]]


def test_distance_identical_is_zero_and_different_is_positive():
    first = extract_diversity_feature_vector(_summary("candidate_1"))
    second = extract_diversity_feature_vector(_summary("candidate_2"))
    third = extract_diversity_feature_vector(_summary("candidate_3", node_count=8))

    assert weighted_distance(first, second) == 0.0
    assert weighted_distance(first, third) > 0.0
    distances = compute_pairwise_distances([first, second, third])
    assert distances[0][1] == 0.0
    assert distances[0][2] > 0.0


def test_diversity_metrics_edge_cases_and_near_duplicate_rate():
    assert compute_diversity_metrics([])["num_candidates"] == 0
    single = compute_diversity_metrics([_summary("candidate_1")])
    assert single["unique_candidate_count"] == 1
    assert single["unique_rate"] == 1.0

    identical = compute_diversity_metrics([_summary("candidate_1"), _summary("candidate_2")])
    assert identical["near_duplicate_pair_count"] == 1
    assert identical["near_duplicate_rate"] == 1.0
    assert identical["unique_candidate_count"] == 1

    different = compute_diversity_metrics([_summary("candidate_1"), _summary("candidate_2", node_count=9)])
    assert different["max_pairwise_distance"] > 0.0
    assert different["unique_candidate_count"] == 2


def test_missing_archive_loads_empty_archive(tmp_path):
    archive = load_final_output_archive(tmp_path / "missing_archive.json")

    assert archive == {"version": 1, "outputs": []}


def test_novelty_empty_archive_is_maximal_and_matching_archive_is_low():
    summary = _summary("candidate_1")
    empty = compute_novelty_against_archive([summary], {"version": 1, "outputs": []})
    assert empty["candidate_novelty"][0]["novelty_score"] == 1.0
    assert empty["candidate_novelty"][0]["nearest_archive_output_id"] is None

    archive = {
        "version": 1,
        "outputs": [
            {
                "output_id": "final_001",
                "graph_path": "outputs/final_001.json",
                "feature_vector": extract_diversity_feature_vector(summary),
            }
        ],
    }
    novelty = compute_novelty_against_archive([summary], archive)
    assert novelty["archive_size"] == 1
    assert novelty["candidate_novelty"][0]["nearest_archive_output_id"] == "final_001"
    assert novelty["candidate_novelty"][0]["nearest_archive_distance"] == 0.0
    assert novelty["candidate_novelty"][0]["novelty_score"] == 0.0
    assert novelty["low_novelty_candidate_count"] == 1


def test_novelty_score_is_bounded_normalized_distance_not_clipped_raw_distance():
    summary = _summary("candidate_1")
    features = extract_diversity_feature_vector(summary)
    archive = {
        "version": 1,
        "outputs": [
            {
                "output_id": "final_001",
                "graph_path": "outputs/final_001.json",
                "feature_vector": {},
            }
        ],
    }

    novelty = compute_novelty_against_archive([summary], archive)
    entry = novelty["candidate_novelty"][0]
    expected_max_distance = round(math.sqrt(len(features)), 6)

    assert entry["nearest_archive_distance"] > 1.0
    assert 0.0 < entry["novelty_score"] < 1.0
    assert entry["novelty_score"] == round(entry["nearest_archive_distance"] / expected_max_distance, 6)


def test_feature_bin_coverage_handles_zero_candidates():
    coverage = compute_feature_bin_coverage([], bin_config={"node_count": [6]})

    assert coverage["num_candidates"] == 0
    assert coverage["occupied_bin_count"] == 0
    assert coverage["total_possible_bin_count"] == 0
    assert coverage["max_possible_occupied_bins"] == 0
    assert coverage["coverage_rate"] == 0.0
    assert coverage["sample_normalized_coverage_rate"] == 0.0


def test_feature_bin_coverage_reports_sample_normalized_rate_for_small_batches():
    coverage = compute_feature_bin_coverage(
        [_summary("candidate_1"), _summary("candidate_2", node_count=9)],
        bin_config={"node_count": [6], "edge_count": [6]},
    )

    assert coverage["num_candidates"] == 2
    assert coverage["num_dimensions"] == 2
    assert coverage["occupied_bin_count"] == 2
    assert coverage["total_possible_bin_count"] == 4
    assert coverage["max_possible_occupied_bins"] == 2
    assert coverage["coverage_rate"] == 0.5
    assert coverage["sample_normalized_coverage_rate"] == 1.0


def test_feature_bin_coverage_counts_duplicate_bins_once():
    coverage = compute_feature_bin_coverage(
        [_summary("candidate_1"), _summary("candidate_2")],
        bin_config={"node_count": [6], "edge_count": [6]},
    )

    assert coverage["occupied_bin_count"] == 1
    assert coverage["total_possible_bin_count"] == 4
    assert coverage["max_possible_occupied_bins"] == 2
    assert coverage["coverage_rate"] == 0.25
    assert coverage["sample_normalized_coverage_rate"] == 0.5


def test_feature_bin_coverage_can_occupy_all_bins_in_tiny_config():
    coverage = compute_feature_bin_coverage(
        [_summary("candidate_1", node_count=5), _summary("candidate_2", node_count=9)],
        bin_config={"node_count": [6]},
    )

    assert coverage["occupied_bin_count"] == 2
    assert coverage["total_possible_bin_count"] == 2
    assert coverage["max_possible_occupied_bins"] == 2
    assert coverage["coverage_rate"] == 1.0
    assert coverage["sample_normalized_coverage_rate"] == 1.0


def test_cli_writes_diversity_report(tmp_path):
    main(
        [
            "generate",
            "--config",
            "configs/generic_building.yaml",
            "--num-candidates",
            "2",
            "--top-k",
            "1",
            "--seed",
            "42",
            "--output-dir",
            str(tmp_path),
        ]
    )

    diversity_report = tmp_path / "diversity_report.json"
    assert diversity_report.exists()
    data = json.loads(diversity_report.read_text(encoding="utf-8"))
    assert "diversity_metrics" in data
    assert "feature_bin_coverage" in data
    assert "novelty_against_archive" in data
    assert data["novelty_against_archive"]["archive_size"] == 0
