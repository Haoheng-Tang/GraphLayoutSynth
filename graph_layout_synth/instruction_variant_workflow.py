"""Shared instruction-guided config-variant proposal engine.

Used by both the ``propose-instruction-variant`` CLI command
(``graph_layout_synth/cli.py``) and the HTTP instruction-variant control
plane (``graph_layout_synth/instruction_variant_control_plane.py``), so the
attempt/repair loop, artifact layout, and review-summary format exist in
exactly one place.

Claude proposes -- and, on repair attempts, revises -- YAML config variants
only. It never generates graph JSON, and it never validates, ranks, repairs,
or certifies layouts itself: every attempt is validated the same
deterministic way (`validate_config_file`), and callers must treat
`InstructionVariantProposal.is_valid` as the sole signal for whether
generation or activation may proceed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from graph_layout_synth.config_validator import validate_config_file
from graph_layout_synth.grammar_variant_assistant import (
    GrammarVariantError,
    build_instruction_variant_prompt,
    build_instruction_variant_repair_prompt,
    extract_yaml_from_llm_response,
    propose_grammar_variant_with_claude,
)


ClaudeCall = Callable[[str, str, int], str]


@dataclass
class InstructionVariantAttempt:
    """One initial or repair attempt's outcome and artifact paths."""

    index: int
    kind: str  # "initial" | "repair"
    is_valid: bool
    validation_report: dict[str, Any]
    attempt_dir: Path
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "isValid": self.is_valid,
            "status": "validated",
            "artifacts": dict(self.artifacts),
        }


@dataclass
class InstructionVariantProposal:
    """Full outcome of a (non-dry-run) instruction-guided proposal run."""

    attempts: list[InstructionVariantAttempt]
    final_yaml_text: str
    final_validation_report: dict[str, Any]
    is_valid: bool
    proposed_config_path: Path
    validation_report_path: Path
    repair_attempts_used: int


class InstructionVariantAttemptError(GrammarVariantError):
    """Raised when an attempt cannot be validated at all (call/extraction failure).

    This is fatal: unlike a validation failure (an ordinary, expected
    outcome), a failed Claude call or unparseable response means no further
    repair attempts are made. ``failed_attempt`` and ``completed_attempts``
    let callers record the full attempt history before propagating the error.
    """

    def __init__(
        self,
        message: str,
        *,
        failed_attempt: dict[str, Any],
        completed_attempts: list[InstructionVariantAttempt],
    ) -> None:
        super().__init__(message)
        self.failed_attempt = failed_attempt
        self.completed_attempts = completed_attempts


def write_instruction_variant_prompt_artifacts(
    *,
    instructions_text: str,
    base_config: dict[str, Any],
    grammar_skills_text: str,
    output_dir: Path,
) -> tuple[str, Path, Path, Path]:
    """Write the four always-on artifacts and return the built prompt.

    Returns ``(prompt_text, submitted_instructions_path, base_config_path,
    llm_prompt_path)``. Used for both dry runs (which stop here) and the
    setup step of a live run.
    """
    prompt = build_instruction_variant_prompt(base_config, grammar_skills_text, instructions_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = output_dir / "submitted_instructions.md"
    instructions_path.write_text(instructions_text.rstrip() + "\n", encoding="utf-8")
    base_config_path = output_dir / "base_config.yaml"
    base_config_path.write_text(yaml.safe_dump(base_config, sort_keys=False), encoding="utf-8")
    prompt_path = output_dir / "llm_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt, instructions_path, base_config_path, prompt_path


def run_instruction_variant_attempts(
    *,
    base_config: dict[str, Any],
    grammar_skills_text: str,
    instructions_text: str,
    initial_prompt: str,
    attempts_dir: Path,
    top_level_dir: Path,
    model: str,
    max_tokens: int,
    repair_attempts: int,
    claude_call: ClaudeCall = propose_grammar_variant_with_claude,
    on_attempt_start: Callable[[int, bool, str], None] | None = None,
    on_response_received: Callable[[int, bool, str], None] | None = None,
    on_attempt_complete: Callable[[InstructionVariantAttempt], None] | None = None,
) -> InstructionVariantProposal:
    """Run the initial proposal and, if needed, up to ``repair_attempts`` repairs.

    Stops at the first attempt that passes deterministic validation. Writes
    per-attempt artifacts under ``attempts_dir`` (``attempt_0_initial``,
    ``attempt_1_repair``, ...) plus top-level convenience copies of the
    latest attempt's config and validation report under ``top_level_dir``.

    Raises ``InstructionVariantAttemptError`` if Claude cannot be reached, or
    if a response contains no extractable YAML -- both are fatal, matching
    the one-shot CLI's original behavior: no further repairs are attempted
    after such a failure, only after an ordinary validation failure.
    """
    attempts: list[InstructionVariantAttempt] = []
    final_yaml_text: str | None = None
    final_validation_report: dict[str, Any] | None = None
    final_valid_config_path: Path | None = None

    for attempt_index in range(0, repair_attempts + 1):
        is_repair = attempt_index > 0
        kind = "repair" if is_repair else "initial"
        attempt_name = f"attempt_{attempt_index}_{kind}"
        attempt_dir = attempts_dir / attempt_name
        attempt_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, str] = {}

        if is_repair:
            prompt_to_send = build_instruction_variant_repair_prompt(
                base_config,
                grammar_skills_text,
                instructions_text,
                final_yaml_text or "",
                (final_validation_report or {}).get("errors", []),
            )
            repair_prompt_path = attempt_dir / "repair_prompt.md"
            repair_prompt_path.write_text(prompt_to_send, encoding="utf-8")
            artifacts["repairPrompt"] = str(repair_prompt_path)
        else:
            prompt_to_send = initial_prompt

        if on_attempt_start is not None:
            on_attempt_start(attempt_index, is_repair, prompt_to_send)

        try:
            response_text = claude_call(prompt_to_send, model, max_tokens)
        except GrammarVariantError as exc:
            raise InstructionVariantAttemptError(
                str(exc),
                failed_attempt={
                    "index": attempt_index,
                    "kind": kind,
                    "status": "call_failed",
                    "errorSummary": str(exc),
                    "artifacts": artifacts,
                },
                completed_attempts=list(attempts),
            ) from exc

        if on_response_received is not None:
            on_response_received(attempt_index, is_repair, response_text)

        raw_response_path = attempt_dir / "raw_llm_response.md"
        raw_response_path.write_text(response_text, encoding="utf-8")
        artifacts["rawLlmResponse"] = str(raw_response_path)

        try:
            yaml_text = extract_yaml_from_llm_response(response_text)
        except GrammarVariantError as exc:
            raise InstructionVariantAttemptError(
                str(exc),
                failed_attempt={
                    "index": attempt_index,
                    "kind": kind,
                    "status": "extraction_failed",
                    "errorSummary": str(exc),
                    "artifacts": artifacts,
                },
                completed_attempts=list(attempts),
            ) from exc

        attempt_config_path = attempt_dir / "proposed_config.yaml"
        attempt_config_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
        artifacts["proposedConfig"] = str(attempt_config_path)

        validation_report = validate_config_file(attempt_config_path).to_dict()
        attempt_report_path = attempt_dir / "config_validation_report.json"
        attempt_report_path.write_text(json.dumps(validation_report, indent=2), encoding="utf-8")
        artifacts["configValidationReport"] = str(attempt_report_path)

        attempt = InstructionVariantAttempt(
            index=attempt_index,
            kind=kind,
            is_valid=bool(validation_report.get("is_valid")),
            validation_report=validation_report,
            attempt_dir=attempt_dir,
            artifacts=artifacts,
        )
        attempts.append(attempt)
        if on_attempt_complete is not None:
            on_attempt_complete(attempt)

        final_yaml_text = yaml_text
        final_validation_report = validation_report
        if attempt.is_valid:
            final_valid_config_path = attempt_config_path
            break

    assert final_yaml_text is not None and final_validation_report is not None

    proposed_config_path = top_level_dir / "proposed_config.yaml"
    proposed_config_path.write_text(final_yaml_text.rstrip() + "\n", encoding="utf-8")
    validation_report_path = top_level_dir / "config_validation_report.json"
    validation_report_path.write_text(json.dumps(final_validation_report, indent=2), encoding="utf-8")

    return InstructionVariantProposal(
        attempts=attempts,
        final_yaml_text=final_yaml_text,
        final_validation_report=final_validation_report,
        is_valid=final_valid_config_path is not None,
        proposed_config_path=proposed_config_path,
        validation_report_path=validation_report_path,
        repair_attempts_used=attempts[-1].index,
    )


def write_instruction_variant_review_summary(
    path: Path,
    *,
    instructions_path: Path,
    base_config_path: Path,
    model: str,
    repair_attempts_requested: int,
    attempts: list[dict[str, Any]],
    is_valid: bool,
    final_validation_report: dict[str, Any],
    proposed_config_path: Path,
    samples_requested: int,
    samples_dir: Path | None,
    visualization_warnings: list[str] | None = None,
) -> None:
    """Write a human-readable summary table of every attempt plus the outcome."""
    repair_attempts_used = max(len(attempts) - 1, 0)
    lines = [
        "# Instruction-Guided Config Variant Review",
        "",
        f"- Instructions: `{instructions_path}`",
        f"- Base config: `{base_config_path}`",
        f"- Model: `{model}`",
        f"- Repair attempts requested: {repair_attempts_requested}",
        f"- Repair attempts used: {repair_attempts_used}",
        f"- Latest proposed config: `{proposed_config_path}`",
        f"- Final config validation: {'PASSED' if is_valid else 'FAILED'}",
        "",
        "## Attempts",
        "",
        "| Attempt | Kind | Valid | Config | Validation report |",
        "| --- | --- | --- | --- | --- |",
    ]
    for attempt in attempts:
        artifacts = attempt.get("artifacts", {})
        lines.append(
            f"| {attempt.get('index')} | {attempt.get('kind')} | "
            f"{'yes' if attempt.get('isValid') else 'no'} | "
            f"`{artifacts.get('proposedConfig', '')}` | "
            f"`{artifacts.get('configValidationReport', '')}` |"
        )

    errors = final_validation_report.get("errors") or []
    warnings = final_validation_report.get("warnings") or []
    if errors:
        lines += ["", "## Final Validation Errors", *(f"- {error}" for error in errors)]
    if warnings:
        lines += ["", "## Final Validation Warnings", *(f"- {warning}" for warning in warnings)]

    lines.append("")
    if not is_valid:
        if repair_attempts_requested > 0:
            lines.append(
                "Repair attempts were exhausted without producing a valid config. "
                "No graph samples were generated."
            )
        else:
            lines.append(
                "No graph samples were generated because the proposed config failed "
                "deterministic validation. Re-run with repair attempts to let Claude "
                "revise the proposal using the validation errors."
            )
    elif samples_requested > 0 and samples_dir is not None:
        lines.append(
            f"Requested {samples_requested} sample(s), generated with the existing "
            f"deterministic generation pipeline under `{samples_dir}`, including PNG "
            f"visualizations (via the existing `--visualize` pipeline flag) alongside "
            f"each generated JSON graph for visual inspection."
        )
        if visualization_warnings:
            lines += [
                "",
                "## PNG Visualization Warnings",
                (
                    "Rendering failed for some generated samples; their JSON graphs "
                    "were still generated and are unaffected."
                ),
                *(f"- {warning}" for warning in visualization_warnings),
            ]
    else:
        lines.append("No graph samples were requested.")

    lines += [
        "",
        (
            "Claude proposed and, if invoked, revised this YAML config variant only. "
            "It did not generate, validate, rank, or certify any graph; deterministic "
            "GraphLayoutSynth code performed validation"
            + (" and generation." if samples_dir else ".")
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
