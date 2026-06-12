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
    "Corridor": "#f2c14e",
    "Room": "#4f8ef7",
    "SupportRoom": "#7bc96f",
    "ServiceRoom": "#d96c75",
    "Zone": "#b39ddb",
    "BuildingFloor": "#9e9e9e",
}

EDGE_STYLES = {
    "door": "solid",
    "wall": "dashed",
}


def _color_settings(config: LayoutConfig | None) -> tuple[dict[str, str], str]:
    node_colors = dict(NODE_COLORS)
    unknown_node_color = "#c7c7c7"
    if config:
        node_colors.update(config.visualization.node_colors)
        unknown_node_color = config.visualization.unknown_node_color
    return node_colors, unknown_node_color


def _node_color(node_type: str | None, node_colors: dict[str, str], unknown_node_color: str) -> str:
    return node_colors.get(node_type or "", unknown_node_color)


def _edge_style(edge_type: str | None) -> str:
    return EDGE_STYLES.get(edge_type or "", "dotted")


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
    configured_node_colors, unknown_node_color = _color_settings(config)
    node_colors = [
        _node_color(node_types.get(node), configured_node_colors, unknown_node_color)
        for node in G.nodes
    ]
    labels = {
        node: node_types.get(node, str(node))
        for node in G.nodes
    }

    nx.draw_networkx_nodes(
        G,
        pos,
        node_color=node_colors,
        node_size=1100,
        edgecolors="#333333",
        linewidths=0.8,
        ax=ax,
    )
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, ax=ax)

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
