"""Room-type catalog exposes each type's declared semantic groups.

Groups are the declared source for role-based frontend behavior (corridor
auto-extension, default room depth). Group names are returned verbatim and
config-defined, never collapsed into computed role flags, so a variant that
names circulation `Spine` still resolves correctly where type-name substring
matching would fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from graph_layout_synth.api.models import ProgramRoomTypeCatalogItem
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.config_contract import build_config_contract, is_corridor_node_type
from graph_layout_synth.grammar_variant_control_plane import LLM_VARIANT_DIR_ENV
from server.main import create_app


CATALOG_URL = "/program-requirements/room-types"
GRAMMAR_MODE_ENV = "GRAPHLAYOUTSYNTH_GRAMMAR_MODE"


def _raw_config() -> dict:
    return yaml.safe_load(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))


def _catalog_items(client: TestClient) -> dict[str, dict]:
    payload = client.get(CATALOG_URL).json()
    return {item["id"]: item for item in payload["roomTypes"]}


def _activate_variant_config(tmp_path, monkeypatch, config: dict, variant_id: str) -> None:
    """Write a config as the active variant and select active-variant mode."""
    variant_path = tmp_path / f"{variant_id}.yaml"
    variant_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir(exist_ok=True)
    (variant_root / "active_variant.json").write_text(
        json.dumps({"variantId": variant_id, "validatedConfigPath": str(variant_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, "active_variant")


# --- Default config groups --------------------------------------------------------


def test_catalog_reports_declared_groups_for_default_config():
    """Exact sorted lists, so group drift fails loudly rather than silently."""
    items = _catalog_items(TestClient(create_app()))

    assert items["OnStageCorridor"]["groups"] == ["corridor"]
    assert items["OffStageCorridor"]["groups"] == ["corridor"]
    # NurseStation carries its support role: the frontend sizes it as a
    # support room rather than a full-depth patient room.
    assert items["NurseStation"]["groups"] == [
        "clinical_support",
        "room_like",
        "support",
    ]
    assert items["PatientRoom"]["groups"] == ["patient", "patient_care", "room_like"]
    assert items["Stair"]["groups"] == ["room_like", "vertical_circulation"]
    assert items["StaffLounge"]["groups"] == ["room_like", "staff_support", "support"]
    assert items["Kitchen"]["groups"] == ["building_service", "room_like"]


def test_every_catalog_item_has_sorted_nonempty_groups():
    items = _catalog_items(TestClient(create_app()))

    for item_id, item in items.items():
        assert isinstance(item["groups"], list), item_id
        # Catalog membership is room_like union corridor, so every entry has
        # at least the group that admitted it.
        assert item["groups"], item_id
        assert item["groups"] == sorted(item["groups"]), item_id
        assert len(item["groups"]) == len(set(item["groups"])), item_id


def test_multi_group_types_return_every_group():
    items = _catalog_items(TestClient(create_app()))

    # StorageRoom is deliberately both a building service and a support room.
    assert items["StorageRoom"]["groups"] == [
        "building_service",
        "room_like",
        "support",
    ]
    assert len(items["NurseStation"]["groups"]) == 3


def test_catalog_item_groups_default_to_empty_list_not_null():
    item = ProgramRoomTypeCatalogItem(id="SomeType")

    assert item.groups == []
    assert item.model_dump(by_alias=True)["groups"] == []


# --- Config-defined group names, not a fixed enum ---------------------------------


def test_variant_naming_circulation_spine_reports_corridor_group(tmp_path, monkeypatch):
    """The case type-name substring matching cannot handle.

    A variant renames circulation to `Spine`, which contains no "corridor"
    token. The catalog still reports its declared `corridor` group, so the
    frontend resolves the role from data rather than spelling.
    """
    config = yaml.safe_load(
        Path(DEFAULT_CONFIG_PATH)
        .read_text(encoding="utf-8")
        .replace("OnStageCorridor", "Spine")
    )
    _activate_variant_config(tmp_path, monkeypatch, config, "variant-spine")

    items = _catalog_items(TestClient(create_app()))

    assert "OnStageCorridor" not in items
    assert items["Spine"]["groups"] == ["corridor"]
    assert items["Spine"]["generated"] is True
    assert items["OffStageCorridor"]["groups"] == ["corridor"]
    # Substring matching would have missed this type entirely.
    assert "corridor" not in "Spine".lower()


def test_variant_custom_group_name_passes_through_verbatim(tmp_path, monkeypatch):
    """Unknown group names are opaque pass-through, not a fixed enum."""
    config = _raw_config()
    config["semantic_node_groups"]["isolation_capable"] = ["PatientRoom"]
    _activate_variant_config(tmp_path, monkeypatch, config, "variant-groups")

    items = _catalog_items(TestClient(create_app()))

    assert items["PatientRoom"]["groups"] == [
        "isolation_capable",
        "patient",
        "patient_care",
        "room_like",
    ]


# --- One source of role truth -----------------------------------------------------


def test_catalog_groups_agree_with_is_corridor_node_type():
    """Guards against a second source of role truth appearing."""
    contract = build_config_contract(_raw_config())
    corridor_types = set(contract.corridor_node_types)
    items = _catalog_items(TestClient(create_app()))

    for item_id, item in items.items():
        helper_says_circulation = is_corridor_node_type(item_id, corridor_types)
        catalog_says_circulation = "corridor" in item["groups"]
        assert helper_says_circulation == catalog_says_circulation, item_id

    assert {
        item_id for item_id, item in items.items() if "corridor" in item["groups"]
    } == corridor_types
