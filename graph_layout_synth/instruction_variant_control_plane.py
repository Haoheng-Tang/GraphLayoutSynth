"""HTTP control plane for instruction-guided grammar/config variant proposals.

Wraps the shared `instruction_variant_workflow` engine with the existing
grammar-variant registry (`grammar_variant_control_plane.py`): a valid
instruction-guided proposal becomes a normal registry record, visible via
`GET /grammar-variants`, inspectable via `GET /grammar-variants/{id}`, and
activatable via `POST /grammar-variants/{id}/activate` through the same
`registry.json` and `GrammarVariantRecord` shape the heuristic-only
`propose_variant_from_instructions` already uses. There is no second,
independent variant registry.

Claude is called only for a live (non-dry-run) proposal carrying non-empty
instruction text -- never for dry runs, program-requirement validation, the
room-type catalog, variant listing/inspection/activation, or
`/suggest-next-room`. Deterministic GraphLayoutSynth validation
(`validate_config_file`, reused unchanged) decides whether any attempt is
accepted, and graph generation (the existing `generate` pipeline, reused
unchanged via `cli.run_generation_for_instruction_variant`) runs only after
some attempt actually validates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.api.models import (
    InstructionVariantAttemptSummary,
    InstructionVariantProposeRequest,
    InstructionVariantProposeResponse,
)
from graph_layout_synth.cli import run_generation_for_instruction_variant
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.grammar_variant_control_plane import (
    GRAMMAR_SKILLS_PATH,
    GrammarVariantControlPlaneError,
    _finalize_record,
    _new_variant_id,
    _read_yaml_mapping,
    _record_from_metadata,
    _utc_now,
    variant_root_from_environment,
)
from graph_layout_synth.instruction_variant_workflow import (
    InstructionVariantAttempt,
    InstructionVariantAttemptError,
    run_instruction_variant_attempts,
    write_instruction_variant_prompt_artifacts,
    write_instruction_variant_review_summary,
)
from graph_layout_synth.llm_evaluator import DEFAULT_CLAUDE_MODEL, load_llm_environment


DEFAULT_MAX_TOKENS = 4000


def _summarize_label(name: str | None, instruction_text: str, max_length: int = 160) -> str:
    if name and name.strip():
        return name.strip()
    summary = " ".join(instruction_text.strip().split())
    if len(summary) <= max_length:
        return summary
    return summary[: max_length - 1].rstrip() + "…"


def _attempt_summaries(
    attempts: list[InstructionVariantAttempt],
) -> list[InstructionVariantAttemptSummary]:
    return [
        InstructionVariantAttemptSummary(
            attempt_index=attempt.index,
            kind=attempt.kind,  # type: ignore[arg-type]
            valid=attempt.is_valid,
            validation_error_count=len(attempt.validation_report.get("errors", [])),
            artifact_dir=str(attempt.attempt_dir),
        )
        for attempt in attempts
    ]


def propose_instruction_variant_from_request(
    request: InstructionVariantProposeRequest,
    *,
    output_root: str | Path | None = None,
    env_path: str = ".env.local",
) -> InstructionVariantProposeResponse:
    """Run one instruction-guided proposal request and return its outcome.

    Every request -- dry run or live -- gets its own artifact directory under
    the existing configured variant root (``outputs/llm_variants/<id>/`` by
    default). Live proposals are always recorded in ``registry.json``
    (matching the existing heuristic-only flow's dry-run/invalid/valid
    bookkeeping); the response's ``variantId`` is ``None`` for dry runs
    specifically, even though the artifacts still live under a real,
    server-assigned directory.
    """
    root = Path(output_root) if output_root is not None else variant_root_from_environment()
    created_at = _utc_now()
    variant_id = _new_variant_id(created_at)
    artifact_dir = root / variant_id
    artifact_dir.mkdir(parents=True, exist_ok=False)

    base_config_path = Path(request.base_config_path or DEFAULT_CONFIG_PATH)
    heuristic_summary = _summarize_label(request.name, request.instruction_text)

    metadata: dict[str, Any] = {
        "variantId": variant_id,
        "createdAt": created_at,
        "status": "failed",
        "baseConfigPath": str(base_config_path),
        "artifactDir": str(artifact_dir),
        "heuristicSummary": heuristic_summary,
        "model": DEFAULT_CLAUDE_MODEL,
        "dryRun": request.dry_run,
        "active": False,
        "artifacts": {},
        "instructionVariant": {
            "name": request.name,
            "repairAttemptsRequested": request.repair_attempts,
            "samplesRequested": request.samples,
        },
    }

    try:
        base_config = _read_yaml_mapping(base_config_path)
        grammar_skills_text = GRAMMAR_SKILLS_PATH.read_text(encoding="utf-8")

        prompt, instructions_path, base_config_artifact_path, prompt_path = (
            write_instruction_variant_prompt_artifacts(
                instructions_text=request.instruction_text,
                base_config=base_config,
                grammar_skills_text=grammar_skills_text,
                output_dir=artifact_dir,
            )
        )
        metadata["artifacts"]["submittedInstructions"] = str(instructions_path)
        metadata["artifacts"]["baseConfig"] = str(base_config_artifact_path)
        metadata["artifacts"]["llmPrompt"] = str(prompt_path)

        if request.dry_run:
            metadata["status"] = "dry_run"
            record = _record_from_metadata(metadata, validation_report=None)
            _finalize_record(root, artifact_dir, metadata, record)
            return InstructionVariantProposeResponse(
                status="dry_run",
                variant_id=None,
                valid=False,
                repair_attempts_used=0,
                generation_ran=False,
                artifact_dir=str(artifact_dir),
                attempts=[],
                errors=[],
                warnings=[],
            )

        load_llm_environment(env_path)

        try:
            proposal = run_instruction_variant_attempts(
                base_config=base_config,
                grammar_skills_text=grammar_skills_text,
                instructions_text=request.instruction_text,
                initial_prompt=prompt,
                attempts_dir=artifact_dir / "attempts",
                top_level_dir=artifact_dir,
                model=DEFAULT_CLAUDE_MODEL,
                max_tokens=DEFAULT_MAX_TOKENS,
                repair_attempts=request.repair_attempts,
                claude_call=assistant.propose_grammar_variant_with_claude,
            )
        except InstructionVariantAttemptError as exc:
            metadata["instructionVariant"]["attempts"] = [
                attempt.to_dict() for attempt in exc.completed_attempts
            ] + [exc.failed_attempt]
            metadata["instructionVariant"]["repairAttemptsUsed"] = exc.failed_attempt["index"]
            metadata["status"] = "failed"
            metadata["errorSummary"] = str(exc)
            validation_report = {"is_valid": False, "errors": [str(exc)], "warnings": []}
            record = _record_from_metadata(metadata, validation_report=validation_report)
            _finalize_record(root, artifact_dir, metadata, record)
            raise GrammarVariantControlPlaneError(str(exc), status_code=400, record=record) from exc

        metadata["artifacts"]["proposedConfig"] = str(proposal.proposed_config_path)
        metadata["artifacts"]["configValidationReport"] = str(proposal.validation_report_path)
        metadata["instructionVariant"]["attempts"] = [attempt.to_dict() for attempt in proposal.attempts]
        metadata["instructionVariant"]["repairAttemptsUsed"] = proposal.repair_attempts_used

        if proposal.is_valid:
            metadata["status"] = "valid"
            metadata["validatedConfigPath"] = str(proposal.proposed_config_path)
        else:
            metadata["status"] = "invalid"
            metadata["errorSummary"] = (
                "; ".join(proposal.final_validation_report.get("errors", []))
                or "Proposed config failed validation after all repair attempts."
            )

        samples_dir: Path | None = None
        generation_ran = False
        if proposal.is_valid and request.samples > 0:
            samples_dir = artifact_dir / "generated_samples"
            # Register the validated config before attempting generation, so a
            # generation failure never leaves a valid config unregistered.
            pre_generation_record = _record_from_metadata(
                metadata,
                validation_report=proposal.final_validation_report,
            )
            _finalize_record(root, artifact_dir, metadata, pre_generation_record)
            try:
                run_generation_for_instruction_variant(
                    proposal.proposed_config_path,
                    samples_dir,
                    request.samples,
                    None,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced as a controlled 500
                raise GrammarVariantControlPlaneError(
                    f"Graph generation failed: {exc}",
                    status_code=500,
                    record=pre_generation_record,
                ) from exc
            generation_ran = True
            metadata["instructionVariant"]["generationRan"] = True
            metadata["artifacts"]["generatedSamplesDir"] = str(samples_dir)

        review_summary_path = artifact_dir / "review_summary.md"
        write_instruction_variant_review_summary(
            review_summary_path,
            instructions_path=instructions_path,
            base_config_path=base_config_artifact_path,
            model=DEFAULT_CLAUDE_MODEL,
            repair_attempts_requested=request.repair_attempts,
            attempts=[attempt.to_dict() for attempt in proposal.attempts],
            is_valid=proposal.is_valid,
            final_validation_report=proposal.final_validation_report,
            proposed_config_path=proposal.proposed_config_path,
            samples_requested=request.samples,
            samples_dir=samples_dir,
        )
        metadata["artifacts"]["reviewSummary"] = str(review_summary_path)

        record = _record_from_metadata(metadata, validation_report=proposal.final_validation_report)
        _finalize_record(root, artifact_dir, metadata, record)

        if generation_ran:
            response_status = "generated"
        elif proposal.is_valid:
            response_status = "proposed_valid"
        else:
            response_status = "proposed_invalid"

        return InstructionVariantProposeResponse(
            status=response_status,  # type: ignore[arg-type]
            variant_id=variant_id,
            valid=proposal.is_valid,
            repair_attempts_used=proposal.repair_attempts_used,
            generation_ran=generation_ran,
            artifact_dir=str(artifact_dir),
            attempts=_attempt_summaries(proposal.attempts),
            errors=list(proposal.final_validation_report.get("errors", [])),
            warnings=list(proposal.final_validation_report.get("warnings", [])),
        )
    except GrammarVariantControlPlaneError:
        raise
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["errorSummary"] = str(exc)
        validation_report = {"is_valid": False, "errors": [str(exc)], "warnings": []}
        record = _record_from_metadata(metadata, validation_report=validation_report)
        _finalize_record(root, artifact_dir, metadata, record)
        raise GrammarVariantControlPlaneError(str(exc), status_code=400, record=record) from exc
