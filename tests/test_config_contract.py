import pytest
import yaml

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, ConfigError, validate_config
from graph_layout_synth.config_contract import build_config_contract
from graph_layout_synth.config_validator import validate_config_file
from graph_layout_synth.grammar_variant_assistant import build_grammar_variant_prompt


def _minimal_config():
    return {
        "project": {"name": "Contract test", "building_type": "GenericBuilding"},
        "random_seed_default": 42,
        "generation": {"num_candidates": 1},
        "allowed_node_types": ["BuildingFloor", "Zone", "Corridor", "Suite"],
        "allowed_edge_types": ["door"],
        "zone_types": ["public"],
        "room_type_counts": {"Suite": 1},
        "semantic_node_groups": {
            "room_like": ["Suite"],
            "corridor": ["Corridor"],
            "support": [],
            "patient": ["Suite"],
        },
        "typed_accessibility_pairs": [],
        "stochastic": {
            "min_zone_count": 1,
            "max_zone_count": 1,
            "min_cluster_size": 1,
            "max_cluster_size": 1,
            "corridor_pattern_choices": ["linear"],
            "support_room_choices": ["Suite"],
        },
        "validation": {
            "require_connected_graph": True,
            "require_corridor_access": True,
            "allow_abstract_nodes_final": False,
        },
        "ranking": {"weights": {}, "targets": {}},
        "visualization": {"node_colors": {}, "unknown_node_color": "#c7c7c7"},
        "grammar_rules": [
            {
                "name": "expand_zone",
                "match": {"type": "Zone", "is_abstract": True},
                "action": {
                    "create_nodes": [
                        {"alias": "room", "type": "Suite", "count": 1, "attributes": {"is_abstract": False}},
                    ],
                    "create_edges": [],
                },
            }
        ],
    }


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_config_contract_reflects_room_mix_targets():
    config = _minimal_config()
    config["room_mix_targets"] = {
        "enabled": True,
        "patient_alias": "suite",
        "clinical_alias": "care",
        "staff_alias": "staff",
        "patient_total_min": 4,
        "patient_total_max": 8,
        "clinical_ratio": 0.5,
        "staff_ratio": 0.25,
        "ratio_tolerance": 0.1,
        "expected_room_type_counts": {"Suite": 6},
    }

    contract = build_config_contract(config)

    assert contract.room_mix_targets["patient_total_min"] == 4
    assert contract.room_mix_targets["clinical_ratio"] == 0.5
    assert contract.semantic_node_groups["patient"] == ["Suite"]
    assert contract.room_mix_reachable_ranges["Suite"] == {"min": 1, "max": 1}


def test_new_allowed_node_type_allows_grammar_rule_use():
    config = _minimal_config()
    config["allowed_node_types"].append("ExamSupport")
    config["room_type_counts"]["ExamSupport"] = 1
    config["semantic_node_groups"]["support"] = ["ExamSupport"]
    config["stochastic"]["support_room_choices"] = ["ExamSupport"]
    config["grammar_rules"][0]["action"]["create_nodes"][0]["type"] = "ExamSupport"

    validated = validate_config(config)

    assert validated.grammar_rules[0]["action"]["create_nodes"][0]["type"] == "ExamSupport"


def test_unknown_node_type_fails_contract_backed_rule_validation():
    config = _minimal_config()
    config["grammar_rules"][0]["action"]["create_nodes"][0]["type"] = "UnknownRoom"

    with pytest.raises(ConfigError, match="unknown value"):
        validate_config(config)


def test_new_allowed_edge_type_allows_grammar_rule_use():
    config = _minimal_config()
    config["allowed_edge_types"].append("service_path")
    config["grammar_rules"][0]["action"]["create_nodes"].append(
        {"alias": "corridor", "type": "Corridor", "count": 1, "attributes": {"is_abstract": False}}
    )
    config["grammar_rules"][0]["action"]["create_edges"].append(
        {"source": "room", "target": "corridor", "edge_type": "service_path"}
    )

    validate_config(config)


def test_unknown_edge_type_fails_contract_backed_rule_validation():
    config = _minimal_config()
    config["grammar_rules"][0]["action"]["create_nodes"].append(
        {"alias": "corridor", "type": "Corridor", "count": 1, "attributes": {"is_abstract": False}}
    )
    config["grammar_rules"][0]["action"]["create_edges"].append(
        {"source": "room", "target": "corridor", "edge_type": "service_path"}
    )

    with pytest.raises(ConfigError, match="unknown edge type"):
        validate_config(config)


def test_typed_accessibility_pairs_validate_against_allowed_node_types(tmp_path):
    config = _minimal_config()
    config["typed_accessibility_pairs"] = [
        {"source_type": "Suite", "target_type": "UnknownSupport", "edge_type": "door"}
    ]
    config_path = _write_yaml(tmp_path / "bad_access.yaml", config)

    report = validate_config_file(config_path)

    assert not report.is_valid
    assert "typed_accessibility_pairs[0].target_type" in report.errors[0]
    assert report.contract_summary["allowed_node_types"] == config["allowed_node_types"]


def test_prompt_includes_live_contract_and_not_old_room_names_for_new_vocabulary():
    config = _minimal_config()
    config["allowed_node_types"].append("CarePod")
    config["room_type_counts"]["CarePod"] = 1
    config["semantic_node_groups"]["support"] = ["CarePod"]
    config["semantic_node_groups"]["clinical_support"] = ["CarePod"]
    config["typed_accessibility_pairs"] = [
        {"source_type": "Suite", "target_type": "CarePod", "edge_type": "door"}
    ]

    prompt = build_grammar_variant_prompt(config, "Generic grammar syntax guidance.")

    assert "Live Config Contract" in prompt
    assert "Suite" in prompt
    assert "CarePod" in prompt
    assert "door" in prompt
    assert "PatientRoom" not in prompt
    assert "ClinicalSupport" not in prompt


def test_default_config_validation_report_includes_contract_summary():
    report = validate_config_file(DEFAULT_CONFIG_PATH)

    assert report.is_valid
    assert report.contract_summary["allowed_node_types"]
    assert report.contract_summary["allowed_edge_types"]
    assert report.contract_summary["room_mix_reachable_ranges"]["ClinicalSupport"] == {"min": 9, "max": 15}


def test_room_mix_expected_count_outside_reachable_range_fails_validation(tmp_path):
    config = _minimal_config()
    config["room_mix_targets"] = {
        "enabled": True,
        "patient_alias": "suite",
        "clinical_alias": "care",
        "staff_alias": "staff",
        "patient_total_min": 1,
        "patient_total_max": 1,
        "clinical_ratio": 0.0,
        "staff_ratio": 0.0,
        "ratio_tolerance": 0.0,
        "expected_room_type_counts": {"Suite": 6},
    }
    config_path = _write_yaml(tmp_path / "bad_room_mix.yaml", config)

    report = validate_config_file(config_path)

    assert not report.is_valid
    assert "outside reachable grammar range 1-1" in report.errors[0]
    assert report.contract_summary["room_mix_reachable_ranges"]["Suite"] == {"min": 1, "max": 1}
