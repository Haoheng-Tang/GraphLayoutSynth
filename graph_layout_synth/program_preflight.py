"""Deterministic preflight validation of user program requirements.

Checks whether user-facing `ProgramRequirements` can be satisfied under the
active backend `GenerationConstraintProfile` and the active config vocabulary.
This is arithmetic feasibility screening, not a constraint solver, and it is
fully independent of the LLM. It must run before LLM grammar-variant proposal
and future program-conditioned generation so that infeasible programs fail
with clear errors first.

Feasibility states:

- ``feasible``: satisfiable within preferred internal bounds.
- ``feasible_with_relaxation``: satisfiable only after relaxing preferred
  internal bounds, but still within hard bounds.
- ``infeasible``: not satisfiable under hard internal bounds (or the
  requirements themselves are invalid).

Config reachability is reported separately as warnings: a program can be
feasible in principle under backend constraints while the current static
grammar config cannot reach it; that is exactly the case an LLM-proposed
config variant is for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from graph_layout_synth.config_contract import (
    ConfigContract,
    build_config_contract,
    grammar_created_node_types,
    reachable_room_count_ranges,
)
from graph_layout_synth.generation_constraint_profile import (
    GenerationConstraintProfile,
    default_constraint_profile,
)
from graph_layout_synth.program_requirements import (
    ProgramRequirements,
    ProgramRequirementsError,
    ProgramRequirementsValidationResult,
    RequirementValidationIssue,
    RoomMixRequirement,
    load_program_requirements_data,
    parse_program_requirements,
)


FALLBACK_PATIENT_TYPE = "PatientRoom"
GENERIC_CAPACITY_SUGGESTION = "Reduce {room_type} count or allow a larger layout/generation scope."


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    path: str | None = None,
    suggestion: str | None = None,
    debug_details: dict[str, Any] | None = None,
) -> RequirementValidationIssue:
    return RequirementValidationIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        path=path,
        suggestion=suggestion,
        debug_details=debug_details,
    )


def _patient_room_types(contract: ConfigContract) -> list[str]:
    configured = contract.semantic_node_groups.get("patient")
    if configured:
        return configured
    if FALLBACK_PATIENT_TYPE in contract.allowed_node_types:
        return [FALLBACK_PATIENT_TYPE]
    return []


def _vocabulary_issues(
    requirements: ProgramRequirements,
    contract: ConfigContract,
) -> list[RequirementValidationIssue]:
    issues: list[RequirementValidationIssue] = []
    allowed_types = set(contract.allowed_node_types)
    allowed_list = ", ".join(contract.allowed_node_types)
    for entry in requirements.room_mix:
        if entry.room_type not in allowed_types:
            issues.append(
                _issue(
                    "UNKNOWN_ROOM_TYPE",
                    "error",
                    f"Room type '{entry.room_type}' is not allowed by the base config vocabulary.",
                    path=f"program.roomMix.{entry.room_type}",
                    suggestion=f"Use one of the config-defined room types: {allowed_list}.",
                )
            )
    for index, preference in enumerate(requirements.adjacency_preferences):
        for key, room_type in (("source", preference.source), ("target", preference.target)):
            if room_type not in allowed_types:
                issues.append(
                    _issue(
                        "UNKNOWN_ROOM_TYPE",
                        "error",
                        f"Adjacency preference {key} room type '{room_type}' is not allowed by the base config vocabulary.",
                        path=f"adjacencyPreferences[{index}].{key}",
                        suggestion=f"Use one of the config-defined room types: {allowed_list}.",
                    )
                )
        if preference.edge_type not in contract.allowed_edge_types:
            issues.append(
                _issue(
                    "UNKNOWN_EDGE_TYPE",
                    "error",
                    f"Adjacency preference edge type '{preference.edge_type}' is not allowed by the base config vocabulary.",
                    path=f"adjacencyPreferences[{index}].edgeType",
                    suggestion=f"Use one of the config-defined edge types: {', '.join(contract.allowed_edge_types)}.",
                )
            )
    return issues


def _capacity_issues(
    entries: list[RoomMixRequirement],
    contract: ConfigContract,
    profile: GenerationConstraintProfile,
) -> list[RequirementValidationIssue]:
    """Arithmetic feasibility of patient-room counts against internal bounds."""
    issues: list[RequirementValidationIssue] = []
    patient_types = set(_patient_room_types(contract))
    group_size = profile.locality.patient_room_group_size
    group_count = profile.locality.local_group_count
    hard_capacity = group_size.hard_max * group_count.hard_max
    preferred_capacity = group_size.preferred_max * group_count.preferred_max
    minimum_production = group_size.min * group_count.min
    debug_details = {
        "patientRoomGroupSize": group_size.to_dict(),
        "localGroupCount": group_count.to_dict(),
        "preferredCapacity": preferred_capacity,
        "hardCapacity": hard_capacity,
        "minimumProduction": minimum_production,
    }

    for entry in entries:
        if entry.room_type not in patient_types:
            continue
        suggestion = GENERIC_CAPACITY_SUGGESTION.format(room_type=entry.room_type)
        path = f"program.roomMix.{entry.room_type}"
        if entry.min_count > hard_capacity:
            issues.append(
                _issue(
                    "PATIENT_ROOM_HARD_CAPACITY_EXCEEDED",
                    "error",
                    f"{entry.room_type} minimum count is {entry.min_count}, but backend hard constraints "
                    f"allow at most {hard_capacity} {entry.room_type} rooms. The system cannot generate "
                    "a valid solution under current constraints.",
                    path=path,
                    suggestion=suggestion,
                    debug_details=debug_details,
                )
            )
            continue
        if entry.max_count < minimum_production:
            issues.append(
                _issue(
                    "PATIENT_ROOM_MINIMUM_PRODUCTION_EXCEEDS_MAX",
                    "error",
                    f"{entry.room_type} maximum count is {entry.max_count}, but backend constraints always "
                    f"generate at least {minimum_production} {entry.room_type} rooms.",
                    path=path,
                    suggestion=f"Increase {entry.room_type} maximum count or allow a smaller layout/generation scope.",
                    debug_details=debug_details,
                )
            )
            continue
        if entry.target_count > hard_capacity:
            issues.append(
                _issue(
                    "PATIENT_ROOM_TARGET_ABOVE_HARD_CAPACITY",
                    "warning",
                    f"{entry.room_type} target count {entry.target_count} exceeds the backend hard capacity "
                    f"of {hard_capacity}; the target cannot be met, but the minimum count remains feasible.",
                    path=path,
                    suggestion=suggestion,
                    debug_details=debug_details,
                )
            )
        elif entry.target_count > preferred_capacity:
            issues.append(
                _issue(
                    "PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED",
                    "warning",
                    f"{entry.room_type} count {entry.target_count} requires larger local groups than preferred, "
                    "but remains feasible under hard backend bounds.",
                    path=path,
                    suggestion=suggestion,
                    debug_details=debug_details,
                )
            )
    return issues


def _corridor_issues(
    entries: list[RoomMixRequirement],
    contract: ConfigContract,
    profile: GenerationConstraintProfile,
) -> list[RequirementValidationIssue]:
    """Arithmetic corridor connection-capacity checks."""
    corridor_types = set(contract.corridor_node_types)
    corridor_entries = [entry for entry in entries if entry.room_type in corridor_types]
    room_entries = [entry for entry in entries if entry.room_type not in corridor_types]
    if not corridor_entries or not room_entries:
        return []

    degree = profile.corridors.corridor_degree
    corridor_max = sum(entry.max_count for entry in corridor_entries)
    corridor_target = sum(entry.target_count for entry in corridor_entries)
    room_min_total = sum(entry.min_count for entry in room_entries)
    room_target_total = sum(entry.target_count for entry in room_entries)
    hard_connection_capacity = corridor_max * degree.hard_max
    preferred_connection_capacity = corridor_target * degree.preferred_max
    debug_details = {
        "corridorDegree": degree.to_dict(),
        "corridorMaxTotal": corridor_max,
        "corridorTargetTotal": corridor_target,
        "roomMinTotal": room_min_total,
        "roomTargetTotal": room_target_total,
        "hardConnectionCapacity": hard_connection_capacity,
        "preferredConnectionCapacity": preferred_connection_capacity,
    }

    issues: list[RequirementValidationIssue] = []
    if room_min_total > hard_connection_capacity:
        issues.append(
            _issue(
                "CORRIDOR_CONNECTION_HARD_CAPACITY_EXCEEDED",
                "error",
                f"The requested rooms need at least {room_min_total} circulation connections, but backend hard "
                f"constraints allow at most {hard_connection_capacity} for the requested corridor counts. "
                "The system cannot generate a valid solution under current constraints.",
                path="program.roomMix",
                suggestion="Reduce room counts or allow more corridors.",
                debug_details=debug_details,
            )
        )
    elif room_target_total > preferred_connection_capacity:
        issues.append(
            _issue(
                "CORRIDOR_CONNECTION_LOAD_ABOVE_PREFERRED",
                "warning",
                f"The requested room targets need about {room_target_total} circulation connections, more than the "
                f"preferred backend capacity of {preferred_connection_capacity} for the requested corridor counts; "
                "the program remains feasible under hard backend bounds.",
                path="program.roomMix",
                suggestion="Reduce room counts or allow more corridors.",
                debug_details=debug_details,
            )
        )
    return issues


def _reachability_issues(
    entries: list[RoomMixRequirement],
    raw_config: dict[str, Any],
) -> list[RequirementValidationIssue]:
    """Warnings for programs the current static grammar config cannot reach."""
    requested_types = [entry.room_type for entry in entries]
    ranges = reachable_room_count_ranges(raw_config, requested_types)
    created_types = set(grammar_created_node_types(raw_config))

    issues: list[RequirementValidationIssue] = []
    for entry in entries:
        path = f"program.roomMix.{entry.room_type}"
        reachable = ranges.get(entry.room_type)
        if reachable is not None:
            debug_details = {"reachableRange": reachable}
            if entry.min_count > reachable["max"] or entry.max_count < reachable["min"]:
                issues.append(
                    _issue(
                        "ROOM_COUNT_NOT_REACHABLE_BY_CURRENT_CONFIG",
                        "warning",
                        f"The current grammar config can generate between {reachable['min']} and {reachable['max']} "
                        f"{entry.room_type} rooms; the requested {entry.min_count}-{entry.max_count} window is outside "
                        "that range. The program may be feasible in principle, but it needs a config variant.",
                        path=path,
                        suggestion="Propose a validated config variant with adjusted grammar-rule counts.",
                        debug_details=debug_details,
                    )
                )
            elif not reachable["min"] <= entry.target_count <= reachable["max"]:
                issues.append(
                    _issue(
                        "ROOM_TARGET_OUTSIDE_CURRENT_CONFIG_RANGE",
                        "warning",
                        f"The {entry.room_type} target count {entry.target_count} is outside the current grammar "
                        f"config's reachable range {reachable['min']}-{reachable['max']}; generation can satisfy "
                        "the min/max window but not the target without a config variant.",
                        path=path,
                        suggestion="Propose a validated config variant with adjusted grammar-rule counts.",
                        debug_details=debug_details,
                    )
                )
        elif entry.room_type not in created_types:
            issues.append(
                _issue(
                    "ROOM_TYPE_NOT_CREATED_BY_CURRENT_GRAMMAR",
                    "warning",
                    f"Room type '{entry.room_type}' exists in the config vocabulary, but the current grammar rules "
                    "never create it; the requested counts are not reachable without a config variant.",
                    path=path,
                    suggestion="Propose a validated config variant whose grammar rules create this room type.",
                )
            )
        else:
            issues.append(
                _issue(
                    "ROOM_COUNT_REACHABILITY_UNKNOWN",
                    "warning",
                    f"Room type '{entry.room_type}' is created stochastically (for example through type choices), "
                    "so a reachable count range cannot be computed for the current config.",
                    path=path,
                )
            )
    return issues


RELAXATION_WARNING_CODES = frozenset(
    {
        "PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED",
        "PATIENT_ROOM_TARGET_ABOVE_HARD_CAPACITY",
        "CORRIDOR_CONNECTION_LOAD_ABOVE_PREFERRED",
    }
)


def _build_result(issues: list[RequirementValidationIssue]) -> ProgramRequirementsValidationResult:
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    if errors:
        feasibility = "infeasible"
    elif any(warning.code in RELAXATION_WARNING_CODES for warning in warnings):
        feasibility = "feasible_with_relaxation"
    else:
        feasibility = "feasible"
    return ProgramRequirementsValidationResult(
        valid=not errors,
        feasibility=feasibility,  # type: ignore[arg-type]
        errors=errors,
        warnings=warnings,
    )


def _escalate_relaxation_issues(
    issues: list[RequirementValidationIssue],
    profile: GenerationConstraintProfile,
) -> list[RequirementValidationIssue]:
    """With relaxation disabled, exceeding preferred bounds becomes infeasible."""
    if profile.generation.max_relaxation_steps > 0:
        return issues
    escalated = []
    for issue in issues:
        if issue.severity == "warning" and issue.code in RELAXATION_WARNING_CODES:
            escalated.append(
                RequirementValidationIssue(
                    code=issue.code,
                    severity="error",
                    message=issue.message + " Internal constraint relaxation is disabled (maxRelaxationSteps=0).",
                    path=issue.path,
                    suggestion=issue.suggestion,
                    debug_details=issue.debug_details,
                )
            )
        else:
            escalated.append(issue)
    return escalated


def run_program_preflight(
    raw_requirements: Any,
    *,
    raw_config: dict[str, Any],
    profile: GenerationConstraintProfile | None = None,
) -> ProgramRequirementsValidationResult:
    """Validate raw program requirements against a raw config mapping."""
    active_profile = profile or default_constraint_profile()
    requirements, issues = parse_program_requirements(raw_requirements)
    if requirements is None:
        return _build_result(issues)

    contract = build_config_contract(raw_config)
    if contract.errors:
        issues.append(
            _issue(
                "BASE_CONFIG_INVALID",
                "error",
                "The base config is invalid, so program requirements cannot be validated against it: "
                + "; ".join(contract.errors),
            )
        )
        return _build_result(issues)

    vocabulary_issues = _vocabulary_issues(requirements, contract)
    issues.extend(vocabulary_issues)
    known_entries = [
        entry
        for entry in requirements.room_mix
        if entry.room_type in set(contract.allowed_node_types)
    ]
    issues.extend(_capacity_issues(known_entries, contract, active_profile))
    issues.extend(_corridor_issues(known_entries, contract, active_profile))
    issues.extend(_reachability_issues(known_entries, raw_config))
    issues = _escalate_relaxation_issues(issues, active_profile)
    return _build_result(issues)


def validate_program_requirements_file(
    requirements_path: str | Path,
    config_path: str | Path,
    profile: GenerationConstraintProfile | None = None,
) -> tuple[ProgramRequirements | None, ProgramRequirementsValidationResult]:
    """Validate a requirements file against a config file.

    Returns the normalized requirements (when parseable) plus the result.
    """
    raw_requirements = load_program_requirements_data(requirements_path)
    raw_config = load_raw_config_mapping(config_path)
    result = run_program_preflight(raw_requirements, raw_config=raw_config, profile=profile)
    requirements, _ = parse_program_requirements(raw_requirements)
    return requirements, result


def load_raw_config_mapping(path: str | Path) -> dict[str, Any]:
    """Read a raw YAML config mapping for preflight validation."""
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProgramRequirementsError(f"Base config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ProgramRequirementsError(f"Base config file is not valid YAML: {config_path}") from exc
    if not isinstance(data, dict) or not data:
        raise ProgramRequirementsError(f"Base config file must contain a non-empty mapping: {config_path}")
    return data


def export_program_requirements_validation_report(
    result: ProgramRequirementsValidationResult,
    path: str | Path,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a validation result (plus optional context fields) as JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = result.to_dict()
    if extra:
        data.update(extra)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
