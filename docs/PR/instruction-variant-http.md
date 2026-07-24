# Expose instruction-guided config variants over HTTP

## Summary

This PR adds `POST /grammar-variants/propose-from-instructions`, exposing
the existing `propose-instruction-variant` CLI workflow to NextRoomPredictor
over HTTP, so the frontend can submit plain-language design instructions and
get back a validated (and, optionally, generated) grammar/config variant.

A valid instruction-guided proposal becomes a normal entry in the *existing*
grammar-variant registry — visible via `GET /grammar-variants`, inspectable
via `GET /grammar-variants/{id}`, and activatable via
`POST /grammar-variants/{id}/activate` — through the same `registry.json`
and `GrammarVariantRecord` shape the heuristic-only proposal flow already
uses. There is no second, independent variant registry.

Claude is called only when this one endpoint is reached with `dryRun=false`
and non-empty `instructionText`. Every other endpoint — `/health`,
`/suggest-next-room`, the room-type catalog, program-requirement validation,
and variant listing/inspection/activation — remains fully deterministic and
LLM-free, which this PR verifies with dedicated regression tests.

This PR also fixes a related developer-experience gap discovered while
testing the new endpoint locally: `.env.local` was silently ignored by
`uvicorn`, so `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true` in that file had no
effect unless it was also exported into the shell before starting the
server.

Finally, this PR adds PNG visualizations for instruction-guided generated
samples: when `--samples`/`samples > 0` and a proposal validates, generated
JSON graphs are now accompanied by PNGs, using the existing
`generate --visualize` flag and `visualize_graph` renderer — no new
rendering code.

## Motivation

GraphLayoutSynth already had a validation-guided repair loop for
instruction-guided proposals on the CLI (`--repair-attempts`), but no way
for a frontend to use it. NextRoomPredictor needs to submit free-form design
instructions from the browser and see the result flow into the same
grammar-variant lifecycle (list, inspect, activate) that structured/heuristic
proposals already use, without a parallel implementation to maintain.

## Design: one engine, two entry points

The CLI's attempt/repair loop, artifact layout, and review-summary format
were extracted into `graph_layout_synth/instruction_variant_workflow.py`, a
module with no CLI or HTTP coupling:

- `run_instruction_variant_attempts(...)`: the initial proposal plus up to
  `repair_attempts` repairs, stopping at the first valid config. Writes
  per-attempt artifacts and raises `InstructionVariantAttemptError` (carrying
  the full attempt history) if a Claude call or YAML extraction ever fails
  outright — the same fatal/non-retryable distinction the CLI already made
  between "invalid config" (retryable) and "no config at all" (not).
- `write_instruction_variant_prompt_artifacts(...)` and
  `write_instruction_variant_review_summary(...)`: the artifact/report
  writers, unchanged in behavior, just relocated.

`cli.py`'s `run_propose_instruction_variant` was refactored to call this
shared engine. All 17 pre-existing CLI tests pass unchanged against the
refactor, confirming it preserved exact behavior — including one subtlety
the refactor could easily have broken: existing tests monkeypatch
`cli.propose_grammar_variant_with_claude`, but a naive shared-module default
parameter binds its own copy of that name at import time. The fix was to
have `cli.py` pass `claude_call=propose_grammar_variant_with_claude`
explicitly (its own module-level reference, resolved at call time), so the
monkeypatch keeps working exactly as before.

`graph_layout_synth/instruction_variant_control_plane.py` is the new
HTTP-specific orchestration layer: it wraps the shared engine with the
*existing* `grammar_variant_control_plane.py` registry helpers
(`_new_variant_id`, `_finalize_record`, `_record_from_metadata`,
`GrammarVariantRecord`) rather than reimplementing any of them. Generation
reuses `cli.run_generation_for_instruction_variant` directly (imported
across modules) — no new generator code.

## Request / response

```http
POST /grammar-variants/propose-from-instructions
```

```json
{
  "name": "inpatient-unit-rules-v1",
  "instructionText": "# Inpatient unit rules\n\n- PatientRoom should connect to Corridor with door edges.\n- ClinicalSupport should be near PatientRoom groups.\n- Avoid a single Corridor node connected to every PatientRoom.",
  "repairAttempts": 2,
  "samples": 0,
  "dryRun": false
}
```

- `instructionText` (required): non-empty after trimming; empty or
  whitespace-only returns a controlled HTTP 400.
- `name` (optional): human label used for the registry's `heuristicSummary`.
- `baseConfigPath` (optional): defaults to `configs/generic_building.yaml`,
  validated the same way the existing `POST /grammar-variants/propose`
  already validates this field — no new path-safety behavior invented.
- `repairAttempts` (default `0`, capped at `3`) and `samples` (default `0`,
  capped at `25`): out-of-range values are rejected (HTTP 400), not
  silently clamped, matching the existing `sampleCount` bound on
  `POST /suggest-next-room`.
- `dryRun` (default `false`): see below.

```json
{
  "status": "proposed_valid",
  "variantId": "20260721T090501123456Z-3f9a1c2d",
  "valid": true,
  "repairAttemptsUsed": 1,
  "generationRan": false,
  "artifactDir": "outputs/llm_variants/20260721T090501123456Z-3f9a1c2d",
  "attempts": [
    {"attemptIndex": 0, "kind": "initial", "valid": false, "validationErrorCount": 2, "artifactDir": "…/attempts/attempt_0_initial"},
    {"attemptIndex": 1, "kind": "repair", "valid": true, "validationErrorCount": 0, "artifactDir": "…/attempts/attempt_1_repair"}
  ],
  "errors": [],
  "warnings": []
}
```

`status` is one of `dry_run`, `proposed_valid`, `generated`,
`proposed_invalid`, or `failed`, mirroring the CLI manifest's vocabulary.

### Dry run

`dryRun: true` writes the same four always-on artifacts the CLI's `--no-call`
writes (`submitted_instructions.md`, `base_config.yaml`, `llm_prompt.md`,
`manifest.json`) under a server-assigned directory, without calling Claude,
running any repair, or generating graphs. `variantId` is always `null` in
the response, even though a real artifact directory (and registry record
with `status: "dry_run"`) exists server-side — dry runs are cheap enough
that a frontend may fire many of them while a user drafts instructions, and
they are not meant to be addressable variants.

### Feature gate

Gated identically to every other `/grammar-variants/*` endpoint, dry runs
included: `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true` or HTTP 403. This
matches the existing `POST /grammar-variants/propose` convention (which
already gates its own dry-run mode the same way) rather than inventing a
new, inconsistent carve-out.

### Generation and activation

If some attempt validates and `samples > 0`, the existing `generate`
pipeline runs against the validated config before the response returns
(`status: "generated"`). The variant is registered as `status: "valid"` in
the registry *before* generation is attempted, so a generation failure (a
distinct controlled HTTP 500) never leaves a valid config unregistered or
unactivatable — the registry's own `status` field is deliberately never set
to `"generated"`, since `activate_variant()` only accepts `"valid"`.

If every attempt (initial plus all repairs) remains invalid, the config is
saved and inspectable but `POST /grammar-variants/{id}/activate` returns
HTTP 400 for it, identically to any other non-`valid` registry record.

## `.env.local` auto-loading fix

While testing the new endpoint locally, `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true`
in `.env.local` had no effect — `server/main.py` never read that file at
startup; only `load_llm_environment()`, called deep inside the Claude-calling
code paths, ever did, and by then the feature-gate check had already run.

`server/main.py` now calls `load_llm_environment()` once, at module import
time, before `create_app()` is ever invoked:

```python
load_llm_environment()
```

This relies on an existing safety property of `load_llm_environment`: it
never overrides a variable already present in `os.environ`. Test isolation
is preserved because `server.main` is imported once during pytest
collection (before any test body runs), and `tests/conftest.py`'s autouse
fixture clears every service env var before each subsequent test — so by
the time any test calls `create_app()`, the environment is already clean
regardless of what a developer's real `.env.local` contains. Verified by
running the full suite with a populated `.env.local` present (all tests
passed) and by a clean-process check confirming `GET /grammar-variants`
now returns 200 without any variables manually exported into the shell.

## PNG visualizations for generated samples

Generated samples were previously JSON-only, which made them hard to
inspect. Rather than write a second rendering path, generation now passes
the existing `generate` CLI's `--visualize` flag from the one shared
function both entry points already call
(`cli.run_generation_for_instruction_variant`), so PNGs are produced by the
same `visualize_graph` renderer `generate --visualize` already uses,
land next to their JSON counterparts in `generated_samples/`
(`candidate_<n>.png`, `top_<rank>_<candidate_id>.png`,
`best_candidate.png`), and follow the exact existing `generate --visualize`
output layout — no new subdirectory, no new renderer.

Per-sample rendering failures are non-fatal: `run_generate`'s three
`visualize_graph()` call sites are now wrapped in a small
`_visualize_graph_safely` helper that catches exceptions, records a warning,
and continues, so one broken PNG never invalidates its already-written JSON
or blocks the remaining samples. `run_generate` returns
`{"visualization_warnings": [...]}`, threaded up through
`run_generation_for_instruction_variant` into:

- the CLI manifest (`generatedSamplesPngDir`, `visualizationWarnings`) and
  `review_summary.md`;
- the HTTP response (appended to the existing `warnings` list;
  `generatedSamplesPngDir` is `null` unless generation actually ran).

Visualization is purely deterministic and post-generation — Claude is never
invoked by it, and none of the existing generation, validation, or ranking
logic changed.

## Guardrails verified by tests

Dedicated regression tests confirm Claude is never called by:

- `GET /program-requirements/room-types`
- `POST /program-requirements/validate`
- `GET /grammar-variants`
- `POST /grammar-variants/{id}/activate`
- `POST /suggest-next-room`
- server startup / `GET /health`

Each stubs the Claude call boundary to raise `AssertionError` if invoked at
all, so any accidental future LLM call in these paths fails loudly.

## Documentation

- `docs/INSTRUCTION_GUIDED_VARIANTS.md`: full HTTP section (request/response
  examples, dry-run semantics, repair-over-HTTP, activation flow, artifact
  structure parity with the CLI); corrected an outdated limitation that said
  this workflow "does not activate proposed variants" (no longer true); noted
  the endpoint is synchronous with no background job queue in this branch.
- `docs/contracts/suggest-next-room-api.md`: the `active_variant` grammar-mode
  explanation now covers both proposal paths that can populate an active
  variant, with an explicit, bolded reassurance that `/suggest-next-room`
  itself never calls Claude regardless of which path produced the active
  variant.
- `README.md`, `AGENTS.md`, `CLAUDE.md`: endpoint listed, guardrails recorded
  (no LLM calls outside this one gated endpoint; no second variant registry).
- `docs/INSTRUCTION_GUIDED_VARIANTS.md`: new "PNG visualizations" section
  (where PNGs land, that rendering is deterministic/post-generation and
  Claude-free, that JSON remains the source artifact) plus updated
  request/response examples showing `generatedSamplesPngDir`.

## Tests

`tests/test_instruction_variant_http.py` (29 tests) covers:

- request validation: empty/whitespace-only `instructionText`; negative and
  above-cap `repairAttempts`/`samples`
- feature gate: disabled by default (403), including for dry runs
- dry run: artifacts written, Claude never called, no activatable variant
  registered, no PNGs produced
- live valid proposal: registered, listed, activatable, no repair call made
- initial invalid, no repair: not activatable, no generation, artifacts
  saved, no PNGs produced
- initial invalid, repair succeeds: repair prompt contains the invalid YAML,
  validation errors, and original instructions; activatable with or without
  `samples > 0`; generation called with the requested count only when valid
- repair exhaustion: every attempt saved, not activatable, no generation
- generation gating: `samples=0` never calls generation or produces PNGs;
  an invalid config never reaches either even when `samples > 0`
- PNG visualization: an unmocked live generation run with `samples > 0`
  produces real, non-empty PNGs alongside JSON via the existing pipeline
  flag; a rendering failure (mocked `visualize_graph`) is recorded in the
  response's `warnings` without blocking Claude-free generation or the
  already-written JSON
- the six LLM-call guardrails listed above

`tests/test_instruction_variant.py` (22 tests, CLI) covers the equivalent
PNG scenarios: the shared generation function is called with
`--visualize` (verified via a mocked `run_generate` capturing `args`), an
unmocked live run produces real PNGs next to JSON, `samples=0`/invalid
config/`--no-call` all produce zero PNGs, and a mocked `visualize_graph`
failure is recorded in `manifest.json`'s `visualizationWarnings` and
`review_summary.md` without raising or repeating the Claude call.

Plus regression: all pre-existing CLI, grammar-variant control-plane,
program-requirements, `/suggest-next-room`, and visualization (`visualize.py`)
test suites pass unchanged.

## Verification

```txt
python -m pytest -q
287 passed, 1 warning

git diff --check
passed
```

Manual smoke test: ran `propose-instruction-variant` end-to-end with the
Claude call stubbed to return a known-valid config (`--samples 3
--repair-attempts 2`) — confirmed `generated_samples/` contains matching
`.json`/`.png` pairs (`best_candidate.*`, `candidate_<n>.*`,
`top_<rank>_candidate_<n>.*`), `manifest.json`'s `generatedSamplesPngDir`
points at that directory, and `review_summary.md` mentions the PNG
visualizations.

## Non-goals

This PR does not:

- change `/suggest-next-room`'s request, response, or matching behavior
- change CLI behavior beyond adding `--visualize` to the shared generation
  call (all pre-existing instruction-variant CLI tests pass unchanged)
- implement a new validator, a new generator, or a new PNG renderer —
  visualization reuses `visualize_graph`/`generate --visualize` unchanged
- create a second, independent variant registry
- implement frontend UI
- implement PDF ingestion
- implement background jobs or polling — the endpoint is synchronous
- let Claude override deterministic validation at any point, or involve
  Claude in visualization at all

## Review checklist

- [x] Claude is called only for this one endpoint, only with `dryRun=false`
      and non-empty `instructionText`.
- [x] CLI and HTTP share one attempt/repair engine and one variant registry.
- [x] Generation runs only after an attempt actually validates; the registry
      record stays `valid` (not `generated`) so activation keeps working.
- [x] Invalid/exhausted proposals are saved and inspectable but never
      activatable.
- [x] Feature gate matches existing `/grammar-variants/*` convention,
      dry runs included.
- [x] `.env.local` auto-load does not leak into tests (verified against a
      populated `.env.local`).
- [x] PNG visualization reuses the existing `generate --visualize` flag and
      `visualize_graph` renderer; no new rendering code was introduced.
- [x] A visualization failure is recorded as a warning and never invalidates
      the generated JSON, blocks remaining samples, or calls Claude.
- [x] `samples=0`, an invalid config, and dry runs all produce zero PNGs.
- [x] Existing CLI, control-plane, program-requirements, visualization, and
      `/suggest-next-room` tests pass unchanged.
- [x] Full test suite passes; `git diff --check` is clean.
