"""Final-output archive utilities for explicitly selected candidates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_layout_synth.diversity import ARCHIVE_VERSION, extract_diversity_feature_vector


class ArchiveError(ValueError):
    """Raised when final-output archiving cannot proceed safely."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def generate_output_id() -> str:
    """Generate a timestamp-based final output id."""
    return f"final_{_timestamp()}"


def load_final_output_archive(path: str | Path) -> dict[str, Any]:
    """Load a final-output archive, or return an empty archive if missing."""
    archive_path = Path(path)
    if not archive_path.exists():
        return {"version": ARCHIVE_VERSION, "outputs": []}
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"Archive file is not valid JSON: {archive_path}") from exc
    if not isinstance(archive, dict) or not isinstance(archive.get("outputs"), list):
        raise ArchiveError(f"Archive file has invalid schema: {archive_path}")
    archive.setdefault("version", ARCHIVE_VERSION)
    return archive


def save_final_output_archive(archive: dict, path: str | Path) -> None:
    """Save a final-output archive, creating parent directories as needed."""
    archive_path = Path(path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(json.dumps(archive, indent=2), encoding="utf-8")


def load_selection_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a machine-readable final-candidate selection file."""
    selection_path = Path(path)
    try:
        selection = json.loads(selection_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ArchiveError(f"Selection file not found: {selection_path}") from exc
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"Selection file is not valid JSON: {selection_path}") from exc
    if not isinstance(selection, dict):
        raise ArchiveError("Selection file must contain a JSON object.")
    if not selection.get("selected_candidate_id"):
        raise ArchiveError("Selection file is missing required field 'selected_candidate_id'.")
    return selection


def resolve_review_summary_from_selection(
    selection: dict,
    output_dir: str | Path,
) -> tuple[str, dict[str, Any]]:
    """Resolve and load the selected candidate review summary."""
    selected_candidate_id = selection.get("selected_candidate_id")
    if not selected_candidate_id:
        raise ArchiveError("Selection is missing required field 'selected_candidate_id'.")
    review_summary_path = Path(output_dir) / f"{selected_candidate_id}_review_summary.json"
    if not review_summary_path.exists():
        raise ArchiveError(f"Expected review summary not found: {review_summary_path}")
    review_summary = json.loads(review_summary_path.read_text(encoding="utf-8"))
    if review_summary.get("candidate_id") != selected_candidate_id:
        raise ArchiveError(
            "Selection candidate id does not match review summary candidate_id: "
            f"{selected_candidate_id} != {review_summary.get('candidate_id')}"
        )
    return str(review_summary_path), review_summary


def _artifact_path(review_summary: dict[str, Any], key: str) -> Any:
    artifact_paths = review_summary.get("artifact_paths", {})
    if not isinstance(artifact_paths, dict):
        return None
    return artifact_paths.get(key)


def _compact_summary(review_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_count": review_summary.get("node_count"),
        "edge_count": review_summary.get("edge_count"),
        "node_type_counts": review_summary.get("node_type_counts", {}),
        "edge_type_counts": review_summary.get("edge_type_counts", {}),
        "key_metrics": review_summary.get("key_metrics", {}),
        "wall_adjacency_summary": review_summary.get("wall_adjacency_summary", {}),
        "typed_accessibility_summary": review_summary.get("typed_accessibility_summary", {}),
    }


def build_archive_entry_from_selection(
    selection: dict,
    review_summary: dict,
    output_id: str | None = None,
) -> dict[str, Any]:
    """Build one final-output archive entry from a selection and review summary."""
    selected_candidate_id = selection.get("selected_candidate_id") or review_summary.get("candidate_id")
    if not selected_candidate_id:
        raise ArchiveError("Cannot build archive entry without a selected candidate id.")
    if review_summary.get("candidate_id") and review_summary.get("candidate_id") != selected_candidate_id:
        raise ArchiveError(
            "Selection candidate id does not match review summary candidate_id: "
            f"{selected_candidate_id} != {review_summary.get('candidate_id')}"
        )
    review_summary_path = _artifact_path(review_summary, "review_summary_path")
    return {
        "output_id": output_id or generate_output_id(),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "candidate_id": selected_candidate_id,
        "selection_source": selection.get("selection_source"),
        "selection_rationale": selection.get("selection_rationale") or selection.get("notes"),
        "review_context_path": selection.get("review_context_path"),
        "final_score": review_summary.get("final_score"),
        "graph_path": _artifact_path(review_summary, "graph_path"),
        "report_path": _artifact_path(review_summary, "report_path"),
        "trace_path": _artifact_path(review_summary, "trace_path"),
        "image_path": _artifact_path(review_summary, "image_path"),
        "review_summary_path": review_summary_path,
        "feature_vector": extract_diversity_feature_vector(review_summary),
        "summary": _compact_summary(review_summary),
    }


def add_final_output_to_archive(
    archive_path: str | Path,
    entry: dict,
    allow_duplicate_output_id: bool = False,
) -> dict[str, Any]:
    """Append an entry to the final-output archive and save it."""
    archive = load_final_output_archive(archive_path)
    output_id = entry.get("output_id")
    if not output_id:
        raise ArchiveError("Archive entry is missing required field 'output_id'.")
    if not allow_duplicate_output_id:
        existing_ids = {output.get("output_id") for output in archive.get("outputs", [])}
        if output_id in existing_ids:
            raise ArchiveError(f"Archive output_id already exists: {output_id}")
    archive.setdefault("version", ARCHIVE_VERSION)
    archive.setdefault("outputs", [])
    archive["outputs"].append(entry)
    save_final_output_archive(archive, archive_path)
    return archive
