from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from graph_layout_synth.generation_constraint_profile import (
    ConstraintProfileError,
    default_constraint_profile,
    load_constraint_profile,
    parse_constraint_profile,
)
from graph_layout_synth.program_preflight import run_program_preflight
from graph_layout_synth.program_requirements import (
    load_program_requirements_data,
    parse_program_requirements,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = PROJECT_ROOT / "configs/generic_building.yaml"
EXAMPLE_YAML_PATH = PROJECT_ROOT / "docs/program_requirements/example_healthcare_program.yaml"
EXAMPLE_JSON_PATH = PROJECT_ROOT / "docs/program_requirements/example_healthcare_program.json"


def _base_config() -> dict:
    return yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))


def _requirements(room_mix: dict | None = None, adjacency: list | None = None) -> dict:
    data: dict = {
        "schemaVersion": 1,
        "program": {
            "roomMix": room_mix
            or {"PatientRoom": {"min": 30, "target": 40, "max": 60}},
        },
    }
    if adjacency is not None:
        data["adjacencyPreferences"] = adjacency
    return data


def _error_codes(result) -> set[str]:
    return {issue.code for issue in result.errors}


def _warning_codes(result) -> set[str]:
    return {issue.code for issue in result.warnings}


def test_valid_yaml_requirements_parse() -> None:
    raw = load_program_requirements_data(EXAMPLE_YAML_PATH)

    requirements, issues = parse_program_requirements(raw)

    assert issues == []
    assert requirements is not None
    assert requirements.schema_version == 1
    by_type = requirements.room_mix_by_type()
    assert by_type["PatientRoom"].min_count == 50
    assert by_type["PatientRoom"].target_count == 56
    assert by_type["PatientRoom"].max_count == 60
    assert len(requirements.adjacency_preferences) == 3
    assert requirements.adjacency_preferences[0].priority == "required"


def test_valid_json_requirements_parse_matches_yaml() -> None:
    yaml_requirements, _ = parse_program_requirements(load_program_requirements_data(EXAMPLE_YAML_PATH))
    json_requirements, issues = parse_program_requirements(load_program_requirements_data(EXAMPLE_JSON_PATH))

    assert issues == []
    assert json_requirements is not None
    assert json_requirements.to_dict() == yaml_requirements.to_dict()


def test_invalid_schema_version_fails() -> None:
    data = _requirements()
    data["schemaVersion"] = 2

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "INVALID_SCHEMA_VERSION" for issue in issues)


def test_min_target_max_inconsistency_fails() -> None:
    data = _requirements(room_mix={"PatientRoom": {"min": 5, "target": 4, "max": 6}})

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "INCONSISTENT_COUNT_WINDOW" for issue in issues)


def test_negative_counts_fail() -> None:
    data = _requirements(room_mix={"PatientRoom": {"min": -1, "target": 2, "max": 3}})

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "NEGATIVE_COUNT" for issue in issues)


def test_area_width_height_are_not_accepted() -> None:
    data = _requirements(
        room_mix={"PatientRoom": {"min": 1, "target": 2, "max": 3, "area": 120}}
    )

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    unsupported = [issue for issue in issues if issue.code == "UNSUPPORTED_FIELD"]
    assert unsupported
    assert "area" in unsupported[0].message

    for field in ("width", "height"):
        data = _requirements(
            room_mix={"PatientRoom": {"min": 1, "target": 2, "max": 3, field: 10}}
        )
        requirements, issues = parse_program_requirements(data)
        assert requirements is None
        assert any(issue.code == "UNSUPPORTED_FIELD" for issue in issues)


def test_invalid_adjacency_edge_type_fails() -> None:
    data = _requirements(
        adjacency=[
            {"source": "PatientRoom", "target": "Corridor", "edgeType": "window", "priority": "required"}
        ]
    )

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "INVALID_ADJACENCY_EDGE_TYPE" for issue in issues)


def test_invalid_adjacency_priority_fails() -> None:
    data = _requirements(
        adjacency=[
            {"source": "PatientRoom", "target": "Corridor", "edgeType": "door", "priority": "mandatory"}
        ]
    )

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "INVALID_ADJACENCY_PRIORITY" for issue in issues)


def test_conflicting_adjacency_preferences_fail() -> None:
    data = _requirements(
        adjacency=[
            {"source": "PatientRoom", "target": "Corridor", "edgeType": "door", "priority": "required"},
            {"source": "Corridor", "target": "PatientRoom", "edgeType": "door", "priority": "avoid"},
        ]
    )

    requirements, issues = parse_program_requirements(data)

    assert requirements is None
    assert any(issue.code == "CONFLICTING_ADJACENCY_PREFERENCES" for issue in issues)


def test_unknown_room_type_fails_against_config_vocabulary() -> None:
    data = _requirements(room_mix={"Ballroom": {"min": 1, "target": 2, "max": 3}})

    result = run_program_preflight(data, raw_config=_base_config())

    assert result.valid is False
    assert result.feasibility == "infeasible"
    assert "UNKNOWN_ROOM_TYPE" in _error_codes(result)


def test_unknown_adjacency_room_type_fails_against_config_vocabulary() -> None:
    data = _requirements(
        adjacency=[
            {"source": "Ballroom", "target": "Corridor", "edgeType": "door", "priority": "preferred"}
        ]
    )

    result = run_program_preflight(data, raw_config=_base_config())

    assert result.valid is False
    assert "UNKNOWN_ROOM_TYPE" in _error_codes(result)


def test_feasible_within_preferred_internal_bounds() -> None:
    result = run_program_preflight(_requirements(), raw_config=_base_config())

    assert result.valid is True
    assert result.feasibility == "feasible"
    assert result.errors == []
    assert result.warnings == []


def test_feasible_only_after_relaxing_preferred_bounds() -> None:
    profile = parse_constraint_profile(
        {
            "locality": {
                "patientRoomGroupSize": {"min": 4, "preferredMax": 4, "hardMax": 12},
                "localGroupCount": {"min": 1, "preferredMax": 2, "hardMax": 20},
            }
        }
    )

    result = run_program_preflight(_requirements(), raw_config=_base_config(), profile=profile)

    assert result.valid is True
    assert result.feasibility == "feasible_with_relaxation"
    assert "PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED" in _warning_codes(result)


def test_infeasible_patient_room_count_under_hard_local_group_capacity() -> None:
    profile = parse_constraint_profile(
        {
            "locality": {
                "patientRoomGroupSize": {"min": 4, "preferredMax": 7, "hardMax": 7},
                "localGroupCount": {"min": 1, "preferredMax": 4, "hardMax": 4},
            }
        }
    )
    data = _requirements(room_mix={"PatientRoom": {"min": 51, "target": 56, "max": 60}})

    result = run_program_preflight(data, raw_config=_base_config(), profile=profile)

    assert result.valid is False
    assert result.feasibility == "infeasible"
    assert "PATIENT_ROOM_HARD_CAPACITY_EXCEEDED" in _error_codes(result)
    error = result.errors[0]
    assert "minimum count is 51" in error.message
    assert "at most 28" in error.message
    assert error.suggestion == "Reduce PatientRoom count or allow a larger layout/generation scope."
    assert "cluster" not in error.message.lower()
    assert error.debug_details["hardCapacity"] == 28


def test_disabled_relaxation_escalates_preferred_bound_warnings() -> None:
    profile = parse_constraint_profile(
        {
            "locality": {
                "patientRoomGroupSize": {"min": 4, "preferredMax": 4, "hardMax": 12},
                "localGroupCount": {"min": 1, "preferredMax": 2, "hardMax": 20},
            },
            "generation": {"maxRelaxationSteps": 0},
        }
    )

    result = run_program_preflight(_requirements(), raw_config=_base_config(), profile=profile)

    assert result.valid is False
    assert result.feasibility == "infeasible"
    assert "PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED" in _error_codes(result)


def test_corridor_connection_hard_capacity_is_checked() -> None:
    profile = parse_constraint_profile(
        {"corridors": {"corridorDegree": {"preferredMax": 2, "hardMax": 2}}}
    )
    data = _requirements(
        room_mix={
            "PatientRoom": {"min": 10, "target": 10, "max": 12},
            "Corridor": {"min": 1, "target": 1, "max": 1},
        }
    )

    result = run_program_preflight(data, raw_config=_base_config(), profile=profile)

    assert result.valid is False
    assert "CORRIDOR_CONNECTION_HARD_CAPACITY_EXCEEDED" in _error_codes(result)


def test_reachable_range_warning_against_current_config() -> None:
    data = _requirements(room_mix={"PatientRoom": {"min": 5, "target": 5, "max": 5}})

    result = run_program_preflight(data, raw_config=_base_config())

    assert result.valid is True
    assert result.feasibility == "feasible"
    assert "ROOM_COUNT_NOT_REACHABLE_BY_CURRENT_CONFIG" in _warning_codes(result)


def test_room_type_not_created_by_grammar_warns() -> None:
    data = _requirements(room_mix={"BuildingFloor": {"min": 1, "target": 1, "max": 1}})

    result = run_program_preflight(data, raw_config=_base_config())

    assert result.valid is True
    assert "ROOM_TYPE_NOT_CREATED_BY_CURRENT_GRAMMAR" in _warning_codes(result)


def test_default_constraint_profile_round_trips() -> None:
    profile = default_constraint_profile()

    assert parse_constraint_profile(profile.to_dict()) == profile


def test_repository_default_profile_file_matches_builtin_defaults() -> None:
    profile = load_constraint_profile(
        PROJECT_ROOT / "configs/program_constraint_profiles/default_healthcare.yaml"
    )

    assert profile == default_constraint_profile()


def test_partial_constraint_profile_overlays_defaults() -> None:
    profile = parse_constraint_profile(
        {"locality": {"patientRoomGroupSize": {"min": 2, "preferredMax": 6, "hardMax": 10}}}
    )

    assert profile.locality.patient_room_group_size.hard_max == 10
    assert profile.locality.local_group_count == default_constraint_profile().locality.local_group_count
    assert profile.corridors == default_constraint_profile().corridors


def test_constraint_profile_rejects_unknown_fields() -> None:
    with pytest.raises(ConstraintProfileError, match="unsupported field"):
        parse_constraint_profile({"clusters": {"count": 4}})


def test_constraint_profile_rejects_inconsistent_bounds() -> None:
    with pytest.raises(ConstraintProfileError, match="min <= preferredMax <= hardMax"):
        parse_constraint_profile(
            {"locality": {"patientRoomGroupSize": {"min": 4, "preferredMax": 12, "hardMax": 8}}}
        )
