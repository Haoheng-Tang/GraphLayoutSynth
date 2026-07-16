from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.api.room_type_catalog import display_name_for_room_type
from graph_layout_synth.api.sampling import (
    GRAMMAR_MODE_ACTIVE_VARIANT,
    GRAMMAR_MODE_ENV,
    SUGGESTION_CONFIG_PATH_ENV,
)
from graph_layout_synth.config_contract import build_config_contract
from graph_layout_synth.grammar_variant_control_plane import (
    ENABLE_LLM_VARIANTS_ENV,
    LLM_VARIANT_DIR_ENV,
)
from server.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = PROJECT_ROOT / "configs/generic_building.yaml"
CATALOG_URL = "/program-requirements/room-types"


def _client() -> TestClient:
    return TestClient(create_app())


def _write_variant_config_with_lounge(path: Path) -> None:
    """Write a valid config variant whose vocabulary adds a Lounge room type."""
    config = yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    config["allowed_node_types"].append("Lounge")
    config["semantic_node_groups"]["room_like"].append("Lounge")
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_default_catalog_returns_sorted_unique_room_types() -> None:
    response = _client().get(CATALOG_URL)

    assert response.status_code == 200
    payload = response.json()
    room_types = payload["roomTypes"]
    ids = [item["id"] for item in room_types]
    assert ids == ["ClinicalSupport", "Corridor", "PatientRoom", "StaffSupport"]
    assert all(item["id"] for item in room_types)
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert payload["source"] == "default_config"
    assert payload["configPath"].endswith("generic_building.yaml")


def test_catalog_display_names_are_humanized() -> None:
    payload = _client().get(CATALOG_URL).json()

    display_names = {
        item["id"]: item["displayName"] for item in payload["roomTypes"]
    }
    assert display_names == {
        "ClinicalSupport": "Clinical support",
        "Corridor": "Corridor",
        "PatientRoom": "Patient room",
        "StaffSupport": "Staff support",
    }


def test_display_name_helper_handles_acronyms() -> None:
    assert display_name_for_room_type("ICURoom") == "ICU room"
    assert display_name_for_room_type("Corridor") == "Corridor"


def test_catalog_matches_config_contract_vocabulary() -> None:
    payload = _client().get(CATALOG_URL).json()
    contract = build_config_contract(
        yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    )

    ids = {item["id"] for item in payload["roomTypes"]}
    assert ids <= set(contract.allowed_node_types)
    assert ids == set(contract.room_like_node_types) | set(
        contract.corridor_node_types
    )


def test_catalog_works_without_llm_variant_feature_gate() -> None:
    assert os.getenv(ENABLE_LLM_VARIANTS_ENV) is None

    response = _client().get(CATALOG_URL)

    assert response.status_code == 200


def test_catalog_does_not_touch_llm_code_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("The catalog endpoint must never call Claude.")

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fail_if_called)

    response = _client().get(CATALOG_URL)

    assert response.status_code == 200


def test_catalog_uses_active_variant_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant_path = tmp_path / "validated_variant.yaml"
    _write_variant_config_with_lounge(variant_path)
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    (variant_root / "active_variant.json").write_text(
        json.dumps({"validatedConfigPath": str(variant_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    payload = _client().get(CATALOG_URL).json()

    ids = [item["id"] for item in payload["roomTypes"]]
    assert "Lounge" in ids
    assert ids == sorted(ids)
    assert payload["source"] == "active_variant"
    assert payload["configPath"] == str(variant_path)


def test_catalog_active_variant_mode_without_pointer_fails_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(tmp_path / "empty"))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)

    response = _client().get(CATALOG_URL)

    assert response.status_code == 400
    assert "active" in response.json()["detail"].lower()


def test_catalog_uses_env_config_compatibility_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant_path = tmp_path / "env_variant.yaml"
    _write_variant_config_with_lounge(variant_path)
    monkeypatch.setenv(SUGGESTION_CONFIG_PATH_ENV, str(variant_path))

    payload = _client().get(CATALOG_URL).json()

    assert "Lounge" in {item["id"] for item in payload["roomTypes"]}
    assert payload["source"] == "env_config"


def test_catalog_supports_explicit_base_config_path(tmp_path: Path) -> None:
    variant_path = tmp_path / "inspected.yaml"
    _write_variant_config_with_lounge(variant_path)

    response = _client().get(
        CATALOG_URL,
        params={"baseConfigPath": str(variant_path)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "Lounge" in {item["id"] for item in payload["roomTypes"]}
    assert payload["source"] == "request_config"
    assert payload["configPath"] == str(variant_path)


def test_catalog_rejects_missing_base_config_path() -> None:
    response = _client().get(
        CATALOG_URL,
        params={"baseConfigPath": "does/not/exist.yaml"},
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]
