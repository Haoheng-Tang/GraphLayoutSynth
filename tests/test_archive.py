import json

import pytest

from graph_layout_synth.archive import (
    ArchiveError,
    add_final_output_to_archive,
    build_archive_entry_from_selection,
    load_final_output_archive,
    load_selection_file,
    resolve_review_summary_from_selection,
    save_final_output_archive,
)
from graph_layout_synth.cli import main


def _review_summary(candidate_id: str = "candidate_3") -> dict:
    return {
        "candidate_id": candidate_id,
        "final_score": 173.2,
        "node_count": 15,
        "edge_count": 23,
        "node_type_counts": {"PatientRoom": 3, "ClinicalSupport": 1},
        "edge_type_counts": {"door": 5, "wall": 4},
        "key_metrics": {"corridor_access_ratio": 1.0, "edge_node_ratio": 1.5},
        "wall_adjacency_summary": {"low_wall_adjacency_room_ratio": 0.25},
        "typed_accessibility_summary": {
            "edge_type": "door",
            "pairs": [
                {
                    "source_type": "PatientRoom",
                    "target_type": "ClinicalSupport",
                    "distance_histogram": {"2": 3},
                }
            ],
        },
        "trace_metadata": {"trace_length": 4, "applied_rule_counts": {"expand": 1}},
        "artifact_paths": {
            "graph_path": "outputs/candidate_3.json",
            "report_path": "outputs/candidate_3_report.json",
            "trace_path": "outputs/candidate_3_trace.json",
            "image_path": "outputs/candidate_3.png",
            "review_summary_path": "outputs/candidate_3_review_summary.json",
        },
    }


def test_loading_missing_archive_returns_empty_versioned_archive(tmp_path):
    archive = load_final_output_archive(tmp_path / "missing.json")

    assert archive == {"version": 1, "outputs": []}


def test_saving_and_loading_archive_round_trip(tmp_path):
    archive_path = tmp_path / "nested" / "archive.json"
    archive = {"version": 1, "outputs": [{"output_id": "final_001"}]}

    save_final_output_archive(archive, archive_path)

    assert load_final_output_archive(archive_path) == archive


def test_malformed_archive_fails_clearly(tmp_path):
    archive_path = tmp_path / "archive.json"
    archive_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(ArchiveError, match="not valid JSON"):
        load_final_output_archive(archive_path)


def test_loading_valid_selection_file(tmp_path):
    selection_path = tmp_path / "llm_selection.json"
    selection_path.write_text(
        json.dumps({"selected_candidate_id": "candidate_3", "selection_source": "mock"}),
        encoding="utf-8",
    )

    selection = load_selection_file(selection_path)

    assert selection["selected_candidate_id"] == "candidate_3"


def test_loading_selection_file_allows_utf8_bom(tmp_path):
    selection_path = tmp_path / "llm_selection.json"
    selection_path.write_text(
        "\ufeff" + json.dumps({"selected_candidate_id": "candidate_3"}),
        encoding="utf-8",
    )

    selection = load_selection_file(selection_path)

    assert selection["selected_candidate_id"] == "candidate_3"


def test_selection_missing_candidate_id_fails_clearly(tmp_path):
    selection_path = tmp_path / "llm_selection.json"
    selection_path.write_text(json.dumps({"selection_source": "mock"}), encoding="utf-8")

    with pytest.raises(ArchiveError, match="selected_candidate_id"):
        load_selection_file(selection_path)


def test_resolve_review_summary_from_selection(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    review_summary_path = output_dir / "candidate_3_review_summary.json"
    review_summary_path.write_text(json.dumps(_review_summary()), encoding="utf-8")

    resolved_path, review_summary = resolve_review_summary_from_selection(
        {"selected_candidate_id": "candidate_3"},
        output_dir,
    )

    assert resolved_path == str(review_summary_path)
    assert review_summary["candidate_id"] == "candidate_3"


def test_review_summary_candidate_mismatch_is_rejected(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "candidate_3_review_summary.json").write_text(
        json.dumps(_review_summary("candidate_4")),
        encoding="utf-8",
    )

    with pytest.raises(ArchiveError, match="does not match"):
        resolve_review_summary_from_selection({"selected_candidate_id": "candidate_3"}, output_dir)


def test_missing_review_summary_reports_expected_path(tmp_path):
    with pytest.raises(ArchiveError, match="Expected review summary not found"):
        resolve_review_summary_from_selection({"selected_candidate_id": "candidate_3"}, tmp_path)


def test_build_archive_entry_from_selection_and_review_summary():
    selection = {
        "selected_candidate_id": "candidate_3",
        "selection_source": "claude",
        "selection_rationale": "Strong valid candidate.",
        "review_context_path": "outputs/llm_evaluation.md",
    }

    entry = build_archive_entry_from_selection(selection, _review_summary(), output_id="final_001")

    assert entry["output_id"] == "final_001"
    assert entry["candidate_id"] == "candidate_3"
    assert entry["selection_source"] == "claude"
    assert entry["selection_rationale"] == "Strong valid candidate."
    assert entry["review_context_path"] == "outputs/llm_evaluation.md"
    assert entry["final_score"] == 173.2
    assert entry["graph_path"] == "outputs/candidate_3.json"
    assert entry["report_path"] == "outputs/candidate_3_report.json"
    assert entry["trace_path"] == "outputs/candidate_3_trace.json"
    assert entry["image_path"] == "outputs/candidate_3.png"
    assert entry["review_summary_path"] == "outputs/candidate_3_review_summary.json"
    assert entry["feature_vector"]
    assert entry["summary"]["typed_accessibility_summary"]


def test_add_entry_appends_and_rejects_duplicate_output_id(tmp_path):
    archive_path = tmp_path / "archive.json"
    entry = build_archive_entry_from_selection(
        {"selected_candidate_id": "candidate_3"},
        _review_summary(),
        output_id="final_001",
    )

    archive = add_final_output_to_archive(archive_path, entry)

    assert len(archive["outputs"]) == 1
    with pytest.raises(ArchiveError, match="already exists"):
        add_final_output_to_archive(archive_path, entry)


def test_add_entry_can_allow_duplicate_output_id(tmp_path):
    archive_path = tmp_path / "archive.json"
    entry = build_archive_entry_from_selection(
        {"selected_candidate_id": "candidate_3"},
        _review_summary(),
        output_id="final_001",
    )

    add_final_output_to_archive(archive_path, entry)
    archive = add_final_output_to_archive(archive_path, entry, allow_duplicate_output_id=True)

    assert len(archive["outputs"]) == 2


def test_cli_archive_final_selection_creates_archive(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "candidate_3_review_summary.json").write_text(json.dumps(_review_summary()), encoding="utf-8")
    selection_path = output_dir / "llm_selection.json"
    selection_path.write_text(
        json.dumps(
            {
                "selected_candidate_id": "candidate_3",
                "selection_source": "mock",
                "selection_rationale": "Smoke selection.",
            }
        ),
        encoding="utf-8",
    )
    archive_path = output_dir / "final_output_archive.json"

    main(
        [
            "archive-final",
            "--selection",
            str(selection_path),
            "--output-dir",
            str(output_dir),
            "--archive-path",
            str(archive_path),
            "--output-id",
            "final_test_001",
        ]
    )

    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["outputs"][0]["output_id"] == "final_test_001"
    assert archive["outputs"][0]["candidate_id"] == "candidate_3"


def test_cli_archive_final_direct_review_summary_mode(tmp_path):
    review_summary_path = tmp_path / "candidate_3_review_summary.json"
    review_summary_path.write_text(json.dumps(_review_summary()), encoding="utf-8")
    archive_path = tmp_path / "archive.json"

    main(
        [
            "archive-final",
            "--review-summary",
            str(review_summary_path),
            "--archive-path",
            str(archive_path),
            "--output-id",
            "final_direct_001",
            "--notes",
            "Manual selection.",
        ]
    )

    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["outputs"][0]["output_id"] == "final_direct_001"
    assert archive["outputs"][0]["selection_source"] == "manual"
    assert archive["outputs"][0]["selection_rationale"] == "Manual selection."
