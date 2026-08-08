"""Diagnostics on `/suggest-next-room`: why a result is empty, and from which config.

`suggestions: []` has three materially different causes. These tests pin the
signature of each so neither repo has to guess:

- no semantic anchor match at all        -> matchedSampleCount == 0
- matched but saturated (no extras left) -> matchedSampleCount > 0, samplesWithCandidates == 0
- short generation                       -> sampleCount < requested
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
)
from graph_layout_synth.grammar_variant_control_plane import LLM_VARIANT_DIR_ENV
from server.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = PROJECT_ROOT / "configs/generic_building.yaml"
SUGGEST_URL = "/suggest-next-room"
ARTIFACT_DIR_ENV = "GRAPHLAYOUTSYNTH_SUGGESTION_ARTIFACT_DIR"


def _request_body(
    anchor_type: str = "OnStageCorridor",
    sample_count: int = 2,
    **extra: object,
) -> dict:
    body: dict = {
        "floorplan": {
            "schemaVersion": 1,
            "rooms": [
                {
                    "id": "room-1",
                    "type": anchor_type,
                    "x": 0,
                    "y": 0,
                    "width": 300,
                    "height": 80,
                },
                {
                    "id": "room-2",
                    "type": "PatientRoom",
                    "x": 0,
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
    body.update(extra)
    return body


def _graph_with_extra_neighbor() -> nx.Graph:
    """An anchor match that leaves one extra relation after subtraction."""
    graph = nx.Graph()
    graph.add_node("g-anchor", type="OnStageCorridor")
    graph.add_node("g-known", type="PatientRoom")
    graph.add_node("g-extra", type="NurseStation")
    graph.add_edge("g-anchor", "g-known", edge_type="door")
    graph.add_edge("g-anchor", "g-extra", edge_type="door")
    return graph


def _saturated_graph() -> nx.Graph:
    """An anchor match whose neighbours exactly cover the known signature."""
    graph = nx.Graph()
    graph.add_node("g-anchor", type="OnStageCorridor")
    graph.add_node("g-known", type="PatientRoom")
    graph.add_edge("g-anchor", "g-known", edge_type="door")
    return graph


def _unmatchable_graph() -> nx.Graph:
    """No node carries the anchor's room type, so nothing can match."""
    graph = nx.Graph()
    graph.add_node("g-a", type="MedicationRoom")
    graph.add_node("g-b", type="CleanUtility")
    graph.add_edge("g-a", "g-b", edge_type="door")
    return graph


def _stub_generation(monkeypatch: pytest.MonkeyPatch, graphs: list[nx.Graph]) -> None:
    """Return fixed graphs while leaving real config-source resolution intact."""
    monkeypatch.setattr(
        sampling_module,
        "load_config",
        lambda path=None: SimpleNamespace(name="stub-config"),
    )
    monkeypatch.setattr(
        sampling_module,
        "generate_candidates",
        lambda sample_count, seed, config: [
            SimpleNamespace(graph=graph) for graph in graphs
        ],
    )


def _activate_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant_id: str) -> Path:
    variant_path = tmp_path / f"{variant_id}.yaml"
    variant_path.write_text(BASE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    variant_root = tmp_path / "llm-variants"
    variant_root.mkdir(exist_ok=True)
    (variant_root / "active_variant.json").write_text(
        json.dumps({"variantId": variant_id, "validatedConfigPath": str(variant_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv(LLM_VARIANT_DIR_ENV, str(variant_root))
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_ACTIVE_VARIANT)
    return variant_path


# --- Counts track the three empty-result causes ----------------------------------


def test_counts_are_consistent_with_returned_suggestions(monkeypatch):
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(
        monkeypatch,
        [_graph_with_extra_neighbor(), _graph_with_extra_neighbor()],
    )

    payload = TestClient(create_app()).post(SUGGEST_URL, json=_request_body()).json()

    assert payload["sampleCount"] == 2
    assert payload["matchedSampleCount"] == 2
    assert payload["samplesWithCandidates"] == 2
    assert [item["roomType"] for item in payload["suggestions"]] == ["NurseStation"]
    # No suggestion can be supported by more samples than contributed candidates.
    assert all(
        item["sampleCount"] <= payload["samplesWithCandidates"]
        for item in payload["suggestions"]
    )
    assert payload["samplesWithCandidates"] <= payload["matchedSampleCount"]
    assert payload["matchedSampleCount"] <= payload["sampleCount"]


def test_no_semantic_match_reports_zero_matched_samples(monkeypatch):
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(monkeypatch, [_unmatchable_graph(), _unmatchable_graph()])

    payload = TestClient(create_app()).post(SUGGEST_URL, json=_request_body()).json()

    assert payload["suggestions"] == []
    assert payload["sampleCount"] == 2
    assert payload["matchedSampleCount"] == 0
    assert payload["samplesWithCandidates"] == 0


def test_matched_but_saturated_anchor_is_distinguishable(monkeypatch):
    """The whole point: matched-and-saturated must not look like never-matched."""
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(monkeypatch, [_saturated_graph(), _saturated_graph()])

    payload = TestClient(create_app()).post(SUGGEST_URL, json=_request_body()).json()

    assert payload["suggestions"] == []
    assert payload["matchedSampleCount"] == 2
    assert payload["samplesWithCandidates"] == 0


def test_partially_saturated_batch_counts_only_contributing_samples(monkeypatch):
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(
        monkeypatch,
        [_graph_with_extra_neighbor(), _saturated_graph(), _unmatchable_graph()],
    )

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(sample_count=3)
    ).json()

    assert payload["sampleCount"] == 3
    assert payload["matchedSampleCount"] == 2
    assert payload["samplesWithCandidates"] == 1


def test_anchor_type_never_generated_by_default_grammar(monkeypatch):
    """End-to-end with the real grammar: `Kitchen` is optional, never generated."""
    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", _fail_if_called)
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(anchor_type="Kitchen", sample_count=2)
    ).json()

    assert payload["suggestions"] == []
    assert payload["sampleCount"] == 2
    assert payload["matchedSampleCount"] == 0
    assert payload["samplesWithCandidates"] == 0


def _fail_if_called(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("/suggest-next-room must never call Claude.")


# --- configSource -----------------------------------------------------------------


def test_config_source_reports_static_default_config(monkeypatch):
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(monkeypatch, [_graph_with_extra_neighbor()])

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(sample_count=1)
    ).json()

    config_source = payload["configSource"]
    assert config_source["mode"] == "static"
    assert config_source["configPath"].endswith("generic_building.yaml")
    # Omitted rather than null, matching the response's existing convention.
    assert "variantId" not in config_source


def test_config_source_reports_active_variant_id(tmp_path, monkeypatch):
    variant_path = _activate_variant(tmp_path, monkeypatch, "variant-diagnostics")
    _stub_generation(monkeypatch, [_graph_with_extra_neighbor()])

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(sample_count=1)
    ).json()

    config_source = payload["configSource"]
    assert config_source["mode"] == "active_variant"
    assert config_source["variantId"] == "variant-diagnostics"
    assert Path(config_source["configPath"]) == variant_path


def test_config_source_present_on_empty_suggestion_responses(monkeypatch):
    """Diagnostics must survive the case they exist for."""
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(monkeypatch, [_unmatchable_graph()])

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(sample_count=1)
    ).json()

    assert payload["suggestions"] == []
    assert payload["configSource"]["mode"] == "static"


# --- Wire and disk agree ----------------------------------------------------------


def test_debug_artifact_and_response_report_identical_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    monkeypatch.setenv(ARTIFACT_DIR_ENV, str(tmp_path / "artifacts"))
    _stub_generation(
        monkeypatch,
        [_graph_with_extra_neighbor(), _saturated_graph(), _unmatchable_graph()],
    )

    payload = TestClient(create_app()).post(
        SUGGEST_URL,
        json=_request_body(sample_count=3, includeDebugArtifacts=True),
    ).json()

    reports = list((tmp_path / "artifacts").glob("*/aggregation_report.json"))
    assert len(reports) == 1
    aggregation_report = json.loads(reports[0].read_text(encoding="utf-8"))

    assert aggregation_report["matchedSampleCount"] == payload["matchedSampleCount"]
    assert (
        aggregation_report["samplesWithCandidates"] == payload["samplesWithCandidates"]
    )
    assert aggregation_report["generatedSampleCount"] == payload["sampleCount"]
    assert aggregation_report["configSource"]["mode"] == payload["configSource"]["mode"]
    assert (
        aggregation_report["configSource"]["configPath"]
        == payload["configSource"]["configPath"]
    )


def test_diagnostics_do_not_require_debug_artifacts(monkeypatch):
    """The response carries the data whether or not artifacts are enabled."""
    monkeypatch.setenv(GRAMMAR_MODE_ENV, GRAMMAR_MODE_STATIC)
    _stub_generation(monkeypatch, [_graph_with_extra_neighbor()])

    payload = TestClient(create_app()).post(
        SUGGEST_URL, json=_request_body(sample_count=1)
    ).json()

    assert payload["matchedSampleCount"] == 1
    assert payload["samplesWithCandidates"] == 1
    assert payload["configSource"]["mode"] == "static"
