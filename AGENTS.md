# AGENTS.md

Concise working notes for future coding agents on GraphLayoutSynth.

## Purpose

GraphLayoutSynth is an early-stage Python research prototype for stochastic graph-grammar generation and deterministic evaluation of building layout graphs. Graphs are NetworkX graphs with attributed nodes and edges. Generated layouts are not geometric plans, building-code checks, life-safety checks, or compliance-certified designs.

Deterministic validation and ranking are the source of truth. Optional Claude evaluation is report interpretation only. Optional Claude grammar-variant assistance proposes YAML configs only; it must not generate raw graphs or bypass validation.

## Current Architecture

- Python package: `graph_layout_synth`
- Graph backend: NetworkX
- Config: YAML, default `configs/generic_building.yaml`
- Config-contract layer: `ConfigContract` derived from the active YAML config.
- CLI commands:
  - `python -m graph_layout_synth generate`
  - `python -m graph_layout_synth validate-config`
  - `python -m graph_layout_synth propose-grammar-variant`
  - `python -m graph_layout_synth archive-final`
  - `python -m graph_layout_synth evaluate-llm`
- Generation uses a seed graph and stochastic YAML `grammar_rules` when present.
- Validators, grammar-variant prompts, semantic room-mix checks, and typed accessibility context should consume the live config contract rather than duplicating vocabulary assumptions.
- Grammar rules support simple exact node-attribute matching, created-node aliases, fixed counts, min/max counts, choice sampling, matched-node updates, optional matched-node removal, and edge modes `one_to_one`, `each_to_one`, `one_to_each`, `adjacent_pairs`.
- Rule-application tracing records applied rule order, matched nodes, sampled parameters, created nodes/edges, and removed nodes.
- Candidate review summaries provide compact human/RAG-oriented graph summaries with artifact pointers, separated support-type counts/ratios, wall-adjacency proxy metrics with node references, and typed accessibility summaries.
- Diversity and novelty metrics use feature vectors extracted from candidate review summaries, not raw graph edit distance, and do not alter deterministic ranking.
- Final-output archiving is explicit and selection-file based; archive entries represent accepted final outputs, not all generated candidates.
- Optional Claude grammar-variant assistance proposes complete YAML config variants only; generated YAML must validate before normal generation.
- Outputs include candidate graph JSON, candidate reports, trace JSON/markdown, review summary JSON, `diversity_report.json`, optional `final_output_archive.json`, `ranking_report.json`, `ranking_report.csv`, and optional PNG visualizations.
- Optional Claude evaluation reads deterministic reports and writes markdown.
- Optional Claude grammar-variant assistance reads config/report/archive context and proposes complete YAML config variants that must validate before generation.

## Key Modules

- `config.py`: loads and validates YAML config; defines config dataclasses.
- `config_contract.py`: derives allowed vocabularies, semantic groups, room-mix targets, reachable room-mix ranges, typed accessibility pairs, and grammar-rule context from raw YAML configs.
- `config_validator.py`: user-facing config validation reports for CLI/tests.
- `rule_schema.py`: validates and applies executable YAML grammar rules.
- `tracing.py`: trace event dataclass, trace JSON/markdown export, and compact trace metadata helpers.
- `grammar.py`: creates the seed graph and orchestrates graph expansion.
- `generator.py`: generates one or more candidates and returns `GenerationResult`.
- `validators.py`: checks connectivity, corridor access, edge types, and remaining abstract nodes.
- `scoring.py`: legacy/simple generation score used as metadata.
- `ranking.py`: deterministic metrics, `final_score`, `score_breakdown`, and tie-break ranking.
- `review_summary.py`: compact candidate and pool review summaries, including degree, wall-adjacency proxy metrics, and typed accessibility summaries.
- `diversity.py`: diversity feature extraction, normalized pairwise distance, archive novelty, and feature-bin coverage metrics.
- `archive.py`: explicit final-output archive utilities using LLM/manual selection files and candidate review summaries.
- `grammar_variant_assistant.py`: optional Claude prompt, YAML extraction, validation, and artifact-writing helpers for grammar/config variants.
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

Validate a config before generation:

```bash
python -m graph_layout_synth validate-config --config configs/generic_building.yaml
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
  --model claude-sonnet-4-6 \
  --env-path .env.local
```

The exact top-k candidate filenames depend on ranking results.

Prompt-only grammar variant dry run:

```bash
python -m graph_layout_synth propose-grammar-variant \
  --base-config configs/generic_building.yaml \
  --variant-requirements docs/PATIENT_SUPPORT_ROOM_MIX_REQUIREMENTS.yaml \
  --write-prompt outputs/grammar_variant_prompt.md \
  --no-call
```

## Secrets And Outputs

- `.env.local` stores `ANTHROPIC_API_KEY`.
- Do not commit `.env`, `.env.local`, `.env.*.local`, real API keys, or generated outputs.
- `.env.example` is safe to commit and should contain only `ANTHROPIC_API_KEY=`.
- Generated files under `outputs/` are ignored except `outputs/.gitkeep`.

Typical generated files:

- `best_candidate.json`
- `best_candidate_report.json`
- `best_candidate_trace.json`
- `best_candidate_trace.md`
- `ranking_report.json`
- `ranking_report.csv`
- `review_summary.json`
- `diversity_report.json`
- optional `final_output_archive.json`
- `candidate_<n>.json`
- `candidate_<n>_report.json`
- `candidate_<n>_trace.json`
- `candidate_<n>_trace.md`
- `candidate_<n>_review_summary.json`
- optional `candidate_<n>.png`
- `top_<rank>_candidate_<n>.json`
- `top_<rank>_candidate_<n>_report.json`
- `top_<rank>_candidate_<n>_trace.json`
- `top_<rank>_candidate_<n>_trace.md`
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
- Do not describe wall-adjacency summary fields as literal geometric corner-room or code-compliance metrics; they are graph-only proxies.
- Keep `ClinicalSupport` and `StaffSupport` separate in review summaries. Do not collapse them into a generic support-room field for RAG review.
- Wall-adjacency node references should include `node_id`, `node_type`, `wall_degree`, and available attributes such as `zone`.
- Typed accessibility currently uses door-edge-only travel by default and includes `PatientRoom` to nearest `ClinicalSupport` as the default pair when present. Do not use it for scoring unless explicitly requested.
- Do not use diversity or novelty metrics to change final ranking/selection in this branch. They are exported diagnostics only.
- Do not auto-archive generated, best, or top-k candidates. Archive entries represent accepted final outputs only.
- Prefer the selection-file workflow for archiving. Direct `--review-summary` archiving is secondary for manual/test workflows.
- Do not update the final-output archive automatically during `generate`; use `archive-final` with explicit selection input.
- Read `docs/GRAMMAR_CONFIG_SKILLS.md` before modifying or generating YAML grammar configs.
- Read `docs/GRAMMAR_CONFIG_SKILLS.md` before changing grammar config generation logic.
- Do not invent unsupported config or grammar-rule fields.
- Use `ConfigContract` instead of duplicating config assumptions in validators, prompt builders, semantic checks, or tests.
- Validators and LLM prompt builders should derive node/edge vocabulary from the current config. Do not hardcode room types except as documented fallback examples.
- Keep `room_mix_targets.expected_room_type_counts` inside `ConfigContract.room_mix_reachable_ranges`; these ranges are derived from grammar-rule zone counts and per-zone room counts.
- Run `validate-config` after changing any config.
- After changing config schema or contract-derived fields, run `validate-config` and tests.
- LLM-generated YAML must be validated before it is used for generation.
- Invalid LLM-generated YAML should be saved separately, such as `*.invalid.yaml`, and must not be used for generation.
- Do not overwrite baseline configs with LLM-generated variants; save variants under `outputs/` or a dedicated variants directory.
- When asking Claude for room-mix changes, prefer config-defined `room_mix_targets` and `semantic_node_groups`; use a structured YAML/JSON file through `--variant-requirements` only for run-specific overrides.
- The current config contract drives prompt text and semantic room-mix validation parameters. Keep `docs/PATIENT_SUPPORT_ROOM_MIX_REQUIREMENTS.yaml` aligned only if it is still used as an override artifact.

## Coding Style

Keep changes minimal and readable. Prefer explicit functions, dataclasses where helpful, deterministic seeds in tests, and JSON/CSV outputs that are easy for both humans and LLMs to inspect. Tests touching LLM evaluation must mock or isolate the Anthropic API boundary.
