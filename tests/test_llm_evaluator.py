import json

import pytest

from graph_layout_synth import llm_evaluator
from graph_layout_synth.llm_evaluator import (
    LlmEvaluationError,
    build_candidate_evaluation_prompt,
    evaluate_candidates_with_claude,
    evaluate_candidates_with_llm,
    load_llm_environment,
)


def test_build_candidate_evaluation_prompt_includes_reports_and_constraints():
    ranking_report = [
        {
            "rank": 1,
            "candidate_id": "candidate_1",
            "ranking_score": 150.0,
            "corridor_access_ratio": 1.0,
        }
    ]
    candidate_reports = [
        {
            "ranking_score": 150.0,
            "metrics": {"validation_passed": 1, "invalid_edge_type_count": 0},
        }
    ]

    prompt = build_candidate_evaluation_prompt(ranking_report, candidate_reports)

    assert "candidate_1" in prompt
    assert "corridor_access_ratio" in prompt
    assert "Do not invent metrics" in prompt
    assert "deterministic ranking remains primary" in prompt


def test_missing_anthropic_api_key_is_handled_gracefully(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LlmEvaluationError, match="ANTHROPIC_API_KEY is missing"):
        evaluate_candidates_with_claude({}, [], model="test-model")


def test_env_local_loading_works(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_path = tmp_path / ".env.local"
    env_path.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

    load_llm_environment(str(env_path))

    assert llm_evaluator.os.environ["ANTHROPIC_API_KEY"] == "test_key"


def test_markdown_output_writing(tmp_path, monkeypatch):
    ranking_report_path = tmp_path / "ranking_report.json"
    candidate_report_path = tmp_path / "candidate_report.json"
    output_path = tmp_path / "llm_evaluation.md"
    ranking_report_path.write_text(
        json.dumps([{"rank": 1, "candidate_id": "candidate_1", "ranking_score": 150.0}]),
        encoding="utf-8",
    )
    candidate_report_path.write_text(
        json.dumps({"metrics": {"validation_passed": 1}, "ranking_score": 150.0}),
        encoding="utf-8",
    )

    def fake_evaluate(ranking_report, candidate_reports, model, max_tokens):
        assert ranking_report[0]["candidate_id"] == "candidate_1"
        assert candidate_reports[0]["metrics"]["validation_passed"] == 1
        assert model == "test-model"
        assert max_tokens == 300
        return "# LLM Evaluation\n\nDeterministic ranking remains primary."

    monkeypatch.setattr(llm_evaluator, "evaluate_candidates_with_claude", fake_evaluate)

    result = evaluate_candidates_with_llm(
        ranking_report_path=str(ranking_report_path),
        candidate_report_paths=[str(candidate_report_path)],
        model="test-model",
        output_path=str(output_path),
        max_tokens=300,
    )

    assert output_path.exists()
    assert "Deterministic ranking remains primary" in output_path.read_text(encoding="utf-8")
    assert result["output_path"] == str(output_path)
