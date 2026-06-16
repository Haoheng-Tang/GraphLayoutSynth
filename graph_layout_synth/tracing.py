"""Rule-application trace helpers for generated layout graphs."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuleApplicationTraceEvent:
    """One grammar rule application during graph generation."""

    step_index: int
    rule_name: str
    matched_node_id: str
    matched_node_attrs: dict[str, Any]
    sampled_parameters: dict[str, Any]
    created_node_ids: list[str]
    created_edges: list[dict[str, str]]
    removed_node_ids: list[str]
    notes: list[str] = field(default_factory=list)


def trace_to_dicts(trace: list[RuleApplicationTraceEvent]) -> list[dict[str, Any]]:
    """Convert trace events to JSON-serializable dictionaries."""
    return [asdict(event) for event in trace]


def trace_rule_counts(trace: list[RuleApplicationTraceEvent]) -> dict[str, int]:
    """Return a compact count of applied rule names."""
    return dict(Counter(event.rule_name for event in trace))


def applied_rule_names(trace: list[RuleApplicationTraceEvent]) -> list[str]:
    """Return rule names in first-application order."""
    return list(dict.fromkeys(event.rule_name for event in trace))


def trace_metadata(
    trace: list[RuleApplicationTraceEvent],
    trace_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build compact report metadata for a trace."""
    metadata = {
        "trace_length": len(trace),
        "applied_rule_names": applied_rule_names(trace),
        "applied_rule_counts": trace_rule_counts(trace),
    }
    if trace_path is not None:
        metadata["trace_path"] = str(trace_path)
    return metadata


def export_trace_json(trace: list[RuleApplicationTraceEvent], output_path: str | Path) -> Path:
    """Write a rule-application trace to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace_to_dicts(trace), indent=2), encoding="utf-8")
    return path


def summarize_trace(trace: list[RuleApplicationTraceEvent]) -> str:
    """Return a short human-readable trace summary."""
    lines = ["Generation trace:"]
    if not trace:
        lines.append("No grammar rules were applied.")
        return "\n".join(lines)

    for event in trace:
        node_count = len(event.created_node_ids)
        edge_count = len(event.created_edges)
        count_parts = []
        for created in event.sampled_parameters.get("create_nodes", []):
            alias = created.get("alias", "node")
            count = created.get("count")
            sampled_type = created.get("type")
            count_parts.append(f"{alias} count = {count}, type = {sampled_type}")
        sampled = f"; sampled {', '.join(count_parts)}" if count_parts else ""
        removed = f"; removed {len(event.removed_node_ids)} node(s)" if event.removed_node_ids else ""
        lines.append(
            f"{event.step_index}. Applied {event.rule_name} to node {event.matched_node_id}"
            f"{sampled}; created {node_count} node(s) and {edge_count} edge(s){removed}."
        )
    return "\n".join(lines)


def export_trace_summary(trace: list[RuleApplicationTraceEvent], output_path: str | Path) -> Path:
    """Write a human-readable trace summary."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summarize_trace(trace), encoding="utf-8")
    return path
