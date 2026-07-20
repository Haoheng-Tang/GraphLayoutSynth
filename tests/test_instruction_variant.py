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


def _fail_if_called(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("Claude must not be called in this scenario.")


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


# --- CLI: dry run ---------------------------------------------------------------


def test_no_call_writes_expected_artifacts_and_prompt_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", _fail_if_called)
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

    main(
        [
            "propose-instruction-variant",
            "--instructions",
            str(instructions_path),
            "--base-config",
            str(base_config_path),
            "--output-dir",
            str(output_dir),
            "--no-call",
        ]
    )

    assert (output_dir / "submitted_instructions.md").is_file()
    assert (output_dir / "base_config.yaml").is_file()
    assert (output_dir / "llm_prompt.md").is_file()
    assert (output_dir / "manifest.json").is_file()
    # Nothing beyond the dry-run artifact set should exist.
    assert not (output_dir / "raw_llm_response.md").exists()
    assert not (output_dir / "proposed_config.yaml").exists()
    assert not (output_dir / "generated_samples").exists()

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
    assert manifest["artifacts"]["llmPrompt"].endswith("llm_prompt.md")


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


# --- CLI: live (mocked) calls -----------------------------------------------


def test_mocked_valid_response_writes_full_artifact_set_without_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    monkeypatch.setattr(
        cli,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

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
        ]
    )

    assert (output_dir / "raw_llm_response.md").is_file()
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

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["claudeCalled"] is True
    assert manifest["status"] == "valid_no_samples"


def test_invalid_proposed_config_writes_failure_and_no_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: (
            "Here is a config.\n```yaml\nproject:\n  name: incomplete config\n```"
        ),
    )
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "propose-instruction-variant",
                "--instructions",
                str(instructions_path),
                "--base-config",
                str(base_config_path),
                "--output-dir",
                str(output_dir),
                "--samples",
                "5",
                "--env-path",
                str(tmp_path / "missing.env"),
            ]
        )
    assert excinfo.value.code == 1

    assert (output_dir / "proposed_config.yaml").is_file()
    report = json.loads((output_dir / "config_validation_report.json").read_text(encoding="utf-8"))
    assert report["is_valid"] is False
    assert report["errors"]
    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "FAILED" in review
    assert "No graph samples were generated" in review
    assert not (output_dir / "generated_samples").exists()

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "invalid"
    assert "errorSummary" in manifest


def test_valid_config_with_samples_calls_generation_pipeline_with_requested_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    monkeypatch.setattr(
        cli,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _fenced_yaml_response(valid_config),
    )
    captured_args: dict[str, object] = {}

    def fake_run_generate(args) -> None:
        captured_args["config"] = args.config
        captured_args["num_candidates"] = args.num_candidates
        captured_args["output_dir"] = args.output_dir
        args.output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli, "run_generate", fake_run_generate)
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

    main(
        [
            "propose-instruction-variant",
            "--instructions",
            str(instructions_path),
            "--base-config",
            str(base_config_path),
            "--output-dir",
            str(output_dir),
            "--samples",
            "3",
            "--env-path",
            str(tmp_path / "missing.env"),
        ]
    )

    assert captured_args["num_candidates"] == 3
    assert captured_args["config"] == output_dir / "proposed_config.yaml"
    assert captured_args["output_dir"] == output_dir / "generated_samples"

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "valid_with_samples"
    assert manifest["artifacts"]["generatedSamplesDir"].endswith("generated_samples")
    review = (output_dir / "review_summary.md").read_text(encoding="utf-8")
    assert "Requested 3 sample(s)" in review


def test_samples_zero_validates_but_does_not_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    monkeypatch.setattr(
        cli,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(cli, "run_generate", _fail_if_called)
    instructions_path = _write_instructions(tmp_path / "instructions.md")
    base_config_path = _write_yaml(tmp_path / "base_config.yaml", _base_config())
    output_dir = tmp_path / "out"

    main(
        [
            "propose-instruction-variant",
            "--instructions",
            str(instructions_path),
            "--base-config",
            str(base_config_path),
            "--output-dir",
            str(output_dir),
            "--samples",
            "0",
            "--env-path",
            str(tmp_path / "missing.env"),
        ]
    )

    assert not (output_dir / "generated_samples").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "valid_no_samples"
