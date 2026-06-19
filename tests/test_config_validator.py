import json

import pytest
import yaml

from graph_layout_synth.cli import main
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.config_validator import validate_config_file


def _minimal_config(grammar_rules=None):
    return {
        "project": {"name": "Test config", "building_type": "GenericBuilding"},
        "random_seed_default": 42,
        "generation": {"num_candidates": 1},
        "allowed_node_types": [
            "BuildingFloor",
            "Zone",
            "Corridor",
            "PatientRoom",
            "ClinicalSupport",
            "StaffSupport",
        ],
        "allowed_edge_types": ["door", "wall"],
        "zone_types": ["public"],
        "room_type_counts": {"PatientRoom": 1},
        "stochastic": {
            "min_zone_count": 1,
            "max_zone_count": 1,
            "min_cluster_size": 1,
            "max_cluster_size": 1,
            "corridor_pattern_choices": ["linear"],
            "support_room_choices": ["ClinicalSupport"],
        },
        "validation": {
            "require_connected_graph": True,
            "require_corridor_access": True,
            "allow_abstract_nodes_final": False,
        },
        "ranking": {"weights": {}, "targets": {}},
        "visualization": {"node_colors": {}, "unknown_node_color": "#c7c7c7"},
        "grammar_rules": grammar_rules if grammar_rules is not None else [],
    }


def _valid_rule():
    return {
        "name": "expand_zone_to_rooms",
        "match": {"type": "Zone", "is_abstract": True},
        "action": {
            "remove_matched_node": True,
            "create_nodes": [
                {
                    "alias": "corridor",
                    "type": "Corridor",
                    "count": 1,
                    "attributes": {"is_abstract": False},
                },
                {
                    "alias": "room",
                    "type": {"choices": ["PatientRoom", "ClinicalSupport"]},
                    "count": {"min": 1, "max": 2},
                    "attributes": {"is_abstract": False},
                },
            ],
            "create_edges": [
                {
                    "source": "room",
                    "target": "corridor",
                    "edge_type": "door",
                    "mode": "each_to_one",
                }
            ],
        },
    }


def _write_config(path, config):
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _report_for_config(tmp_path, config):
    return validate_config_file(_write_config(tmp_path / "config.yaml", config))


def test_default_config_validates_successfully():
    report = validate_config_file(DEFAULT_CONFIG_PATH)

    assert report.is_valid
    assert report.errors == []


def test_minimal_valid_config_validates_successfully(tmp_path):
    report = _report_for_config(tmp_path, _minimal_config([_valid_rule()]))

    assert report.is_valid


def test_invalid_yaml_fails_clearly(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("project: [", encoding="utf-8")

    report = validate_config_file(path)

    assert not report.is_valid
    assert "not valid YAML" in report.errors[0]


def test_missing_config_file_fails_clearly(tmp_path):
    report = validate_config_file(tmp_path / "missing.yaml")

    assert not report.is_valid
    assert "not found" in report.errors[0]


def test_missing_grammar_rule_name_fails(tmp_path):
    rule = _valid_rule()
    del rule["name"]

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "name" in report.errors[0]
    assert "missing rule name" in report.errors[0]


@pytest.mark.parametrize("missing_key", ["match", "action"])
def test_missing_match_or_action_fails(tmp_path, missing_key):
    rule = _valid_rule()
    del rule[missing_key]

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert missing_key in report.errors[0]


def test_invalid_create_nodes_entry_fails(tmp_path):
    rule = _valid_rule()
    del rule["action"]["create_nodes"][0]["type"]

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "create_nodes[0].type" in report.errors[0]


def test_invalid_create_edges_entry_fails(tmp_path):
    rule = _valid_rule()
    del rule["action"]["create_edges"][0]["target"]

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "create_edges[0].target" in report.errors[0]


def test_invalid_count_format_fails(tmp_path):
    rule = _valid_rule()
    rule["action"]["create_nodes"][1]["count"] = {"around": 3}

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "count" in report.errors[0]


def test_min_greater_than_max_count_fails(tmp_path):
    rule = _valid_rule()
    rule["action"]["create_nodes"][1]["count"] = {"min": 3, "max": 2}

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "min must be less than or equal to max" in report.errors[0]


def test_unknown_edge_type_fails_when_allowed_edge_types_defined(tmp_path):
    rule = _valid_rule()
    rule["action"]["create_edges"][0]["edge_type"] = "adjacency"

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "unknown edge type 'adjacency'" in report.errors[0]


def test_unknown_node_type_fails_when_allowed_node_types_defined(tmp_path):
    rule = _valid_rule()
    rule["action"]["create_nodes"][1]["type"] = "WaitingRoom"

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "unknown value(s): WaitingRoom" in report.errors[0]


def test_alias_reference_error_fails(tmp_path):
    rule = _valid_rule()
    rule["action"]["create_edges"][0]["source"] = "patient_room"

    report = _report_for_config(tmp_path, _minimal_config([rule]))

    assert not report.is_valid
    assert "unknown alias 'patient_room'" in report.errors[0]


def test_validate_config_cli_succeeds_and_writes_report(tmp_path):
    report_path = tmp_path / "validation_report.json"

    main(
        [
            "validate-config",
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--output",
            str(report_path),
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["is_valid"]
    assert report["errors"] == []


def test_validate_config_cli_fails_on_invalid_config(tmp_path):
    config_path = _write_config(tmp_path / "bad_config.yaml", _minimal_config([{"name": "bad", "action": {}}]))

    with pytest.raises(SystemExit) as exc:
        main(["validate-config", "--config", str(config_path)])

    assert exc.value.code == 1
