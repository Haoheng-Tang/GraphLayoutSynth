"""Optional Claude assistant for proposing YAML grammar/config variants."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from graph_layout_synth.config import ConfigError, validate_config
from graph_layout_synth.config_contract import ConfigContract, build_config_contract
from graph_layout_synth.llm_evaluator import DEFAULT_CLAUDE_MODEL, load_llm_environment


class GrammarVariantError(RuntimeError):
    """Raised when a grammar variant cannot be proposed or validated."""


GRAMMAR_VARIANT_SYSTEM_PROMPT = (
    "You propose GraphLayoutSynth YAML config variants. "
    "Return complete schema-valid YAML configs, not raw graphs. "
    "The deterministic validator and generator remain the source of truth."
)

VARIANT_REQUIREMENTS_KEYS = {"version", "design_intent", "room_mix_targets"}
ROOM_MIX_TARGET_KEYS = {
    "enabled",
    "patient_alias",
    "clinical_alias",
    "staff_alias",
    "patient_total_min",
    "patient_total_max",
    "clinical_ratio",
    "staff_ratio",
    "ratio_tolerance",
    "suggested_per_zone_counts",
    "expected_room_type_counts",
}
ROOM_MIX_COUNT_KEYS = {"patient", "clinical", "staff"}
FALLBACK_PATIENT_TYPE = "PatientRoom"
FALLBACK_CLINICAL_TYPE = "ClinicalSupport"
FALLBACK_STAFF_TYPE = "StaffSupport"


def _compact_json(data: Any, *, max_chars: int = 12000) -> str:
    text = json.dumps(data, indent=2, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _unknown_mapping_keys(data: dict[str, Any], allowed_keys: set[str]) -> list[str]:
    return sorted(key for key in data if key not in allowed_keys)


def _require_positive_int(data: dict[str, Any], key: str, path: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GrammarVariantError(f"{path}.{key} must be a positive integer.")
    return value


def _require_ratio(data: dict[str, Any], key: str, path: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise GrammarVariantError(f"{path}.{key} must be a non-negative number.")
    return float(value)


def _validate_count_parameter(value: Any, path: str) -> None:
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise GrammarVariantError(f"{path} must be a positive integer.")
        return
    if isinstance(value, dict):
        unknown = _unknown_mapping_keys(value, {"min", "max"})
        if unknown:
            raise GrammarVariantError(f"{path} has unsupported field(s): {', '.join(unknown)}.")
        minimum = _require_positive_int(value, "min", path)
        maximum = _require_positive_int(value, "max", path)
        if minimum > maximum:
            raise GrammarVariantError(f"{path}.min must be less than or equal to max.")
        return
    raise GrammarVariantError(f"{path} must be a positive integer or min/max mapping.")


def _first_group_type(contract: ConfigContract | None, *group_names: str) -> str | None:
    if contract is None:
        return None
    for group_name in group_names:
        values = contract.semantic_node_groups.get(group_name, [])
        if values:
            return values[0]
    return None


def _default_room_mix_alias_types(contract: ConfigContract | None = None) -> dict[str, str]:
    if contract is None:
        return {
            "patient": FALLBACK_PATIENT_TYPE,
            "clinical": FALLBACK_CLINICAL_TYPE,
            "staff": FALLBACK_STAFF_TYPE,
        }

    patient_type = (
        _first_group_type(contract, "patient", "patient_room", "patient_rooms")
        or (FALLBACK_PATIENT_TYPE if FALLBACK_PATIENT_TYPE in contract.allowed_node_types else None)
        or (contract.room_like_node_types[0] if contract.room_like_node_types else None)
    )
    clinical_type = (
        _first_group_type(contract, "clinical", "clinical_support")
        or (FALLBACK_CLINICAL_TYPE if FALLBACK_CLINICAL_TYPE in contract.allowed_node_types else None)
        or (contract.support_node_types[0] if contract.support_node_types else None)
    )
    staff_type = (
        _first_group_type(contract, "staff", "staff_support")
        or (FALLBACK_STAFF_TYPE if FALLBACK_STAFF_TYPE in contract.allowed_node_types else None)
        or next((node_type for node_type in contract.support_node_types if node_type != clinical_type), None)
        or clinical_type
    )
    return {
        "patient": patient_type or FALLBACK_PATIENT_TYPE,
        "clinical": clinical_type or FALLBACK_CLINICAL_TYPE,
        "staff": staff_type or FALLBACK_STAFF_TYPE,
    }


def _room_mix_alias_types_from_requirements(
    requirements: dict[str, Any],
    contract: ConfigContract | None = None,
) -> dict[str, str]:
    room_mix = requirements.get("room_mix_targets", {})
    default_types = _default_room_mix_alias_types(contract)
    return {
        room_mix.get("patient_alias", "patient"): default_types["patient"],
        room_mix.get("clinical_alias", "clinical"): default_types["clinical"],
        room_mix.get("staff_alias", "staff"): default_types["staff"],
    }


def validate_variant_requirements(
    requirements: dict[str, Any],
    contract: ConfigContract | None = None,
) -> dict[str, Any]:
    """Validate structured variant requirements and return a normalized copy."""
    if not isinstance(requirements, dict):
        raise GrammarVariantError("Variant requirements must be a mapping.")
    unknown = _unknown_mapping_keys(requirements, VARIANT_REQUIREMENTS_KEYS)
    if unknown:
        raise GrammarVariantError(f"Variant requirements have unsupported field(s): {', '.join(unknown)}.")

    version = requirements.get("version")
    if version != 1:
        raise GrammarVariantError("Variant requirements field 'version' must be 1.")
    design_intent = requirements.get("design_intent", "")
    if design_intent is not None and not isinstance(design_intent, str):
        raise GrammarVariantError("Variant requirements field 'design_intent' must be a string.")

    normalized: dict[str, Any] = {"version": 1, "design_intent": design_intent or ""}
    room_mix = requirements.get("room_mix_targets")
    if room_mix is None:
        return normalized
    if not isinstance(room_mix, dict):
        raise GrammarVariantError("Variant requirements field 'room_mix_targets' must be a mapping.")
    unknown_room_mix = _unknown_mapping_keys(room_mix, ROOM_MIX_TARGET_KEYS)
    if unknown_room_mix:
        raise GrammarVariantError(
            "Variant requirements field 'room_mix_targets' has unsupported field(s): "
            + ", ".join(unknown_room_mix)
            + "."
        )

    enabled = room_mix.get("enabled", True)
    if not isinstance(enabled, bool):
        raise GrammarVariantError("room_mix_targets.enabled must be true or false.")
    normalized_room_mix: dict[str, Any] = {"enabled": enabled}
    if not enabled:
        normalized["room_mix_targets"] = normalized_room_mix
        return normalized

    for key in ("patient_alias", "clinical_alias", "staff_alias"):
        value = room_mix.get(key)
        if not isinstance(value, str) or not value:
            raise GrammarVariantError(f"room_mix_targets.{key} must be a non-empty string.")
        normalized_room_mix[key] = value

    patient_total_min = _require_positive_int(room_mix, "patient_total_min", "room_mix_targets")
    patient_total_max = _require_positive_int(room_mix, "patient_total_max", "room_mix_targets")
    if patient_total_min > patient_total_max:
        raise GrammarVariantError("room_mix_targets.patient_total_min must be less than or equal to patient_total_max.")
    normalized_room_mix["patient_total_min"] = patient_total_min
    normalized_room_mix["patient_total_max"] = patient_total_max
    normalized_room_mix["clinical_ratio"] = _require_ratio(room_mix, "clinical_ratio", "room_mix_targets")
    normalized_room_mix["staff_ratio"] = _require_ratio(room_mix, "staff_ratio", "room_mix_targets")
    normalized_room_mix["ratio_tolerance"] = _require_ratio(room_mix, "ratio_tolerance", "room_mix_targets")

    suggested_counts = room_mix.get("suggested_per_zone_counts", {})
    if suggested_counts:
        if not isinstance(suggested_counts, dict):
            raise GrammarVariantError("room_mix_targets.suggested_per_zone_counts must be a mapping.")
        unknown_counts = _unknown_mapping_keys(suggested_counts, ROOM_MIX_COUNT_KEYS)
        if unknown_counts:
            raise GrammarVariantError(
                "room_mix_targets.suggested_per_zone_counts has unsupported field(s): "
                + ", ".join(unknown_counts)
                + "."
            )
        for key, value in suggested_counts.items():
            _validate_count_parameter(value, f"room_mix_targets.suggested_per_zone_counts.{key}")
    normalized_room_mix["suggested_per_zone_counts"] = suggested_counts

    expected_counts = room_mix.get("expected_room_type_counts", {})
    if expected_counts:
        if not isinstance(expected_counts, dict):
            raise GrammarVariantError("room_mix_targets.expected_room_type_counts must be a mapping.")
        allowed_room_types = set(contract.allowed_node_types) if contract is not None else set(_default_room_mix_alias_types().values())
        unknown_room_types = _unknown_mapping_keys(expected_counts, allowed_room_types)
        if unknown_room_types:
            raise GrammarVariantError(
                "room_mix_targets.expected_room_type_counts has unsupported field(s): "
                + ", ".join(unknown_room_types)
                + "."
            )
        for key in expected_counts:
            _require_positive_int(expected_counts, key, "room_mix_targets.expected_room_type_counts")
    normalized_room_mix["expected_room_type_counts"] = expected_counts

    normalized["room_mix_targets"] = normalized_room_mix
    return normalized


def load_variant_requirements(path: str | Path, contract: ConfigContract | None = None) -> dict[str, Any]:
    """Load YAML/JSON variant requirements and validate their strict structure."""
    requirements_path = Path(path)
    try:
        raw_requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GrammarVariantError(f"Variant requirements file not found: {requirements_path}") from exc
    except yaml.YAMLError as exc:
        raise GrammarVariantError(f"Variant requirements file is not valid YAML/JSON: {requirements_path}") from exc
    return validate_variant_requirements(raw_requirements, contract=contract)


def room_mix_kwargs_from_requirements(
    requirements: dict[str, Any] | None,
    contract: ConfigContract | None = None,
) -> dict[str, Any]:
    """Extract validate_room_mix_targets keyword arguments from requirements."""
    if not requirements:
        return {}
    room_mix = requirements.get("room_mix_targets")
    if not isinstance(room_mix, dict) or not room_mix.get("enabled", False):
        return {}
    return {
        "patient_total_min": room_mix["patient_total_min"],
        "patient_total_max": room_mix["patient_total_max"],
        "clinical_ratio": room_mix["clinical_ratio"],
        "staff_ratio": room_mix["staff_ratio"],
        "ratio_tolerance": room_mix["ratio_tolerance"],
        "patient_alias": room_mix["patient_alias"],
        "clinical_alias": room_mix["clinical_alias"],
        "staff_alias": room_mix["staff_alias"],
        "expected_alias_types": _room_mix_alias_types_from_requirements(requirements, contract),
    }


def room_mix_kwargs_from_contract(contract: ConfigContract | None) -> dict[str, Any]:
    """Extract room-mix validation kwargs from the live config contract."""
    if contract is None or not contract.room_mix_targets:
        return {}
    requirements = validate_variant_requirements(
        {
            "version": 1,
            "design_intent": "",
            "room_mix_targets": contract.room_mix_targets,
        },
        contract=contract,
    )
    return room_mix_kwargs_from_requirements(requirements, contract=contract)


def variant_requirements_to_design_intent(
    requirements: dict[str, Any] | None,
    contract: ConfigContract | None = None,
) -> str | None:
    """Render structured variant requirements into prompt text for Claude."""
    if not requirements:
        return None
    parts = []
    design_intent = requirements.get("design_intent")
    if design_intent:
        parts.append(str(design_intent).strip())

    room_mix = requirements.get("room_mix_targets")
    if isinstance(room_mix, dict) and room_mix.get("enabled", False):
        alias_types = _room_mix_alias_types_from_requirements(requirements, contract)
        alias_type_text = ", ".join(f"`{alias}` for `{node_type}`" for alias, node_type in alias_types.items())
        patient_type = alias_types[room_mix["patient_alias"]]
        clinical_type = alias_types[room_mix["clinical_alias"]]
        staff_type = alias_types[room_mix["staff_alias"]]
        parts.append(
            "Structured room-mix requirements:\n"
            f"- Use separate aliases {alias_type_text}.\n"
            f"- Generate {room_mix['patient_total_min']}-{room_mix['patient_total_max']} total {patient_type} nodes.\n"
            f"- Generate {clinical_type} at about {room_mix['clinical_ratio']:.0%} of {patient_type} count.\n"
            f"- Generate {staff_type} at about {room_mix['staff_ratio']:.0%} of {patient_type} count.\n"
            f"- Ratio tolerance for semantic validation is {room_mix['ratio_tolerance']:.0%}.\n"
            "- Do not group independently targeted room types under a shared room/support alias."
        )
        suggested_counts = room_mix.get("suggested_per_zone_counts")
        if suggested_counts:
            parts.append(
                "Suggested per-zone create_nodes counts:\n"
                + yaml.safe_dump(suggested_counts, sort_keys=True).strip()
            )
        expected_counts = room_mix.get("expected_room_type_counts")
        if expected_counts:
            parts.append(
                "Update top-level room_type_counts to align with:\n"
                + yaml.safe_dump(expected_counts, sort_keys=True).strip()
                + "\nEnsure each top-level room_type_counts value falls within the total min/max range "
                "implied by the grammar rule counts and the generated zone count range."
            )
    return "\n\n".join(parts).strip() or None


def build_grammar_variant_prompt(
    base_config: dict,
    grammar_skills_text: str,
    design_intent: str | None = None,
    diversity_report: dict | None = None,
    review_summary: dict | None = None,
    archive: dict | None = None,
) -> str:
    """Build the prompt for proposing a complete YAML config variant."""
    base_yaml = yaml.safe_dump(base_config, sort_keys=False)
    contract = build_config_contract(base_config)
    sections = [
        "# Task\n"
        "Propose one complete GraphLayoutSynth YAML config variant. "
        "The variant will be validated before use and then run through the existing procedural generator.\n",
        "# Grammar Config Skills\n" + grammar_skills_text.strip(),
        "# Live Config Contract\n"
        "These values are derived from the actual base YAML config and are the config-specific source of truth. "
        "Use only the listed node and edge vocabularies unless the generated config updates every relevant section consistently.\n"
        "```json\n"
        + _compact_json(contract.to_summary())
        + "\n```",
        "# Base YAML Config\n```yaml\n" + base_yaml.strip() + "\n```",
    ]
    if design_intent:
        sections.append("# Design Intent\n" + design_intent.strip())
    if diversity_report is not None:
        sections.append("# Diversity Report Summary\n```json\n" + _compact_json(diversity_report) + "\n```")
    if review_summary is not None:
        sections.append("# Review Summary\n```json\n" + _compact_json(review_summary) + "\n```")
    if archive is not None:
        sections.append("# Final Output Archive Summary\n```json\n" + _compact_json(archive) + "\n```")

    sections.append(
        "# Output Instructions\n"
        "Return a complete YAML config only in a fenced yaml block.\n"
        "Do not return partial patches.\n"
        "Do not invent unsupported fields.\n"
        "Do not overwrite the base config.\n"
        "Preserve required top-level sections.\n"
        "Preserve schema validity.\n"
        "Use the same grammar config format described in GRAMMAR_CONFIG_SKILLS.md.\n"
        "Keep allowed node types, allowed edge types, semantic groups, room-mix targets, typed accessibility pairs, "
        "and grammar rules internally consistent with the Live Config Contract.\n"
        "When room_mix_targets.expected_room_type_counts is present, ensure each room_type_counts value is reachable "
        "from the generated grammar counts. For example, zone_count_range multiplied by per-zone create_nodes counts "
        "must include the declared room_type_counts value.\n"
        "Prefer modifying stochastic parameters, grammar rule counts, room-type choices, support-room mixes, and grammar-rule variants.\n"
        "Do not include raw graph JSON.\n"
        "Do not include prose inside the YAML block.\n"
        "If you include rationale, put it outside the YAML block."
    )
    return "\n\n".join(sections).strip() + "\n"


INSTRUCTION_VARIANT_PREAMBLE = (
    "Translate the following design instructions into a complete GraphLayoutSynth "
    "YAML config variant.\n\n"
    "Express each design rule using supported existing config concepts such as "
    "grammar_rules (match/action, create_nodes, create_edges, edge modes, stochastic "
    "counts and choices), typed_accessibility_pairs, semantic_node_groups, "
    "room_mix_targets, validation settings, or ranking weights and targets. If a rule "
    "cannot be expressed exactly with these existing config concepts, approximate it "
    "as closely as possible using them rather than inventing a new, unsupported "
    "config field.\n\n"
    "Do not generate graph samples, node-link JSON, or any other raw graph output. "
    "Propose only a YAML config variant; GraphLayoutSynth's deterministic code "
    "validates the config and generates graphs from it separately."
)


def build_instruction_variant_design_intent(instructions_text: str) -> str:
    """Wrap raw design instructions with instruction-variant framing for the prompt."""
    return (
        INSTRUCTION_VARIANT_PREAMBLE
        + "\n\n# Design Instructions\n"
        + instructions_text.strip()
    )


def build_instruction_variant_prompt(
    base_config: dict,
    grammar_skills_text: str,
    instructions_text: str,
) -> str:
    """Build the prompt for translating free-form design instructions into a config variant.

    Reuses ``build_grammar_variant_prompt`` for the base config, live config
    contract, and strict complete-YAML output instructions, adding only the
    instruction-specific framing and the verbatim instruction text as design intent.
    """
    return build_grammar_variant_prompt(
        base_config,
        grammar_skills_text,
        design_intent=build_instruction_variant_design_intent(instructions_text),
    )


def build_instruction_variant_repair_prompt(
    base_config: dict,
    grammar_skills_text: str,
    instructions_text: str,
    invalid_yaml_text: str,
    validation_errors: list[str],
) -> str:
    """Build a repair prompt asking Claude to correct an invalid config proposal.

    Includes the original instructions, base config/schema guidance, the
    invalid YAML, and the deterministic validation errors that must be
    fixed. Like the initial prompt, this asks for a complete corrected YAML
    config, never a patch, and never graph samples: deterministic
    GraphLayoutSynth validation remains the sole judge of acceptance.
    """
    base_yaml = yaml.safe_dump(base_config, sort_keys=False)
    contract = build_config_contract(base_config)
    errors_text = (
        "\n".join(f"- {error}" for error in validation_errors)
        if validation_errors
        else "- (no specific error messages were provided)"
    )
    sections = [
        "# Task\n"
        "Your previous GraphLayoutSynth YAML config proposal failed deterministic "
        "validation. Correct it. The corrected variant will be validated again "
        "before use and then run through the existing procedural generator.\n",
        "# Grammar Config Skills\n" + grammar_skills_text.strip(),
        "# Live Config Contract\n"
        "These values are derived from the actual base YAML config and are the "
        "config-specific source of truth. Use only the listed node and edge "
        "vocabularies unless the corrected config updates every relevant section "
        "consistently.\n"
        "```json\n" + _compact_json(contract.to_summary()) + "\n```",
        "# Base YAML Config\n```yaml\n" + base_yaml.strip() + "\n```",
        "# Original Design Instructions\n" + instructions_text.strip(),
        "# Your Previous Invalid YAML Proposal\n```yaml\n" + invalid_yaml_text.strip() + "\n```",
        "# Deterministic Validation Errors\n" + errors_text,
    ]
    sections.append(
        "# Output Instructions\n"
        "Return a complete corrected YAML config only in a fenced yaml block.\n"
        "Do not return a patch or diff.\n"
        "Do not invent unsupported fields.\n"
        "Preserve required top-level sections and schema validity.\n"
        "Fix every listed validation error while keeping the config internally "
        "consistent with the Live Config Contract and continuing to reflect the "
        "original design instructions.\n"
        "Do not generate graph samples, node-link JSON, or any other raw graph "
        "output; propose only a corrected YAML config.\n"
        "Deterministic GraphLayoutSynth validation, not this response, will decide "
        "whether the corrected config is accepted.\n"
        "Do not include prose inside the YAML block. If you include rationale, put "
        "it outside the YAML block."
    )
    return "\n\n".join(sections).strip() + "\n"


def _extract_message_text(message: Any) -> str:
    content = getattr(message, "content", [])
    text_parts = []
    for block in content:
        block_type = getattr(block, "type", None)
        block_text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type")
            block_text = block.get("text")
        if block_type == "text" and block_text:
            text_parts.append(str(block_text))
    return "\n".join(text_parts).strip()


def propose_grammar_variant_with_claude(
    prompt: str,
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = 4000,
) -> str:
    """Call Claude and return the raw response text."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GrammarVariantError(
            "ANTHROPIC_API_KEY is missing. Add it to .env.local or set it in the environment."
        )

    try:
        from anthropic import Anthropic, APIError
    except ImportError as exc:
        raise GrammarVariantError(
            "The optional Anthropic SDK is not installed. Install with: python -m pip install -e \".[llm]\""
        ) from exc

    client = Anthropic()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=GRAMMAR_VARIANT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as exc:
        raise GrammarVariantError(f"Claude grammar variant request failed: {exc}") from exc
    text = _extract_message_text(message)
    if not text:
        raise GrammarVariantError("Claude response did not contain text content.")
    return text


def extract_yaml_from_llm_response(response_text: str) -> str:
    """Extract YAML from a Claude response, preferring the first fenced yaml block."""
    if not response_text or not response_text.strip():
        raise GrammarVariantError("Claude response was empty; no YAML config found.")

    fenced_blocks = re.findall(
        r"```(?:yaml|yml)\s*\n(.*?)```",
        response_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    yaml_text = fenced_blocks[0].strip() if fenced_blocks else response_text.strip()
    if not yaml_text:
        raise GrammarVariantError("Extracted YAML config is empty.")

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise GrammarVariantError("Claude response did not contain parseable YAML.") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise GrammarVariantError("Extracted YAML must be a non-empty mapping.")
    return yaml_text


def extract_rationale_from_llm_response(response_text: str) -> str:
    """Return non-YAML text outside fenced yaml/yml blocks."""
    rationale = re.sub(
        r"```(?:yaml|yml)\s*\n.*?```",
        "",
        response_text,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    return rationale


def write_variant_outputs(
    yaml_text: str,
    rationale_text: str | None,
    output_config_path: str | Path,
    rationale_output_path: str | Path | None = None,
) -> None:
    """Write a valid YAML variant and optional rationale text."""
    output_path = Path(output_config_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")

    if rationale_output_path and rationale_text:
        rationale_path = Path(rationale_output_path)
        rationale_path.parent.mkdir(parents=True, exist_ok=True)
        rationale_path.write_text(rationale_text.rstrip() + "\n", encoding="utf-8")


def invalid_variant_path(output_config_path: str | Path) -> Path:
    """Return the sidecar path for invalid LLM YAML."""
    return Path(output_config_path).with_suffix(".invalid.yaml")


def _count_range(count_spec: Any) -> tuple[int, int]:
    if count_spec is None:
        return (1, 1)
    if isinstance(count_spec, int) and not isinstance(count_spec, bool):
        return (count_spec, count_spec)
    if isinstance(count_spec, dict):
        minimum = count_spec.get("min")
        maximum = count_spec.get("max")
        if isinstance(minimum, int) and isinstance(maximum, int):
            return (minimum, maximum)
    return (0, 0)


def _type_values(type_spec: Any) -> list[str]:
    if isinstance(type_spec, str):
        return [type_spec]
    if isinstance(type_spec, dict) and isinstance(type_spec.get("choices"), list):
        return [value for value in type_spec["choices"] if isinstance(value, str)]
    return []


def _find_rule(config: dict, match_type: str) -> dict | None:
    for rule in config.get("grammar_rules", []):
        if isinstance(rule, dict) and rule.get("match", {}).get("type") == match_type:
            return rule
    return None


def _estimated_zone_count_range(config: dict) -> tuple[int, int]:
    floor_rule = _find_rule(config, "BuildingFloor")
    if not floor_rule:
        return (1, 1)
    zone_min = 0
    zone_max = 0
    for entry in floor_rule.get("action", {}).get("create_nodes", []):
        if "Zone" in _type_values(entry.get("type")):
            entry_min, entry_max = _count_range(entry.get("count", 1))
            zone_min += entry_min
            zone_max += entry_max
    return (zone_min or 1, zone_max or 1)


def _alias_entry(rule: dict, alias: str) -> dict | None:
    for entry in rule.get("action", {}).get("create_nodes", []):
        if isinstance(entry, dict) and entry.get("alias") == alias:
            return entry
    return None


def _allowed_ratio_total_range(
    patient_total_min: int,
    patient_total_max: int,
    target_ratio: float,
    tolerance: float,
) -> tuple[int, int]:
    lower_ratio = max(0.0, target_ratio - tolerance)
    upper_ratio = target_ratio + tolerance
    return (
        int(patient_total_min * lower_ratio),
        int(patient_total_max * upper_ratio + 0.999999),
    )


def validate_room_mix_targets(
    config: dict,
    *,
    patient_total_min: int = 20,
    patient_total_max: int = 30,
    clinical_ratio: float = 0.25,
    staff_ratio: float = 0.10,
    ratio_tolerance: float = 0.08,
    patient_alias: str = "patient",
    clinical_alias: str = "clinical",
    staff_alias: str = "staff",
    expected_alias_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate requested room-mix semantics for an LLM-generated config."""
    zone_rule = _find_rule(config, "Zone")
    if not zone_rule:
        raise GrammarVariantError("Room-mix target check failed: no grammar rule matches type Zone.")

    fallback_alias_types = _default_room_mix_alias_types(build_config_contract(config))
    expected_aliases = expected_alias_types or {
        patient_alias: fallback_alias_types["patient"],
        clinical_alias: fallback_alias_types["clinical"],
        staff_alias: fallback_alias_types["staff"],
    }
    per_zone_ranges: dict[str, tuple[int, int]] = {}
    for alias, node_type in expected_aliases.items():
        entry = _alias_entry(zone_rule, alias)
        if entry is None:
            raise GrammarVariantError(
                f"Room-mix target check failed: missing create_nodes alias '{alias}' for {node_type}."
            )
        values = _type_values(entry.get("type"))
        if values != [node_type]:
            raise GrammarVariantError(
                f"Room-mix target check failed: alias '{alias}' must have type {node_type}, got {values}."
            )
        per_zone_ranges[node_type] = _count_range(entry.get("count", 1))

    zone_min, zone_max = _estimated_zone_count_range(config)
    totals = {
        node_type: {
            "min": zone_min * count_range[0],
            "max": zone_max * count_range[1],
        }
        for node_type, count_range in per_zone_ranges.items()
    }

    patient_type = expected_aliases[patient_alias]
    clinical_type = expected_aliases[clinical_alias]
    staff_type = expected_aliases[staff_alias]

    patient = totals[patient_type]
    if patient["min"] < patient_total_min or patient["max"] > patient_total_max:
        raise GrammarVariantError(
            f"Room-mix target check failed: expected {patient_type} total "
            f"{patient_total_min}-{patient_total_max}, got {patient['min']}-{patient['max']}."
        )

    clinical_min, clinical_max = _allowed_ratio_total_range(
        patient_total_min,
        patient_total_max,
        clinical_ratio,
        ratio_tolerance,
    )
    clinical = totals[clinical_type]
    if clinical["min"] < clinical_min or clinical["max"] > clinical_max:
        raise GrammarVariantError(
            f"Room-mix target check failed: expected {clinical_type} total about "
            f"{clinical_ratio:.0%} of {patient_type} ({clinical_min}-{clinical_max}), "
            f"got {clinical['min']}-{clinical['max']}."
        )

    staff_min, staff_max = _allowed_ratio_total_range(
        patient_total_min,
        patient_total_max,
        staff_ratio,
        ratio_tolerance,
    )
    staff = totals[staff_type]
    if staff["min"] < staff_min or staff["max"] > staff_max:
        raise GrammarVariantError(
            f"Room-mix target check failed: expected {staff_type} total about "
            f"{staff_ratio:.0%} of {patient_type} ({staff_min}-{staff_max}), got {staff['min']}-{staff['max']}."
        )

    room_type_counts = config.get("room_type_counts", {})
    for node_type, total_range in totals.items():
        count_value = room_type_counts.get(node_type)
        if not isinstance(count_value, int) or not (total_range["min"] <= count_value <= total_range["max"]):
            raise GrammarVariantError(
                "Room-mix target check failed: room_type_counts."
                f"{node_type}={count_value!r} must be within estimated generated range "
                f"{total_range['min']}-{total_range['max']}."
            )

    return {
        "zone_count_range": {"min": zone_min, "max": zone_max},
        "estimated_totals": totals,
    }


def validate_variant_yaml_text(yaml_text: str) -> dict:
    """Parse and validate a generated YAML config, returning the raw mapping."""
    try:
        raw_config = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise GrammarVariantError(f"Generated YAML is not valid YAML: {exc}") from exc
    if not isinstance(raw_config, dict) or not raw_config:
        raise GrammarVariantError("Generated YAML must be a non-empty mapping.")
    try:
        validate_config(raw_config)
    except ConfigError as exc:
        raise GrammarVariantError(f"Generated config failed validation: {exc}") from exc
    contract = build_config_contract(raw_config)
    if contract.errors:
        raise GrammarVariantError(
            "Generated config failed contract validation: " + "; ".join(contract.errors)
        )
    return raw_config


def propose_grammar_variant(
    *,
    base_config: dict,
    grammar_skills_text: str,
    output_config_path: str | Path,
    variant_requirements: dict[str, Any] | None = None,
    design_intent: str | None = None,
    diversity_report: dict | None = None,
    review_summary: dict | None = None,
    archive: dict | None = None,
    rationale_output_path: str | Path | None = None,
    raw_output_path: str | Path | None = None,
    model: str = DEFAULT_CLAUDE_MODEL,
    max_tokens: int = 4000,
    env_path: str = ".env.local",
    require_room_mix_targets: bool = False,
    patient_total_min: int = 20,
    patient_total_max: int = 30,
    clinical_ratio: float = 0.25,
    staff_ratio: float = 0.10,
    ratio_tolerance: float = 0.08,
) -> dict[str, Any]:
    """Call Claude, validate its YAML, and write variant artifacts."""
    base_contract = build_config_contract(base_config)
    normalized_requirements = validate_variant_requirements(variant_requirements, contract=base_contract) if variant_requirements else None
    requirements_design_intent = variant_requirements_to_design_intent(normalized_requirements, contract=base_contract)
    design_intent_parts = [part for part in (requirements_design_intent, design_intent) if part]
    merged_design_intent = "\n\n".join(design_intent_parts) or None
    requirements_room_mix_kwargs = room_mix_kwargs_from_requirements(normalized_requirements, contract=base_contract)
    contract_room_mix_kwargs = room_mix_kwargs_from_contract(base_contract)
    effective_room_mix_kwargs = requirements_room_mix_kwargs or contract_room_mix_kwargs
    if effective_room_mix_kwargs:
        require_room_mix_targets = True
        patient_total_min = effective_room_mix_kwargs["patient_total_min"]
        patient_total_max = effective_room_mix_kwargs["patient_total_max"]
        clinical_ratio = effective_room_mix_kwargs["clinical_ratio"]
        staff_ratio = effective_room_mix_kwargs["staff_ratio"]
        ratio_tolerance = effective_room_mix_kwargs["ratio_tolerance"]

    prompt = build_grammar_variant_prompt(
        base_config,
        grammar_skills_text,
        design_intent=merged_design_intent,
        diversity_report=diversity_report,
        review_summary=review_summary,
        archive=archive,
    )
    load_llm_environment(env_path)
    response_text = propose_grammar_variant_with_claude(prompt, model=model, max_tokens=max_tokens)

    if raw_output_path:
        raw_path = Path(raw_output_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(response_text, encoding="utf-8")

    yaml_text = extract_yaml_from_llm_response(response_text)
    try:
        raw_variant_config = validate_variant_yaml_text(yaml_text)
        room_mix_report = None
        if require_room_mix_targets:
            room_mix_report = validate_room_mix_targets(
                raw_variant_config,
                patient_total_min=patient_total_min,
                patient_total_max=patient_total_max,
                clinical_ratio=clinical_ratio,
                staff_ratio=staff_ratio,
                ratio_tolerance=ratio_tolerance,
                patient_alias=effective_room_mix_kwargs.get("patient_alias", "patient"),
                clinical_alias=effective_room_mix_kwargs.get("clinical_alias", "clinical"),
                staff_alias=effective_room_mix_kwargs.get("staff_alias", "staff"),
                expected_alias_types=effective_room_mix_kwargs.get("expected_alias_types"),
            )
    except GrammarVariantError:
        invalid_path = invalid_variant_path(output_config_path)
        invalid_path.parent.mkdir(parents=True, exist_ok=True)
        invalid_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
        raise

    rationale_text = extract_rationale_from_llm_response(response_text)
    write_variant_outputs(yaml_text, rationale_text, output_config_path, rationale_output_path)
    return {
        "output_config_path": str(output_config_path),
        "rationale_output_path": str(rationale_output_path) if rationale_output_path and rationale_text else None,
        "raw_output_path": str(raw_output_path) if raw_output_path else None,
        "model": model,
        "room_mix_report": room_mix_report,
    }
