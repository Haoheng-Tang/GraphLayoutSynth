from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import graph_layout_synth.cli as cli
from graph_layout_synth.cli import main
from graph_layout_synth.grammar_variant_assistant import (
    build_instruction_variant_design_intent,
    build_instruction_variant_prompt,
    build_instruction_variant_repair_prompt,
)


def _base_config() -> dict:
    """A compact, fully valid GraphLayoutSynth config for fast tests."""
    return {
        "project": {"name": "Test config", "building_type": "GenericBuilding"},
        "random_seed_default": 42,
        "generation": {"num_candidates": 1},
        "allowed_node_types": [
            "BuildingFloor",
            "Zone",
            "Corridor",
            "PatientRoom",
            "ClinicalSupport",
            "StaffSupport",
        ],
        "allowed_edge_types": ["door", "wall"],
        "zone_types": ["public"],
        "room_type_counts": {"PatientRoom": 1, "ClinicalSupport": 1},
        "stochastic": {
            "min_zone_count": 1,
            "max_zone_count": 1,
            "min_cluster_size": 1,
            "max_cluster_size": 1,
            "corridor_pattern_choices": ["linear"],
            "support_room_choices": ["ClinicalSupport"],
        },
        "validation": {
            "require_connected_graph": True,
            "require_corridor_access": True,
            "allow_abstract_nodes_final": False,
        },
        "ranking": {"weights": {}, "targets": {}},
        "visualization": {"node_colors": {}, "unknown_node_color": "#c7c7c7"},
        "grammar_rules": [
            {
                "name": "expand_floor",
                "match": {"type": "BuildingFloor", "is_abstract": True},
                "action": {
                    "remove_matched_node": True,
                    "create_nodes": [
                        {
                            "alias": "zone",
                            "type": "Zone",
                            "count": 1,
                            "attributes": {"is_abstract": True},
                        }
                    ],
                    "create_edges": [],
                },
            },
            {
                "name": "expand_zone",
                "match": {"type": "Zone", "is_abstract": True},
                "action": {
                    "remove_matched_node": True,
                    "create_nodes": [
                        {
                            "alias": "corridor",
                            "type": "Corridor",
                            "count": 1,
                            "attributes": {"is_abstract": False},
                        },
                        {
                            "alias": "room",
                            "type": "PatientRoom",
                            "count": 1,
                            "attributes": {"is_abstract": False},
                        },
                    ],
                    "create_edges": [
                        {
                            "source": "room",
                            "target": "corridor",
                            "edge_type": "door",
                            "mode": "each_to_one",
                        }
                    ],
                },
            },
        ],
    }


INSTRUCTIONS_MARKER = "Avoid a single corridor hub connecting to every patient room."


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _write_instructions(path: Path) -> Path:
    path.write_text(
        "# Inpatient unit design instructions\n\n"
        f"- {INSTRUCTIONS_MARKER}\n"
        "- Patient rooms should connect to corridors with door edges.\n",
        encoding="utf-8",
    )
    return path


def _fenced_yaml_response(config: dict, *, rationale: str = "Rationale text.") -> str:
    return f"{rationale}\n```yaml\n{yaml.safe_dump(config, sort_keys=False)}\n```"


def _invalid_yaml_response(marker: str) -> str:
    return f"Here is a config.\n```yaml\nproject:\n  name: incomplete config {marker}\n```"


def _fail_if_called(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("Claude must not be called in this scenario.")


class _SequencedClaudeCalls:
    """Mock Claude call boundary returning canned responses in order."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, model: str, max_tokens: int) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("Claude was called more times than expected.")
        return self._responses.pop(0)

    @property
    def call_count(self) -> int:
        return len(self.prompts)


def _run(tmp_path: Path, output_dir: Path, extra_args: list[str]) -> None:
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    main(
        [
            "propose-instruction-variant",
            "--instructions",
            str(instructions_path),
            "--base-config",
            str(base_config_path),
            "--output-dir",
            str(output_dir),
            "--env-path",
            str(tmp_path / "missing.env"),
            *extra_args,
        ]
    )


# --- Prompt builder unit tests -------------------------------------------------


def test_prompt_includes_instruction_text_and_base_config() -> None:
    config = _base_config()
    prompt = build_instruction_variant_prompt(config, "Grammar skills text.", INSTRUCTIONS_MARKER)

    assert INSTRUCTIONS_MARKER in prompt
    assert "Test config" in prompt  # from the base config's project.name
    assert "Do not generate graph samples" in prompt


def test_design_intent_wraps_instructions_with_framing() -> None:
    design_intent = build_instruction_variant_design_intent(INSTRUCTIONS_MARKER)

    assert "# Design Instructions" in design_intent
    assert INSTRUCTIONS_MARKER in design_intent
    assert "unsupported config field" in design_intent


def test_repair_prompt_includes_invalid_yaml_and_validation_errors_and_instructions() -> None:
    config = _base_config()
    prompt = build_instruction_variant_repair_prompt(
        config,
        "Grammar skills text.",
        INSTRUCTIONS_MARKER,
        "project:\n  name: broken\n",
        ["Config is missing required field 'allowed_node_types'."],
    )

    assert INSTRUCTIONS_MARKER in prompt
    assert "project:\n  name: broken" in prompt
    assert "Config is missing required field 'allowed_node_types'." in prompt
    assert "complete corrected YAML config" in prompt
    assert "Do not return a patch or diff." in prompt
    assert "Do not invent unsupported fields." in prompt
    assert "Do not generate graph samples" in prompt
    assert "Deterministic GraphLayoutSynth validation" in prompt


# --- CLI: dry run ---------------------------------------------------------------


def test_no_call_writes_expected_artifacts_and_prompt_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--no-call"])

    assert (output_dir / "submitted_instructions.md").is_file()
    assert (output_dir / "base_config.yaml").is_file()
    assert (output_dir / "llm_prompt.md").is_file()
    assert (output_dir / "manifest.json").is_file()
    # Nothing beyond the dry-run artifact set should exist.
    assert not (output_dir / "proposed_config.yaml").exists()
    assert not (output_dir / "generated_samples").exists()
    assert not (output_dir / "attempts").exists()

    submitted = (output_dir / "submitted_instructions.md").read_text(encoding="utf-8")
    assert INSTRUCTIONS_MARKER in submitted

    prompt_text = (output_dir / "llm_prompt.md").read_text(encoding="utf-8")
    assert INSTRUCTIONS_MARKER in prompt_text
    assert "Test config" in prompt_text

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["noCall"] is True
    assert manifest["claudeCalled"] is False
    assert manifest["status"] == "dry_run"
    assert manifest["samplesRequested"] == 0
    assert manifest["repairAttemptsRequested"] == 0
    assert manifest["repairAttemptsUsed"] == 0
    assert manifest["artifacts"]["llmPrompt"].endswith("llm_prompt.md")


def test_no_call_with_repair_attempts_still_never_calls_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--no-call", "--repair-attempts", "2"])

    assert not (output_dir / "attempts").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "dry_run"
    assert manifest["claudeCalled"] is False
    assert manifest["repairAttemptsRequested"] == 2
    assert manifest["repairAttemptsUsed"] == 0


def test_missing_instruction_file_returns_controlled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", _fail_if_called)
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

    with pytest.raises(SystemExit, match="not found"):
        main(
            [
                "propose-instruction-variant",
                "--instructions",
                str(tmp_path / "does_not_exist.md"),
                "--base-config",
                str(base_config_path),
                "--output-dir",
                str(output_dir),
                "--no-call",
            ]
        )

    assert not output_dir.exists()


# --- CLI: live (mocked) calls, no repair ----------------------------------------


def test_mocked_valid_response_writes_full_artifact_set_without_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, [])

    assert claude.call_count == 1
    initial_dir = output_dir / "attempts" / "attempt_0_initial"
    assert (initial_dir / "raw_llm_response.md").is_file()
    assert (initial_dir / "proposed_config.yaml").is_file()
    assert (initial_dir / "config_validation_report.json").is_file()
    assert not (initial_dir / "repair_prompt.md").exists()

    assert (output_dir / "proposed_config.yaml").is_file()
    assert (output_dir / "config_validation_report.json").is_file()
    assert (output_dir / "review_summary.md").is_file()
    assert not (output_dir / "generated_samples").exists()

    proposed = yaml.safe_load((output_dir / "proposed_config.yaml").read_text(encoding="utf-8"))
    assert proposed == valid_config

    report = json.loads((output_dir / "config_validation_report.json").read_text(encoding="utf-8"))
    assert report["is_valid"] is True

    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "PASSED" in review
    assert "No graph samples were requested" in review
    assert "| 0 | initial | yes |" in review

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["claudeCalled"] is True
    assert manifest["status"] == "proposed_valid"
    assert manifest["generationRan"] is False
    assert manifest["repairAttemptsUsed"] == 0
    assert len(manifest["attempts"]) == 1


def test_initial_invalid_with_zero_repair_attempts_does_not_call_repair_or_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = _SequencedClaudeCalls(_invalid_yaml_response("v1"))
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as excinfo:
        _run(tmp_path, output_dir, ["--samples", "5"])
    assert excinfo.value.code == 1

    assert claude.call_count == 1
    assert (output_dir / "attempts" / "attempt_0_initial").is_dir()
    assert not (output_dir / "attempts" / "attempt_1_repair").exists()
    assert (output_dir / "proposed_config.yaml").is_file()
    report = json.loads((output_dir / "config_validation_report.json").read_text(encoding="utf-8"))
    assert report["is_valid"] is False
    assert report["errors"]
    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "FAILED" in review
    assert "No graph samples were generated" in review
    assert not (output_dir / "generated_samples").exists()

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "proposed_invalid"
    assert manifest["repairAttemptsRequested"] == 0
    assert manifest["repairAttemptsUsed"] == 0
    assert "errorSummary" in manifest
    assert len(manifest["attempts"]) == 1


def test_valid_config_with_samples_calls_generation_pipeline_with_requested_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    captured_args: dict[str, object] = {}

    def fake_run_generate(args) -> None:
        captured_args["config"] = args.config
        captured_args["num_candidates"] = args.num_candidates
        captured_args["output_dir"] = args.output_dir
        args.output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli, "run_generate", fake_run_generate)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--samples", "3"])

    assert captured_args["num_candidates"] == 3
    assert captured_args["config"] == output_dir / "proposed_config.yaml"
    assert captured_args["output_dir"] == output_dir / "generated_samples"

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "generated"
    assert manifest["generationRan"] is True
    assert manifest["artifacts"]["generatedSamplesDir"].endswith("generated_samples")
    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "Requested 3 sample(s)" in review


def test_samples_zero_validates_but_does_not_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--samples", "0"])

    assert not (output_dir / "generated_samples").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "proposed_valid"
    assert manifest["generationRan"] is False


# --- CLI: validation-guided repair -----------------------------------------------


def test_initial_invalid_with_one_repair_attempt_calls_repair_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--repair-attempts", "1"])

    assert claude.call_count == 2
    repair_dir = output_dir / "attempts" / "attempt_1_repair"
    assert (repair_dir / "repair_prompt.md").is_file()
    assert (repair_dir / "raw_llm_response.md").is_file()
    assert (repair_dir / "proposed_config.yaml").is_file()
    assert (repair_dir / "config_validation_report.json").is_file()

    proposed = yaml.safe_load((output_dir / "proposed_config.yaml").read_text(encoding="utf-8"))
    assert proposed == valid_config

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "proposed_valid"
    assert manifest["repairAttemptsRequested"] == 1
    assert manifest["repairAttemptsUsed"] == 1
    assert len(manifest["attempts"]) == 2


def test_repair_prompt_sent_to_claude_includes_invalid_yaml_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--repair-attempts", "1"])

    assert len(claude.prompts) == 2
    repair_prompt = claude.prompts[1]
    assert "incomplete config v1" in repair_prompt
    assert INSTRUCTIONS_MARKER in repair_prompt
    assert "Deterministic Validation Errors" in repair_prompt
    assert "complete corrected YAML config" in repair_prompt
    assert "Do not return a patch or diff." in repair_prompt

    initial_report = json.loads(
        (
            output_dir / "attempts" / "attempt_0_initial" / "config_validation_report.json"
        ).read_text(encoding="utf-8")
    )
    for error in initial_report["errors"]:
        assert error in repair_prompt


def test_repair_success_triggers_generation_when_samples_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    captured_args: dict[str, object] = {}

    def fake_run_generate(args) -> None:
        captured_args["config"] = args.config
        captured_args["num_candidates"] = args.num_candidates
        args.output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli, "run_generate", fake_run_generate)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--repair-attempts", "1", "--samples", "4"])

    assert captured_args["num_candidates"] == 4
    assert captured_args["config"] == output_dir / "proposed_config.yaml"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "generated"
    assert manifest["generationRan"] is True
    assert manifest["repairAttemptsUsed"] == 1


def test_repair_still_invalid_writes_second_report_and_no_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _invalid_yaml_response("v2"),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as excinfo:
        _run(tmp_path, output_dir, ["--repair-attempts", "1", "--samples", "5"])
    assert excinfo.value.code == 1

    assert claude.call_count == 2
    initial_report = json.loads(
        (
            output_dir / "attempts" / "attempt_0_initial" / "config_validation_report.json"
        ).read_text(encoding="utf-8")
    )
    repair_report = json.loads(
        (
            output_dir / "attempts" / "attempt_1_repair" / "config_validation_report.json"
        ).read_text(encoding="utf-8")
    )
    assert initial_report["is_valid"] is False
    assert repair_report["is_valid"] is False

    top_level_report = json.loads(
        (output_dir / "config_validation_report.json").read_text(encoding="utf-8")
    )
    assert top_level_report == repair_report
    assert not (output_dir / "generated_samples").exists()

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "proposed_invalid"
    assert manifest["repairAttemptsUsed"] == 1
    assert len(manifest["attempts"]) == 2
    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "Repair attempts were exhausted" in review


def test_multiple_repair_attempts_stop_at_first_valid_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _invalid_yaml_response("v2"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    # Allow up to 3 repair attempts; success on the 2nd repair (3rd call overall)
    # must stop the loop before a 3rd repair attempt is made.
    _run(tmp_path, output_dir, ["--repair-attempts", "3"])

    assert claude.call_count == 3
    assert (output_dir / "attempts" / "attempt_0_initial").is_dir()
    assert (output_dir / "attempts" / "attempt_1_repair").is_dir()
    assert (output_dir / "attempts" / "attempt_2_repair").is_dir()
    assert not (output_dir / "attempts" / "attempt_3_repair").exists()

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "proposed_valid"
    assert manifest["repairAttemptsRequested"] == 3
    assert manifest["repairAttemptsUsed"] == 2
    assert len(manifest["attempts"]) == 3
    assert [attempt["isValid"] for attempt in manifest["attempts"]] == [False, False, True]


def test_manifest_records_attempts_correctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--repair-attempts", "1"])

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    attempts = manifest["attempts"]
    assert len(attempts) == 2

    assert attempts[0]["index"] == 0
    assert attempts[0]["kind"] == "initial"
    assert attempts[0]["isValid"] is False
    assert "repairPrompt" not in attempts[0]["artifacts"]
    assert set(attempts[0]["artifacts"]) >= {"rawLlmResponse", "proposedConfig", "configValidationReport"}

    assert attempts[1]["index"] == 1
    assert attempts[1]["kind"] == "repair"
    assert attempts[1]["isValid"] is True
    assert set(attempts[1]["artifacts"]) >= {
        "repairPrompt",
        "rawLlmResponse",
        "proposedConfig",
        "configValidationReport",
    }


def test_review_summary_lists_initial_and_repair_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    output_dir = tmp_path / "out"

    _run(tmp_path, output_dir, ["--repair-attempts", "1"])

    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "Repair attempts requested: 1" in review
    assert "Repair attempts used: 1" in review
    assert "| 0 | initial | no |" in review
    assert "| 1 | repair | yes |" in review
