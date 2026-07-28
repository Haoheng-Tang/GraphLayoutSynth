from collections import Counter
from random import Random

import networkx as nx
import pytest
import yaml

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config, validate_config
from graph_layout_synth.generator import generate_candidate
from graph_layout_synth.rule_schema import (
    RuleSchemaError,
    apply_grammar_rule,
    load_grammar_rules,
    sample_choice,
    sample_count,
    validate_grammar_rule,
)


def _balanced_rule(
    source_count: int,
    target_count: int,
    max_sources_per_target: int,
) -> dict:
    """One rule creating patient sources and clinical targets, wired balanced."""
    return {
        "name": "assign_patients_to_clinical",
        "match": {"type": "Zone"},
        "action": {
            "create_nodes": [
                {
                    "alias": "patient",
                    "type": "PatientRoom",
                    "count": source_count,
                    "attributes": {"is_abstract": False},
                },
                {
                    "alias": "clinical",
                    "type": "ClinicalSupport",
                    "count": target_count,
                    "attributes": {"is_abstract": False},
                },
            ],
            "create_edges": [
                {
                    "source": "patient",
                    "target": "clinical",
                    "edge_type": "door",
                    "mode": "balanced_each_to_one",
                    "max_sources_per_target": max_sources_per_target,
                }
            ],
        },
    }


def _assignment_loads(graph: nx.Graph) -> Counter:
    """Per-ClinicalSupport counts of door edges arriving from PatientRoom nodes."""
    loads = Counter(
        {
            node: 0
            for node, attrs in graph.nodes(data=True)
            if attrs.get("type") == "ClinicalSupport"
        }
    )
    for left, right, attrs in graph.edges(data=True):
        if attrs.get("edge_type") != "door":
            continue
        types = {
            left: graph.nodes[left].get("type"),
            right: graph.nodes[right].get("type"),
        }
        if set(types.values()) == {"PatientRoom", "ClinicalSupport"}:
            target = left if types[left] == "ClinicalSupport" else right
            loads[target] += 1
    return loads


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


def test_generation_uses_config_defined_rules_without_final_zone_nodes():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)

    node_types = {
        attrs.get("type")
        for _, attrs in result.graph.nodes(data=True)
    }
    assert "Zone" not in node_types
    assert "Corridor" in node_types
    assert any(attrs.get("edge_type") == "wall" for _, _, attrs in result.graph.edges(data=True))
    assert result.is_valid


def test_adjacent_pairs_edge_mode_connects_consecutive_created_nodes():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    rule = {
        "name": "make_rooms",
        "match": {"type": "Zone"},
        "action": {
            "create_nodes": [
                {
                    "alias": "room",
                    "type": "PatientRoom",
                    "count": 3,
                    "attributes": {"is_abstract": False},
                }
            ],
            "create_edges": [
                {
                    "source": "room",
                    "target": "room",
                    "edge_type": "wall",
                    "mode": "adjacent_pairs",
                }
            ],
        },
    }

    created = apply_grammar_rule(graph, rule, "zone", Random(123))
    wall_edges = {
        tuple(sorted((left, right)))
        for left, right, attrs in graph.edges(data=True)
        if attrs.get("edge_type") == "wall"
    }

    assert created == ["zone_room_1", "zone_room_2", "zone_room_3"]
    assert wall_edges == {
        ("zone_room_1", "zone_room_2"),
        ("zone_room_2", "zone_room_3"),
    }


def test_generation_with_config_rules_is_deterministic():
    config = load_config(DEFAULT_CONFIG_PATH)
    first = generate_candidate(seed=42, config=config).graph
    second = generate_candidate(seed=42, config=config).graph

    assert list(first.nodes(data=True)) == list(second.nodes(data=True))
    assert list(first.edges(data=True)) == list(second.edges(data=True))


def test_balanced_each_to_one_full_capacity_not_required():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)

    apply_grammar_rule(graph, _balanced_rule(15, 5, 4), "zone", Random(123))
    loads = _assignment_loads(graph)

    assert sorted(loads.values()) == [3, 3, 3, 3, 3]


def test_balanced_each_to_one_uneven_division_differs_by_at_most_one():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)

    apply_grammar_rule(graph, _balanced_rule(14, 5, 4), "zone", Random(123))
    loads = _assignment_loads(graph)

    assert sorted(loads.values()) == [2, 3, 3, 3, 3]


def test_balanced_each_to_one_exact_capacity_fills_every_target():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)

    apply_grammar_rule(graph, _balanced_rule(20, 5, 4), "zone", Random(123))
    loads = _assignment_loads(graph)

    assert sorted(loads.values()) == [4, 4, 4, 4, 4]


def test_balanced_each_to_one_gives_every_source_exactly_one_assignment():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)

    apply_grammar_rule(graph, _balanced_rule(14, 5, 4), "zone", Random(123))

    patients = [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs.get("type") == "PatientRoom"
    ]
    assert len(patients) == 14
    for patient in patients:
        clinical_neighbors = [
            neighbor
            for neighbor in graph.neighbors(patient)
            if graph.nodes[neighbor].get("type") == "ClinicalSupport"
            and graph.edges[patient, neighbor].get("edge_type") == "door"
        ]
        assert len(clinical_neighbors) == 1


def test_balanced_each_to_one_infeasible_capacity_raises_before_adding_edges():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)

    with pytest.raises(RuleSchemaError) as exc_info:
        apply_grammar_rule(graph, _balanced_rule(21, 5, 4), "zone", Random(123))

    message = str(exc_info.value)
    assert "assign_patients_to_clinical" in message
    assert "21 source(s)" in message
    assert "5 target(s)" in message
    assert "max_sources_per_target=4" in message
    assert "total capacity 20" in message
    assert sum(_assignment_loads(graph).values()) == 0


def test_balanced_each_to_one_empty_sources_creates_no_edges():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    rule = {
        "name": "assign_neighbors_to_clinical",
        "match": {"type": "Zone"},
        "action": {
            "create_nodes": [
                {
                    "alias": "clinical",
                    "type": "ClinicalSupport",
                    "count": 2,
                    "attributes": {"is_abstract": False},
                }
            ],
            "create_edges": [
                {
                    "source": "__neighbors__",
                    "target": "clinical",
                    "edge_type": "door",
                    "mode": "balanced_each_to_one",
                    "max_sources_per_target": 4,
                }
            ],
        },
    }

    apply_grammar_rule(graph, rule, "zone", Random(123))

    assert graph.number_of_edges() == 0


def test_balanced_each_to_one_empty_targets_with_sources_is_infeasible():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    rule = {
        "name": "assign_patients_to_neighbors",
        "match": {"type": "Zone"},
        "action": {
            "create_nodes": [
                {
                    "alias": "patient",
                    "type": "PatientRoom",
                    "count": 2,
                    "attributes": {"is_abstract": False},
                }
            ],
            "create_edges": [
                {
                    "source": "patient",
                    "target": "__neighbors__",
                    "edge_type": "door",
                    "mode": "balanced_each_to_one",
                    "max_sources_per_target": 4,
                }
            ],
        },
    }

    with pytest.raises(RuleSchemaError, match="0 target"):
        apply_grammar_rule(graph, rule, "zone", Random(123))
    assert graph.number_of_edges() == 0


@pytest.mark.parametrize(
    ("capacity", "expected_message"),
    [
        (None, "is required for mode 'balanced_each_to_one'"),
        (0, "must be a positive integer"),
        (-2, "must be a positive integer"),
        (True, "must be a positive integer"),
        (2.5, "must be a positive integer"),
        ("4", "must be a positive integer"),
    ],
)
def test_balanced_each_to_one_capacity_validation(capacity, expected_message):
    rule = _balanced_rule(4, 2, 1)
    edge_entry = rule["action"]["create_edges"][0]
    if capacity is None:
        del edge_entry["max_sources_per_target"]
    else:
        edge_entry["max_sources_per_target"] = capacity

    with pytest.raises(RuleSchemaError, match=expected_message):
        validate_grammar_rule(rule)


def test_max_sources_per_target_is_rejected_for_other_modes():
    rule = _balanced_rule(4, 2, 4)
    rule["action"]["create_edges"][0]["mode"] = "each_to_one"

    with pytest.raises(RuleSchemaError, match="only supported with mode 'balanced_each_to_one'"):
        validate_grammar_rule(rule)


def test_balanced_each_to_one_passes_schema_validation():
    validate_grammar_rule(
        _balanced_rule(4, 2, 4),
        allowed_node_types=["Zone", "PatientRoom", "ClinicalSupport"],
        allowed_edge_types=["door", "wall"],
    )


def test_each_to_one_still_connects_all_sources_to_first_target():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    rule = _balanced_rule(3, 2, 4)
    edge_entry = rule["action"]["create_edges"][0]
    edge_entry["mode"] = "each_to_one"
    del edge_entry["max_sources_per_target"]

    apply_grammar_rule(graph, rule, "zone", Random(123))
    loads = _assignment_loads(graph)

    assert sorted(loads.values()) == [0, 3]
    assert loads["zone_clinical_1"] == 3


def test_balanced_each_to_one_is_deterministic_for_same_inputs():
    first = nx.Graph()
    first.add_node("zone", type="Zone", is_abstract=True)
    second = nx.Graph()
    second.add_node("zone", type="Zone", is_abstract=True)

    apply_grammar_rule(first, _balanced_rule(14, 5, 4), "zone", Random(123))
    apply_grammar_rule(second, _balanced_rule(14, 5, 4), "zone", Random(123))

    assert list(first.nodes(data=True)) == list(second.nodes(data=True))
    assert sorted(map(sorted, first.edges())) == sorted(map(sorted, second.edges()))


def test_balanced_each_to_one_trace_records_mode_capacity_and_edge_count():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    trace_events = []

    apply_grammar_rule(
        graph,
        _balanced_rule(15, 5, 4),
        "zone",
        Random(123),
        trace_events=trace_events,
        step_index=1,
    )

    assert len(trace_events) == 1
    edge_trace = trace_events[0].sampled_parameters["create_edges"][0]
    assert edge_trace["mode"] == "balanced_each_to_one"
    assert edge_trace["max_sources_per_target"] == 4
    assert edge_trace["created_edge_count"] == 15
    assert len(trace_events[0].created_edges) == 15


def test_existing_mode_trace_entries_gain_no_capacity_field():
    graph = nx.Graph()
    graph.add_node("zone", type="Zone", is_abstract=True)
    rule = _balanced_rule(3, 1, 4)
    edge_entry = rule["action"]["create_edges"][0]
    edge_entry["mode"] = "each_to_one"
    del edge_entry["max_sources_per_target"]
    trace_events = []

    apply_grammar_rule(graph, rule, "zone", Random(123), trace_events=trace_events)

    edge_trace = trace_events[0].sampled_parameters["create_edges"][0]
    assert "max_sources_per_target" not in edge_trace
    assert edge_trace["created_edge_count"] == 3


def test_generation_with_balanced_mode_distributes_patient_rooms(tmp_path):
    config_data = {
        "project": {"name": "Balanced test", "building_type": "GenericBuilding"},
        "random_seed_default": 42,
        "generation": {"num_candidates": 1},
        "allowed_node_types": [
            "BuildingFloor",
            "Zone",
            "Corridor",
            "PatientRoom",
            "ClinicalSupport",
        ],
        "allowed_edge_types": ["door", "wall"],
        "zone_types": ["public"],
        "room_type_counts": {"PatientRoom": 45, "ClinicalSupport": 15},
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
        "grammar_rules": [
            {
                "name": "expand_floor",
                "match": {"type": "BuildingFloor", "is_abstract": True},
                "action": {
                    "remove_matched_node": True,
                    "create_nodes": [
                        {
                            "alias": "zone",
                            "type": "Zone",
                            "count": 1,
                            "attributes": {"is_abstract": True},
                        }
                    ],
                    "create_edges": [],
                },
            },
            {
                "name": "expand_zone_balanced",
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
                            "alias": "patient",
                            "type": "PatientRoom",
                            "count": 45,
                            "attributes": {"is_abstract": False},
                        },
                        {
                            "alias": "clinical",
                            "type": "ClinicalSupport",
                            "count": 15,
                            "attributes": {"is_abstract": False},
                        },
                    ],
                    "create_edges": [
                        {
                            "source": "patient",
                            "target": "corridor",
                            "edge_type": "door",
                            "mode": "each_to_one",
                        },
                        {
                            "source": "clinical",
                            "target": "corridor",
                            "edge_type": "door",
                            "mode": "each_to_one",
                        },
                        {
                            "source": "patient",
                            "target": "clinical",
                            "edge_type": "door",
                            "mode": "balanced_each_to_one",
                            "max_sources_per_target": 4,
                        },
                    ],
                },
            },
        ],
    }
    config_path = tmp_path / "balanced_config.yaml"
    config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)

    result = generate_candidate(seed=42, config=config)
    loads = _assignment_loads(result.graph)

    assert sum(loads.values()) == 45
    assert sorted(loads.values()) == [3] * 15

    repeat = generate_candidate(seed=42, config=config)
    assert list(result.graph.edges(data=True)) == list(repeat.graph.edges(data=True))


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
