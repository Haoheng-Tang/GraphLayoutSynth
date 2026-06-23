"""Static PNG visualization utilities for layout graphs."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from graph_layout_synth.config import LayoutConfig


NODE_COLORS = {
    "Corridor": "#f2cc8f",
    "Room": "#e07a5f",
    "SupportRoom": "#81b29a",
    "ServiceRoom": "#3d405b",
    "Zone": "#f4f1de",
    "BuildingFloor": "#9e9e9e",
}

EDGE_STYLES = {
    "door": "solid",
    "wall": "dashed",
}

BASE_NODE_SIZE = 100
MIN_NODE_SIZE = 10
NODE_SIZE_REFERENCE_COUNT = 12


def _node_color(node_type: str | None) -> str:
    return NODE_COLORS.get(node_type or "", "#c7c7c7")


def _edge_style(edge_type: str | None) -> str:
    return EDGE_STYLES.get(edge_type or "", "dotted")


def _configured_colors(config: LayoutConfig | None) -> tuple[dict[str, str], str]:
    node_colors = dict(NODE_COLORS)
    unknown_node_color = "#c7c7c7"
    if config:
        node_colors.update(config.visualization.node_colors)
        unknown_node_color = config.visualization.unknown_node_color
    return node_colors, unknown_node_color


def scaled_node_size(node_count: int) -> int:
    """Return a readable node size that shrinks as graph node count grows."""
    if node_count <= 0:
        return BASE_NODE_SIZE
    scale = (NODE_SIZE_REFERENCE_COUNT / max(node_count, NODE_SIZE_REFERENCE_COUNT)) ** 0.5
    return max(MIN_NODE_SIZE, round(BASE_NODE_SIZE * scale))


def visualize_graph(
    G: nx.Graph,
    output_path: str | Path,
    title: str | None = None,
    config: LayoutConfig | None = None,
) -> Path:
    """Save a static PNG visualization of a layout graph."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 6))
    pos = nx.spring_layout(G, seed=42)

    node_types = nx.get_node_attributes(G, "type")
    configured_node_colors, unknown_node_color = _configured_colors(config)
    node_colors = [
        configured_node_colors.get(node_types.get(node, ""), unknown_node_color)
        for node in G.nodes
    ]
    nx.draw_networkx_nodes(
        G,
        pos,
        node_color=node_colors,
        node_size=scaled_node_size(G.number_of_nodes()),
        edgecolors="#333333",
        linewidths=0.8,
        ax=ax,
    )

    edge_types = nx.get_edge_attributes(G, "edge_type")
    for edge_style in {"solid", "dashed", "dotted"}:
        edgelist = [
            (left, right)
            for left, right in G.edges
            if _edge_style(edge_types.get((left, right))) == edge_style
        ]
        if edgelist:
            nx.draw_networkx_edges(
                G,
                pos,
                edgelist=edgelist,
                style=edge_style,
                width=1.8,
                edge_color="#555555",
                ax=ax,
            )

    if title:
        ax.set_title(title)
    ax.axis("off")

    node_legend = [
        Patch(facecolor=color, edgecolor="#333333", label=node_type)
        for node_type, color in configured_node_colors.items()
        if node_type in set(node_types.values())
    ]
    edge_legend = [
        Line2D([0], [0], color="#555555", linestyle="solid", label="door"),
        Line2D([0], [0], color="#555555", linestyle="dashed", label="wall"),
        Line2D([0], [0], color="#555555", linestyle="dotted", label="unknown edge"),
    ]
    ax.legend(
        handles=node_legend + edge_legend,
        loc="best",
        fontsize=8,
        frameon=False,
    )

    fig.tight_layout()
    fig.savefig(path, format="png", dpi=150)
    plt.close(fig)
    return path
