import pytest

from graph_layout_synth.config import ConfigError, DEFAULT_CONFIG_PATH, load_config, validate_config
from graph_layout_synth.generator import generate_candidate


def test_default_config_file_loads():
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.project.building_type == "GenericBuilding"
    assert "door" in config.allowed_edge_types
    assert config.visualization.node_colors["PatientRoom"] == "#4f8ef7"


def test_required_config_fields_are_validated():
    with pytest.raises(ConfigError, match="project"):
        validate_config({})


def test_generation_works_with_config():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=123, config=config)

    assert result.graph.number_of_nodes() > 0
    assert result.graph.graph["building_type"] == "GenericBuilding"


def test_invalid_config_raises_clear_error():
    config = load_config(DEFAULT_CONFIG_PATH)
    raw_config = {
        "project": {
            "name": config.project.name,
            "building_type": config.project.building_type,
        },
        "random_seed_default": config.random_seed_default,
        "generation": {"num_candidates": 1},
        "allowed_node_types": config.allowed_node_types,
        "allowed_edge_types": ["wall"],
        "zone_types": config.zone_types,
        "room_type_counts": config.room_type_counts,
        "stochastic": {
            "min_zone_count": 2,
            "max_zone_count": 1,
            "min_cluster_size": config.stochastic.min_cluster_size,
            "max_cluster_size": config.stochastic.max_cluster_size,
            "corridor_pattern_choices": config.stochastic.corridor_pattern_choices,
            "support_room_choices": config.stochastic.support_room_choices,
        },
        "validation": {
            "require_connected_graph": True,
            "require_corridor_access": True,
            "allow_abstract_nodes_final": False,
        },
        "visualization": {
            "node_colors": config.visualization.node_colors,
            "unknown_node_color": config.visualization.unknown_node_color,
        },
    }

    with pytest.raises(ConfigError, match="allowed_edge_types"):
        validate_config(raw_config)
