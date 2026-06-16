# GraphLayoutSynth

GraphLayoutSynth is an early-stage Python research prototype for generating and evaluating building layout graphs with stochastic graph-grammar rules.

It represents layouts as attributed NetworkX graphs:

- nodes are spaces such as floors, zones, corridors, patient rooms, and support rooms
- edges are relationships such as door connections or wall adjacencies
- deterministic validation and metric-based ranking are the source of truth
- optional Claude evaluation can interpret reports, but does not rank, repair, or certify layouts

Generated graphs are research prototypes. They are not geometric plans, construction documents, building-code checks, life-safety checks, or compliance-certified layouts.

![RAG-augmented graph generation workflow](rag_augmented_graph_generation_workflow.png)

## Current Pipeline

```text
YAML configuration and grammar_rules
  -> stochastic NetworkX graph generation
  -> rule-based validation
  -> deterministic metric ranking
  -> JSON/CSV reports and optional PNG visualization
  -> optional Claude interpretation of deterministic reports
```

The package is `graph_layout_synth`. The CLI entry point is:

```bash
python -m graph_layout_synth
```

## Installation

Use the local project environment if available:

```bash
mamba activate musa-550-fall-2024
python -m pip install -e ".[dev]"
```

Core dependencies are NetworkX, PyYAML, and Matplotlib. Development installs also include pytest.

Install optional Claude support only when you need LLM report interpretation:

```bash
python -m pip install -e ".[llm]"
```

Create `.env.local` at the repository root for local Claude evaluation:

```text
ANTHROPIC_API_KEY=your_api_key_here
```

Do not commit `.env.local`. The committed `.env.example` contains only an empty placeholder.

## Configuration

The default config is `configs/generic_building.yaml`. It defines:

- project metadata and default random seed
- generation defaults such as candidate count
- allowed node and edge types
- room type counts and stochastic generation settings
- validation settings
- explicit executable `grammar_rules`
- deterministic ranking weights and targets
- visualization colors

Pass another YAML file with `--config` to run the same pipeline with different settings.

## Grammar Rules

`grammar_rules` are a small executable YAML rule schema, not a full graph-grammar formalism. Rules match nodes by exact attribute values, then update matched nodes, create nodes, create edges, and optionally remove the matched node.

Supported features include:

- simple node-attribute matching, such as `type: Zone` and `is_abstract: true`
- fixed counts, such as `count: 1`
- stochastic min/max counts, such as `count: {min: 3, max: 5}`
- stochastic choices, such as `type: {choices: [PatientRoom, ClinicalSupport]}`
- created-node aliases for edge creation
- edge modes: `one_to_one`, `each_to_one`, and `one_to_each`
- the special `matched` alias, plus `__neighbors__` for existing neighbors

Example:

```yaml
grammar_rules:
  - name: expand_zone_to_room_cluster
    match:
      type: Zone
      is_abstract: true
    action:
      remove_matched_node: false
      update_matched_node_attributes:
        is_abstract: false
      create_nodes:
        - alias: corridor
          type: Corridor
          count: 1
          attributes:
            is_abstract: false
        - alias: room
          type:
            choices:
              - PatientRoom
              - ClinicalSupport
              - StaffSupport
          count:
            min: 3
            max: 5
          attributes:
            is_abstract: false
      create_edges:
        - source: matched
          target: corridor
          edge_type: door
        - source: room
          target: corridor
          edge_type: door
          mode: each_to_one
```

The generator still contains older built-in expansion helpers, but config-defined grammar rules are used when present.

## Rule Application Tracing

The `generate` command exports a lightweight rule-application trace for each generated candidate. Trace files show how a candidate was produced, including the rule applied at each step, the matched node and attributes, sampled counts and choices, created nodes, created edges, and removed nodes.

Tracing is useful for debugging grammar behavior, explaining why two seeded candidates differ, and giving deterministic reports more provenance without changing ranking behavior.

Trace artifacts are saved under `--output-dir` alongside graph and report files:

- `candidate_<n>_trace.json` and `candidate_<n>_trace.md` for every generated candidate
- `best_candidate_trace.json` and `best_candidate_trace.md` for the top-ranked candidate
- `top_<rank>_candidate_<n>_trace.json` and `.md` for each exported top-k candidate

Candidate reports and ranking reports include compact trace metadata: `trace_path`, `trace_length`, `applied_rule_names`, and `applied_rule_counts`. Inspect the JSON trace for full step details or the markdown trace for a short human-readable summary.

## Generate Graphs

Generate ranked candidates:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 2 \
  --seed 42 \
  --output-dir outputs
```

Generate ranked candidates with PNG visualizations:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 2 \
  --seed 42 \
  --visualize \
  --output-dir outputs
```

## Candidate Ranking

Ranking is deterministic and metric-based. Each candidate receives:

- `final_score`
- backward-compatible `ranking_score`
- `score_breakdown`
- `metrics`
- deterministic `tie_break_keys`

Metrics include counts, validation status, corridor access ratio, edge-node ratio, room-corridor ratio, door-wall ratio, corridor fraction, room-to-corridor distances, dead ends, support-room ratio, abstract-node count, and invalid-edge count.

Score components include validation, connectivity, corridor access, edge density, corridor efficiency, door/wall balance, distance efficiency, support mix, and penalties for dead ends, invalid edges, and abstract nodes. Ranking weights and heuristic targets live in the config under `ranking`.

The older simple `score` in candidate reports is generation metadata. Use `final_score`, `score_breakdown`, and `metrics` for deterministic ranking interpretation.

## Outputs

The `generate` command writes these files under `--output-dir`:

- `best_candidate.json`: NetworkX node-link JSON for the top-ranked candidate
- `best_candidate_report.json`: validation, count, metric, and score summary for the top candidate
- `ranking_report.json`: full deterministic ranking report without embedded graph objects
- `ranking_report.csv`: compact tabular ranking report
- `top_<rank>_candidate_<n>.json`: node-link JSON for each top-k candidate
- `top_<rank>_candidate_<n>_report.json`: report for each top-k candidate
- `candidate_<n>_trace.json` and `candidate_<n>_trace.md`: rule-application trace for each generated candidate
- `best_candidate_trace.json` and `best_candidate_trace.md`: trace aliases for the top-ranked candidate
- `top_<rank>_candidate_<n>_trace.json` and `.md`: trace aliases for exported top-k candidates
- `best_candidate.png` and `top_<rank>_candidate_<n>.png`: optional visualizations when `--visualize` is used

Generated output artifacts are intentionally ignored by git, except `outputs/.gitkeep`.

## Claude LLM Evaluation

The optional `evaluate-llm` command reads deterministic ranking and candidate reports, calls Claude, and writes a markdown interpretation. It can summarize tradeoffs, compare top candidates, and suggest possible repair directions.

It must not be treated as a replacement for deterministic ranking, a code-level correctness check, a graph repair tool, or a building-code/life-safety certification.

Example:

```bash
python -m graph_layout_synth evaluate-llm \
  --ranking-report outputs/ranking_report.json \
  --candidate-reports outputs/top_1_candidate_1_report.json outputs/top_2_candidate_2_report.json \
  --output outputs/llm_evaluation.md \
  --model claude-3-5-haiku-latest \
  --env-path .env.local
```

The exact top-k candidate report names depend on the generated ranking.

## Development

Run tests:

```bash
python -m pytest
```

Run a smoke test:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 2 \
  --seed 42 \
  --visualize \
  --output-dir outputs
```

Tests must not make live Anthropic API calls. LLM-related tests should mock or isolate the optional API boundary.

## Package Map

- `graph_layout_synth/config.py`: YAML loading, validation, and typed config dataclasses
- `graph_layout_synth/rule_schema.py`: executable YAML grammar rule validation and application
- `graph_layout_synth/grammar.py`: seed graph and expansion orchestration
- `graph_layout_synth/generator.py`: candidate generation and generation metadata
- `graph_layout_synth/validators.py`: graph validity checks
- `graph_layout_synth/scoring.py`: legacy/simple generation score
- `graph_layout_synth/ranking.py`: deterministic candidate metrics, scoring, and ranking
- `graph_layout_synth/export.py`: graph, candidate report, and ranking report export
- `graph_layout_synth/visualize.py`: static PNG graph visualization
- `graph_layout_synth/llm_evaluator.py`: optional Claude interpretation of deterministic reports
- `graph_layout_synth/cli.py`: `generate` and `evaluate-llm` commands
