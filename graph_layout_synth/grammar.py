"""Small explicit grammar rules for Milestone 1 graph generation."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config


VALID_EDGE_TYPES = {"door", "wall"}


@dataclass(frozen=True)
class ZoneSpec:
    """Configuration for expanding a zone into rooms and a corridor."""

    name: str
    room_count: int


def _weighted_choice(weights: dict[str, int], rng: Random) -> str:
    choices = []
    for choice, count in weights.items():
        choices.extend([choice] * count)
    return rng.choice(choices)


def seed_graph(config: LayoutConfig | None = None) -> nx.Graph:
    """Create the initial graph with one abstract building floor node."""
    config = config or load_config()
    graph = nx.Graph()
    graph.graph["project_name"] = config.project.name
    graph.graph["building_type"] = config.project.building_type
    graph.add_node(
        "floor",
        type="BuildingFloor",
        zone=None,
        is_abstract=True,
    )
    return graph


def expand_floor_to_zones(
    graph: nx.Graph,
    rng: Random,
    config: LayoutConfig | None = None,
) -> list[str]:
    """Replace the abstract floor node with a small stochastic set of zones."""
    config = config or load_config()
    if "floor" not in graph:
        return []

    zone_count = rng.randint(config.stochastic.min_zone_count, config.stochastic.max_zone_count)
    zone_names = [f"zone_{index + 1}" for index in range(zone_count)]

    graph.remove_node("floor")
    for index, zone_name in enumerate(zone_names):
        graph.add_node(
            zone_name,
            type="Zone",
            zone=zone_name,
            zone_type=config.zone_types[index % len(config.zone_types)],
            is_abstract=True,
        )

    for left, right in zip(zone_names, zone_names[1:]):
        graph.add_edge(left, right, edge_type="wall")

    return zone_names


def expand_zone_to_room_cluster(
    graph: nx.Graph,
    zone_node: str,
    rng: Random,
    config: LayoutConfig | None = None,
) -> None:
    """Replace one abstract zone with a corridor and several rooms."""
    config = config or load_config()
    if zone_node not in graph:
        return

    room_count = rng.randint(config.stochastic.min_cluster_size, config.stochastic.max_cluster_size)
    corridor_pattern = rng.choice(config.stochastic.corridor_pattern_choices)
    corridor_node = f"{zone_node}_corridor"
    neighbors = list(graph.neighbors(zone_node))

    graph.remove_node(zone_node)
    graph.add_node(
        corridor_node,
        type="Corridor",
        zone=zone_node,
        is_abstract=False,
    )

    for neighbor in neighbors:
        graph.add_edge(corridor_node, neighbor, edge_type="door")

    for index in range(room_count):
        room_node = f"{zone_node}_room_{index + 1}"
        room_type = _weighted_choice(config.room_type_counts, rng)
        graph.add_node(
            room_node,
            type=room_type,
            zone=zone_node,
            is_abstract=False,
        )
        graph.add_edge(room_node, corridor_node, edge_type="door")

        wall_probability = 0.5 if corridor_pattern == "linear" else 0.2
        if index > 0 and rng.random() < wall_probability:
            graph.add_edge(room_node, f"{zone_node}_room_{index}", edge_type="wall")


def complete_expansion(
    graph: nx.Graph,
    rng: Random,
    config: LayoutConfig | None = None,
) -> nx.Graph:
    """Expand all abstract nodes in a seed graph."""
    config = config or load_config()
    zone_nodes = expand_floor_to_zones(graph, rng, config)
    for zone_node in zone_nodes:
        expand_zone_to_room_cluster(graph, zone_node, rng, config)
    return graph
