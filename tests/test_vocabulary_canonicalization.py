"""Vocabulary canonicalization: group-declared circulation, no legacy types,
catalog tier flags, and group-derived support metrics."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
import yaml
from fastapi.testclient import TestClient

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, validate_config
from graph_layout_synth.config_contract import is_corridor_node_type
from graph_layout_synth.diversity import extract_diversity_feature_vector
from graph_layout_synth.generator import generate_candidate
from graph_layout_synth.grammar_variant_control_plane import LLM_VARIANT_DIR_ENV
from graph_layout_synth.ranking import compute_candidate_metrics
from graph_layout_synth.review_summary import (
    build_candidate_review_summary,
    support_type_summary,
    typed_accessibility_summary,
)
from graph_layout_synth.validators import validate_graph
from server.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_URL = "/program-requirements/room-types"

ESSENTIAL_GENERATED_TYPES = {
    "PatientRoom",
    "OnStageCorridor",
    "OffStageCorridor",
    "NurseStation",
    "MedicationRoom",
    "CleanUtility",
    "SoiledUtility",
    "EquipmentRoom",
    "StorageRoom",
    "StaffLounge",
    "StaffToilet",
    "PublicToilet",
    "Stair",
    "Elevator",
    "MEPRoom",
    "UtilityRoom",
}


def _raw_config() -> dict:
    return yaml.safe_load(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))


# --- Group-first circulation detection -------------------------------------------


def test_corridor_helper_prefers_declared_group_over_token():
    corridor_types = {"Gallery", "OnStageCorridor"}

    assert is_corridor_node_type("Gallery", corridor_types)
    assert is_corridor_node_type("OnStageCorridor", corridor_types)
    # A token-named type outside the declared group is not circulation.
    assert not is_corridor_node_type("ServiceCorridor", corridor_types)
    # Token fallback without group context.
    assert is_corridor_node_type("ServiceCorridor")
    assert not is_corridor_node_type("Gallery")
    assert not is_corridor_node_type(None)


def test_non_token_circulation_type_validates_through_declared_group(tmp_path):
    """Circulation is a declared property: a `Gallery` corridor group works."""
    config_data = _raw_config()
    config_data["allowed_node_types"] = [
        t for t in config_data["allowed_node_types"] if t not in ("OnStageCorridor", "OffStageCorridor")
    ] + ["Gallery"]
    config_data["semantic_node_groups"]["corridor"] = ["Gallery"]
    config_data["room_type_counts"] = {"PatientRoom": 2, "Gallery": 1}
    config_data["typed_accessibility_pairs"] = [
        {"source_type": "PatientRoom", "target_type": "NurseStation", "edge_type": "door"}
    ]
    # Minimal grammar so the config validates with the new vocabulary.
    config_data["grammar_rules"] = [
        {
            "name": "expand_floor",
            "match": {"type": "BuildingFloor", "is_abstract": True},
            "action": {
                "remove_matched_node": True,
                "create_nodes": [
                    {"alias": "zone", "type": "Zone", "count": 1, "attributes": {"is_abstract": True}}
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
                    {"alias": "gallery", "type": "Gallery", "count": 1, "attributes": {"is_abstract": False}},
                    {"alias": "patient", "type": "PatientRoom", "count": 2, "attributes": {"is_abstract": False}},
                ],
                "create_edges": [
                    {"source": "patient", "target": "gallery", "edge_type": "door", "mode": "each_to_one"}
                ],
            },
        },
    ]
    config = validate_config(config_data)
    assert config.corridor_node_types == ["Gallery"]

    graph = nx.Graph()
    graph.add_node("gallery", type="Gallery", is_abstract=False)
    graph.add_node("room", type="PatientRoom", is_abstract=False)
    graph.add_edge("gallery", "room", edge_type="door")

    assert validate_graph(graph, config).is_valid


def test_config_without_any_circulation_type_fails():
    config_data = _raw_config()
    config_data["allowed_node_types"] = [
        t for t in config_data["allowed_node_types"] if t not in ("OnStageCorridor", "OffStageCorridor")
    ]
    config_data["semantic_node_groups"].pop("corridor")
    config_data["room_type_counts"].pop("OnStageCorridor")
    config_data["room_type_counts"].pop("OffStageCorridor")
    config_data["grammar_rules"] = []
    config_data["typed_accessibility_pairs"] = []

    with pytest.raises(ConfigError, match="circulation"):
        validate_config(config_data)


# --- No legacy types anywhere in the default config ------------------------------


def test_default_generation_contains_no_legacy_types():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)

    generated_types = {attrs.get("type") for _, attrs in result.graph.nodes(data=True)}
    assert not {"Corridor", "ClinicalSupport", "StaffSupport"} & generated_types
    assert result.is_valid


def test_legacy_floorplan_labels_still_accepted_by_suggest():
    """Imported floorplans with legacy labels validate; they just cannot match."""
    client = TestClient(create_app())

    response = client.post(
        "/suggest-next-room",
        json={
            "floorplan": {
                "schemaVersion": 1,
                "rooms": [
                    {"id": "r1", "type": "Corridor", "x": 0, "y": 0, "width": 300, "height": 80},
                    {"id": "r2", "type": "ClinicalSupport", "x": 0, "y": 100, "width": 100, "height": 100},
                ],
                "edges": [
                    {"id": "e1", "sourceRoomId": "r1", "targetRoomId": "r2", "edgeType": "door"}
                ],
            },
            "anchorRoomId": "r1",
            "sampleCount": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["suggestions"] == []


# --- Group-derived support metrics ------------------------------------------------


def test_ranking_support_metric_is_group_aware():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)

    metrics = compute_candidate_metrics(
        result.graph,
        support_types=set(config.support_node_types),
        corridor_types=set(config.corridor_node_types),
    )

    # Two wards' clinical/staff support rooms plus utility/storage.
    assert metrics.support_room_count >= 10
    assert metrics.support_room_ratio > 0.0


def test_review_summary_keeps_support_categories_separate():
    config = load_config(DEFAULT_CONFIG_PATH)
    raw_config = _raw_config()
    result = generate_candidate(seed=42, config=config)
    support_groups = {
        name: raw_config["semantic_node_groups"][name]
        for name in ("clinical_support", "staff_support")
    }

    summary = build_candidate_review_summary(
        "candidate_1",
        result.graph,
        typed_accessibility_pairs=[("PatientRoom", "NurseStation")],
        support_groups=support_groups,
    )

    group_counts = summary["support_group_counts"]
    assert set(group_counts) == {"clinical_support", "staff_support"}
    assert group_counts["clinical_support"] >= 8
    assert group_counts["staff_support"] >= 2
    assert summary["support_type_counts"]["NurseStation"] == 2


def test_support_summary_token_fallback_without_groups():
    graph = nx.Graph()
    graph.add_node("s1", type="ClinicalSupport", is_abstract=False)
    graph.add_node("s2", type="NurseStation", is_abstract=False)

    summary = support_type_summary(graph)

    assert summary["support_type_counts"] == {"ClinicalSupport": 1}
    assert summary["support_group_counts"] == {}


def test_typed_accessibility_defaults_target_nurse_station_and_medication():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)

    summary = typed_accessibility_summary(result.graph)

    targets = {pair["target_type"]: pair for pair in summary["pairs"]}
    assert set(targets) == {"NurseStation", "MedicationRoom"}
    for pair in targets.values():
        assert pair["source_count"] >= 20
        assert pair["reachable_count"] == pair["source_count"]
        assert pair["distance_mean"] > 0


def test_diversity_features_follow_config_derived_pairs():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config)
    summary = build_candidate_review_summary(
        "candidate_1",
        result.graph,
        typed_accessibility_pairs=[
            ("PatientRoom", "NurseStation"),
            ("PatientRoom", "MedicationRoom"),
        ],
        support_groups={"clinical_support": ["NurseStation", "MedicationRoom"]},
    )

    features = extract_diversity_feature_vector(summary)

    assert "typed_access.PatientRoom_to_NurseStation.distance_mean" in features
    assert "typed_access.PatientRoom_to_MedicationRoom.distance_mean" in features
    assert "support_group_ratio.clinical_support" in features
    assert not any("ClinicalSupport" in key for key in features)


# --- Catalog tier flags -----------------------------------------------------------


def test_catalog_reports_generated_and_optional_tiers():
    client = TestClient(create_app())

    payload = client.get(CATALOG_URL).json()

    items = {item["id"]: item for item in payload["roomTypes"]}
    assert len(items) == 32
    generated_ids = {item_id for item_id, item in items.items() if item["generated"]}
    assert generated_ids == ESSENTIAL_GENERATED_TYPES
    assert len(generated_ids) == 16
    for item in items.values():
        assert item["tier"] == ("generated" if item["generated"] else "optional")
    assert items["Kitchen"]["tier"] == "optional"
    assert items["PatientRoom"]["tier"] == "generated"


def test_catalog_tier_flags_follow_active_variant(tmp_path, monkeypatch):
    """A variant whose grammar generates an optional type flips its tier."""
    variant_config = _raw_config()
    service_rule = next(
        rule for rule in variant_config["grammar_rules"] if rule["name"] == "expand_service_zone"
    )
    service_rule["action"]["create_nodes"].append(
        {"alias": "kitchen", "type": "Kitchen", "count": 1, "attributes": {"is_abstract": False}}
    )
    service_rule["action"]["create_edges"].append(
        {"source": "kitchen", "target": "offstage", "edge_type": "door", "mode": "each_to_one"}
    )
    variant_path = tmp_path / "kitchen_variant.yaml"
    variant_path.write_text(yaml.safe_dump(variant_config, sort_keys=False), encoding="utf-8")
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir()
    (variant_root / "active_variant.json").write_text(
        json.dumps(
            {"variantId": "variant-kitchen", "validatedConfigPath": str(variant_path)}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv("GRAPHLAYOUTSYNTH_GRAMMAR_MODE", "active_variant")
    client = TestClient(create_app())

    payload = client.get(CATALOG_URL).json()

    items = {item["id"]: item for item in payload["roomTypes"]}
    assert payload["source"] == "active_variant"
    assert items["Kitchen"]["generated"] is True
    assert items["Kitchen"]["tier"] == "generated"
    assert items["TeamRoom"]["tier"] == "optional"
