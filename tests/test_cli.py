import json

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

    assert ranking_report.exists()
    assert ranking_csv.exists()
    data = json.loads(ranking_report.read_text(encoding="utf-8"))
    assert data[0]["rank"] == 1
    assert "corridor_access_ratio" in data[0]
