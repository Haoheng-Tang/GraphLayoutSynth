# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GraphLayoutSynth is an early-stage Python research prototype that generates and evaluates building layout graphs using stochastic YAML graph-grammar rules over attributed NetworkX graphs. Nodes are spaces (floors, zones, corridors, patient rooms, support rooms); edges are `door` or `wall` relationships.

The core principle: **deterministic validation and metric-based ranking are the source of truth**. Optional Claude workflows only interpret deterministic reports (`evaluate-llm`) or propose YAML config variants (`propose-grammar-variant`). The LLM never generates raw graphs, never ranks candidates, and is never called during `/suggest-next-room` requests. Generated graphs are research prototypes, not geometric plans or code-compliance-checked layouts — never claim otherwise.

`AGENTS.md` contains the full guardrail list and is kept current; read it before substantive changes.

## Commands

```bash
# Environment (preferred local env) and install
mamba activate musa-550-fall-2024
python -m pip install -e ".[dev]"          # core + pytest + httpx
python -m pip install -e ".[llm]"          # optional: anthropic SDK for LLM commands

# Tests
python -m pytest                            # full suite
python -m pytest tests/test_semantic_anchor_matching.py -q   # one file
python -m pytest tests/test_generator.py::test_name -q       # one test

# Validate a config (run after any config or schema change)
python -m graph_layout_synth validate-config --config configs/generic_building.yaml

# Smoke-test generation
python -m graph_layout_synth generate --config configs/generic_building.yaml \
  --num-candidates 5 --top-k 2 --seed 42 --visualize --output-dir outputs

# Local FastAPI server (NextRoomPredictor integration)
python -m uvicorn server.main:app --reload --port 8000
```

Other CLI commands (`graph_layout_synth/cli.py`): `validate-program-requirements` (deterministic preflight of user program requirements — no LLM, no generation), `propose-grammar-variant` (use `--no-call` for a prompt-only dry run that needs no API key), `archive-final`, `evaluate-llm`.

`.env.local` at the repo root holds `ANTHROPIC_API_KEY` for LLM commands. Never commit it. Everything under `outputs/` is git-ignored except `outputs/.gitkeep`.

## Architecture

### Generation pipeline

```
YAML config + grammar_rules → stochastic generation → rule tracing → validation
  → deterministic ranking → review summaries → diversity/novelty metrics
  → JSON/CSV reports + optional PNGs → optional Claude interpretation
```

Flow through the package: `config.py` loads YAML into dataclasses → `grammar.py` builds the seed graph and orchestrates expansion → `rule_schema.py` applies executable YAML `grammar_rules` (exact attribute matching, stochastic counts/choices, edge modes) → `generator.py` produces candidates → `validators.py` checks validity → `ranking.py` computes deterministic metrics, `final_score`, `score_breakdown`, and tie-breaks → `review_summary.py` / `diversity.py` produce review-only diagnostics → `export.py` / `tracing.py` / `visualize.py` write artifacts.

Key separations to preserve:
- `scoring.py` is a legacy/simple score kept as metadata; `ranking.py` is the real ranking. Use `final_score`, not `score`.
- Diversity/novelty metrics (`diversity.py`) and review summaries are diagnostics only — they must not change ranking or selection.
- Archiving (`archive.py`) is explicit via `archive-final` with a selection file; never auto-archive during generation.

### Config contract

`config_contract.py` derives a live `ConfigContract` from the active YAML config: allowed node/edge types, semantic groups, room-mix targets, reachable room-mix ranges, typed accessibility pairs, and grammar-rule context. Validators, LLM prompt builders, semantic room-mix checks, and tests must consume this contract instead of hardcoding vocabulary. Read `docs/GRAMMAR_CONFIG_SKILLS.md` before modifying `grammar_rules` or config-generation logic, and do not invent unsupported config fields.

### HTTP API layer

`server/main.py` (FastAPI) exposes `GET /health`, `POST /suggest-next-room`, `POST /program-requirements/validate`, and feature-gated grammar-variant endpoints. `graph_layout_synth/api/` holds the Pydantic models, the frontend↔internal ID adapter, strict semantic anchor matching (one-way one-hop multiset coverage over `(neighbor room type, edge type)` signatures — see `docs/PR/semantic-anchor-matching.md`), neighbor and intended-edge aggregation, the mockable `GraphSampler` boundary, and optional debug artifact writing.

Serialization note: the installed FastAPI omits `None`-valued optional fields from response JSON, so optional suggestion fields (`edgeType`, `edgeTypeCounts`, `intendedEdges`) are *absent* on the wire rather than `null` — don't assert their presence in endpoint tests, and know that `model_dump()` (used in debug artifacts) still includes them as `None`.

The suggestion endpoint's contract is deliberately narrow: it predicts neighbor room types only (with optional `edgeType` guidance and optional `intendedEdges` — evidence-backed secondary connections from the suggested room to existing frontend rooms) — no geometry, side, direction, or placement, which stay in the NextRoomPredictor frontend. Frontend room IDs stay external; internal node IDs stay private. Contract and integration details live in `docs/contracts/` and `docs/integration/nextroompredictor-api.md`; `docs/PR/` records the design rationale for each merged feature.

Server behavior is controlled by env vars: `GRAPHLAYOUTSYNTH_GRAMMAR_MODE` (`static` default / `env_config` / `active_variant`) selects the suggestion sampler config (CLI generation is unaffected — always pass `--config` there); `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS` gates the variant control plane (`grammar_variant_control_plane.py`, artifacts under `outputs/llm_variants/`); `GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS`/`_PNGS` enable debug artifact saving. Static `/suggest-next-room` must keep working without LLM dependencies, API keys, or variant state; if `active_variant` mode has no valid active pointer, fail explicitly rather than falling back.

### Program requirements preflight

User-facing `ProgramRequirements` (room types with min/target/max counts plus adjacency preferences; `program_requirements.py`) are strictly separated from the backend `GenerationConstraintProfile` (group-size/corridor-degree/relaxation bounds; `generation_constraint_profile.py`) — never expose cluster/group/degree parameters to users, and never accept area/width/height in the v1 schema. `program_preflight.py` deterministically classifies programs as `feasible`, `feasible_with_relaxation`, or `infeasible`, and runs before any LLM variant proposal (CLI `--program-requirements` or `programRequirements` on `POST /grammar-variants/propose`): errors block the Claude call, warnings continue and are saved in artifacts. `POST /program-requirements/validate` exposes the same check for frontend preflight. See `docs/PROGRAM_REQUIREMENTS.md`.

### LLM variant workflow

`grammar_variant_assistant.py` builds the prompt (embedding the live config contract), calls Claude, extracts YAML, and validates it. Invalid results are saved as `*.invalid.yaml` sidecars and must never be used for generation or activated. LLM variants are written under `outputs/`, never over baseline configs in `configs/`.

## Key Rules

- Start branches from `main`; keep changes small and aligned with the branch goal. Preserve existing CLI behavior and tests unless a behavior change is requested.
- Tests must never make live Anthropic API calls — mock or isolate the API boundary. Use deterministic seeds in tests.
- `tests/conftest.py` clears `ANTHROPIC_API_KEY` and every GraphLayoutSynth service env var before each test, because `load_llm_environment` writes real `.env.local` keys into `os.environ` at runtime. Keep new service env vars in that conftest list, and don't rely on ambient environment in tests.
- Do not add heavy dependencies (geometry, OR-Tools, deep learning, web UI) unless explicitly requested. Core deps are NetworkX, PyYAML, Matplotlib, plus FastAPI/Pydantic/Uvicorn for the API.
- Keep `ClinicalSupport` and `StaffSupport` as separate types in review summaries; do not collapse them.
- Wall-adjacency and accessibility metrics are graph-only proxies — never describe them as geometric or code-compliance metrics, and do not use them for scoring unless requested.
- After changing config schema or contract-derived fields, run `validate-config` and the test suite.
