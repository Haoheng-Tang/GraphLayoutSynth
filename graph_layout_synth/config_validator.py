"""User-facing config validation report helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_layout_synth.config import ConfigError, load_config


@dataclass(frozen=True)
class ConfigValidationReport:
    """Serializable validation result for a YAML config file."""

    config_path: str
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": self.config_path,
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_config_file(path: str | Path) -> ConfigValidationReport:
    """Validate a config file without generating any graphs."""
    config_path = Path(path)
    try:
        load_config(config_path)
    except ConfigError as exc:
        return ConfigValidationReport(
            config_path=str(config_path),
            is_valid=False,
            errors=[str(exc)],
        )
    return ConfigValidationReport(config_path=str(config_path), is_valid=True)


def export_config_validation_report(report: ConfigValidationReport, path: str | Path) -> None:
    """Write a config validation report as JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
