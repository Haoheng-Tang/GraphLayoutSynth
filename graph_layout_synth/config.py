"""YAML configuration loading for graph grammar generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("configs/generic_building.yaml")


class ConfigError(ValueError):
    """Raised when a config file is missing required fields or is invalid."""


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    building_type: str


@dataclass(frozen=True)
class GenerationDefaults:
    num_candidates: int


@dataclass(frozen=True)
class StochasticConfig:
    min_zone_count: int
    max_zone_count: int
    min_cluster_size: int
    max_cluster_size: int
    corridor_pattern_choices: list[str]
    support_room_choices: list[str]


@dataclass(frozen=True)
class ValidationSettings:
    require_connected_graph: bool
    require_corridor_access: bool
    allow_abstract_nodes_final: bool


@dataclass(frozen=True)
class VisualizationSettings:
    node_colors: dict[str, str]
    unknown_node_color: str


@dataclass(frozen=True)
class LayoutConfig:
    project: ProjectConfig
    random_seed_default: int | None
    generation: GenerationDefaults
    allowed_node_types: list[str]
    allowed_edge_types: list[str]
    zone_types: list[str]
    room_type_counts: dict[str, int]
    stochastic: StochasticConfig
    validation: ValidationSettings
    visualization: VisualizationSettings


def _require_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Config field '{key}' must be a mapping.")
    return value


def _require_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"Config field '{key}' must be a non-empty list of strings.")
    return value


def _require_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"Config field '{key}' must be an integer.")
    return value


def _require_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"Config field '{key}' must be true or false.")
    return value


def _optional_visualization(config: dict[str, Any]) -> VisualizationSettings:
    visualization = config.get("visualization", {})
    if not isinstance(visualization, dict):
        raise ConfigError("Config field 'visualization' must be a mapping.")

    node_colors = visualization.get("node_colors", {})
    if not isinstance(node_colors, dict) or not all(
        isinstance(node_type, str) and isinstance(color, str)
        for node_type, color in node_colors.items()
    ):
        raise ConfigError("Config field 'visualization.node_colors' must map node types to color strings.")

    unknown_node_color = visualization.get("unknown_node_color", "#c7c7c7")
    if not isinstance(unknown_node_color, str):
        raise ConfigError("Config field 'visualization.unknown_node_color' must be a color string.")

    return VisualizationSettings(
        node_colors=node_colors,
        unknown_node_color=unknown_node_color,
    )


def validate_config(config: dict[str, Any]) -> LayoutConfig:
    """Validate a raw config dictionary and return a typed config."""
    if not isinstance(config, dict):
        raise ConfigError("Config must be a mapping.")

    project = _require_mapping(config, "project")
    generation = _require_mapping(config, "generation")
    stochastic = _require_mapping(config, "stochastic")
    validation = _require_mapping(config, "validation")

    project_name = project.get("name")
    building_type = project.get("building_type")
    if not isinstance(project_name, str) or not project_name:
        raise ConfigError("Config field 'project.name' must be a non-empty string.")
    if not isinstance(building_type, str) or not building_type:
        raise ConfigError("Config field 'project.building_type' must be a non-empty string.")

    random_seed_default = config.get("random_seed_default")
    if random_seed_default is not None and not isinstance(random_seed_default, int):
        raise ConfigError("Config field 'random_seed_default' must be an integer or null.")

    room_type_counts = config.get("room_type_counts")
    if (
        not isinstance(room_type_counts, dict)
        or not room_type_counts
        or not all(isinstance(key, str) and isinstance(value, int) and value > 0 for key, value in room_type_counts.items())
    ):
        raise ConfigError("Config field 'room_type_counts' must map room types to positive integer counts.")

    allowed_node_types = _require_list(config, "allowed_node_types")
    allowed_edge_types = _require_list(config, "allowed_edge_types")
    zone_types = _require_list(config, "zone_types")

    if "Corridor" not in allowed_node_types:
        raise ConfigError("Config field 'allowed_node_types' must include 'Corridor'.")
    if "door" not in allowed_edge_types:
        raise ConfigError("Config field 'allowed_edge_types' must include 'door'.")
    unknown_room_types = set(room_type_counts) - set(allowed_node_types)
    if unknown_room_types:
        raise ConfigError(
            "Room type counts include types not listed in allowed_node_types: "
            + ", ".join(sorted(unknown_room_types))
            + "."
        )

    min_zone_count = _require_int(stochastic, "min_zone_count")
    max_zone_count = _require_int(stochastic, "max_zone_count")
    min_cluster_size = _require_int(stochastic, "min_cluster_size")
    max_cluster_size = _require_int(stochastic, "max_cluster_size")

    if min_zone_count < 1 or max_zone_count < min_zone_count:
        raise ConfigError("Zone count bounds must be positive and ordered.")
    if min_cluster_size < 1 or max_cluster_size < min_cluster_size:
        raise ConfigError("Cluster size bounds must be positive and ordered.")
    num_candidates = _require_int(generation, "num_candidates")
    if num_candidates < 1:
        raise ConfigError("Config field 'generation.num_candidates' must be at least 1.")

    corridor_pattern_choices = _require_list(stochastic, "corridor_pattern_choices")
    support_room_choices = _require_list(stochastic, "support_room_choices")
    unknown_support_types = set(support_room_choices) - set(allowed_node_types)
    if unknown_support_types:
        raise ConfigError(
            "Support room choices include types not listed in allowed_node_types: "
            + ", ".join(sorted(unknown_support_types))
            + "."
        )

    return LayoutConfig(
        project=ProjectConfig(name=project_name, building_type=building_type),
        random_seed_default=random_seed_default,
        generation=GenerationDefaults(num_candidates=num_candidates),
        allowed_node_types=allowed_node_types,
        allowed_edge_types=allowed_edge_types,
        zone_types=zone_types,
        room_type_counts=room_type_counts,
        stochastic=StochasticConfig(
            min_zone_count=min_zone_count,
            max_zone_count=max_zone_count,
            min_cluster_size=min_cluster_size,
            max_cluster_size=max_cluster_size,
            corridor_pattern_choices=corridor_pattern_choices,
            support_room_choices=support_room_choices,
        ),
        validation=ValidationSettings(
            require_connected_graph=_require_bool(validation, "require_connected_graph"),
            require_corridor_access=_require_bool(validation, "require_corridor_access"),
            allow_abstract_nodes_final=_require_bool(validation, "allow_abstract_nodes_final"),
        ),
        visualization=_optional_visualization(config),
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LayoutConfig:
    """Load and validate a YAML config file."""
    config_path = Path(path)
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file is not valid YAML: {config_path}") from exc

    return validate_config(raw_config)
