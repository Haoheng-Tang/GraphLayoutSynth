from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import graph_layout_synth.grammar_variant_assistant as assistant
import graph_layout_synth.instruction_variant_control_plane as instruction_variant_control_plane
from graph_layout_synth.grammar_variant_control_plane import (
    ENABLE_LLM_VARIANTS_ENV,
    LLM_VARIANT_DIR_ENV,
)
from server.main import create_app


PROPOSE_URL = "/grammar-variants/propose-from-instructions"
INSTRUCTIONS_MARKER = "Avoid a single corridor hub connecting to every patient room."


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


@pytest.fixture(autouse=True)
def clear_variant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENABLE_LLM_VARIANTS_ENV, raising=False)
    monkeypatch.delenv(LLM_VARIANT_DIR_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _enabled_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(ENABLE_LLM_VARIANTS_ENV, "true")
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "llm-variants"))
    return TestClient(create_app())


def _write_base_config(tmp_path: Path) -> Path:
    path = tmp_path / "base_config.yaml"
    path.write_text(yaml.safe_dump(_base_config(), sort_keys=False), encoding="utf-8")
    return path


def _request_body(base_config_path: Path, **overrides: object) -> dict:
    body: dict[str, object] = {
        "instructionText": f"# Rules\n\n- {INSTRUCTIONS_MARKER}\n",
        "baseConfigPath": str(base_config_path),
    }
    body.update(overrides)
    return body


# --- 1. Request validation ------------------------------------------------------


def test_empty_instruction_text_returns_controlled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, instructionText=""))

    assert response.status_code == 400


def test_whitespace_only_instruction_text_returns_controlled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, instructionText="   \n\t  "))

    assert response.status_code == 400


def test_negative_repair_attempts_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=-1))

    assert response.status_code == 400


def test_repair_attempts_above_cap_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=4))

    assert response.status_code == 400


def test_negative_samples_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, samples=-1))

    assert response.status_code == 400


def test_samples_above_cap_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, samples=26))

    assert response.status_code == 400


# --- Feature gate -----------------------------------------------------------------


def test_endpoint_disabled_by_default_returns_403(tmp_path: Path) -> None:
    client = TestClient(create_app())
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path))

    assert response.status_code == 403


def test_dry_run_also_gated_by_feature_flag(tmp_path: Path) -> None:
    """Dry runs follow the same gate as the rest of `/grammar-variants/*`."""
    client = TestClient(create_app())
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, dryRun=True))

    assert response.status_code == 403


# --- 2. Dry run --------------------------------------------------------------------


def test_dry_run_writes_artifacts_and_never_calls_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, dryRun=True))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["variantId"] is None
    assert body["valid"] is False
    assert body["generationRan"] is False
    assert body["attempts"] == []

    artifact_dir = Path(body["artifactDir"])
    assert (artifact_dir / "submitted_instructions.md").is_file()
    assert (artifact_dir / "base_config.yaml").is_file()
    assert (artifact_dir / "llm_prompt.md").is_file()
    assert INSTRUCTIONS_MARKER in (artifact_dir / "submitted_instructions.md").read_text(encoding="utf-8")
    assert INSTRUCTIONS_MARKER in (artifact_dir / "llm_prompt.md").read_text(encoding="utf-8")
    assert not (artifact_dir / "attempts").exists()
    assert not (artifact_dir / "generated_samples").exists()


def test_dry_run_does_not_register_an_activatable_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    client.post(PROPOSE_URL, json=_request_body(base_config_path, dryRun=True))

    listed = client.get("/grammar-variants").json()["variants"]
    dry_run_records = [record for record in listed if record["status"] == "dry_run"]
    assert dry_run_records
    activation = client.post(f"/grammar-variants/{dry_run_records[0]['variantId']}/activate")
    assert activation.status_code == 400


# --- 3. Live valid proposal ---------------------------------------------------------


def test_live_valid_response_is_registered_listed_and_activatable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "proposed_valid"
    assert body["valid"] is True
    assert body["repairAttemptsUsed"] == 0
    assert claude.call_count == 1
    variant_id = body["variantId"]
    assert variant_id

    listed = client.get("/grammar-variants").json()["variants"]
    assert any(record["variantId"] == variant_id for record in listed)

    activation = client.post(f"/grammar-variants/{variant_id}/activate")
    assert activation.status_code == 200
    assert activation.json()["variant"]["active"] is True


# --- 4. Initial invalid, no repair --------------------------------------------------


def test_initial_invalid_no_repair_is_not_activatable_and_does_not_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = _SequencedClaudeCalls(_invalid_yaml_response("v1"))
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        _fail_if_called,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, samples=5))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "proposed_invalid"
    assert body["valid"] is False
    assert body["generationRan"] is False
    assert claude.call_count == 1
    assert body["errors"]

    artifact_dir = Path(body["artifactDir"])
    assert (artifact_dir / "proposed_config.yaml").is_file()
    report = json.loads((artifact_dir / "config_validation_report.json").read_text(encoding="utf-8"))
    assert report["is_valid"] is False
    assert not (artifact_dir / "generated_samples").exists()

    activation = client.post(f"/grammar-variants/{body['variantId']}/activate")
    assert activation.status_code == 400


# --- 5. Initial invalid, repair succeeds --------------------------------------------


def test_repair_prompt_includes_errors_yaml_and_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=1))

    assert response.status_code == 200
    assert claude.call_count == 2
    repair_prompt = claude.prompts[1]
    assert "incomplete config v1" in repair_prompt
    assert INSTRUCTIONS_MARKER in repair_prompt
    assert "Deterministic Validation Errors" in repair_prompt
    assert "complete corrected YAML config" in repair_prompt


def test_repair_success_registers_activatable_variant_without_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        _fail_if_called,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=1, samples=0))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "proposed_valid"
    assert body["valid"] is True
    assert body["repairAttemptsUsed"] == 1
    assert body["generationRan"] is False

    activation = client.post(f"/grammar-variants/{body['variantId']}/activate")
    assert activation.status_code == 200


def test_repair_success_generates_when_samples_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _fenced_yaml_response(valid_config),
    )
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    captured: dict[str, object] = {}

    def fake_generate(config_path: Path, output_dir: Path, samples: int, seed) -> None:
        captured["config_path"] = config_path
        captured["samples"] = samples
        output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        fake_generate,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=1, samples=4))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "generated"
    assert body["generationRan"] is True
    assert captured["samples"] == 4
    assert captured["config_path"] == Path(body["artifactDir"]) / "proposed_config.yaml"

    activation = client.post(f"/grammar-variants/{body['variantId']}/activate")
    assert activation.status_code == 200


# --- 6. Repair exhaustion ------------------------------------------------------------


def test_repair_exhaustion_saves_every_attempt_and_is_not_activatable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = _SequencedClaudeCalls(
        _invalid_yaml_response("v1"),
        _invalid_yaml_response("v2"),
    )
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        _fail_if_called,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, repairAttempts=1, samples=5))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "proposed_invalid"
    assert body["valid"] is False
    assert body["generationRan"] is False
    assert claude.call_count == 2
    assert len(body["attempts"]) == 2
    assert [attempt["valid"] for attempt in body["attempts"]] == [False, False]

    artifact_dir = Path(body["artifactDir"])
    assert (artifact_dir / "attempts" / "attempt_0_initial" / "config_validation_report.json").is_file()
    assert (artifact_dir / "attempts" / "attempt_1_repair" / "config_validation_report.json").is_file()
    assert not (artifact_dir / "generated_samples").exists()

    activation = client.post(f"/grammar-variants/{body['variantId']}/activate")
    assert activation.status_code == 400


# --- 7. Generation gating ------------------------------------------------------------


def test_samples_zero_does_not_call_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        _fail_if_called,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, samples=0))

    assert response.status_code == 200
    assert response.json()["generationRan"] is False


def test_invalid_config_never_reaches_generation_even_with_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = _SequencedClaudeCalls(_invalid_yaml_response("v1"))
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    monkeypatch.setattr(
        instruction_variant_control_plane,
        "run_generation_for_instruction_variant",
        _fail_if_called,
    )
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)

    response = client.post(PROPOSE_URL, json=_request_body(base_config_path, samples=10))

    assert response.status_code == 200
    assert response.json()["generationRan"] is False


# --- 8. Guardrails against accidental LLM calls --------------------------------------


def test_room_type_catalog_does_not_call_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = TestClient(create_app())

    response = client.get("/program-requirements/room-types")

    assert response.status_code == 200


def test_program_requirements_validate_does_not_call_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = TestClient(create_app())

    response = client.post(
        "/program-requirements/validate",
        json={"programRequirements": {"schemaVersion": 1, "program": {"roomMix": {}}}},
    )

    assert response.status_code == 200


def test_list_grammar_variants_does_not_call_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = _enabled_client(tmp_path, monkeypatch)

    response = client.get("/grammar-variants")

    assert response.status_code == 200


def test_activate_grammar_variant_does_not_call_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_config = _base_config()
    claude = _SequencedClaudeCalls(_fenced_yaml_response(valid_config))
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", claude)
    client = _enabled_client(tmp_path, monkeypatch)
    base_config_path = _write_base_config(tmp_path)
    variant_id = client.post(PROPOSE_URL, json=_request_body(base_config_path)).json()["variantId"]
    assert claude.call_count == 1

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    response = client.post(f"/grammar-variants/{variant_id}/activate")

    assert response.status_code == 200


def test_suggest_next_room_does_not_call_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    client = TestClient(create_app())

    response = client.post(
        "/suggest-next-room",
        json={
            "floorplan": {
                "schemaVersion": 1,
                "rooms": [
                    {"id": "room-1", "type": "Corridor", "x": 0, "y": 0, "width": 10, "height": 10},
                ],
                "edges": [],
            },
            "anchorRoomId": "room-1",
            "sampleCount": 1,
        },
    )

    assert response.status_code == 200


def test_server_startup_does_not_call_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
