import yaml
import pytest

import graph_layout_synth.cli as cli
import graph_layout_synth.grammar_variant_assistant as assistant
from graph_layout_synth.cli import main
from graph_layout_synth.grammar_variant_assistant import (
    GrammarVariantError,
    build_grammar_variant_prompt,
    extract_yaml_from_llm_response,
    load_variant_requirements,
    propose_grammar_variant,
    propose_grammar_variant_with_claude,
    room_mix_kwargs_from_requirements,
    validate_variant_requirements,
    validate_room_mix_targets,
    variant_requirements_to_design_intent,
)


def _base_config():
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
                "name": "expand_zone",
                "match": {"type": "Zone", "is_abstract": True},
                "action": {
                    "create_nodes": [
                        {
                            "alias": "room",
                            "type": "PatientRoom",
                            "count": 1,
                            "attributes": {"is_abstract": False},
                        }
                    ],
                    "create_edges": [],
                },
            }
        ],
    }


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _variant_requirements():
    return {
        "version": 1,
        "design_intent": "Use explicit patient/support room-mix targets.",
        "room_mix_targets": {
            "enabled": True,
            "patient_alias": "patient",
            "clinical_alias": "clinical",
            "staff_alias": "staff",
            "patient_total_min": 20,
            "patient_total_max": 30,
            "clinical_ratio": 0.25,
            "staff_ratio": 0.10,
            "ratio_tolerance": 0.08,
            "suggested_per_zone_counts": {
                "patient": {"min": 7, "max": 10},
                "clinical": {"min": 2, "max": 3},
                "staff": 1,
            },
            "expected_room_type_counts": {
                "PatientRoom": 24,
                "ClinicalSupport": 6,
                "StaffSupport": 3,
            },
        },
    }


def _room_mix_config():
    config = _base_config()
    config["room_type_counts"] = {"PatientRoom": 24, "ClinicalSupport": 6, "StaffSupport": 3}
    config["grammar_rules"] = [
        {
            "name": "expand_floor_to_zones",
            "match": {"type": "BuildingFloor", "is_abstract": True},
            "action": {
                "remove_matched_node": True,
                "create_nodes": [
                    {"alias": "public_zone", "type": "Zone", "count": 1, "attributes": {"is_abstract": True}},
                    {"alias": "private_zone", "type": "Zone", "count": 1, "attributes": {"is_abstract": True}},
                    {"alias": "service_zone", "type": "Zone", "count": 1, "attributes": {"is_abstract": True}},
                ],
                "create_edges": [
                    {"source": "public_zone", "target": "private_zone", "edge_type": "door"},
                    {"source": "private_zone", "target": "service_zone", "edge_type": "door"},
                ],
            },
        },
        {
            "name": "expand_zone_to_room_cluster",
            "match": {"type": "Zone", "is_abstract": True},
            "action": {
                "remove_matched_node": True,
                "create_nodes": [
                    {"alias": "corridor", "type": "Corridor", "count": 1, "attributes": {"is_abstract": False}},
                    {
                        "alias": "patient",
                        "type": "PatientRoom",
                        "count": {"min": 7, "max": 10},
                        "attributes": {"is_abstract": False},
                    },
                    {
                        "alias": "clinical",
                        "type": "ClinicalSupport",
                        "count": {"min": 2, "max": 3},
                        "attributes": {"is_abstract": False},
                    },
                    {"alias": "staff", "type": "StaffSupport", "count": 1, "attributes": {"is_abstract": False}},
                ],
                "create_edges": [
                    {"source": "corridor", "target": "__neighbors__", "edge_type": "door", "mode": "one_to_each"},
                    {"source": "patient", "target": "corridor", "edge_type": "door", "mode": "each_to_one"},
                    {"source": "clinical", "target": "corridor", "edge_type": "door", "mode": "each_to_one"},
                    {"source": "staff", "target": "corridor", "edge_type": "door", "mode": "each_to_one"},
                ],
            },
        },
    ]
    return config


def test_structured_variant_requirements_render_prompt_and_room_mix_kwargs(tmp_path):
    requirements_path = _write_yaml(tmp_path / "requirements.yaml", _variant_requirements())
    requirements = load_variant_requirements(requirements_path)
    design_intent = variant_requirements_to_design_intent(requirements)
    kwargs = room_mix_kwargs_from_requirements(requirements)

    assert "20-30 total PatientRoom" in design_intent
    assert "patient" in design_intent
    assert kwargs["patient_total_min"] == 20
    assert kwargs["clinical_ratio"] == 0.25
    assert kwargs["patient_alias"] == "patient"


def test_structured_variant_requirements_reject_unknown_fields():
    requirements = _variant_requirements()
    requirements["room_mix_targets"]["patient_totel_min"] = 20

    with pytest.raises(GrammarVariantError, match="unsupported field"):
        validate_variant_requirements(requirements)


def test_prompt_builder_includes_skills_base_config_intent_and_reports():
    prompt = build_grammar_variant_prompt(
        _base_config(),
        "Skill text: do not invent unsupported fields.",
        design_intent="Improve patient access.",
        diversity_report={"feature_bin_coverage": {"occupied_bin_count": 3}},
        review_summary={"pool_summary": {"num_candidates": 2}},
        archive={"outputs": [{"output_id": "final_001"}]},
    )

    assert "Skill text" in prompt
    assert "Improve patient access" in prompt
    assert "GraphLayoutSynth" in prompt
    assert "allowed_node_types" in prompt
    assert "feature_bin_coverage" in prompt
    assert "pool_summary" in prompt
    assert "final_001" in prompt
    assert "Return a complete YAML config only in a fenced yaml block" in prompt


def test_extract_yaml_from_fenced_yaml_block():
    response = "Rationale first.\n```yaml\nproject:\n  name: x\n```\nMore rationale."

    yaml_text = extract_yaml_from_llm_response(response)

    assert yaml.safe_load(yaml_text)["project"]["name"] == "x"


def test_extract_yaml_from_raw_yaml_response():
    yaml_text = extract_yaml_from_llm_response("project:\n  name: x\n")

    assert yaml.safe_load(yaml_text)["project"]["name"] == "x"


def test_invalid_or_missing_yaml_response_fails_clearly():
    with pytest.raises(GrammarVariantError, match="non-empty mapping"):
        extract_yaml_from_llm_response("This is rationale, not YAML.")

    with pytest.raises(GrammarVariantError, match="empty"):
        extract_yaml_from_llm_response("")


def test_dry_run_prompt_writing_does_not_call_claude(tmp_path, monkeypatch):
    config_path = _write_yaml(tmp_path / "config.yaml", _base_config())
    prompt_path = tmp_path / "prompt.md"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Claude should not be called in no-call mode")

    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", fail_if_called)

    main(
        [
            "propose-grammar-variant",
            "--base-config",
            str(config_path),
            "--design-intent",
            "Vary the grammar.",
            "--write-prompt",
            str(prompt_path),
            "--no-call",
        ]
    )

    assert prompt_path.exists()
    assert "Vary the grammar" in prompt_path.read_text(encoding="utf-8")


def test_cli_no_call_with_structured_requirements_writes_prompt(tmp_path, monkeypatch):
    config_path = _write_yaml(tmp_path / "config.yaml", _base_config())
    requirements_path = _write_yaml(tmp_path / "requirements.yaml", _variant_requirements())
    prompt_path = tmp_path / "prompt.md"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Claude should not be called in no-call mode")

    monkeypatch.setattr(cli, "propose_grammar_variant_with_claude", fail_if_called)

    main(
        [
            "propose-grammar-variant",
            "--base-config",
            str(config_path),
            "--variant-requirements",
            str(requirements_path),
            "--write-prompt",
            str(prompt_path),
            "--no-call",
        ]
    )

    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert "Structured room-mix requirements" in prompt_text
    assert "20-30 total PatientRoom" in prompt_text


def test_generated_yaml_validation_is_called(tmp_path, monkeypatch):
    calls = {"count": 0}
    base_config = _base_config()
    response = "```yaml\n" + yaml.safe_dump(base_config, sort_keys=False) + "```"

    def fake_call(prompt, model, max_tokens):
        return response

    def fake_validate(yaml_text):
        calls["count"] += 1
        return yaml.safe_load(yaml_text)

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fake_call)
    monkeypatch.setattr(assistant, "validate_variant_yaml_text", fake_validate)

    output_path = tmp_path / "variant.yaml"
    result = propose_grammar_variant(
        base_config=base_config,
        grammar_skills_text="skills",
        output_config_path=output_path,
        raw_output_path=tmp_path / "raw.md",
        model="test-model",
    )

    assert output_path.exists()
    assert calls["count"] == 1
    assert result["output_config_path"] == str(output_path)


def test_invalid_generated_yaml_is_saved_as_invalid_sidecar(tmp_path, monkeypatch):
    response = "```yaml\nproject:\n  name: Missing required sections\n```"

    def fake_call(prompt, model, max_tokens):
        return response

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fake_call)

    output_path = tmp_path / "variant.yaml"
    with pytest.raises(GrammarVariantError, match="failed validation"):
        propose_grammar_variant(
            base_config=_base_config(),
            grammar_skills_text="skills",
            output_config_path=output_path,
            raw_output_path=tmp_path / "raw.md",
            model="test-model",
        )

    invalid_path = tmp_path / "variant.invalid.yaml"
    assert invalid_path.exists()
    assert not output_path.exists()


def test_room_mix_target_check_passes_for_separate_alias_counts():
    report = validate_room_mix_targets(_room_mix_config())

    assert report["estimated_totals"]["PatientRoom"] == {"min": 21, "max": 30}
    assert report["estimated_totals"]["ClinicalSupport"] == {"min": 6, "max": 9}
    assert report["estimated_totals"]["StaffSupport"] == {"min": 3, "max": 3}


def test_room_mix_target_check_rejects_grouped_low_count_config():
    with pytest.raises(GrammarVariantError, match="missing create_nodes alias 'patient'"):
        validate_room_mix_targets(_base_config())


def test_room_mix_semantic_failure_is_saved_as_invalid_sidecar(tmp_path, monkeypatch):
    response = "```yaml\n" + yaml.safe_dump(_base_config(), sort_keys=False) + "```"

    def fake_call(prompt, model, max_tokens):
        return response

    monkeypatch.setattr(assistant, "propose_grammar_variant_with_claude", fake_call)

    output_path = tmp_path / "variant.yaml"
    with pytest.raises(GrammarVariantError, match="Room-mix target check failed"):
        propose_grammar_variant(
            base_config=_base_config(),
            grammar_skills_text="skills",
            output_config_path=output_path,
            require_room_mix_targets=True,
            model="test-model",
        )

    assert (tmp_path / "variant.invalid.yaml").exists()
    assert not output_path.exists()


def test_cli_no_call_prompt_writing_works(tmp_path):
    config_path = _write_yaml(tmp_path / "config.yaml", _base_config())
    prompt_path = tmp_path / "prompt.md"

    main(
        [
            "propose-grammar-variant",
            "--base-config",
            str(config_path),
            "--write-prompt",
            str(prompt_path),
            "--no-call",
        ]
    )

    assert prompt_path.exists()
    assert "Base YAML Config" in prompt_path.read_text(encoding="utf-8")


def test_missing_api_key_is_handled_gracefully(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(GrammarVariantError, match="ANTHROPIC_API_KEY is missing"):
        propose_grammar_variant_with_claude("prompt", model="test-model")
