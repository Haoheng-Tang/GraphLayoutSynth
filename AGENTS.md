# AGENTS.md

Working notes for future coding agents on GraphLayoutSynth.

## Project Summary

GraphLayoutSynth is an early-stage Python research prototype for stochastic graph-grammar generation and evaluation of building layout graphs.

The project currently supports:

- stochastic graph generation with NetworkX
- YAML config loading from `configs/generic_building.yaml`
- rule-based validation
- deterministic metric-based candidate ranking
- JSON/CSV ranking reports
- candidate JSON reports
- PNG graph visualization with Matplotlib
- optional Claude-based LLM interpretation of deterministic reports

Important principle: deterministic validation and ranking are the source of truth. LLM evaluation is optional interpretation only.

## Current Branch Context

Recent work happened on `feat/ranking-score-refinement`.

Goal of that branch:

- refine deterministic ranking so valid candidates do not collapse to identical scores
- keep generator behavior unchanged
- avoid new LLM behavior, OR-Tools, geometry generation, and UI work

Implemented ranking refinement:

- added metrics such as `edge_node_ratio`, `room_corridor_ratio`, `door_wall_ratio`, `corridor_fraction`, `dead_end_count`, room-to-corridor distances, and support-room ratios
- added `final_score`
- added `score_breakdown`
- kept `ranking_score` as a backward-compatible alias of `final_score`
- added deterministic `tie_break_keys`
- updated ranking and candidate reports so LLM evaluation can understand the score structure
- moved ranking weights and heuristic targets into `configs/generic_building.yaml` under `ranking`

Before further work, always check:

```bash
git branch --show-current
git status --short --ignored
```

## Environment

Preferred environment:

```bash
mamba activate musa-550-fall-2024
```

Install editable package:

```bash
python -m pip install -e ".[dev]"
```

Install optional Claude support:

```bash
python -m pip install -e ".[llm]"
```

For demo/UI spike branches only:

```bash
python -m pip install -e ".[llm,demo]"
```

## Important Local Files

Do not commit secrets or generated outputs.

Ignored local files include:

- `.env`
- `.env.local`
- `.env.*.local`
- `*.egg-info/`
- `outputs/*.json`
- `outputs/*.csv`
- `outputs/*.png`
- `outputs/*.md`
- generated caches

`.env.local` should contain:

```text
ANTHROPIC_API_KEY=your_api_key_here
```

`.env.example` is safe to commit and contains an empty placeholder.

## Core Commands

Run tests:

```bash
python -m pytest
```

Generate ranked candidates:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 5 \
  --seed 42 \
  --visualize \
  --output-dir outputs/ranking_refinement_check
```

Run optional Claude evaluation:

```bash
python -m graph_layout_synth evaluate-llm \
  --ranking-report outputs/ranking_refinement_check/ranking_report.json \
  --candidate-reports outputs/ranking_refinement_check/top_1_candidate_3_report.json \
  --output outputs/ranking_refinement_check/llm_evaluation.md \
  --model claude-3-5-haiku-latest \
  --env-path .env.local
```

The exact `top_*_candidate_*_report.json` file names depend on the ranking output.

## Package Map

- `graph_layout_synth/config.py`
  - Loads and validates YAML config.
  - Defines typed config dataclasses.

- `graph_layout_synth/grammar.py`
  - Contains minimal graph grammar expansion rules.
  - Do not refactor heavily unless the task explicitly asks.

- `graph_layout_synth/generator.py`
  - Orchestrates candidate generation.
  - Returns `GenerationResult`.

- `graph_layout_synth/validators.py`
  - Connectivity, corridor access, edge type, and abstract-node validation.

- `graph_layout_synth/scoring.py`
  - Legacy/simple scoring used by generation metadata.
  - Ranking refinement is in `ranking.py`.

- `graph_layout_synth/ranking.py`
  - Deterministic candidate ranking.
  - New ranking output fields are `final_score`, `score_breakdown`, `metrics`, and `tie_break_keys`.
  - `ranking_score` is retained as alias for compatibility.
  - Uses defaults from code, but CLI generation passes YAML-defined `config.ranking` settings.

- `graph_layout_synth/export.py`
  - JSON graph export.
  - Candidate report export.
  - Ranking report JSON/CSV export.

- `graph_layout_synth/visualize.py`
  - Static PNG visualization.
  - Uses config-provided node colors when available.

- `graph_layout_synth/llm_evaluator.py`
  - Optional Claude interpretation.
  - Must not replace deterministic ranking.
  - Tests must mock/isolate live API calls.

- `graph_layout_synth/cli.py`
  - CLI subcommands:
    - `generate`
    - `evaluate-llm`

## Ranking Report Shape

`ranking_report.json` should contain a list of candidates shaped like:

```json
{
  "rank": 1,
  "candidate_id": "candidate_3",
  "final_score": 179.0076,
  "ranking_score": 179.0076,
  "score_breakdown": {
    "validation": 100.0,
    "connectivity": 20.0,
    "corridor_access": 30.0,
    "edge_density": 9.3336,
    "corridor_efficiency": 8.888,
    "door_wall_balance": 2.5,
    "distance_efficiency": 6.0,
    "support_mix": 4.286,
    "dead_end_penalty": -2.0,
    "invalid_edge_penalty": 0.0,
    "abstract_node_penalty": 0.0
  },
  "metrics": {
    "node_count": 9,
    "edge_count": 12,
    "room_count": 7,
    "corridor_count": 2,
    "corridor_access_ratio": 1.0,
    "edge_node_ratio": 1.3333
  },
  "tie_break_keys": {
    "validation_passed_desc": 1,
    "corridor_access_ratio_desc": 1.0,
    "invalid_edge_type_count_asc": 0,
    "abstract_node_count_asc": 0,
    "dead_end_count_asc": 1,
    "edge_node_ratio_desc": 1.3333,
    "candidate_id_asc": "candidate_3"
  }
}
```

## LLM Evaluation Constraints

LLM evaluation should:

- read deterministic reports
- summarize and interpret metrics
- compare top candidates
- suggest possible repair directions

LLM evaluation must not:

- invent metrics
- replace deterministic ranking
- claim code-level correctness
- assume geometry not present in the graph
- certify building-code or life-safety compliance
- generate or repair graphs automatically

## Testing Guidance

Always run:

```bash
python -m pytest
```

Tests should not make live Anthropic API calls.

If a task touches ranking, check:

- `tests/test_ranking.py`
- `tests/test_export.py`
- `tests/test_cli.py`
- `tests/test_report_export.py`

If a task touches LLM evaluation, check:

- `tests/test_llm_evaluator.py`
- `tests/test_cli.py`

## Git Notes

This repo has previously had local Codex checkpoint refs under `.git/refs/codex` that broke `git fetch` with errors like:

```text
fatal: bad object refs/codex/turn-diffs/checkpoints/...
```

The fix used locally was:

```bash
Remove-Item -LiteralPath .git\refs\codex -Recurse -Force
git config --local --add fetch.hideRefs refs/codex
git fetch --prune origin
```

Do not delete normal branches or remote refs when cleaning this up.

## Development Style

Keep changes minimal and readable.

Avoid:

- broad refactors
- changing generator behavior unless explicitly requested
- adding OR-Tools
- adding geometry generation
- adding web UI unless the branch explicitly asks for it
- live API calls in tests

Prefer:

- explicit functions
- dataclasses where helpful
- deterministic seeds in tests
- JSON/CSV outputs that remain easy for LLMs and humans to inspect
