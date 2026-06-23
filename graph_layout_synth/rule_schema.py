"""Minimal executable YAML rule schema for graph generation."""

from __future__ import annotations

from random import Random
from typing import Any

import networkx as nx

from graph_layout_synth.tracing import RuleApplicationTraceEvent


class RuleSchemaError(ValueError):
    """Raised when a grammar rule is malformed or unsupported."""


MATCH_KEYS = {"type", "zone", "zone_type", "is_abstract"}
ACTION_KEYS = {"remove_matched_node", "update_matched_node_attributes", "create_nodes", "create_edges"}
CREATE_NODE_KEYS = {"alias", "type", "count", "attributes"}
CREATE_EDGE_KEYS = {"source", "target", "edge_type", "mode"}
NODE_ATTRIBUTE_KEYS = {"type", "zone", "zone_type", "is_abstract"}
EDGE_MODES = {"one_to_one", "each_to_one", "one_to_each", "adjacent_pairs"}
SPECIAL_ALIASES = {"matched", "__neighbors__"}


def _rule_label(rule: Any, index: int | None = None) -> str:
    if isinstance(rule, dict) and rule.get("name"):
        return f"grammar_rules[{index}] '{rule['name']}'" if index is not None else f"grammar rule '{rule['name']}'"
    return f"grammar_rules[{index}]" if index is not None else "grammar rule"


def _error(rule: Any, path: str, message: str, index: int | None = None) -> RuleSchemaError:
    return RuleSchemaError(f"{_rule_label(rule, index)} at {path}: {message}")


def _unknown_keys(value: dict[str, Any], allowed: set[str]) -> list[str]:
    return sorted(key for key in value if key not in allowed)


def _validate_choice_spec(
    value: Any,
    path: str,
    rule: dict,
    index: int | None,
    allowed_values: list[str] | None = None,
) -> None:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, dict):
        unknown = _unknown_keys(value, {"choices"})
        if unknown:
            raise _error(rule, path, f"unsupported choice field(s): {', '.join(unknown)}.", index)
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices or not all(isinstance(item, str) and item for item in choices):
            raise _error(rule, path, "choice spec must contain a non-empty list of strings.", index)
        values = choices
    else:
        raise _error(rule, path, "must be a string or {'choices': [...]} object.", index)

    if allowed_values is not None:
        unknown_values = sorted(set(values) - set(allowed_values))
        if unknown_values:
            raise _error(rule, path, f"unknown value(s): {', '.join(unknown_values)}.", index)


def _validate_count_spec(count_spec: Any, rule: dict, path: str, index: int | None = None) -> None:
    if isinstance(count_spec, int) and not isinstance(count_spec, bool):
        if count_spec < 1:
            raise _error(rule, path, "count must be a positive integer.", index)
        return
    if isinstance(count_spec, dict):
        unknown = _unknown_keys(count_spec, {"min", "max"})
        if unknown:
            raise _error(rule, path, f"unsupported count field(s): {', '.join(unknown)}.", index)
        minimum = count_spec.get("min")
        maximum = count_spec.get("max")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
        ):
            raise _error(rule, path, "count min and max must be integers.", index)
        if minimum < 1 or maximum < 1:
            raise _error(rule, path, "count min and max must be positive integers.", index)
        if minimum > maximum:
            raise _error(rule, path, "count min must be less than or equal to max.", index)
        return
    raise _error(rule, path, "unknown count format: expected integer or {'min': int, 'max': int}.", index)


def _validate_node_attributes(attributes: Any, rule: dict, path: str, index: int | None = None) -> None:
    if not isinstance(attributes, dict):
        raise _error(rule, path, "must be a mapping.", index)
    unknown = _unknown_keys(attributes, NODE_ATTRIBUTE_KEYS)
    if unknown:
        raise _error(rule, path, f"unsupported node attribute(s): {', '.join(unknown)}.", index)


def sample_count(count_spec: Any, rng: Random) -> int:
    """Sample a node count from a fixed integer or min/max mapping."""
    if isinstance(count_spec, int) and not isinstance(count_spec, bool):
        if count_spec < 1:
            raise RuleSchemaError("Count must be a positive integer.")
        return count_spec
    if isinstance(count_spec, dict):
        if set(count_spec) != {"min", "max"}:
            raise RuleSchemaError("Unknown count format: expected {'min': int, 'max': int}.")
        minimum = count_spec.get("min")
        maximum = count_spec.get("max")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 1
            or maximum < 1
            or maximum < minimum
        ):
            raise RuleSchemaError("Count bounds must be positive integers with min <= max.")
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


def validate_grammar_rule(
    rule: dict,
    *,
    allowed_node_types: list[str] | None = None,
    allowed_edge_types: list[str] | None = None,
    index: int | None = None,
) -> None:
    """Validate one minimal grammar rule."""
    if not isinstance(rule, dict):
        raise RuleSchemaError(f"{_rule_label(rule, index)}: must be a mapping.")
    name = rule.get("name")
    if not isinstance(name, str) or not name:
        raise _error(rule, "name", "missing rule name.", index)
    unknown_rule_keys = _unknown_keys(rule, {"name", "match", "action"})
    if unknown_rule_keys:
        raise _error(rule, ".", f"unsupported rule field(s): {', '.join(unknown_rule_keys)}.", index)

    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        raise _error(rule, "match", "missing match section.", index)
    unknown_match_keys = _unknown_keys(match, MATCH_KEYS)
    if unknown_match_keys:
        raise _error(rule, "match", f"unsupported match key(s): {', '.join(unknown_match_keys)}.", index)
    if "type" in match:
        if not isinstance(match["type"], str) or not match["type"]:
            raise _error(rule, "match.type", "must be a non-empty string.", index)
        if allowed_node_types is not None and match["type"] not in allowed_node_types:
            raise _error(rule, "match.type", f"unknown node type '{match['type']}'.", index)
    if "is_abstract" in match and not isinstance(match["is_abstract"], bool):
        raise _error(rule, "match.is_abstract", "must be true or false.", index)
    for string_key in ("zone", "zone_type"):
        if string_key in match and not isinstance(match[string_key], str):
            raise _error(rule, f"match.{string_key}", "must be a string.", index)

    action = rule.get("action")
    if not isinstance(action, dict):
        raise _error(rule, "action", "missing action section.", index)
    unknown_action_keys = _unknown_keys(action, ACTION_KEYS)
    if unknown_action_keys:
        raise _error(rule, "action", f"unsupported action field(s): {', '.join(unknown_action_keys)}.", index)

    if "remove_matched_node" in action and not isinstance(action["remove_matched_node"], bool):
        raise _error(rule, "action.remove_matched_node", "must be true or false.", index)
    if "update_matched_node_attributes" in action:
        _validate_node_attributes(action["update_matched_node_attributes"], rule, "action.update_matched_node_attributes", index)

    create_nodes = action.get("create_nodes", [])
    if not isinstance(create_nodes, list):
        raise _error(rule, "action.create_nodes", "must be a list.", index)
    aliases: set[str] = set()
    for node_index, entry in enumerate(create_nodes):
        node_path = f"action.create_nodes[{node_index}]"
        if not isinstance(entry, dict):
            raise _error(rule, node_path, "must be a mapping.", index)
        unknown_node_keys = _unknown_keys(entry, CREATE_NODE_KEYS)
        if unknown_node_keys:
            raise _error(rule, node_path, f"unsupported create_nodes field(s): {', '.join(unknown_node_keys)}.", index)
        alias = entry.get("alias")
        if not isinstance(alias, str) or not alias:
            raise _error(rule, f"{node_path}.alias", "must be a non-empty string.", index)
        if alias in SPECIAL_ALIASES:
            raise _error(rule, f"{node_path}.alias", "must not use a reserved alias.", index)
        if alias in aliases:
            raise _error(rule, f"{node_path}.alias", f"duplicate alias '{alias}'.", index)
        aliases.add(alias)
        if "type" not in entry:
            raise _error(rule, f"{node_path}.type", "is required.", index)
        _validate_choice_spec(entry["type"], f"{node_path}.type", rule, index, allowed_node_types)
        _validate_count_spec(entry.get("count", 1), rule, f"{node_path}.count", index)
        attributes = entry.get("attributes", {})
        _validate_node_attributes(attributes, rule, f"{node_path}.attributes", index)

    create_edges = action.get("create_edges", [])
    if not isinstance(create_edges, list):
        raise _error(rule, "action.create_edges", "must be a list.", index)
    valid_aliases = aliases | SPECIAL_ALIASES
    for edge_index, entry in enumerate(create_edges):
        edge_path = f"action.create_edges[{edge_index}]"
        if not isinstance(entry, dict):
            raise _error(rule, edge_path, "must be a mapping.", index)
        unknown_edge_keys = _unknown_keys(entry, CREATE_EDGE_KEYS)
        if unknown_edge_keys:
            raise _error(rule, edge_path, f"unsupported create_edges field(s): {', '.join(unknown_edge_keys)}.", index)
        for endpoint in ("source", "target"):
            alias = entry.get(endpoint)
            if not isinstance(alias, str) or not alias:
                raise _error(rule, f"{edge_path}.{endpoint}", "must be a non-empty string.", index)
            if alias not in valid_aliases:
                raise _error(rule, f"{edge_path}.{endpoint}", f"unknown alias '{alias}'.", index)
        edge_type = entry.get("edge_type")
        if not isinstance(edge_type, str) or not edge_type:
            raise _error(rule, f"{edge_path}.edge_type", "must be a non-empty string.", index)
        if allowed_edge_types is not None and edge_type not in allowed_edge_types:
            raise _error(rule, f"{edge_path}.edge_type", f"unknown edge type '{edge_type}'.", index)
        mode = entry.get("mode", "one_to_one")
        if mode not in EDGE_MODES:
            raise _error(rule, f"{edge_path}.mode", f"invalid mode '{mode}'.", index)


def load_grammar_rules(config: dict) -> list[dict]:
    """Load and validate grammar rules from a raw config mapping."""
    rules = config.get("grammar_rules", [])
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise RuleSchemaError("Config field 'grammar_rules' must be a list.")
    from graph_layout_synth.config_contract import build_config_contract

    contract = build_config_contract(config)
    for index, rule in enumerate(rules):
        validate_grammar_rule(
            rule,
            allowed_node_types=contract.allowed_node_types,
            allowed_edge_types=contract.allowed_edge_types,
            index=index,
        )
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
