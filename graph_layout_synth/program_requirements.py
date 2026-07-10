"""User-facing program requirements: schema, loaders, and local validation.

`ProgramRequirements` captures real design/program decisions only: room types,
min/target/max room counts, and optional high-level adjacency preferences.
Procedural search/generation parameters (local group sizes, corridor hub
limits, relaxation limits) are internal and live in
`generation_constraint_profile.py`; they must not appear in this schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


PROGRAM_REQUIREMENTS_SCHEMA_VERSION = 1
TOP_LEVEL_KEYS = {"schemaVersion", "program", "adjacencyPreferences"}
PROGRAM_KEYS = {"roomMix"}
ROOM_MIX_ENTRY_KEYS = {"min", "target", "max"}
ADJACENCY_PREFERENCE_KEYS = {"source", "target", "edgeType", "priority"}
ALLOWED_ADJACENCY_EDGE_TYPES = ("door", "wall")
ALLOWED_ADJACENCY_PRIORITIES = ("required", "preferred", "avoid")


class ProgramRequirementsError(RuntimeError):
    """Raised when program requirements cannot be read at all."""


@dataclass(frozen=True)
class RequirementValidationIssue:
    """One structured error or warning from program-requirements validation."""

    code: str
    severity: Literal["error", "warning"]
    message: str
    path: str | None = None
    suggestion: str | None = None
    debug_details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            data["path"] = self.path
        if self.suggestion is not None:
            data["suggestion"] = self.suggestion
        if self.debug_details is not None:
            data["debugDetails"] = self.debug_details
        return data


@dataclass(frozen=True)
class ProgramRequirementsValidationResult:
    """Structured outcome of the deterministic program-requirements preflight."""

    valid: bool
    feasibility: Literal["feasible", "feasible_with_relaxation", "infeasible"]
    errors: list[RequirementValidationIssue] = field(default_factory=list)
    warnings: list[RequirementValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "feasibility": self.feasibility,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class RoomMixRequirement:
    """User-facing min/target/max count window for one room type."""

    room_type: str
    min_count: int
    target_count: int
    max_count: int


@dataclass(frozen=True)
class AdjacencyPreference:
    """One high-level user-facing adjacency preference between room types."""

    source: str
    target: str
    edge_type: str
    priority: str


@dataclass(frozen=True)
class ProgramRequirements:
    """Canonical user-facing program input for validation and future generation."""

    schema_version: int
    room_mix: list[RoomMixRequirement]
    adjacency_preferences: list[AdjacencyPreference] = field(default_factory=list)

    def room_mix_by_type(self) -> dict[str, RoomMixRequirement]:
        return {entry.room_type: entry for entry in self.room_mix}

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized user-facing camel-case shape."""
        data: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "program": {
                "roomMix": {
                    entry.room_type: {
                        "min": entry.min_count,
                        "target": entry.target_count,
                        "max": entry.max_count,
                    }
                    for entry in self.room_mix
                }
            },
        }
        if self.adjacency_preferences:
            data["adjacencyPreferences"] = [
                {
                    "source": preference.source,
                    "target": preference.target,
                    "edgeType": preference.edge_type,
                    "priority": preference.priority,
                }
                for preference in self.adjacency_preferences
            ]
        return data


def _error(code: str, message: str, path: str | None = None, suggestion: str | None = None) -> RequirementValidationIssue:
    return RequirementValidationIssue(code=code, severity="error", message=message, path=path, suggestion=suggestion)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unknown_keys(data: dict[str, Any], allowed: set[str]) -> list[str]:
    return sorted(str(key) for key in data if key not in allowed)


def _parse_room_mix_entry(
    room_type: Any,
    entry: Any,
    issues: list[RequirementValidationIssue],
) -> RoomMixRequirement | None:
    if not isinstance(room_type, str) or not room_type.strip():
        issues.append(
            _error(
                "INVALID_ROOM_TYPE_NAME",
                "Room type names in program.roomMix must be non-empty strings.",
                path="program.roomMix",
            )
        )
        return None
    path = f"program.roomMix.{room_type}"
    if not isinstance(entry, dict):
        issues.append(_error("INVALID_ROOM_MIX_ENTRY", f"Room mix entry for {room_type} must be a mapping with min, target, and max.", path=path))
        return None
    unknown = _unknown_keys(entry, ROOM_MIX_ENTRY_KEYS)
    if unknown:
        issues.append(
            _error(
                "UNSUPPORTED_FIELD",
                f"Room mix entry for {room_type} has unsupported field(s): {', '.join(unknown)}. "
                "V1 room mix entries accept only min, target, and max.",
                path=path,
            )
        )
        return None

    counts: dict[str, int] = {}
    for key in ("min", "target", "max"):
        value = entry.get(key)
        if not _is_count(value):
            issues.append(_error("INVALID_COUNT", f"Room mix field '{key}' for {room_type} must be an integer.", path=f"{path}.{key}"))
            return None
        if value < 0:
            issues.append(_error("NEGATIVE_COUNT", f"Room mix field '{key}' for {room_type} must be non-negative.", path=f"{path}.{key}"))
            return None
        counts[key] = value
    if not counts["min"] <= counts["target"] <= counts["max"]:
        issues.append(
            _error(
                "INCONSISTENT_COUNT_WINDOW",
                f"Room mix counts for {room_type} must satisfy min <= target <= max; "
                f"got min={counts['min']}, target={counts['target']}, max={counts['max']}.",
                path=path,
            )
        )
        return None
    return RoomMixRequirement(
        room_type=room_type,
        min_count=counts["min"],
        target_count=counts["target"],
        max_count=counts["max"],
    )


def _parse_adjacency_preference(
    entry: Any,
    index: int,
    issues: list[RequirementValidationIssue],
) -> AdjacencyPreference | None:
    path = f"adjacencyPreferences[{index}]"
    if not isinstance(entry, dict):
        issues.append(_error("INVALID_ADJACENCY_PREFERENCE", "Each adjacency preference must be a mapping.", path=path))
        return None
    unknown = _unknown_keys(entry, ADJACENCY_PREFERENCE_KEYS)
    if unknown:
        issues.append(
            _error(
                "UNSUPPORTED_FIELD",
                f"Adjacency preference has unsupported field(s): {', '.join(unknown)}. "
                "V1 adjacency preferences accept only source, target, edgeType, and priority.",
                path=path,
            )
        )
        return None

    parsed: dict[str, str] = {}
    for key in ("source", "target"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(_error("INVALID_ADJACENCY_ROOM_TYPE", f"Adjacency preference field '{key}' must be a non-empty room type name.", path=f"{path}.{key}"))
            return None
        parsed[key] = value
    edge_type = entry.get("edgeType")
    if edge_type not in ALLOWED_ADJACENCY_EDGE_TYPES:
        issues.append(
            _error(
                "INVALID_ADJACENCY_EDGE_TYPE",
                f"Adjacency preference field 'edgeType' must be one of: {', '.join(ALLOWED_ADJACENCY_EDGE_TYPES)}.",
                path=f"{path}.edgeType",
            )
        )
        return None
    priority = entry.get("priority")
    if priority not in ALLOWED_ADJACENCY_PRIORITIES:
        issues.append(
            _error(
                "INVALID_ADJACENCY_PRIORITY",
                f"Adjacency preference field 'priority' must be one of: {', '.join(ALLOWED_ADJACENCY_PRIORITIES)}.",
                path=f"{path}.priority",
            )
        )
        return None
    return AdjacencyPreference(
        source=parsed["source"],
        target=parsed["target"],
        edge_type=edge_type,
        priority=priority,
    )


def parse_program_requirements(
    data: Any,
) -> tuple[ProgramRequirements | None, list[RequirementValidationIssue]]:
    """Parse raw requirements data, collecting local field-validation issues.

    Returns the normalized model when the input is structurally valid, or
    ``None`` plus every local error found. Vocabulary and feasibility checks
    against a config and constraint profile happen in `program_preflight`.
    """
    issues: list[RequirementValidationIssue] = []
    if not isinstance(data, dict) or not data:
        issues.append(_error("INVALID_REQUIREMENTS_DOCUMENT", "Program requirements must be a non-empty mapping."))
        return None, issues

    unknown = _unknown_keys(data, TOP_LEVEL_KEYS)
    if unknown:
        issues.append(
            _error(
                "UNSUPPORTED_FIELD",
                f"Program requirements have unsupported top-level field(s): {', '.join(unknown)}. "
                "V1 accepts only schemaVersion, program, and adjacencyPreferences.",
            )
        )

    schema_version = data.get("schemaVersion")
    if schema_version != PROGRAM_REQUIREMENTS_SCHEMA_VERSION:
        issues.append(
            _error(
                "INVALID_SCHEMA_VERSION",
                f"Program requirements field 'schemaVersion' must be {PROGRAM_REQUIREMENTS_SCHEMA_VERSION}.",
                path="schemaVersion",
            )
        )

    program = data.get("program")
    room_mix_entries: list[RoomMixRequirement] = []
    if not isinstance(program, dict):
        issues.append(_error("MISSING_PROGRAM", "Program requirements must contain a 'program' mapping.", path="program"))
    else:
        unknown_program = _unknown_keys(program, PROGRAM_KEYS)
        if unknown_program:
            issues.append(
                _error(
                    "UNSUPPORTED_FIELD",
                    f"'program' has unsupported field(s): {', '.join(unknown_program)}. V1 accepts only program.roomMix.",
                    path="program",
                )
            )
        room_mix = program.get("roomMix")
        if not isinstance(room_mix, dict) or not room_mix:
            issues.append(
                _error(
                    "MISSING_ROOM_MIX",
                    "Program requirements must contain a non-empty 'program.roomMix' mapping of room types to count windows.",
                    path="program.roomMix",
                )
            )
        else:
            for room_type, entry in room_mix.items():
                parsed = _parse_room_mix_entry(room_type, entry, issues)
                if parsed is not None:
                    room_mix_entries.append(parsed)

    adjacency_preferences: list[AdjacencyPreference] = []
    raw_preferences = data.get("adjacencyPreferences", [])
    if raw_preferences is None:
        raw_preferences = []
    if not isinstance(raw_preferences, list):
        issues.append(_error("INVALID_ADJACENCY_PREFERENCES", "'adjacencyPreferences' must be a list.", path="adjacencyPreferences"))
    else:
        for index, entry in enumerate(raw_preferences):
            parsed_preference = _parse_adjacency_preference(entry, index, issues)
            if parsed_preference is not None:
                adjacency_preferences.append(parsed_preference)

    _append_conflicting_preference_issues(adjacency_preferences, issues)

    if any(issue.severity == "error" for issue in issues):
        return None, issues
    return (
        ProgramRequirements(
            schema_version=PROGRAM_REQUIREMENTS_SCHEMA_VERSION,
            room_mix=room_mix_entries,
            adjacency_preferences=adjacency_preferences,
        ),
        issues,
    )


def _append_conflicting_preference_issues(
    preferences: list[AdjacencyPreference],
    issues: list[RequirementValidationIssue],
) -> None:
    priorities_by_relation: dict[tuple[str, str, str], set[str]] = {}
    for preference in preferences:
        key = tuple(sorted((preference.source, preference.target))) + (preference.edge_type,)
        priorities_by_relation.setdefault(key, set()).add(preference.priority)
    for (source, target, edge_type), priorities in priorities_by_relation.items():
        if "avoid" in priorities and priorities & {"required", "preferred"}:
            issues.append(
                _error(
                    "CONFLICTING_ADJACENCY_PREFERENCES",
                    f"Adjacency preferences for {source}-{target} via {edge_type} both request and avoid the same relation.",
                    path="adjacencyPreferences",
                )
            )


def load_program_requirements_data(path: str | Path) -> Any:
    """Read raw program requirements from a YAML or JSON file."""
    requirements_path = Path(path)
    try:
        text = requirements_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProgramRequirementsError(f"Program requirements file not found: {requirements_path}") from exc
    if requirements_path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProgramRequirementsError(f"Program requirements file is not valid JSON: {requirements_path}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProgramRequirementsError(f"Program requirements file is not valid YAML: {requirements_path}") from exc


def program_requirements_to_design_intent(requirements: ProgramRequirements) -> str:
    """Render validated requirements as deterministic design-intent text."""
    lines = ["Validated user program requirements (roomMix counts):"]
    for entry in requirements.room_mix:
        lines.append(
            f"- {entry.room_type}: min {entry.min_count}, target {entry.target_count}, max {entry.max_count}"
        )
    if requirements.adjacency_preferences:
        lines.append("Adjacency preferences:")
        for preference in requirements.adjacency_preferences:
            lines.append(
                f"- {preference.source} -> {preference.target} via {preference.edge_type} ({preference.priority})"
            )
    return "\n".join(lines)
