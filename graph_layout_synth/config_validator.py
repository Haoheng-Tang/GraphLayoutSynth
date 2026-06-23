"""User-facing config validation report helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from graph_layout_synth.config import ConfigError, validate_config
from graph_layout_synth.config_contract import build_config_contract


@dataclass(frozen=True)
class ConfigValidationReport:
    """Serializable validation result for a YAML config file."""

    config_path: str
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    contract_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "config_path": self.config_path,
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        if self.contract_summary is not None:
            data["contract_summary"] = self.contract_summary
        return data


def validate_config_file(path: str | Path) -> ConfigValidationReport:
    """Validate a config file without generating any graphs."""
    config_path = Path(path)
    contract = None
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        contract = build_config_contract(raw_config) if isinstance(raw_config, dict) else None
        validate_config(raw_config)
    except FileNotFoundError as exc:
        return ConfigValidationReport(
            config_path=str(config_path),
            is_valid=False,
            errors=[f"Config file not found: {config_path}"],
        )
    except yaml.YAMLError:
        return ConfigValidationReport(
            config_path=str(config_path),
            is_valid=False,
            errors=[f"Config file is not valid YAML: {config_path}"],
        )
    except ConfigError as exc:
        return ConfigValidationReport(
            config_path=str(config_path),
            is_valid=False,
            errors=[str(exc)],
            contract_summary=contract.to_summary() if contract is not None else None,
        )
    return ConfigValidationReport(
        config_path=str(config_path),
        is_valid=not contract.errors,
        errors=contract.errors,
        warnings=contract.warnings,
        contract_summary=contract.to_summary(),
    )


def export_config_validation_report(report: ConfigValidationReport, path: str | Path) -> None:
    """Write a config validation report as JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
