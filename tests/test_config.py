import pytest

from graph_layout_synth.config import ConfigError, DEFAULT_CONFIG_PATH, load_config, validate_config
from graph_layout_synth.generator import generate_candidate


def test_default_config_file_loads():
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.project.building_type == "GenericBuilding"
    assert "door" in config.allowed_edge_types
    assert config.visualization.node_colors["PatientRoom"]


def test_invalid_config_raises_clear_error():
    with pytest.raises(ConfigError, match="project"):
        validate_config({})


def test_generation_works_with_config():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=123, config=config)

    assert result.graph.number_of_nodes() > 0
    assert result.graph.graph["building_type"] == "GenericBuilding"
