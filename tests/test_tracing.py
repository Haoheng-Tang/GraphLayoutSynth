import json

import networkx as nx

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config
from graph_layout_synth.export import export_report_json
from graph_layout_synth.generator import generate_candidate
from graph_layout_synth.tracing import (
    export_trace_json,
    summarize_trace,
    trace_metadata,
    trace_rule_counts,
    trace_to_dicts,
)


def test_generation_trace_records_rule_applications():
    config = load_config(DEFAULT_CONFIG_PATH)
    result = generate_candidate(seed=42, config=config, trace=True)

    assert result.trace
    assert [event.step_index for event in result.trace] == list(range(1, len(result.trace) + 1))
    assert result.trace[0].rule_name == "expand_floor_to_zones"
    assert result.trace[0].matched_node_id == "floor"
    assert result.trace[0].matched_node_attrs["type"] == "BuildingFloor"
    assert result.trace[0].created_node_ids
    assert result.trace[0].created_edges
    assert result.trace[0].removed_node_ids == ["floor"]


def test_generation_trace_records_sampled_parameters_and_is_deterministic():
    config = load_config(DEFAULT_CONFIG_PATH)
    first = generate_candidate(seed=42, config=config, trace=True).trace
    second = generate_candidate(seed=42, config=config, trace=True).trace

    assert trace_to_dicts(first) == trace_to_dicts(second)
    cluster_events = [
        event
        for event in first
        if event.rule_name == "expand_zone_to_room_cluster"
    ]
    assert cluster_events
    assert all("create_nodes" in event.sampled_parameters for event in cluster_events)
    assert all(event.created_edges for event in cluster_events)


def test_trace_json_export_and_summary(tmp_path):
    config = load_config(DEFAULT_CONFIG_PATH)
    trace = generate_candidate(seed=42, config=config, trace=True).trace

    output_path = export_trace_json(trace, tmp_path / "candidate_1_trace.json")
    data = json.loads(output_path.read_text(encoding="utf-8"))
    summary = summarize_trace(trace)

    assert output_path.exists()
    assert data[0]["rule_name"] == "expand_floor_to_zones"
    assert data[0]["step_index"] == 1
    assert "Generation trace:" in summary
    assert "expand_floor_to_zones" in summary


def test_trace_metadata_is_included_in_candidate_report(tmp_path):
    graph = nx.Graph()
    graph.add_node("corridor", type="Corridor", zone="zone_1", is_abstract=False)
    graph.add_node("room", type="PatientRoom", zone="zone_1", is_abstract=False)
    graph.add_edge("corridor", "room", edge_type="door")
    trace = generate_candidate(seed=42, trace=True).trace
    metadata = trace_metadata(trace, tmp_path / "candidate_1_trace.json")

    output_path = export_report_json(
        graph,
        tmp_path / "report.json",
        score=105.0,
        is_valid=True,
        validation_errors=[],
        trace_metadata=metadata,
    )
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert data["trace_path"].endswith("candidate_1_trace.json")
    assert data["trace_length"] == len(trace)
    assert "expand_floor_to_zones" in data["applied_rule_names"]
    assert data["applied_rule_counts"] == trace_rule_counts(trace)
