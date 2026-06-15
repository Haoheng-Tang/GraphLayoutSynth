"""Disposable Streamlit demo UI for GraphLayoutSynth."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from graph_layout_synth.llm_evaluator import (
    LlmEvaluationError,
    evaluate_candidates_with_llm,
    load_llm_environment,
)


def run_generation_command(
    config_path: str,
    num_candidates: int,
    top_k: int,
    seed: int,
    output_dir: str,
    visualize: bool,
) -> subprocess.CompletedProcess[str]:
    """Run the existing generation CLI for the demo."""
    command = [
        sys.executable,
        "-m",
        "graph_layout_synth",
        "generate",
        "--config",
        config_path,
        "--num-candidates",
        str(num_candidates),
        "--top-k",
        str(top_k),
        "--seed",
        str(seed),
        "--output-dir",
        output_dir,
    ]
    if visualize:
        command.append("--visualize")

    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def read_ranking_table(output_dir: Path) -> pd.DataFrame | None:
    """Read ranking CSV or JSON if available."""
    csv_path = output_dir / "ranking_report.csv"
    json_path = output_dir / "ranking_report.json"
    if csv_path.exists():
        return pd.read_csv(csv_path)
    if json_path.exists():
        return pd.DataFrame(json.loads(json_path.read_text(encoding="utf-8")))
    return None


def top_candidate_report_paths(output_dir: Path, top_k: int) -> list[str]:
    """Find top-k candidate report files produced by the CLI."""
    report_paths = sorted(output_dir.glob("top_*_candidate_*_report.json"))
    return [str(path) for path in report_paths[:top_k]]


def run_llm_evaluation(
    output_dir: Path,
    model: str,
    max_tokens: int,
    top_k: int,
) -> str:
    """Run the existing Claude evaluator and return markdown."""
    load_llm_environment(".env.local")
    ranking_report = output_dir / "ranking_report.json"
    if not ranking_report.exists():
        raise LlmEvaluationError("ranking_report.json was not found. Run generation first.")

    result = evaluate_candidates_with_llm(
        ranking_report_path=str(ranking_report),
        candidate_report_paths=top_candidate_report_paths(output_dir, top_k),
        model=model,
        output_path=str(output_dir / "llm_evaluation.md"),
        env_path=".env.local",
        max_tokens=max_tokens,
    )
    return result["markdown"]


st.set_page_config(page_title="GraphLayoutSynth Demo", layout="wide")
st.title("GraphLayoutSynth Demo")
st.caption("Local spike UI for generation, ranking, PNG inspection, and optional Claude interpretation.")

with st.sidebar:
    st.header("Controls")
    config_path = st.text_input("Config path", value="configs/generic_building.yaml")
    num_candidates = st.number_input("Number of candidates", min_value=1, value=10, step=1)
    top_k = st.number_input("Top-k", min_value=1, value=3, step=1)
    seed = st.number_input("Random seed", value=42, step=1)
    output_dir = st.text_input("Output directory", value="outputs/demo")
    visualize = st.checkbox("Visualize candidates", value=True)
    run_claude = st.checkbox("Run Claude LLM evaluation", value=False)
    model = st.text_input("Claude model", value="claude-3-5-haiku-latest")
    max_tokens = st.number_input("Max tokens", min_value=100, value=1200, step=100)
    run_button = st.button("Run generation", type="primary")

output_path = Path(output_dir)

if run_button:
    with st.spinner("Generating and ranking candidates..."):
        result = run_generation_command(
            config_path=config_path,
            num_candidates=int(num_candidates),
            top_k=int(top_k),
            seed=int(seed),
            output_dir=str(output_path),
            visualize=visualize,
        )

    if result.returncode != 0:
        st.error("Generation failed.")
        if result.stderr:
            st.code(result.stderr)
    else:
        st.success(f"Generation completed. Outputs saved under `{output_path}`.")
        if result.stdout:
            st.code(result.stdout)

    if result.returncode == 0 and run_claude:
        with st.spinner("Running Claude evaluation..."):
            try:
                markdown = run_llm_evaluation(output_path, model, int(max_tokens), int(top_k))
                st.session_state["llm_evaluation"] = markdown
                st.success(f"Claude evaluation saved to `{output_path / 'llm_evaluation.md'}`.")
            except LlmEvaluationError as exc:
                st.warning(str(exc))
            except Exception as exc:  # Demo UI: show the problem instead of crashing Streamlit.
                st.error(f"Claude evaluation failed: {exc}")

st.header("Ranking Report")
ranking_table = read_ranking_table(output_path)
if ranking_table is None:
    st.info("No ranking report found yet. Click Run generation to create one.")
else:
    st.dataframe(ranking_table, use_container_width=True)
    st.write(f"Ranking JSON: `{output_path / 'ranking_report.json'}`")
    st.write(f"Ranking CSV: `{output_path / 'ranking_report.csv'}`")

st.header("Top Candidates")
if ranking_table is not None:
    top_rows = ranking_table.head(int(top_k)).to_dict(orient="records")
    for row in top_rows:
        rank = int(row["rank"])
        candidate_id = row["candidate_id"]
        score = row.get("ranking_score", row.get("score", 0.0))
        prefix = f"top_{rank}_{candidate_id}"

        with st.expander(f"Rank {rank}: {candidate_id} | score {float(score):.1f}", expanded=rank == 1):
            cols = st.columns(4)
            cols[0].metric("Valid", row.get("validation_passed", ""))
            cols[1].metric("Rooms", row.get("room_count", ""))
            cols[2].metric("Corridor access", row.get("corridor_access_ratio", ""))
            cols[3].metric("Invalid edges", row.get("invalid_edge_type_count", ""))

            png_path = output_path / f"{prefix}.png"
            if png_path.exists():
                st.image(str(png_path), caption=str(png_path), use_container_width=True)
            elif visualize:
                st.warning(f"PNG not found: `{png_path}`")

            st.write(f"Graph JSON: `{output_path / f'{prefix}.json'}`")
            st.write(f"Candidate report: `{output_path / f'{prefix}_report.json'}`")

st.header("Claude Evaluation")
llm_path = output_path / "llm_evaluation.md"
if "llm_evaluation" in st.session_state:
    st.markdown(st.session_state["llm_evaluation"])
elif llm_path.exists():
    st.markdown(llm_path.read_text(encoding="utf-8"))
else:
    st.info("Enable Claude evaluation and run generation to create an LLM interpretation.")
