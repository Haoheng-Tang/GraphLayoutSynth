"""Small explicit grammar rules for Milestone 1 graph generation."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

import networkx as nx


VALID_EDGE_TYPES = {"door", "wall"}


@dataclass(frozen=True)
class ZoneSpec:
    """Configuration for expanding a zone into rooms and a corridor."""

    name: str
    room_count: int


def seed_graph() -> nx.Graph:
    """Create the initial graph with one abstract building floor node."""
    graph = nx.Graph()
    graph.add_node(
        "floor",
        type="BuildingFloor",
        zone=None,
        is_abstract=True,
    )
    return graph


def expand_floor_to_zones(graph: nx.Graph, rng: Random) -> list[str]:
    """Replace the abstract floor node with a small stochastic set of zones."""
    if "floor" not in graph:
        return []

    zone_count = rng.randint(2, 3)
    zone_names = [f"zone_{index + 1}" for index in range(zone_count)]

    graph.remove_node("floor")
    for zone_name in zone_names:
        graph.add_node(
            zone_name,
            type="Zone",
            zone=zone_name,
            is_abstract=True,
        )

    for left, right in zip(zone_names, zone_names[1:]):
        graph.add_edge(left, right, edge_type="wall")

    return zone_names


def expand_zone_to_room_cluster(graph: nx.Graph, zone_node: str, rng: Random) -> None:
    """Replace one abstract zone with a corridor and several rooms."""
    if zone_node not in graph:
        return

    room_count = rng.randint(2, 4)
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
        room_type = rng.choice(["Room", "SupportRoom", "ServiceRoom"])
        graph.add_node(
            room_node,
            type=room_type,
            zone=zone_node,
            is_abstract=False,
        )
        graph.add_edge(room_node, corridor_node, edge_type="door")

        if index > 0 and rng.random() < 0.5:
            graph.add_edge(room_node, f"{zone_node}_room_{index}", edge_type="wall")


def complete_expansion(graph: nx.Graph, rng: Random) -> nx.Graph:
    """Expand all abstract nodes in a seed graph."""
    zone_nodes = expand_floor_to_zones(graph, rng)
    for zone_node in zone_nodes:
        expand_zone_to_room_cluster(graph, zone_node, rng)
    return graph
