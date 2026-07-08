from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest
from fastapi.testclient import TestClient

import graph_layout_synth.api.sampling as sampling_module
import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.api.sampling import (
    GRAMMAR_MODE_ACTIVE_VARIANT,
    GRAMMAR_MODE_ENV,
    GRAMMAR_MODE_STATIC,
    SUGGESTION_CONFIG_PATH_ENV,
    ExistingGeneratorSampler,
)
from graph_layout_synth.grammar_variant_control_plane import (
    ENABLE_LLM_VARIANTS_ENV,
    LLM_VARIANT_DIR_ENV,
    GrammarVariantControlPlaneError,
    active_variant_path,
    list_variant_records,
    propose_variant_from_instructions,
)
from server.main import create_app


BASE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs/generic_building.yaml"


@pytest.fixture(autouse=True)
def clear_variant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENABLE_LLM_VARIANTS_ENV, raising=False)
    monkeypatch.delenv(LLM_VARIANT_DIR_ENV, raising=False)
    monkeypatch.delenv(GRAMMAR_MODE_ENV, raising=False)
    monkeypatch.delenv(SUGGESTION_CONFIG_PATH_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _enabled_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(ENABLE_LLM_VARIANTS_ENV, "true")
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "llm-variants"))
    return TestClient(create_app())


def _valid_llm_response() -> str:
    yaml_text = BASE_CONFIG_PATH.read_text(encoding="utf-8")
    return "Rationale: reuse the validated default config.\n```yaml\n" + yaml_text + "\n```"


def test_variant_endpoints_are_disabled_by_default() -> None:
    client = TestClient(create_app())

    response = client.get("/grammar-variants")

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_dry_run_proposal_creates_artifacts_and_registry_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)

    def fail_if_called(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Claude should not be called for dry-run proposals.")

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fail_if_called)

    response = client.post(
        "/grammar-variants/propose",
        json={
            "heuristicInstructions": "Dry-run only; keep the schema valid.",
            "dryRun": True,
        },
    )

    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "dry_run"
    assert record["active"] is False
    artifact_dir = Path(record["artifactDir"])
    assert (artifact_dir / "metadata.json").is_file()
    assert (artifact_dir / "heuristic_instructions.md").is_file()
    assert (artifact_dir / "base_config_path.txt").is_file()
    assert (artifact_dir / "prompt.md").is_file()
    assert (artifact_dir / "validation_report.json").is_file()
    registry = list_variant_records(tmp_path / "llm-variants")
    assert [item["variantId"] for item in registry] == [record["variantId"]]


def test_mocked_live_proposal_creates_valid_variant_and_detail(
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
            "heuristicInstructions": "Create a valid config variant.",
            "model": "test-model",
        },
    )

    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "valid"
    assert record["model"] == "test-model"
    artifact_dir = Path(record["artifactDir"])
    assert (artifact_dir / "raw_llm_response.md").is_file()
    assert (artifact_dir / "extracted_variant.yaml").is_file()
    assert (artifact_dir / "validated_variant.yaml").is_file()
    assert (artifact_dir / "validation_report.json").is_file()
    assert (artifact_dir / "rationale.md").is_file()
    validation = json.loads((artifact_dir / "validation_report.json").read_text())
    assert validation["is_valid"] is True

    detail = client.get(f"/grammar-variants/{record['variantId']}")

    assert detail.status_code == 200
    assert detail.json()["record"]["variantId"] == record["variantId"]
    assert "validatedYaml" in detail.json()


def test_invalid_yaml_creates_invalid_record_and_cannot_activate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: "```yaml\nproject:\n  name: missing sections\n```",
    )

    response = client.post(
        "/grammar-variants/propose",
        json={"heuristicInstructions": "Return an invalid config."},
    )

    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "invalid"
    assert Path(record["artifactDir"], "invalid_variant.yaml").is_file()
    assert "errorSummary" in record

    activation = client.post(f"/grammar-variants/{record['variantId']}/activate")

    assert activation.status_code == 400
    assert "Only valid" in activation.json()["detail"]


def test_valid_variant_can_be_activated_and_listed(
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
    proposal = client.post(
        "/grammar-variants/propose",
        json={
            "heuristicInstructions": "Create and activate a valid variant.",
            "activateIfValid": True,
        },
    )
    record = proposal.json()

    assert proposal.status_code == 200
    assert record["status"] == "valid"
    assert record["active"] is True
    pointer = json.loads(
        active_variant_path(tmp_path / "llm-variants").read_text(encoding="utf-8")
    )
    assert pointer["variantId"] == record["variantId"]

    listed = client.get("/grammar-variants").json()["variants"]

    assert listed[0]["variantId"] == record["variantId"]
    assert listed[0]["active"] is True


def test_missing_api_key_after_prompt_build_returns_controlled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(GrammarVariantControlPlaneError, match="ANTHROPIC_API_KEY"):
        propose_variant_from_instructions(
            "Live call should fail cleanly without an API key.",
            base_config_path=BASE_CONFIG_PATH.resolve(),
            output_root=tmp_path / "llm-variants",
            env_path=str(tmp_path / "missing.env"),
        )

    records = list_variant_records(tmp_path / "llm-variants")
    assert records[0]["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in records[0]["errorSummary"]


def test_endpoint_missing_api_key_returns_controlled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = _enabled_client(tmp_path, monkeypatch)

    response = client.post(
        "/grammar-variants/propose",
        json={
            "heuristicInstructions": "Attempt a live call without an API key.",
            "baseConfigPath": str(BASE_CONFIG_PATH.resolve()),
        },
    )

    assert response.status_code == 400
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]["message"]
    records = list_variant_records(tmp_path / "llm-variants")
    assert records[0]["status"] == "failed"


def test_sampler_uses_static_config_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    expected_config = object()
    captured_configs = []

    monkeypatch.setattr(sampling_module, "load_config", lambda: expected_config)
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: captured_configs.append(config)
        or [SimpleNamespace(graph=nx.Graph())],
    )

    ExistingGeneratorSampler().sample(frontend, "frontend-anchor", sample_count=1)

    assert captured_configs == [expected_config]


def test_sampler_uses_env_config_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    config_path = tmp_path / "variant.yaml"
    expected_config = object()
    loaded_paths = []

    monkeypatch.setenv(GRAMMAR_MODE_ENV, "env_config")
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setattr(
        sampling_module,
        "load_config",
        lambda path: loaded_paths.append(path) or expected_config,
    )
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=nx.Graph())],
    )

    ExistingGeneratorSampler().sample(frontend, "frontend-anchor", sample_count=1)

    assert loaded_paths == [config_path]


def test_sampler_uses_active_variant_in_active_variant_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "llm-variants"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _valid_llm_response(),
    )
    record = propose_variant_from_instructions(
        "Create a valid active config.",
        output_root=tmp_path / "llm-variants",
        activate_if_valid=True,
    )
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    loaded_paths = []
    expected_config = object()

    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    monkeypatch.setattr(
        sampling_module,
        "load_config",
        lambda path: loaded_paths.append(path) or expected_config,
    )
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=nx.Graph())],
    )

    ExistingGeneratorSampler().sample(frontend, "frontend-anchor", sample_count=1)

    assert loaded_paths == [Path(record["validatedConfigPath"])]


def test_static_mode_ignores_env_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    expected_config = object()

    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(tmp_path / "ignored.yaml"))
    monkeypatch.setattr(sampling_module, "load_config", lambda: expected_config)
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=nx.Graph())],
    )

    ExistingGeneratorSampler().sample(frontend, "frontend-anchor", sample_count=1)
