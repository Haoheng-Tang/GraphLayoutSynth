"""Mapping between frontend floorplan IDs and internal graph node IDs."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from graph_layout_synth.api.models import FloorplanState


@dataclass(frozen=True)
class AdaptedFloorplan:
    """An internal graph plus its reversible external-ID mapping."""

    graph: nx.Graph
    frontend_to_internal: dict[str, int]
    internal_to_frontend: dict[int, str]

    def internal_id(self, frontend_room_id: str) -> int:
        """Resolve one stable frontend room ID to its internal integer ID."""
        try:
            return self.frontend_to_internal[frontend_room_id]
        except KeyError as exc:
            raise ValueError(
                f"Room ID '{frontend_room_id}' does not exist in the floorplan."
            ) from exc


def floorplan_to_graph(floorplan: FloorplanState) -> AdaptedFloorplan:
    """Copy a validated frontend floorplan into GraphLayoutSynth graph form."""
    frontend_to_internal = {
        room.id: internal_id
        for internal_id, room in enumerate(floorplan.rooms)
    }
    internal_to_frontend = {
        internal_id: frontend_id
        for frontend_id, internal_id in frontend_to_internal.items()
    }

    graph = nx.Graph(schema_version=floorplan.schema_version)
    for room in floorplan.rooms:
        internal_id = frontend_to_internal[room.id]
        attributes = {
            "type": room.type,
            "x": room.x,
            "y": room.y,
            "width": room.width,
            "height": room.height,
            "is_abstract": False,
            "external_id": room.id,
        }
        if room.rotation is not None:
            attributes["rotation"] = room.rotation
        graph.add_node(internal_id, **attributes)

    for edge in floorplan.edges:
        attributes = {
            "edge_id": edge.id,
            "edge_type": edge.edge_type,
        }
        if edge.side is not None:
            attributes["side"] = edge.side
        graph.add_edge(
            frontend_to_internal[edge.source_room_id],
            frontend_to_internal[edge.target_room_id],
            **attributes,
        )

    return AdaptedFloorplan(
        graph=graph,
        frontend_to_internal=frontend_to_internal,
        internal_to_frontend=internal_to_frontend,
    )
