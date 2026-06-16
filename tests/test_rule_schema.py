from random import Random

import pytest

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config, validate_config
from graph_layout_synth.generator import generate_candidate
from graph_layout_synth.rule_schema import (
    RuleSchemaError,
    load_grammar_rules,
    sample_choice,
    sample_count,
    validate_grammar_rule,
)


def test_valid_grammar_rule_parsing():
    config = load_config(DEFAULT_CONFIG_PATH)

    assert len(config.grammar_rules) >= 1
    assert config.grammar_rules[0]["name"] == "expand_floor_to_zones"


def test_invalid_grammar_rule_raises_clear_error():
    with pytest.raises(RuleSchemaError, match="missing rule name"):
        validate_grammar_rule({"match": {"type": "Zone"}, "action": {}})


def test_count_sampling_fixed_and_min_max_counts():
    rng = Random(123)

    assert sample_count(3, rng) == 3
    sampled = sample_count({"min": 2, "max": 4}, rng)
    assert 2 <= sampled <= 4


def test_unknown_count_format_raises_clear_error():
    with pytest.raises(RuleSchemaError, match="Unknown count format"):
        sample_count({"around": 3}, Random(123))


def test_choice_sampling_from_list():
    sampled = sample_choice({"choices": ["PatientRoom", "ClinicalSupport"]}, Random(123))

    assert sampled in {"PatientRoom", "ClinicalSupport"}


def test_generation_uses_config_defined_rules():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)

    zone_types = {
        attrs.get("zone_type")
        for _, attrs in result.graph.nodes(data=True)
        if attrs.get("type") == "Zone"
    }
    assert {"PublicZone", "PrivateZone", "ServiceZone"}.issubset(zone_types)
    assert result.is_valid


def test_generation_with_config_rules_is_deterministic():
    config = load_config(DEFAULT_CONFIG_PATH)
    first = generate_candidate(seed=42, config=config).graph
    second = generate_candidate(seed=42, config=config).graph

    assert list(first.nodes(data=True)) == list(second.nodes(data=True))
    assert list(first.edges(data=True)) == list(second.edges(data=True))


def test_load_grammar_rules_requires_list():
    with pytest.raises(RuleSchemaError, match="grammar_rules"):
        load_grammar_rules({"grammar_rules": {"name": "bad"}})


def test_invalid_config_rule_raises_config_error():
    with pytest.raises(Exception, match="missing match section"):
        validate_config(
            {
                "project": {"name": "x", "building_type": "x"},
                "generation": {"num_candidates": 1},
                "allowed_node_types": ["BuildingFloor", "Zone", "Corridor", "PatientRoom"],
                "allowed_edge_types": ["door", "wall"],
                "zone_types": ["public"],
                "room_type_counts": {"PatientRoom": 1},
                "stochastic": {
                    "min_zone_count": 1,
                    "max_zone_count": 1,
                    "min_cluster_size": 1,
                    "max_cluster_size": 1,
                    "corridor_pattern_choices": ["linear"],
                    "support_room_choices": ["PatientRoom"],
                },
                "validation": {
                    "require_connected_graph": True,
                    "require_corridor_access": True,
                    "allow_abstract_nodes_final": False,
                },
                "grammar_rules": [{"name": "bad", "action": {}}],
            }
        )
