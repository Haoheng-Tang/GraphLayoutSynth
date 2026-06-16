import json

import graph_layout_synth.cli as cli
from graph_layout_synth.cli import main


def test_cli_ranking_report_is_created(tmp_path):
    main(
        [
            "generate",
            "--config",
            "configs/generic_building.yaml",
            "--num-candidates",
            "2",
            "--top-k",
            "1",
            "--seed",
            "42",
            "--output-dir",
            str(tmp_path),
        ]
    )

    ranking_report = tmp_path / "ranking_report.json"
    ranking_csv = tmp_path / "ranking_report.csv"
    candidate_trace = tmp_path / "candidate_1_trace.json"
    best_trace = tmp_path / "best_candidate_trace.json"

    assert ranking_report.exists()
    assert ranking_csv.exists()
    assert candidate_trace.exists()
    assert best_trace.exists()
    assert list(tmp_path.glob("top_1_candidate_*_trace.json"))
    data = json.loads(ranking_report.read_text(encoding="utf-8"))
    assert data[0]["rank"] == 1
    assert "final_score" in data[0]
    assert "score_breakdown" in data[0]
    assert "metrics" in data[0]
    assert "tie_break_keys" in data[0]
    assert "trace_path" in data[0]
    assert data[0]["trace_length"] > 0
    assert "expand_floor_to_zones" in data[0]["applied_rule_names"]
    assert "corridor_access_ratio" in data[0]["metrics"]


def test_cli_evaluate_llm_writes_output(tmp_path, monkeypatch):
    ranking_report = tmp_path / "ranking_report.json"
    output_path = tmp_path / "llm_evaluation.md"
    ranking_report.write_text(
        json.dumps([{"rank": 1, "candidate_id": "candidate_1", "ranking_score": 150.0}]),
        encoding="utf-8",
    )

    def fake_evaluate_candidates_with_llm(**kwargs):
        output = kwargs["output_path"]
        with open(output, "w", encoding="utf-8") as file:
            file.write("# LLM Evaluation\n")
        return {"output_path": output, "model": kwargs["model"], "markdown": "# LLM Evaluation\n"}

    monkeypatch.setattr(cli, "evaluate_candidates_with_llm", fake_evaluate_candidates_with_llm)

    main(
        [
            "evaluate-llm",
            "--ranking-report",
            str(ranking_report),
            "--output",
            str(output_path),
            "--model",
            "test-model",
        ]
    )

    assert output_path.exists()
