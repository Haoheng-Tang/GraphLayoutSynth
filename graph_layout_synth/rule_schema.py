"""Minimal executable YAML rule schema for graph generation."""

from __future__ import annotations

from random import Random
from typing import Any

import networkx as nx

from graph_layout_synth.tracing import RuleApplicationTraceEvent


class RuleSchemaError(ValueError):
    """Raised when a grammar rule is malformed or unsupported."""


def sample_count(count_spec: Any, rng: Random) -> int:
    """Sample a node count from a fixed integer or min/max mapping."""
    if isinstance(count_spec, int) and not isinstance(count_spec, bool):
        if count_spec < 0:
            raise RuleSchemaError("Count must be non-negative.")
        return count_spec
    if isinstance(count_spec, dict):
        minimum = count_spec.get("min")
        maximum = count_spec.get("max")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 0
            or maximum < minimum
        ):
            raise RuleSchemaError("Unknown count format: expected {'min': int, 'max': int}.")
        return rng.randint(minimum, maximum)
    raise RuleSchemaError("Unknown count format: expected integer or {'min': int, 'max': int}.")


def sample_choice(choice_spec: Any, rng: Random) -> Any:
    """Sample a value from {'choices': [...]} or return a fixed value."""
    if isinstance(choice_spec, dict) and "choices" in choice_spec:
        choices = choice_spec["choices"]
        if not isinstance(choices, list) or not choices:
            raise RuleSchemaError("Choice spec must contain a non-empty choices list.")
        return rng.choice(choices)
    return choice_spec


def validate_grammar_rule(rule: dict) -> None:
    """Validate one minimal grammar rule."""
    if not isinstance(rule, dict):
        raise RuleSchemaError("Grammar rule must be a mapping.")
    if not rule.get("name"):
        raise RuleSchemaError("Grammar rule is missing rule name.")
    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        raise RuleSchemaError(f"Grammar rule '{rule['name']}' is missing match section.")
    action = rule.get("action")
    if not isinstance(action, dict):
        raise RuleSchemaError(f"Grammar rule '{rule['name']}' is missing action section.")

    create_nodes = action.get("create_nodes", [])
    if not isinstance(create_nodes, list):
        raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_nodes section.")
    for entry in create_nodes:
        if not isinstance(entry, dict) or not entry.get("alias") or "type" not in entry:
            raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_nodes entry.")
        sample_count(entry.get("count", 1), Random(0))
        attributes = entry.get("attributes", {})
        if not isinstance(attributes, dict):
            raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_nodes attributes.")

    create_edges = action.get("create_edges", [])
    if not isinstance(create_edges, list):
        raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_edges section.")
    for entry in create_edges:
        if (
            not isinstance(entry, dict)
            or not entry.get("source")
            or not entry.get("target")
            or not entry.get("edge_type")
        ):
            raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_edges entry.")
        mode = entry.get("mode", "one_to_one")
        if mode not in {"one_to_one", "each_to_one", "one_to_each", "adjacent_pairs"}:
            raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid create_edges mode '{mode}'.")


def load_grammar_rules(config: dict) -> list[dict]:
    """Load and validate grammar rules from a raw config mapping."""
    rules = config.get("grammar_rules", [])
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise RuleSchemaError("Config field 'grammar_rules' must be a list.")
    for rule in rules:
        validate_grammar_rule(rule)
    return rules


def node_matches(graph: nx.Graph, node: str, match: dict) -> bool:
    """Return whether a node matches simple attribute constraints."""
    attrs = graph.nodes[node]
    return all(attrs.get(key) == value for key, value in match.items())


def apply_grammar_rule(
    graph: nx.Graph,
    rule: dict,
    matched_node: str,
    rng: Random,
    trace_events: list[RuleApplicationTraceEvent] | None = None,
    step_index: int | None = None,
) -> list[str]:
    """Apply one grammar rule to one matched node and return created node ids."""
    action = rule["action"]
    neighbors = list(graph.neighbors(matched_node))
    alias_nodes: dict[str, list[str]] = {"matched": [matched_node]}
    matched_node_attrs = dict(graph.nodes[matched_node])
    sampled_parameters: dict[str, Any] = {"create_nodes": [], "create_edges": []}
    created_edges: list[dict[str, str]] = []
    removed_node_ids: list[str] = []

    update_attrs = action.get("update_matched_node_attributes", {})
    if update_attrs:
        if not isinstance(update_attrs, dict):
            raise RuleSchemaError(f"Grammar rule '{rule['name']}' has invalid update_matched_node_attributes.")
        graph.nodes[matched_node].update(update_attrs)

    for entry in action.get("create_nodes", []):
        alias = entry["alias"]
        count = sample_count(entry.get("count", 1), rng)
        alias_nodes[alias] = []
        for index in range(count):
            node_id = _created_node_id(graph, matched_node, alias, index, count)
            attrs = dict(entry.get("attributes", {}))
            attrs["type"] = sample_choice(entry["type"], rng)
            attrs.setdefault("is_abstract", False)
            attrs.setdefault("zone", _default_zone(graph, matched_node, node_id))
            graph.add_node(node_id, **attrs)
            alias_nodes[alias].append(node_id)
        sampled_parameters["create_nodes"].append(
            {
                "alias": alias,
                "count": count,
                "type": [graph.nodes[node].get("type") for node in alias_nodes[alias]],
            }
        )

    for entry in action.get("create_edges", []):
        sources = _resolve_alias(entry["source"], alias_nodes, neighbors)
        targets = _resolve_alias(entry["target"], alias_nodes, neighbors)
        mode = entry.get("mode", "one_to_one")
        edge_type = entry["edge_type"]
        new_edges = _create_edges(graph, sources, targets, mode, edge_type)
        created_edges.extend(new_edges)
        sampled_parameters["create_edges"].append(
            {
                "source": entry["source"],
                "target": entry["target"],
                "mode": mode,
                "edge_type": edge_type,
                "created_edge_count": len(new_edges),
            }
        )

    if action.get("remove_matched_node", False):
        removed_node_ids.append(matched_node)
        graph.remove_node(matched_node)

    created_node_ids = [
        node
        for alias, nodes in alias_nodes.items()
        if alias != "matched"
        for node in nodes
    ]
    if trace_events is not None:
        trace_events.append(
            RuleApplicationTraceEvent(
                step_index=step_index if step_index is not None else len(trace_events) + 1,
                rule_name=rule["name"],
                matched_node_id=matched_node,
                matched_node_attrs=matched_node_attrs,
                sampled_parameters=sampled_parameters,
                created_node_ids=created_node_ids,
                created_edges=created_edges,
                removed_node_ids=removed_node_ids,
            )
        )

    return created_node_ids


def _created_node_id(graph: nx.Graph, matched_node: str, alias: str, index: int, count: int) -> str:
    suffix = alias if count == 1 else f"{alias}_{index + 1}"
    base = f"{matched_node}_{suffix}"
    if base not in graph:
        return base
    counter = 2
    while f"{base}_{counter}" in graph:
        counter += 1
    return f"{base}_{counter}"


def _default_zone(graph: nx.Graph, matched_node: str, node_id: str) -> str:
    matched_type = graph.nodes[matched_node].get("type")
    if matched_type == "Zone":
        return matched_node
    return graph.nodes[matched_node].get("zone") or node_id


def _resolve_alias(name: str, alias_nodes: dict[str, list[str]], neighbors: list[str]) -> list[str]:
    if name == "__neighbors__":
        return neighbors
    if name not in alias_nodes:
        raise RuleSchemaError(f"Unknown create_edges alias '{name}'.")
    return alias_nodes[name]


def _create_edges(graph: nx.Graph, sources: list[str], targets: list[str], mode: str, edge_type: str) -> list[dict[str, str]]:
    created_edges = []
    if not sources or not targets:
        return created_edges
    if mode == "adjacent_pairs":
        nodes = sources if sources == targets else sources + targets
        for source, target in zip(nodes, nodes[1:]):
            if source == target:
                continue
            graph.add_edge(source, target, edge_type=edge_type)
            created_edges.append({"source": source, "target": target, "edge_type": edge_type})
        return created_edges
    if mode == "each_to_one":
        for source in sources:
            graph.add_edge(source, targets[0], edge_type=edge_type)
            created_edges.append({"source": source, "target": targets[0], "edge_type": edge_type})
        return created_edges
    if mode == "one_to_each":
        for target in targets:
            graph.add_edge(sources[0], target, edge_type=edge_type)
            created_edges.append({"source": sources[0], "target": target, "edge_type": edge_type})
        return created_edges
    for source, target in zip(sources, targets):
        graph.add_edge(source, target, edge_type=edge_type)
        created_edges.append({"source": source, "target": target, "edge_type": edge_type})
    return created_edges
