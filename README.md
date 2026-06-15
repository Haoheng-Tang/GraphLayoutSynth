# GraphLayoutSynth

Stochastic graph-grammar generation and evaluation for building layout graphs.

## Overview

GraphLayoutSynth is an early-stage research prototype for generating building layout graphs using procedural graph-grammar rules, stochastic sampling, and rule-based validation.

The project represents building layouts as attributed graphs:

* Nodes represent spaces such as rooms, corridors, zones, or service areas.
* Edges represent spatial relationships such as door connections or wall adjacencies.
* Node attributes may include room type, area, aspect ratio, orientation, zone, and other spatial or functional properties.
* Edge attributes may include connection type, adjacency type, or circulation relationship.

The initial goal is not to train a deep graph generative model. Instead, the project explores a small-data, rule-guided approach where candidate layout graphs are generated through stochastic procedural rules and evaluated using explicit constraints and metrics.

## Milestone 1

**Minimal stochastic graph-grammar prototype**

The current implementation generates small building layout graphs from explicit stochastic grammar rules, validates them, scores them, and exports the best candidate to JSON.

Implemented features:

* Define a seed graph such as `BuildingFloor`.
* Expand abstract nodes into zones, room clusters, corridors, and rooms.
* Add stochastic rule parameters such as zone count, room count, and room-type mix.
* Validate generated graphs using basic constraints.
* Score and rank feasible candidates.
* Export generated graphs as JSON.

## Quickstart

Use the requested mamba environment:

```bash
mamba activate musa-550-fall-2024
python -m pip install -e ".[dev]"
```

Generate candidates:

```bash
python -m graph_layout_synth generate --config configs/generic_building.yaml --num-candidates 10 --seed 42 --output-dir outputs
```

Generate candidates with PNG visualizations:

```bash
python -m graph_layout_synth generate --config configs/generic_building.yaml --num-candidates 10 --seed 42 --visualize
```

Rank candidates and save the top candidates:

```bash
python -m graph_layout_synth generate --config configs/generic_building.yaml --num-candidates 50 --top-k 5 --seed 42 --visualize
```

## Configuration

The default YAML config lives at `configs/generic_building.yaml`. It controls the project/building type name, default seed, candidate count, allowed node and edge types, zone types, room type mix, stochastic cluster parameters, corridor pattern choices, basic validation settings, and visualization colors.

You can edit this file or pass another YAML file with `--config` to change grammar and validation parameters without changing Python code.

## Candidate Ranking

Candidate ranking is deterministic and metric-based. Each generated graph receives a `final_score` from transparent score components:

* validation pass reward
* connectivity reward or disconnected penalty
* corridor access reward
* edge-density fit
* corridor-efficiency fit
* door/wall balance
* room-to-corridor distance efficiency
* support-room mix
* dead-end, invalid-edge, and abstract-node penalties

Ranking reports include `final_score`, `score_breakdown`, `metrics`, and deterministic `tie_break_keys`. The legacy `ranking_score` field is kept as an alias of `final_score` for compatibility.

Ranking weights and heuristic targets, such as the target `edge_node_ratio`, are configured in `configs/generic_building.yaml` under the `ranking` section. This keeps scoring assumptions visible and tunable without editing Python code.

The CLI writes `ranking_report.json` and `ranking_report.csv` under the output directory, keeps saving `best_candidate.json`, and saves top-k graph/report artifacts. When `--visualize` is enabled, it also saves PNGs for the top-k candidates.

LLM evaluation interprets the deterministic report, but it does not replace the deterministic ranking or certify validity.

## LLM Evaluation

LLM evaluation is optional. It reads deterministic ranking and candidate reports, then asks Claude for a natural-language interpretation. It does not replace deterministic ranking, does not generate graphs, and should not be treated as a validity certificate.

Install the optional Anthropic dependency when you want to use this command:

```bash
python -m pip install -e ".[llm]"
```

Create `.env.local` at the repository root:

```text
ANTHROPIC_API_KEY=your_api_key_here
```

Run evaluation:

```bash
python -m graph_layout_synth evaluate-llm \
  --ranking-report outputs/ranking_report.json \
  --candidate-reports outputs/top_candidate_000_report.json outputs/top_candidate_001_report.json \
  --output outputs/llm_evaluation.md
```

The LLM can summarize, compare, critique, and suggest possible repair directions for top candidates. Deterministic metrics and ranking remain the primary ranking method.

Run tests:

```bash
pytest
```

## Initial Scope

The first prototype will support generic building layout graphs rather than one specific building type.

Example room or space types may include:

* Corridor
* PatientRoom
* ClinicalSupport
* StaffSupport
* PublicZone
* PrivateZone
* VerticalCore

Example edge types may include:

* Door connection
* Wall adjacency

## Method

The initial generation pipeline is:

```text
Structured grammar rules
        ↓
Stochastic graph-rewrite generator
        ↓
Rule-based constraint checker
        ↓
Metric-based candidate scoring
        ↓
Candidate export and interpretation
```

Large language models may be used later as auxiliary tools for rule formalization, candidate interpretation, and ranking, but the core generation engine is intended to remain explicit and inspectable.

## Branch

Initial development branch:

```text
m1_stochastic_grammar
```

## Status

Early research prototype.
