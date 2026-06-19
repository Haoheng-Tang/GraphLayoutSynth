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
  -> rule-application tracing
  -> rule-based validation
  -> deterministic metric ranking
  -> candidate review summaries for human/RAG inspection
  -> diversity and novelty metrics over review-summary features
  -> JSON/CSV reports, trace files, summaries, and optional PNG visualization
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

## Grammar Config Validation

Validate grammar configs before using them for generation, especially when a YAML variant was proposed by Claude or another LLM:

```bash
python -m graph_layout_synth validate-config --config configs/generic_building.yaml
```

You can also write a small JSON validation report:

```bash
python -m graph_layout_synth validate-config \
  --config outputs/llm_grammar_variant.yaml \
  --output outputs/config_validation_report.json
```

The Claude-facing instruction document for schema-valid YAML variants is `docs/GRAMMAR_CONFIG_SKILLS.md`. Read it before modifying `grammar_rules` or asking an LLM to propose config variants.

## Grammar Rules

`grammar_rules` are a small executable YAML rule schema, not a full graph-grammar formalism. Rules match nodes by exact attribute values, then update matched nodes, create nodes, create edges, and optionally remove the matched node.

Supported features include:

- simple node-attribute matching, such as `type: Zone` and `is_abstract: true`
- fixed counts, such as `count: 1`
- stochastic min/max counts, such as `count: {min: 3, max: 5}`
- stochastic choices, such as `type: {choices: [PatientRoom, ClinicalSupport]}`
- created-node aliases for edge creation
- edge modes: `one_to_one`, `each_to_one`, `one_to_each`, and `adjacent_pairs`
- the special `matched` alias, plus `__neighbors__` for existing neighbors

Example:

```yaml
grammar_rules:
  - name: expand_zone_to_room_cluster
    match:
      type: Zone
      is_abstract: true
    action:
      remove_matched_node: true
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
        - source: corridor
          target: __neighbors__
          edge_type: door
          mode: one_to_each
        - source: room
          target: corridor
          edge_type: door
          mode: each_to_one
        - source: room
          target: room
          edge_type: wall
          mode: adjacent_pairs
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

## Candidate Review Summaries

The `generate` command also exports compact candidate review summaries for human inspection and later RAG-style graph review. These summaries are JSON files that avoid embedding full raw graphs by default while keeping pointers to retrievable artifacts such as graph JSON, candidate reports, traces, images, and the summary file itself.

Each candidate summary includes validity status, final score, score breakdown when available, key deterministic metrics, node and edge counts, node and edge type counts, separate support-type counts and ratios such as `ClinicalSupport` and `StaffSupport`, graph degree statistics, typed accessibility metrics, trace metadata, artifact paths, and a wall-adjacency proxy.

The wall-adjacency proxy counts incident `wall` edges for concrete room-like nodes. It is not a geometric corner-room or code-compliance metric. It is a graph-only signal for whether non-corridor rooms have low wall adjacency, because most room-like spaces in this prototype are expected to share walls with at least two neighboring spaces.

The wall-adjacency summary includes aggregate counts and node references such as `low_wall_adjacency_nodes` and `isolated_wall_nodes`, each with `node_id`, `node_type`, `wall_degree`, and available attributes such as `zone`, so later RAG steps can retrieve the relevant graph fragments rather than only seeing pool-level counts.

Typed accessibility metrics summarize shortest-path travel distances between source and target node types using door edges by default. The initial default pair is `PatientRoom` to nearest `ClinicalSupport`. Each pair summary includes reachable and unreachable source counts, min/mean/median/max distances, a distance histogram, and `far_source_nodes` with `node_id`, `node_type`, `nearest_target_id`, and `distance`. These metrics are review context only and are not used for scoring.

Review summary artifacts are saved under `--output-dir`:

- `candidate_<n>_review_summary.json` for each generated candidate
- `review_summary.json` with `pool_summary` and `candidate_summaries`

Ranking report entries include `review_summary_path` so downstream review code can retrieve the compact candidate summary before deciding whether to load full graph, report, trace, or image artifacts.

## Diversity and Novelty Metrics

The `generate` command writes `diversity_report.json` with lightweight diversity and novelty diagnostics. These metrics are review context only; they do not change deterministic ranking, top-k export, or final candidate selection.

Diversity is measured within the current generated candidate batch. Novelty is measured against an optional archive of previously selected final outputs. Both use numeric feature vectors extracted from candidate review summaries rather than raw graph edit distance.

Feature extraction includes graph and report features such as node and edge counts, type counts, degree histograms, trace rule counts, wall-adjacency proxy metrics, and typed accessibility features. For typed accessibility, the current default feature source is the `PatientRoom` to nearest `ClinicalSupport` door-distance histogram and scalar distance fields when present.

The optional archive path can be passed with `--archive-path`. If omitted, generation looks for `final_output_archive.json` under `--output-dir`; if the file does not exist, novelty is computed as if the archive were empty. This branch computes novelty metrics only and does not update the archive automatically.

Archive novelty reports preserve `nearest_archive_distance` as the raw weighted Euclidean distance in normalized feature space. Because that raw distance spans many feature dimensions, it can exceed `1.0`. The exported `novelty_score` is bounded by normalizing that raw distance by the maximum possible weighted Euclidean distance for the active feature dimensions.

Feature-bin coverage reports both global and sample-normalized coverage. `coverage_rate` is occupied bins divided by all possible bins in the configured behavior space. `sample_normalized_coverage_rate` is occupied bins divided by the maximum number of bins this generated sample could have occupied, `min(num_candidates, total_possible_bin_count)`. For small batches, `sample_normalized_coverage_rate` is usually the more interpretable value.

Generation also accepts:

- `--near-duplicate-threshold`: distance threshold for within-batch near-duplicate detection, default `0.05`
- `--low-novelty-threshold`: novelty-score threshold for archive comparison, default `0.10`

## Final Output Archive

The final-output archive stores accepted final graph outputs selected by an explicit selection process, usually an LLM review step. It is used by later generation runs for novelty comparison. The archive is not a record of every generated or top-ranked candidate, and it is not updated automatically during generation.

The preferred archive input is a machine-readable selection file:

```json
{
  "selected_candidate_id": "candidate_3",
  "selection_rationale": "Candidate 3 has strong validity, balanced graph metrics, and useful novelty.",
  "selection_source": "claude",
  "review_context_path": "outputs/llm_evaluation.md"
}
```

Archive a selected final output with:

```bash
python -m graph_layout_synth archive-final \
  --selection outputs/llm_selection.json \
  --output-dir outputs \
  --archive-path outputs/final_output_archive.json \
  --output-id final_run_001
```

The minimal command is:

```bash
python -m graph_layout_synth archive-final \
  --selection outputs/llm_selection.json \
  --output-dir outputs
```

This resolves `selected_candidate_id` to `outputs/<candidate_id>_review_summary.json`, validates the candidate ID, extracts artifact paths, stores selection notes, and writes or updates `outputs/final_output_archive.json`. If `--output-id` is omitted, a timestamp-based id is generated. Duplicate `output_id` values are rejected unless `--allow-duplicate-output-id` is passed.

Direct review-summary archiving is also available for manual or test workflows:

```bash
python -m graph_layout_synth archive-final \
  --review-summary outputs/candidate_3_review_summary.json \
  --notes "Manual final selection." \
  --output-dir outputs
```

## Generate Graphs

Generate ranked candidates:

```bash
python -m graph_layout_synth generate \
  --config configs/generic_building.yaml \
  --num-candidates 5 \
  --top-k 2 \
  --seed 42 \
  --near-duplicate-threshold 0.05 \
  --low-novelty-threshold 0.10 \
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

- `candidate_<n>.json`: NetworkX node-link JSON for each generated candidate
- `candidate_<n>_report.json`: validation, count, metric, score, trace, and review-summary metadata for each generated candidate
- `candidate_<n>_review_summary.json`: compact structured review summary for each generated candidate
- `review_summary.json`: pool-level and candidate-level review summaries
- `diversity_report.json`: diversity, novelty, and feature-bin coverage metrics
- `final_output_archive.json`: optional archive of accepted final outputs, created by `archive-final`
- `best_candidate.json`: NetworkX node-link JSON for the top-ranked candidate
- `best_candidate_report.json`: validation, count, metric, and score summary for the top candidate
- `ranking_report.json`: full deterministic ranking report without embedded graph objects
- `ranking_report.csv`: compact tabular ranking report
- `top_<rank>_candidate_<n>.json`: node-link JSON for each top-k candidate
- `top_<rank>_candidate_<n>_report.json`: report for each top-k candidate
- `candidate_<n>_trace.json` and `candidate_<n>_trace.md`: rule-application trace for each generated candidate
- `best_candidate_trace.json` and `best_candidate_trace.md`: trace aliases for the top-ranked candidate
- `top_<rank>_candidate_<n>_trace.json` and `.md`: trace aliases for exported top-k candidates
- `candidate_<n>.png`, `best_candidate.png`, and `top_<rank>_candidate_<n>.png`: optional visualizations when `--visualize` is used

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
- `graph_layout_synth/config_validator.py`: user-facing config validation reports for CLI and tests
- `graph_layout_synth/rule_schema.py`: executable YAML grammar rule validation and application
- `graph_layout_synth/grammar.py`: seed graph and expansion orchestration
- `graph_layout_synth/generator.py`: candidate generation and generation metadata
- `graph_layout_synth/validators.py`: graph validity checks
- `graph_layout_synth/scoring.py`: legacy/simple generation score
- `graph_layout_synth/ranking.py`: deterministic candidate metrics, scoring, and ranking
- `graph_layout_synth/review_summary.py`: compact candidate and pool review summaries for human/RAG inspection
- `graph_layout_synth/diversity.py`: diversity feature extraction, pairwise diversity, archive novelty, and feature-bin coverage
- `graph_layout_synth/archive.py`: explicit final-output archive creation from selection files or review summaries
- `graph_layout_synth/export.py`: graph, candidate report, and ranking report export
- `graph_layout_synth/visualize.py`: static PNG graph visualization
- `graph_layout_synth/tracing.py`: rule-application trace event and trace export helpers
- `graph_layout_synth/llm_evaluator.py`: optional Claude interpretation of deterministic reports
- `graph_layout_synth/cli.py`: `generate`, `validate-config`, `archive-final`, and `evaluate-llm` commands
