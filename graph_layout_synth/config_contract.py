"""Derived config vocabulary and validation context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FALLBACK_EXCLUDED_ROOM_TYPES = {"BuildingFloor", "Zone"}
FALLBACK_CORRIDOR_TOKEN = "corridor"
DEFAULT_ACCESSIBILITY_SOURCE = "PatientRoom"
DEFAULT_ACCESSIBILITY_TARGET = "ClinicalSupport"


@dataclass(frozen=True)
class ConfigContract:
    """Live vocabulary and constraints derived from a raw YAML config."""

    allowed_node_types: list[str]
    allowed_edge_types: list[str]
    room_like_node_types: list[str]
    corridor_node_types: list[str]
    support_node_types: list[str]
    semantic_node_groups: dict[str, list[str]]
    room_mix_targets: dict[str, Any]
    room_mix_reachable_ranges: dict[str, dict[str, int]]
    typed_accessibility_pairs: list[dict[str, str]]
    grammar_rule_names: list[str]
    grammar_rule_schema_summary: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        """Return a compact JSON-serializable summary."""
        return {
            "allowed_node_types": self.allowed_node_types,
            "allowed_edge_types": self.allowed_edge_types,
            "room_like_node_types": self.room_like_node_types,
            "corridor_node_types": self.corridor_node_types,
            "support_node_types": self.support_node_types,
            "semantic_node_groups": self.semantic_node_groups,
            "room_mix_targets": self.room_mix_targets,
            "room_mix_reachable_ranges": self.room_mix_reachable_ranges,
            "typed_accessibility_pairs": self.typed_accessibility_pairs,
            "grammar_rule_names": self.grammar_rule_names,
            "grammar_rule_schema_summary": self.grammar_rule_schema_summary,
        }

    def typed_accessibility_type_pairs(self, edge_type: str | None = "door") -> list[tuple[str, str]]:
        """Return source/target pairs, optionally filtered by edge type."""
        pairs = []
        for pair in self.typed_accessibility_pairs:
            if edge_type is not None and pair.get("edge_type", "door") != edge_type:
                continue
            pairs.append((pair["source_type"], pair["target_type"]))
        return pairs


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _mapping_of_string_lists(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    groups: dict[str, list[str]] = {}
    for group_name, node_types in value.items():
        if isinstance(group_name, str):
            groups[group_name] = _string_list(node_types)
    return groups


def _infer_corridor_types(allowed_node_types: list[str], semantic_groups: dict[str, list[str]]) -> list[str]:
    configured = semantic_groups.get("corridor") or semantic_groups.get("corridors")
    if configured:
        return _unique(configured)
    return [
        node_type
        for node_type in allowed_node_types
        if FALLBACK_CORRIDOR_TOKEN in node_type.lower()
    ]


def _infer_room_like_types(
    config: dict[str, Any],
    allowed_node_types: list[str],
    corridor_node_types: list[str],
    semantic_groups: dict[str, list[str]],
) -> list[str]:
    configured = semantic_groups.get("room_like") or semantic_groups.get("rooms")
    if configured:
        return _unique(configured)
    room_type_counts = config.get("room_type_counts", {})
    counted_room_types = _string_list(list(room_type_counts)) if isinstance(room_type_counts, dict) else []
    if counted_room_types:
        return _unique(counted_room_types)
    excluded = set(FALLBACK_EXCLUDED_ROOM_TYPES) | set(corridor_node_types)
    return [node_type for node_type in allowed_node_types if node_type not in excluded]


def _infer_support_types(
    config: dict[str, Any],
    allowed_node_types: list[str],
    semantic_groups: dict[str, list[str]],
) -> list[str]:
    configured = semantic_groups.get("support") or semantic_groups.get("support_rooms")
    if configured:
        return _unique(configured)
    stochastic = config.get("stochastic", {})
    if isinstance(stochastic, dict):
        support_choices = _string_list(stochastic.get("support_room_choices"))
        if support_choices:
            return _unique(support_choices)
    return [
        node_type
        for node_type in allowed_node_types
        if "support" in node_type.lower()
    ]


def _default_accessibility_pairs(allowed_node_types: list[str], allowed_edge_types: list[str]) -> list[dict[str, str]]:
    if (
        DEFAULT_ACCESSIBILITY_SOURCE in allowed_node_types
        and DEFAULT_ACCESSIBILITY_TARGET in allowed_node_types
        and "door" in allowed_edge_types
    ):
        return [
            {
                "source_type": DEFAULT_ACCESSIBILITY_SOURCE,
                "target_type": DEFAULT_ACCESSIBILITY_TARGET,
                "edge_type": "door",
            }
        ]
    return []


def _typed_accessibility_pairs(config: dict[str, Any], allowed_node_types: list[str], allowed_edge_types: list[str]) -> list[dict[str, str]]:
    raw_pairs = config.get("typed_accessibility_pairs")
    if raw_pairs is None:
        return _default_accessibility_pairs(allowed_node_types, allowed_edge_types)
    if not isinstance(raw_pairs, list):
        return []

    pairs: list[dict[str, str]] = []
    for pair in raw_pairs:
        if not isinstance(pair, dict):
            continue
        source_type = pair.get("source_type")
        target_type = pair.get("target_type")
        edge_type = pair.get("edge_type", "door")
        if isinstance(source_type, str) and isinstance(target_type, str) and isinstance(edge_type, str):
            pairs.append(
                {
                    "source_type": source_type,
                    "target_type": target_type,
                    "edge_type": edge_type,
                }
            )
    return pairs


def _grammar_rule_names(config: dict[str, Any]) -> list[str]:
    names = []
    rules = config.get("grammar_rules", [])
    if not isinstance(rules, list):
        return names
    for index, rule in enumerate(rules):
        if isinstance(rule, dict) and isinstance(rule.get("name"), str):
            names.append(rule["name"])
        else:
            names.append(f"grammar_rules[{index}]")
    return names


def _type_values(type_spec: Any) -> list[str]:
    if isinstance(type_spec, str):
        return [type_spec]
    if isinstance(type_spec, dict) and isinstance(type_spec.get("choices"), list):
        return [value for value in type_spec["choices"] if isinstance(value, str)]
    return []


def _count_range(count_spec: Any) -> tuple[int, int]:
    if count_spec is None:
        return (1, 1)
    if isinstance(count_spec, int) and not isinstance(count_spec, bool):
        return (count_spec, count_spec)
    if isinstance(count_spec, dict):
        minimum = count_spec.get("min")
        maximum = count_spec.get("max")
        if isinstance(minimum, int) and not isinstance(minimum, bool) and isinstance(maximum, int) and not isinstance(maximum, bool):
            return (minimum, maximum)
    return (0, 0)


def _find_rule(config: dict[str, Any], match_type: str) -> dict[str, Any] | None:
    rules = config.get("grammar_rules", [])
    if not isinstance(rules, list):
        return None
    for rule in rules:
        if isinstance(rule, dict) and rule.get("match", {}).get("type") == match_type:
            return rule
    return None


def _estimated_zone_count_range(config: dict[str, Any]) -> tuple[int, int]:
    floor_rule = _find_rule(config, "BuildingFloor")
    if floor_rule:
        zone_min = 0
        zone_max = 0
        for entry in floor_rule.get("action", {}).get("create_nodes", []):
            if isinstance(entry, dict) and "Zone" in _type_values(entry.get("type")):
                entry_min, entry_max = _count_range(entry.get("count", 1))
                zone_min += entry_min
                zone_max += entry_max
        if zone_min or zone_max:
            return (zone_min, zone_max)

    stochastic = config.get("stochastic", {})
    if isinstance(stochastic, dict):
        minimum = stochastic.get("min_zone_count")
        maximum = stochastic.get("max_zone_count")
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > 0 and maximum >= minimum:
            return (minimum, maximum)
    return (1, 1)


def _room_mix_reachable_ranges(config: dict[str, Any], room_mix_targets: dict[str, Any]) -> dict[str, dict[str, int]]:
    if not room_mix_targets or room_mix_targets.get("enabled", True) is False:
        return {}
    expected_counts = room_mix_targets.get("expected_room_type_counts", {})
    if not isinstance(expected_counts, dict) or not expected_counts:
        return {}

    zone_rule = _find_rule(config, "Zone")
    if not zone_rule:
        return {}
    zone_min, zone_max = _estimated_zone_count_range(config)
    per_zone_ranges: dict[str, tuple[int, int]] = {}
    for entry in zone_rule.get("action", {}).get("create_nodes", []):
        if not isinstance(entry, dict):
            continue
        values = _type_values(entry.get("type"))
        if len(values) != 1:
            continue
        node_type = values[0]
        if node_type not in expected_counts:
            continue
        count_min, count_max = _count_range(entry.get("count", 1))
        previous_min, previous_max = per_zone_ranges.get(node_type, (0, 0))
        per_zone_ranges[node_type] = (previous_min + count_min, previous_max + count_max)

    return {
        node_type: {
            "min": zone_min * count_range[0],
            "max": zone_max * count_range[1],
        }
        for node_type, count_range in sorted(per_zone_ranges.items())
    }


def _validate_known_node_types(
    field_name: str,
    values: list[str],
    allowed_node_types: list[str],
    errors: list[str],
) -> None:
    unknown = sorted(set(values) - set(allowed_node_types))
    if unknown:
        errors.append(
            f"Config contract field '{field_name}' references unknown node type(s): "
            + ", ".join(unknown)
            + "."
        )


def _validate_contract(contract: ConfigContract) -> ConfigContract:
    errors = list(contract.errors)
    warnings = list(contract.warnings)

    for group_name, node_types in contract.semantic_node_groups.items():
        _validate_known_node_types(
            f"semantic_node_groups.{group_name}",
            node_types,
            contract.allowed_node_types,
            errors,
        )

    _validate_known_node_types("room_like_node_types", contract.room_like_node_types, contract.allowed_node_types, errors)
    _validate_known_node_types("corridor_node_types", contract.corridor_node_types, contract.allowed_node_types, errors)
    _validate_known_node_types("support_node_types", contract.support_node_types, contract.allowed_node_types, errors)

    for index, pair in enumerate(contract.typed_accessibility_pairs):
        _validate_known_node_types(
            f"typed_accessibility_pairs[{index}].source_type",
            [pair.get("source_type", "")],
            contract.allowed_node_types,
            errors,
        )
        _validate_known_node_types(
            f"typed_accessibility_pairs[{index}].target_type",
            [pair.get("target_type", "")],
            contract.allowed_node_types,
            errors,
        )
        edge_type = pair.get("edge_type")
        if edge_type not in contract.allowed_edge_types:
            errors.append(
                f"Config contract field 'typed_accessibility_pairs[{index}].edge_type' "
                f"references unknown edge type '{edge_type}'."
            )

    if contract.room_mix_targets:
        enabled = contract.room_mix_targets.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append("Config contract field 'room_mix_targets.enabled' must be true or false.")
        expected_counts = contract.room_mix_targets.get("expected_room_type_counts", {})
        if expected_counts:
            if not isinstance(expected_counts, dict):
                errors.append("Config contract field 'room_mix_targets.expected_room_type_counts' must be a mapping.")
            else:
                _validate_known_node_types(
                    "room_mix_targets.expected_room_type_counts",
                    [key for key in expected_counts if isinstance(key, str)],
                    contract.allowed_node_types,
                    errors,
                )
                for node_type, expected_count in expected_counts.items():
                    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
                        continue
                    reachable_range = contract.room_mix_reachable_ranges.get(node_type)
                    if not reachable_range:
                        warnings.append(
                            "Config contract could not compute a room-mix reachable range for "
                            f"{node_type}; use exact create_nodes types in the Zone grammar rule to enable this check."
                        )
                        continue
                    if not (reachable_range["min"] <= expected_count <= reachable_range["max"]):
                        errors.append(
                            "Config contract field 'room_mix_targets.expected_room_type_counts."
                            f"{node_type}'={expected_count} is outside reachable grammar range "
                            f"{reachable_range['min']}-{reachable_range['max']}."
                        )

    if not contract.support_node_types:
        warnings.append(
            "Config contract could not infer support_node_types; define semantic_node_groups.support "
            "or stochastic.support_room_choices if support metrics are expected."
        )
    if not contract.typed_accessibility_pairs:
        warnings.append(
            "Config contract has no typed_accessibility_pairs; typed accessibility summaries will use no config-derived pairs."
        )

    return ConfigContract(
        allowed_node_types=contract.allowed_node_types,
        allowed_edge_types=contract.allowed_edge_types,
        room_like_node_types=contract.room_like_node_types,
        corridor_node_types=contract.corridor_node_types,
        support_node_types=contract.support_node_types,
        semantic_node_groups=contract.semantic_node_groups,
        room_mix_targets=contract.room_mix_targets,
        room_mix_reachable_ranges=contract.room_mix_reachable_ranges,
        typed_accessibility_pairs=contract.typed_accessibility_pairs,
        grammar_rule_names=contract.grammar_rule_names,
        grammar_rule_schema_summary=contract.grammar_rule_schema_summary,
        errors=errors,
        warnings=warnings,
    )


def build_config_contract(config: dict[str, Any]) -> ConfigContract:
    """Build a live contract from a raw YAML config mapping."""
    allowed_node_types = _unique(_string_list(config.get("allowed_node_types")))
    allowed_edge_types = _unique(_string_list(config.get("allowed_edge_types")))
    semantic_groups = _mapping_of_string_lists(config.get("semantic_node_groups"))

    corridor_node_types = _infer_corridor_types(allowed_node_types, semantic_groups)
    room_like_node_types = _infer_room_like_types(config, allowed_node_types, corridor_node_types, semantic_groups)
    support_node_types = _infer_support_types(config, allowed_node_types, semantic_groups)

    semantic_node_groups = dict(semantic_groups)
    semantic_node_groups.setdefault("room_like", room_like_node_types)
    semantic_node_groups.setdefault("corridor", corridor_node_types)
    semantic_node_groups.setdefault("support", support_node_types)

    room_mix_targets = config.get("room_mix_targets", {})
    if not isinstance(room_mix_targets, dict):
        room_mix_targets = {}
    room_mix_reachable_ranges = _room_mix_reachable_ranges(config, room_mix_targets)

    typed_pairs = _typed_accessibility_pairs(config, allowed_node_types, allowed_edge_types)
    grammar_rule_names = _grammar_rule_names(config)
    schema_summary = {
        "rule_count": len(grammar_rule_names),
        "supported_edge_modes": ["adjacent_pairs", "each_to_one", "one_to_each", "one_to_one"],
        "supports_create_node_count_ranges": True,
        "supports_type_choices": True,
    }

    return _validate_contract(
        ConfigContract(
            allowed_node_types=allowed_node_types,
            allowed_edge_types=allowed_edge_types,
            room_like_node_types=room_like_node_types,
            corridor_node_types=corridor_node_types,
            support_node_types=support_node_types,
            semantic_node_groups=semantic_node_groups,
            room_mix_targets=room_mix_targets,
            room_mix_reachable_ranges=room_mix_reachable_ranges,
            typed_accessibility_pairs=typed_pairs,
            grammar_rule_names=grammar_rule_names,
            grammar_rule_schema_summary=schema_summary,
        )
    )
