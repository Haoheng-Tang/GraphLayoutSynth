"""HTTP-control-plane support for LLM-proposed grammar/config variants.

This module wraps the existing grammar-variant assistant with durable artifact,
registry, and activation bookkeeping. The LLM remains constrained to proposing
complete YAML configs; it never generates raw graphs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.config_contract import build_config_contract
from graph_layout_synth.config_validator import validate_config_file
from graph_layout_synth.llm_evaluator import load_llm_environment


ENABLE_LLM_VARIANTS_ENV = "GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS"
LLM_VARIANT_DIR_ENV = "GRAPHLAYOUTSYNTH_LLM_VARIANT_DIR"
DEFAULT_VARIANT_ROOT = Path("outputs/llm_variants")
REGISTRY_FILENAME = "registry.json"
ACTIVE_VARIANT_FILENAME = "active_variant.json"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRAMMAR_SKILLS_PATH = PROJECT_ROOT / "docs" / "GRAMMAR_CONFIG_SKILLS.md"


class GrammarVariantControlPlaneError(RuntimeError):
    """Raised for controlled variant-control-plane failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.record = record


@dataclass(frozen=True)
class GrammarVariantRecord:
    """Serializable registry entry for one variant proposal."""

    variant_id: str
    created_at: str
    status: str
    active: bool
    base_config_path: str
    artifact_dir: str
    heuristic_summary: str
    model: str | None = None
    validated_config_path: str | None = None
    error_summary: str | None = None
    validation_summary: dict[str, Any] | None = None
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {
            "variantId": self.variant_id,
            "createdAt": self.created_at,
            "status": self.status,
            "active": self.active,
            "baseConfigPath": self.base_config_path,
            "artifactDir": self.artifact_dir,
            "heuristicSummary": self.heuristic_summary,
            "dryRun": self.dry_run,
        }
        if self.model:
            data["model"] = self.model
        if self.validated_config_path:
            data["validatedConfigPath"] = self.validated_config_path
        if self.error_summary:
            data["errorSummary"] = self.error_summary
        if self.validation_summary is not None:
            data["validationSummary"] = self.validation_summary
        return data


def environment_flag(name: str) -> bool:
    """Return whether a boolean environment flag is enabled."""
    return os.getenv(name, "").strip().lower() in TRUE_ENV_VALUES


def llm_variant_control_plane_enabled() -> bool:
    """Return whether the HTTP grammar-variant control plane is enabled."""
    return environment_flag(ENABLE_LLM_VARIANTS_ENV)


def variant_root_from_environment() -> Path:
    """Return the configured root for variant artifacts and registry files."""
    configured = os.getenv(LLM_VARIANT_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_VARIANT_ROOT


def registry_path(output_root: str | Path | None = None) -> Path:
    root = Path(output_root) if output_root is not None else variant_root_from_environment()
    return root / REGISTRY_FILENAME


def active_variant_path(output_root: str | Path | None = None) -> Path:
    root = Path(output_root) if output_root is not None else variant_root_from_environment()
    return root / ACTIVE_VARIANT_FILENAME


def list_variant_records(output_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Read compact variant registry records."""
    path = registry_path(output_root)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise GrammarVariantControlPlaneError(
            f"Variant registry must contain a JSON array: {path}",
            status_code=500,
        )
    return [dict(record) for record in data if isinstance(record, dict)]


def get_variant_record(
    variant_id: str,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return one variant registry record."""
    for record in list_variant_records(output_root):
        if record.get("variantId") == variant_id:
            return record
    raise GrammarVariantControlPlaneError(
        f"Grammar variant '{variant_id}' was not found.",
        status_code=404,
    )


def variant_detail(
    variant_id: str,
    output_root: str | Path | None = None,
    *,
    include_validated_yaml: bool = True,
) -> dict[str, Any]:
    """Return detailed metadata and safe artifact pointers for one variant."""
    record = get_variant_record(variant_id, output_root)
    artifact_dir = Path(record["artifactDir"])
    metadata_path = artifact_dir / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.exists()
        else {}
    )
    detail = {"record": record, "metadata": metadata}
    if include_validated_yaml and record.get("validatedConfigPath"):
        validated_path = Path(record["validatedConfigPath"])
        if validated_path.exists() and validated_path.stat().st_size <= 250_000:
            detail["validatedYaml"] = validated_path.read_text(encoding="utf-8")
    return detail


def active_variant_config_path(
    output_root: str | Path | None = None,
) -> Path:
    """Return the validated config path for the active variant."""
    path = active_variant_path(output_root)
    if not path.exists():
        raise GrammarVariantControlPlaneError(
            "No active grammar variant is configured.",
            status_code=400,
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    config_path = data.get("validatedConfigPath")
    if not isinstance(config_path, str) or not config_path:
        raise GrammarVariantControlPlaneError(
            "Active grammar variant pointer is missing validatedConfigPath.",
            status_code=500,
        )
    return Path(config_path)


def activate_variant(
    variant_id: str,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Activate a previously validated variant and update registry state."""
    record = get_variant_record(variant_id, output_root)
    if record.get("status") != "valid":
        raise GrammarVariantControlPlaneError(
            f"Only valid grammar variants can be activated; '{variant_id}' is "
            f"{record.get('status')}.",
            status_code=400,
        )
    validated_config_path = record.get("validatedConfigPath")
    if not validated_config_path:
        raise GrammarVariantControlPlaneError(
            f"Variant '{variant_id}' has no validated config path.",
            status_code=400,
        )
    validation = validate_config_file(validated_config_path)
    if not validation.is_valid:
        raise GrammarVariantControlPlaneError(
            "Validated config no longer passes validation: "
            + "; ".join(validation.errors),
            status_code=400,
        )

    records = list_variant_records(output_root)
    activated = None
    for item in records:
        is_active = item.get("variantId") == variant_id
        item["active"] = is_active
        if is_active:
            activated = item
        artifact_dir = item.get("artifactDir")
        metadata_path = Path(artifact_dir) / "metadata.json" if artifact_dir else None
        if metadata_path is not None and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["active"] = is_active
            _write_json(metadata_path, metadata)
    _write_registry(records, output_root)

    pointer = {
        "variantId": variant_id,
        "activatedAt": _utc_now(),
        "validatedConfigPath": validated_config_path,
        "artifactDir": record.get("artifactDir"),
    }
    path = active_variant_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, pointer)
    return activated or get_variant_record(variant_id, output_root)


def propose_variant_from_instructions(
    heuristic_instructions: str,
    base_config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_root: str | Path | None = None,
    variant_requirements: dict[str, Any] | None = None,
    model: str | None = None,
    dry_run: bool = False,
    activate_if_valid: bool = False,
    env_path: str = ".env.local",
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """Propose or dry-run a grammar config variant and record its artifacts."""
    if not isinstance(heuristic_instructions, str) or not heuristic_instructions.strip():
        raise GrammarVariantControlPlaneError(
            "heuristicInstructions must be a non-empty string.",
            status_code=400,
        )

    root = Path(output_root) if output_root is not None else variant_root_from_environment()
    created_at = _utc_now()
    variant_id = _new_variant_id(created_at)
    artifact_dir = root / variant_id
    artifact_dir.mkdir(parents=True, exist_ok=False)

    base_path = Path(base_config_path)
    selected_model = model or assistant.DEFAULT_CLAUDE_MODEL
    heuristic_summary = _summarize(heuristic_instructions)
    metadata: dict[str, Any] = {
        "variantId": variant_id,
        "createdAt": created_at,
        "status": "failed",
        "baseConfigPath": str(base_path),
        "artifactDir": str(artifact_dir),
        "heuristicSummary": heuristic_summary,
        "model": selected_model,
        "dryRun": dry_run,
        "active": False,
        "artifacts": {},
    }
    try:
        _write_text(artifact_dir / "heuristic_instructions.md", heuristic_instructions.strip() + "\n")
        metadata["artifacts"]["heuristicInstructions"] = str(artifact_dir / "heuristic_instructions.md")
        _write_text(artifact_dir / "base_config_path.txt", str(base_path) + "\n")
        metadata["artifacts"]["baseConfigPath"] = str(artifact_dir / "base_config_path.txt")

        base_config = _read_yaml_mapping(base_path)
        assistant.validate_variant_yaml_text(yaml.safe_dump(base_config, sort_keys=False))
        base_contract = build_config_contract(base_config)
        normalized_requirements = (
            assistant.validate_variant_requirements(variant_requirements, contract=base_contract)
            if variant_requirements
            else None
        )
        requirements_design_intent = assistant.variant_requirements_to_design_intent(
            normalized_requirements,
            contract=base_contract,
        )
        design_intent = "\n\n".join(
            part
            for part in (requirements_design_intent, heuristic_instructions.strip())
            if part
        )
        grammar_skills_text = GRAMMAR_SKILLS_PATH.read_text(encoding="utf-8")
        prompt = assistant.build_grammar_variant_prompt(
            base_config,
            grammar_skills_text,
            design_intent=design_intent,
        )
        _write_text(artifact_dir / "prompt.md", prompt)
        metadata["artifacts"]["prompt"] = str(artifact_dir / "prompt.md")
        if normalized_requirements is not None:
            metadata["variantRequirements"] = normalized_requirements

        if dry_run:
            validation_report = {
                "isValid": None,
                "errors": [],
                "warnings": [],
                "message": "Dry run only; no YAML was proposed or validated.",
            }
            _write_json(artifact_dir / "validation_report.json", validation_report)
            metadata["artifacts"]["validationReport"] = str(artifact_dir / "validation_report.json")
            metadata["status"] = "dry_run"
            record = _record_from_metadata(metadata, validation_report=validation_report)
            _finalize_record(root, artifact_dir, metadata, record)
            return record

        try:
            load_llm_environment(env_path)
            response_text = assistant.propose_grammar_variant_with_claude(
                prompt,
                model=selected_model,
                max_tokens=max_tokens,
            )
        except assistant.GrammarVariantError as exc:
            return _fail_with_record(
                root,
                artifact_dir,
                metadata,
                str(exc),
                status="failed",
                raise_error=True,
            )

        _write_text(artifact_dir / "raw_llm_response.md", response_text)
        metadata["artifacts"]["rawLlmResponse"] = str(artifact_dir / "raw_llm_response.md")

        try:
            yaml_text = assistant.extract_yaml_from_llm_response(response_text)
        except assistant.GrammarVariantError as exc:
            return _fail_with_record(
                root,
                artifact_dir,
                metadata,
                str(exc),
                status="failed",
                raise_error=True,
            )
        _write_text(artifact_dir / "extracted_variant.yaml", yaml_text.rstrip() + "\n")
        metadata["artifacts"]["extractedVariant"] = str(artifact_dir / "extracted_variant.yaml")

        try:
            raw_variant_config = assistant.validate_variant_yaml_text(yaml_text)
            room_mix_kwargs = (
                assistant.room_mix_kwargs_from_requirements(normalized_requirements, contract=base_contract)
                or assistant.room_mix_kwargs_from_contract(base_contract)
            )
            room_mix_report = None
            if room_mix_kwargs:
                room_mix_report = assistant.validate_room_mix_targets(
                    raw_variant_config,
                    **room_mix_kwargs,
                )
        except assistant.GrammarVariantError as exc:
            _write_text(artifact_dir / "invalid_variant.yaml", yaml_text.rstrip() + "\n")
            metadata["artifacts"]["invalidVariant"] = str(artifact_dir / "invalid_variant.yaml")
            validation_report = {
                "isValid": False,
                "errors": [str(exc)],
                "warnings": [],
            }
            _write_json(artifact_dir / "validation_report.json", validation_report)
            metadata["artifacts"]["validationReport"] = str(artifact_dir / "validation_report.json")
            metadata["status"] = "invalid"
            metadata["errorSummary"] = str(exc)
            record = _record_from_metadata(metadata, validation_report=validation_report)
            _finalize_record(root, artifact_dir, metadata, record)
            return record

        validated_path = artifact_dir / "validated_variant.yaml"
        _write_text(validated_path, yaml_text.rstrip() + "\n")
        metadata["artifacts"]["validatedVariant"] = str(validated_path)
        metadata["validatedConfigPath"] = str(validated_path)
        validation_report = validate_config_file(validated_path).to_dict()
        if room_mix_report is not None:
            validation_report["room_mix_report"] = room_mix_report
        _write_json(artifact_dir / "validation_report.json", validation_report)
        metadata["artifacts"]["validationReport"] = str(artifact_dir / "validation_report.json")

        rationale_text = assistant.extract_rationale_from_llm_response(response_text)
        if rationale_text:
            _write_text(artifact_dir / "rationale.md", rationale_text.rstrip() + "\n")
            metadata["artifacts"]["rationale"] = str(artifact_dir / "rationale.md")

        metadata["status"] = "valid"
        record = _record_from_metadata(metadata, validation_report=validation_report)
        _finalize_record(root, artifact_dir, metadata, record)
        if activate_if_valid:
            return activate_variant(variant_id, root)
        return record
    except GrammarVariantControlPlaneError:
        raise
    except Exception as exc:
        return _fail_with_record(
            root,
            artifact_dir,
            metadata,
            str(exc),
            status="failed",
            raise_error=True,
        )


def _fail_with_record(
    root: Path,
    artifact_dir: Path,
    metadata: dict[str, Any],
    error_summary: str,
    *,
    status: str,
    raise_error: bool,
) -> dict[str, Any]:
    validation_report = {
        "isValid": False,
        "errors": [error_summary],
        "warnings": [],
    }
    _write_json(artifact_dir / "validation_report.json", validation_report)
    metadata["artifacts"]["validationReport"] = str(artifact_dir / "validation_report.json")
    metadata["status"] = status
    metadata["errorSummary"] = error_summary
    record = _record_from_metadata(metadata, validation_report=validation_report)
    _finalize_record(root, artifact_dir, metadata, record)
    if raise_error:
        raise GrammarVariantControlPlaneError(
            error_summary,
            status_code=400,
            record=record,
        )
    return record


def _finalize_record(
    root: Path,
    artifact_dir: Path,
    metadata: dict[str, Any],
    record: dict[str, Any],
) -> None:
    metadata.update(record)
    _write_json(artifact_dir / "metadata.json", metadata)
    _upsert_record(record, root)


def _record_from_metadata(
    metadata: dict[str, Any],
    *,
    validation_report: dict[str, Any] | None,
) -> dict[str, Any]:
    record = GrammarVariantRecord(
        variant_id=metadata["variantId"],
        created_at=metadata["createdAt"],
        status=metadata["status"],
        active=bool(metadata.get("active", False)),
        base_config_path=metadata["baseConfigPath"],
        artifact_dir=metadata["artifactDir"],
        heuristic_summary=metadata["heuristicSummary"],
        model=metadata.get("model"),
        validated_config_path=metadata.get("validatedConfigPath"),
        error_summary=metadata.get("errorSummary"),
        validation_summary=_compact_validation_summary(validation_report),
        dry_run=bool(metadata.get("dryRun", False)),
    ).to_dict()
    record["artifactPaths"] = dict(metadata.get("artifacts", {}))
    return record


def _compact_validation_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "isValid": report.get("isValid", report.get("is_valid")),
        "errorCount": len(report.get("errors", [])),
        "warningCount": len(report.get("warnings", [])),
    }


def _upsert_record(record: dict[str, Any], output_root: str | Path | None = None) -> None:
    records = [
        existing
        for existing in list_variant_records(output_root)
        if existing.get("variantId") != record.get("variantId")
    ]
    records.append(record)
    _write_registry(records, output_root)


def _write_registry(records: list[dict[str, Any]], output_root: str | Path | None = None) -> None:
    path = registry_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, records)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise GrammarVariantControlPlaneError(
            f"YAML file must contain a non-empty mapping: {path}",
            status_code=400,
        )
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_variant_id(created_at: str) -> str:
    compact = (
        created_at.replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("Z", "Z")
    )
    return f"{compact}-{uuid4().hex[:8]}"


def _summarize(text: str, max_length: int = 160) -> str:
    summary = " ".join(text.strip().split())
    if len(summary) <= max_length:
        return summary
    return summary[: max_length - 1].rstrip() + "…"
