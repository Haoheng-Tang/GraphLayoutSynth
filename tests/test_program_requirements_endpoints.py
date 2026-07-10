from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.cli import main
from graph_layout_synth.grammar_variant_control_plane import (
    ENABLE_LLM_VARIANTS_ENV,
    LLM_VARIANT_DIR_ENV,
    list_variant_records,
)
from server.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = PROJECT_ROOT / "configs/generic_building.yaml"
EXAMPLE_YAML_PATH = PROJECT_ROOT / "docs/program_requirements/example_healthcare_program.yaml"

INFEASIBLE_PROFILE = {
    "locality": {
        "patientRoomGroupSize": {"min": 4, "preferredMax": 7, "hardMax": 7},
        "localGroupCount": {"min": 1, "preferredMax": 4, "hardMax": 4},
    }
}


def _valid_program_requirements() -> dict:
    return {
        "schemaVersion": 1,
        "program": {"roomMix": {"PatientRoom": {"min": 30, "target": 40, "max": 60}}},
    }


def _infeasible_program_requirements() -> dict:
    return {
        "schemaVersion": 1,
        "program": {"roomMix": {"PatientRoom": {"min": 51, "target": 56, "max": 60}}},
    }


@pytest.fixture(autouse=True)
def clear_variant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENABLE_LLM_VARIANTS_ENV, raising=False)
    monkeypatch.delenv(LLM_VARIANT_DIR_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _enabled_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(ENABLE_LLM_VARIANTS_ENV, "true")
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "llm-variants"))
    return TestClient(create_app())


def _valid_llm_response() -> str:
    yaml_text = BASE_CONFIG_PATH.read_text(encoding="utf-8")
    return "Rationale: reuse the validated default config.\n```yaml\n" + yaml_text + "\n```"


def test_cli_validate_program_requirements_writes_report(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "program_validation.json"

    main(
        [
            "validate-program-requirements",
            "--requirements",
            str(EXAMPLE_YAML_PATH),
            "--base-config",
            str(BASE_CONFIG_PATH),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr().out
    assert "Feasibility: feasible." in captured
    assert "Program requirements are valid." in captured
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["valid"] is True
    assert report["feasibility"] == "feasible"
    assert report["constraintProfile"]["locality"]["patientRoomGroupSize"]["hardMax"] == 12


def test_cli_validate_program_requirements_uses_constraints_file(tmp_path: Path) -> None:
    requirements_path = tmp_path / "requirements.yaml"
    requirements_path.write_text(yaml.safe_dump(_infeasible_program_requirements()), encoding="utf-8")
    constraints_path = tmp_path / "constraints.yaml"
    constraints_path.write_text(yaml.safe_dump(INFEASIBLE_PROFILE), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-program-requirements",
                "--requirements",
                str(requirements_path),
                "--base-config",
                str(BASE_CONFIG_PATH),
                "--constraints",
                str(constraints_path),
            ]
        )

    assert excinfo.value.code == 1


def test_cli_validate_program_requirements_fails_on_invalid_schema(tmp_path: Path, capsys) -> None:
    requirements_path = tmp_path / "requirements.yaml"
    data = _valid_program_requirements()
    data["schemaVersion"] = 2
    requirements_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-program-requirements",
                "--requirements",
                str(requirements_path),
                "--base-config",
                str(BASE_CONFIG_PATH),
            ]
        )

    assert excinfo.value.code == 1
    assert "INVALID_SCHEMA_VERSION" in capsys.readouterr().out


def test_http_validate_endpoint_returns_feasible_result() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/program-requirements/validate",
        json={"programRequirements": _valid_program_requirements()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["feasibility"] == "feasible"
    assert payload["errors"] == []


def test_http_validate_endpoint_reports_infeasible_program() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/program-requirements/validate",
        json={
            "programRequirements": _infeasible_program_requirements(),
            "constraintProfile": INFEASIBLE_PROFILE,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert payload["feasibility"] == "infeasible"
    codes = {issue["code"] for issue in payload["errors"]}
    assert "PATIENT_ROOM_HARD_CAPACITY_EXCEEDED" in codes


def test_http_validate_endpoint_rejects_bad_base_config_path() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/program-requirements/validate",
        json={
            "programRequirements": _valid_program_requirements(),
            "baseConfigPath": "does/not/exist.yaml",
        },
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_http_validate_endpoint_rejects_bad_constraint_profile() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/program-requirements/validate",
        json={
            "programRequirements": _valid_program_requirements(),
            "constraintProfile": {"clusters": {"count": 4}},
        },
    )

    assert response.status_code == 400
    assert "unsupported field" in response.json()["detail"]


def test_variant_proposal_fails_before_llm_when_program_requirements_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fail_if_called(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Claude must not be called when program requirements are infeasible.")

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fail_if_called)

    response = client.post(
        "/grammar-variants/propose",
        json={
            "heuristicInstructions": "Increase the patient room count.",
            "baseConfigPath": str(BASE_CONFIG_PATH.resolve()),
            "programRequirements": _infeasible_program_requirements(),
            "constraintProfile": INFEASIBLE_PROFILE,
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Program requirements preflight failed" in detail["message"]
    record = detail["variant"]
    assert record["status"] == "failed"
    artifact_dir = Path(record["artifactDir"])
    assert (artifact_dir / "submitted_program_requirements.json").is_file()
    assert (artifact_dir / "program_constraint_profile.yaml").is_file()
    assert (artifact_dir / "program_requirements.yaml").is_file()
    validation = json.loads((artifact_dir / "program_requirements_validation.json").read_text(encoding="utf-8"))
    assert validation["valid"] is False
    assert validation["feasibility"] == "infeasible"
    records = list_variant_records(tmp_path / "llm-variants")
    assert records[0]["status"] == "failed"


def test_variant_proposal_saves_program_requirement_artifacts_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _valid_llm_response(),
    )

    response = client.post(
        "/grammar-variants/propose",
        json={
            "heuristicInstructions": "Respect the supplied program requirements.",
            "baseConfigPath": str(BASE_CONFIG_PATH.resolve()),
            "programRequirements": _valid_program_requirements(),
        },
    )

    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "valid"
    artifact_dir = Path(record["artifactDir"])
    assert (artifact_dir / "submitted_program_requirements.json").is_file()
    assert (artifact_dir / "program_requirements.yaml").is_file()
    assert (artifact_dir / "program_constraint_profile.yaml").is_file()
    validation = json.loads((artifact_dir / "program_requirements_validation.json").read_text(encoding="utf-8"))
    assert validation["valid"] is True
    metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["programRequirementsValidation"]["valid"] is True
    assert metadata["programRequirementsValidation"]["feasibility"] == "feasible"
    prompt_text = (artifact_dir / "prompt.md").read_text(encoding="utf-8")
    assert "Validated user program requirements" in prompt_text


def test_cli_variant_proposal_fails_before_llm_when_program_requirements_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graph_layout_synth.cli as cli

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("The LLM environment must not be loaded when preflight fails.")

    monkeypatch.setattr(cli, "load_llm_environment", fail_if_called)
    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", fail_if_called)

    requirements_path = tmp_path / "requirements.yaml"
    requirements_path.write_text(yaml.safe_dump(_infeasible_program_requirements()), encoding="utf-8")
    constraints_path = tmp_path / "constraints.yaml"
    constraints_path.write_text(yaml.safe_dump(INFEASIBLE_PROFILE), encoding="utf-8")
    output_config = tmp_path / "variant.yaml"

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "propose-grammar-variant",
                "--base-config",
                str(BASE_CONFIG_PATH),
                "--program-requirements",
                str(requirements_path),
                "--program-constraints",
                str(constraints_path),
                "--output-config",
                str(output_config),
            ]
        )

    assert excinfo.value.code == 1
    report = json.loads((tmp_path / "variant_program_validation.json").read_text(encoding="utf-8"))
    assert report["valid"] is False
    assert report["feasibility"] == "infeasible"
    assert (tmp_path / "variant_program_requirements.yaml").is_file()
    assert not output_config.exists()
