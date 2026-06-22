"""Optional Claude assistant for proposing YAML grammar/config variants."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from graph_layout_synth.config import ConfigError, validate_config
from graph_layout_synth.llm_evaluator import DEFAULT_CLAUDE_MODEL, load_llm_environment


class GrammarVariantError(RuntimeError):
    """Raised when a grammar variant cannot be proposed or validated."""


GRAMMAR_VARIANT_SYSTEM_PROMPT = (
    "You propose GraphLayoutSynth YAML config variants. "
    "Return complete schema-valid YAML configs, not raw graphs. "
    "The deterministic validator and generator remain the source of truth."
)


def _compact_json(data: Any, *, max_chars: int = 12000) -> str:
    text = json.dumps(data, indent=2, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


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
    sections = [
        "# Task\n"
        "Propose one complete GraphLayoutSynth YAML config variant. "
        "The variant will be validated before use and then run through the existing procedural generator.\n",
        "# Grammar Config Skills\n" + grammar_skills_text.strip(),
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
        "Prefer modifying stochastic parameters, grammar rule counts, room-type choices, support-room mixes, and grammar-rule variants.\n"
        "Do not include raw graph JSON.\n"
        "Do not include prose inside the YAML block.\n"
        "If you include rationale, put it outside the YAML block."
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
) -> dict[str, Any]:
    """Validate requested room-mix semantics for an LLM-generated config."""
    zone_rule = _find_rule(config, "Zone")
    if not zone_rule:
        raise GrammarVariantError("Room-mix target check failed: no grammar rule matches type Zone.")

    expected_aliases = {
        patient_alias: "PatientRoom",
        clinical_alias: "ClinicalSupport",
        staff_alias: "StaffSupport",
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

    patient = totals["PatientRoom"]
    if patient["min"] < patient_total_min or patient["max"] > patient_total_max:
        raise GrammarVariantError(
            "Room-mix target check failed: expected PatientRoom total "
            f"{patient_total_min}-{patient_total_max}, got {patient['min']}-{patient['max']}."
        )

    clinical_min, clinical_max = _allowed_ratio_total_range(
        patient_total_min,
        patient_total_max,
        clinical_ratio,
        ratio_tolerance,
    )
    clinical = totals["ClinicalSupport"]
    if clinical["min"] < clinical_min or clinical["max"] > clinical_max:
        raise GrammarVariantError(
            "Room-mix target check failed: expected ClinicalSupport total about "
            f"{clinical_ratio:.0%} of PatientRoom ({clinical_min}-{clinical_max}), "
            f"got {clinical['min']}-{clinical['max']}."
        )

    staff_min, staff_max = _allowed_ratio_total_range(
        patient_total_min,
        patient_total_max,
        staff_ratio,
        ratio_tolerance,
    )
    staff = totals["StaffSupport"]
    if staff["min"] < staff_min or staff["max"] > staff_max:
        raise GrammarVariantError(
            "Room-mix target check failed: expected StaffSupport total about "
            f"{staff_ratio:.0%} of PatientRoom ({staff_min}-{staff_max}), got {staff['min']}-{staff['max']}."
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
    return raw_config


def propose_grammar_variant(
    *,
    base_config: dict,
    grammar_skills_text: str,
    output_config_path: str | Path,
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
    prompt = build_grammar_variant_prompt(
        base_config,
        grammar_skills_text,
        design_intent=design_intent,
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
