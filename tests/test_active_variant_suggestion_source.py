"""`/suggest-next-room` config-source resolution across grammar modes.

The suggestion sampler lives in ``app.state.predictor`` for the whole server
process, so these tests exercise config resolution through one persistent
app instance: activating a grammar variant (heuristic- or instruction-
generated) must take effect on the next suggestion request without a server
restart, must never call Claude, and must fail explicitly -- not fall back
to the base config -- when active-variant mode has no valid pointer.
"""

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
)
from server.main import create_app


BASE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs/generic_building.yaml"


def _suggest_body(sample_count: int = 2) -> dict:
    return {
        "floorplan": {
            "schemaVersion": 1,
            "rooms": [
                {
                    "id": "room-1",
                    "type": "Corridor",
                    "x": 100,
                    "y": 100,
                    "width": 150,
                    "height": 80,
                },
                {
                    "id": "room-2",
                    "type": "PatientRoom",
                    "x": 250,
                    "y": 100,
                    "width": 150,
                    "height": 110,
                },
            ],
            "edges": [
                {
                    "id": "edge-1",
                    "sourceRoomId": "room-1",
                    "targetRoomId": "room-2",
                    "edgeType": "door",
                }
            ],
            "selectedRoomId": "room-1",
        },
        "anchorRoomId": "room-1",
        "sampleCount": sample_count,
    }


def _valid_llm_response() -> str:
    yaml_text = BASE_CONFIG_PATH.read_text(encoding="utf-8")
    return "Rationale: reuse the validated default config.\n```yaml\n" + yaml_text + "\n```"


def _enabled_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(ENABLE_LLM_VARIANTS_ENV, "true")
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "llm-variants"))
    return TestClient(create_app())


def _patch_generation(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Capture config paths loaded by the sampler; stub graph generation."""
    loaded_paths: list[Path] = []
    monkeypatch.setattr(
        sampling_module,
        "load_config",
        lambda path=None: loaded_paths.append(path) or object(),
    )
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=nx.Graph())],
    )
    return loaded_paths


def _activate_valid_variant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Propose one mocked-Claude heuristic variant and activate it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant,
        "propose_grammar_variant_with_claude",
        lambda prompt, model, max_tokens: _valid_llm_response(),
    )
    propose = client.post(
        "/grammar-variants/propose",
        json={"heuristicInstructions": "Create a valid config variant."},
    )
    assert propose.status_code == 200
    record = propose.json()
    assert record["status"] == "valid"
    activate = client.post(f"/grammar-variants/{record['variantId']}/activate")
    assert activate.status_code == 200
    return activate.json()["variant"]


# --- Active variant mode --------------------------------------------------------


def test_suggest_next_room_uses_activated_variant_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    record = _activate_valid_variant(client, monkeypatch)
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    response = client.post("/suggest-next-room", json=_suggest_body())

    assert response.status_code == 200
    assert loaded_paths == [Path(record["validatedConfigPath"])]


def test_activating_new_variant_takes_effect_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the process-lifetime sampler must not pin the first config.

    Before the fix, `ExistingGeneratorSampler.resolved_config()` cached the
    first resolution forever, so a variant activated after the first
    suggestion request was silently ignored until a server restart.
    """
    client = _enabled_client(tmp_path, monkeypatch)
    first_record = _activate_valid_variant(client, monkeypatch)
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    first = client.post("/suggest-next-room", json=_suggest_body())
    assert first.status_code == 200

    second_record = _activate_valid_variant(client, monkeypatch)
    assert second_record["variantId"] != first_record["variantId"]

    second = client.post("/suggest-next-room", json=_suggest_body())
    assert second.status_code == 200

    assert loaded_paths == [
        Path(first_record["validatedConfigPath"]),
        Path(second_record["validatedConfigPath"]),
    ]


def test_unchanged_active_variant_reuses_parsed_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    record = _activate_valid_variant(client, monkeypatch)
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200
    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200

    assert loaded_paths == [Path(record["validatedConfigPath"])]


def test_active_variant_mode_without_pointer_fails_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "empty"))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    loaded_paths = _patch_generation(monkeypatch)
    client = TestClient(create_app())

    response = client.post("/suggest-next-room", json=_suggest_body())

    assert response.status_code == 400
    assert "no active grammar variant" in response.json()["detail"].lower()
    assert loaded_paths == []


def test_active_variant_with_missing_config_file_fails_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    (variant_root / "active_variant.json").write_text(
        json.dumps(
            {
                "variantId": "variant-x",
                "validatedConfigPath": str(tmp_path / "deleted.yaml"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    loaded_paths = _patch_generation(monkeypatch)
    client = TestClient(create_app())

    response = client.post("/suggest-next-room", json=_suggest_body())

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]
    assert loaded_paths == []


# --- Instruction-generated variants ---------------------------------------------


def test_instruction_generated_variant_drives_suggestions_after_activation(
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

    propose = client.post(
        "/grammar-variants/propose-from-instructions",
        json={"instructionText": "# Rules\n\n- Keep corridors short.\n"},
    )
    assert propose.status_code == 200
    proposal = propose.json()
    assert proposal["valid"] is True
    variant_id = proposal["variantId"]

    activate = client.post(f"/grammar-variants/{variant_id}/activate")
    assert activate.status_code == 200
    record = activate.json()["variant"]

    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    response = client.post("/suggest-next-room", json=_suggest_body())

    assert response.status_code == 200
    assert loaded_paths == [Path(record["validatedConfigPath"])]
    assert variant_id in str(loaded_paths[0])


# --- No LLM calls from suggestions ----------------------------------------------


def test_suggest_next_room_never_calls_claude_in_any_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("/suggest-next-room must never call Claude.")

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fail_if_called)
    _patch_generation(monkeypatch)
    client = TestClient(create_app())

    # static (mode unset)
    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200

    # static (explicit)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200

    # env_config
    monkeypatch.setenv(GRAMMAR_MODE_ENV, "env_config")
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(BASE_CONFIG_PATH))
    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200

    # active_variant (pointer written directly; no proposal flow involved)
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    (variant_root / "active_variant.json").write_text(
        json.dumps(
            {
                "variantId": "variant-manual",
                "validatedConfigPath": str(BASE_CONFIG_PATH),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.delenv(SUGGESTION_CONFIG_PATH_ENV, raising=False)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    assert client.post("/suggest-next-room", json=_suggest_body()).status_code == 200


# --- Catalog consistency --------------------------------------------------------


def test_catalog_and_suggestions_resolve_same_active_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enabled_client(tmp_path, monkeypatch)
    record = _activate_valid_variant(client, monkeypatch)
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    catalog = client.get("/program-requirements/room-types")
    suggestion = client.post("/suggest-next-room", json=_suggest_body())

    assert catalog.status_code == 200
    assert suggestion.status_code == 200
    payload = catalog.json()
    assert payload["source"] == "active_variant"
    assert Path(payload["configPath"]) == Path(record["validatedConfigPath"])
    assert loaded_paths == [Path(record["validatedConfigPath"])]


def test_catalog_and_suggestions_resolve_same_source_in_static_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    client = TestClient(create_app())

    catalog = client.get("/program-requirements/room-types")
    suggestion = client.post("/suggest-next-room", json=_suggest_body())

    assert catalog.status_code == 200
    assert suggestion.status_code == 200
    payload = catalog.json()
    assert payload["source"] == "default_config"
    assert payload["configPath"].endswith("generic_building.yaml")
    # Static mode loads the default config (no explicit path argument).
    assert loaded_paths == [None]


def test_catalog_and_suggestions_resolve_same_source_in_env_config_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "env_config.yaml"
    config_path.write_text(
        BASE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    loaded_paths = _patch_generation(monkeypatch)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, "env_config")
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(config_path))
    client = TestClient(create_app())

    catalog = client.get("/program-requirements/room-types")
    suggestion = client.post("/suggest-next-room", json=_suggest_body())

    assert catalog.status_code == 200
    assert suggestion.status_code == 200
    payload = catalog.json()
    assert payload["source"] == "env_config"
    assert Path(payload["configPath"]) == config_path
    assert loaded_paths == [config_path]


# --- Program-requirements validation source consistency -------------------------


def _write_config_with_extra_room_type(path: Path, room_type: str) -> None:
    """Write a valid config variant whose vocabulary adds one room type."""
    import yaml

    config = yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    config["allowed_node_types"].append(room_type)
    config["semantic_node_groups"]["room_like"].append(room_type)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _lounge_program_body() -> dict:
    return {
        "programRequirements": {
            "schemaVersion": 1,
            "program": {"roomMix": {"Lounge": {"min": 1, "target": 1, "max": 2}}},
        }
    }


def test_program_validate_follows_active_variant_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalog must never offer a type that default validation rejects.

    A variant adds a `Lounge` type that the base config does not know. With
    the variant active, the catalog shows `Lounge` and validation accepts
    it through the same shared config-source resolution.
    """
    variant_config = tmp_path / "lounge_variant.yaml"
    _write_config_with_extra_room_type(variant_config, "Lounge")
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    (variant_root / "active_variant.json").write_text(
        json.dumps(
            {"variantId": "variant-lounge", "validatedConfigPath": str(variant_config)}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    client = TestClient(create_app())

    catalog_ids = [
        item["id"]
        for item in client.get("/program-requirements/room-types").json()["roomTypes"]
    ]
    assert "Lounge" in catalog_ids

    response = client.post("/program-requirements/validate", json=_lounge_program_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert not any(
        issue["code"] == "UNKNOWN_ROOM_TYPE" for issue in payload["errors"]
    )


def test_program_validate_uses_default_config_without_grammar_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())

    response = client.post("/program-requirements/validate", json=_lounge_program_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert any(issue["code"] == "UNKNOWN_ROOM_TYPE" for issue in payload["errors"])


def test_program_validate_explicit_base_config_overrides_grammar_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lounge_config = tmp_path / "lounge_config.yaml"
    _write_config_with_extra_room_type(lounge_config, "Lounge")
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    client = TestClient(create_app())

    body = _lounge_program_body()
    body["baseConfigPath"] = str(lounge_config)
    response = client.post("/program-requirements/validate", json=body)

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_program_validate_missing_active_pointer_fails_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "empty"))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    client = TestClient(create_app())

    response = client.post("/program-requirements/validate", json=_lounge_program_body())

    assert response.status_code == 400
    assert "no active grammar variant" in response.json()["detail"].lower()


# --- Sampler unit behavior ------------------------------------------------------


def test_sampler_rechecks_active_pointer_on_every_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    config_a = tmp_path / "variant_a.yaml"
    config_b = tmp_path / "variant_b.yaml"
    config_a.write_text("placeholder: a\n", encoding="utf-8")
    config_b.write_text("placeholder: b\n", encoding="utf-8")
    pointer_path = variant_root / "active_variant.json"

    def _point_to(variant_id: str, config_path: Path) -> None:
        pointer_path.write_text(
            json.dumps(
                {"variantId": variant_id, "validatedConfigPath": str(config_path)}
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    loaded_paths = _patch_generation(monkeypatch)

    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    sampler = ExistingGeneratorSampler()

    _point_to("variant-a", config_a)
    sampler.sample(frontend, "frontend-anchor", sample_count=1)
    _point_to("variant-b", config_b)
    sampler.sample(frontend, "frontend-anchor", sample_count=1)
    sampler.sample(frontend, "frontend-anchor", sample_count=1)

    assert loaded_paths == [config_a, config_b]
    assert sampler.last_config_source is not None
    assert sampler.last_config_source.mode == GRAMMAR_MODE_ACTIVE_VARIANT
    assert sampler.last_config_source.variant_id == "variant-b"


def test_injected_config_override_always_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [SimpleNamespace(graph=nx.Graph())],
    )
    injected = object()
    frontend = nx.Graph()
    frontend.add_node("frontend-anchor", type="Corridor")
    sampler = ExistingGeneratorSampler(config=injected)

    sampler.sample(frontend, "frontend-anchor", sample_count=1)

    assert sampler.config is injected
    assert sampler.last_resolved_config is injected
    assert sampler.last_config_source is not None
    assert sampler.last_config_source.mode == "injected"
