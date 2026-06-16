# AGENTS.md

Concise working notes for future coding agents on GraphLayoutSynth.

## Purpose

GraphLayoutSynth is an early-stage Python research prototype for stochastic graph-grammar generation and deterministic evaluation of building layout graphs. Graphs are NetworkX graphs with attributed nodes and edges. Generated layouts are not geometric plans, building-code checks, life-safety checks, or compliance-certified designs.

Deterministic validation and ranking are the source of truth. Optional Claude evaluation is report interpretation only.

## Current Architecture

- Python package: `graph_layout_synth`
- Graph backend: NetworkX
- Config: YAML, default `configs/generic_building.yaml`
- CLI commands:
  - `python -m graph_layout_synth generate`
  - `python -m graph_layout_synth evaluate-llm`
- Generation uses a seed graph and stochastic YAML `grammar_rules` when present.
- Grammar rules support simple exact node-attribute matching, created-node aliases, fixed counts, min/max counts, choice sampling, matched-node updates, optional matched-node removal, and edge modes `one_to_one`, `each_to_one`, `one_to_each`.
- Outputs include candidate graph JSON, candidate reports, `ranking_report.json`, `ranking_report.csv`, and optional PNG visualizations.
- Optional Claude evaluation reads deterministic reports and writes markdown.

## Key Modules

- `config.py`: loads and validates YAML config; defines config dataclasses.
- `rule_schema.py`: validates and applies executable YAML grammar rules.
- `grammar.py`: creates the seed graph and orchestrates graph expansion.
- `generator.py`: generates one or more candidates and returns `GenerationResult`.
- `validators.py`: checks connectivity, corridor access, edge types, and remaining abstract nodes.
- `scoring.py`: legacy/simple generation score used as metadata.
- `ranking.py`: deterministic metrics, `final_score`, `score_breakdown`, and tie-break ranking.
- `export.py`: node-link graph JSON, candidate reports, ranking JSON, and ranking CSV.
- `visualize.py`: static Matplotlib PNG graph visualization.
- `llm_evaluator.py`: optional Claude interpretation; never replaces deterministic ranking.
- `cli.py`: command-line interface.

## Branch Discipline

- Start from `main` unless the user explicitly says otherwise.
- Do not merge, use, or revive disposed spike/demo UI branches as implementation context.
- Keep changes small and aligned with the requested branch goal.
- Preserve existing CLI behavior and tests unless the user explicitly asks for a behavior change.

## Environment And Dependencies

Preferred local environment:

```bash
mamba activate musa-550-fall-2024
python -m pip install -e ".[dev]"
```

Optional Claude support:

```bash
python -m pip install -e ".[llm]"
```

Core dependencies are NetworkX, PyYAML, and Matplotlib. Dev dependency is pytest. Do not add heavy dependencies unless explicitly requested.

## Commands

Always check the worktree first:

```bash
git branch --show-current
git status --short --ignored
```

Run tests:

```bash
python -m pytest
```

Smoke test generation:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 2 \
  --seed 42 \
  --visualize \
  --output-dir outputs
```

Optional LLM evaluation after generating reports:

```bash
python -m graph_layout_synth evaluate-llm \
  --ranking-report outputs/ranking_report.json \
  --candidate-reports outputs/top_1_candidate_1_report.json \
  --output outputs/llm_evaluation.md \
  --model claude-3-5-haiku-latest \
  --env-path .env.local
```

The exact top-k candidate filenames depend on ranking results.

## Secrets And Outputs

- `.env.local` stores `ANTHROPIC_API_KEY`.
- Do not commit `.env`, `.env.local`, `.env.*.local`, real API keys, or generated outputs.
- `.env.example` is safe to commit and should contain only `ANTHROPIC_API_KEY=`.
- Generated files under `outputs/` are ignored except `outputs/.gitkeep`.

Typical generated files:

- `best_candidate.json`
- `best_candidate_report.json`
- `ranking_report.json`
- `ranking_report.csv`
- `top_<rank>_candidate_<n>.json`
- `top_<rank>_candidate_<n>_report.json`
- optional `best_candidate.png` and `top_<rank>_candidate_<n>.png`
- optional `llm_evaluation.md`

## Guardrails

- Do not commit `.env.local`.
- Do not use disposed spike/demo branches as a base or source of truth.
- Do not replace deterministic ranking with LLM ranking.
- Do not make live LLM API calls in tests.
- Do not add heavy dependencies unless requested.
- Do not implement geometry, OR-Tools, a web UI, deep learning, or product features unless explicitly requested.
- Do not change generation, ranking, visualization, LLM evaluation, config behavior, or tests on documentation-only branches unless an obvious docs-related fix requires it.
- Do not claim generated graphs are code-compliant or life-safety certified layouts.

## Coding Style

Keep changes minimal and readable. Prefer explicit functions, dataclasses where helpful, deterministic seeds in tests, and JSON/CSV outputs that are easy for both humans and LLMs to inspect. Tests touching LLM evaluation must mock or isolate the Anthropic API boundary.
