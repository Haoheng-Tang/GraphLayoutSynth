# Expose suggestion diagnostics in the /suggest-next-room response

Branch: `feat/suggestion-diagnostics` → `main`

## Summary

`POST /suggest-next-room` returned `suggestions: []` for three materially different situations and the response could not distinguish them: no generated graph contained a semantic anchor match, matches were found but had no extra relations left after subtraction (the grammar considers the anchor saturated), or generation returned fewer graphs than requested. This adds three additive top-level fields — `matchedSampleCount`, `samplesWithCandidates`, and `configSource` — populated on every response including empty ones, so a human or a variant-authoring tool can tell "the grammar doesn't speak this vocabulary" from "the grammar thinks you're done" without enabling a debug artifact run and reading JSON off disk.

`configSource` also closes the second blind spot: a stale `GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant` in `.env.local` silently served an old variant's vocabulary twice during this sprint, and both times it was caught by noticing wrong room types rather than by reading a response.

## Where the values come from

`CandidateAggregation` gains `matched_sample_count` and `samples_with_candidates`, counted in the pass the aggregator already makes over the generated graphs. The per-graph work moved into a new `matches_and_candidate_relations_for_generated_graph`, which returns `(match count, extra relations)` from a single walk; `candidate_relations_for_generated_graph` now delegates to it and keeps its existing signature, so no caller changed and nothing is walked twice. This is the fix that makes the distinction possible at all: previously the per-graph helper returned only a relation set, so "no match" and "matched with no extras" were both the empty set.

`configSource` is built from the `SuggestionConfigSource` frozen dataclass the sampler already exposes as `last_config_source`, converted to its API shape by a small `_config_source_info` helper in the predictor. `resolve_suggestion_config_source()` remains the single resolver — nothing new resolves config. Mocked samplers in tests expose no config source, so the field stays optional and is simply absent for them rather than inventing a value.

## Wire and disk agree by construction

`_build_aggregation_report` no longer recounts these two numbers from its own matching report; it reads them off the response. The artifact keeps its own richer per-graph detail (`totalMatchingNodes`, per-node signatures) and its `configSource` block as before, and the response no longer depends on artifacts being enabled in either direction.

## Response shape

```json
{
  "suggestions": [],
  "sampleCount": 10,
  "predictorVersion": "graphlayoutsynth-v1",
  "matchedSampleCount": 0,
  "samplesWithCandidates": 0,
  "configSource": {"mode": "static", "configPath": "configs/generic_building.yaml"}
}
```

`variantId` is omitted rather than `null` outside active-variant mode, following the endpoint's existing absent-vs-null convention (`response_model_exclude_none=True`). Documented rather than changed, so the frontend's existing "treat absent and null equivalently" rule covers it.

## Live verification (static mode, default config, sampleCount 10)

| Request | suggestions | sampleCount | matchedSampleCount | samplesWithCandidates | configSource |
| --- | ---: | ---: | ---: | ---: | --- |
| `OnStageCorridor` anchor + 1 PatientRoom door | 10 types | 10 | 10 | 10 | `static`, `configs\generic_building.yaml` |
| `Kitchen` anchor (never generated) | 0 | 10 | **0** | 0 | `static`, `configs\generic_building.yaml` |
| `PatientRoom` anchor + 3 neighbours (corridor door, patient wall, nurse door) | 1 type | 10 | 10 | 10 | `static`, `configs\generic_building.yaml` |

The `Kitchen` case is the diagnostic working as intended: `Kitchen` is an `optional`-tier type the default grammar never creates, so no generated node can carry the anchor's room type and `matchedSampleCount` is 0 — previously indistinguishable from a saturated anchor. Case 1 returned `CleanUtility`, `EquipmentRoom`, `MedicationRoom`, `NurseStation`, `OffStageCorridor`, `OnStageCorridor`, `PatientRoom`, `SoiledUtility`, `Stair`, `StorageRoom`, all at 10/10 support. Case 3 returned `PatientRoom` by `wall` at 10/10 with a `NurseStation` intended edge.

## Tests

10 new tests in `tests/test_suggestion_diagnostics.py`. The saturated case is constructed deterministically with a stub sampler rather than hoping the default grammar produces one: a generated node typed `OnStageCorridor` whose only neighbour is a `PatientRoom` by `door` exactly covers the anchor signature, so it matches and subtracts to nothing. Coverage: counts consistent with returned suggestions plus the `samplesWithCandidates <= matchedSampleCount <= sampleCount` invariant; no-match reports zero matched samples; matched-but-saturated reports `matchedSampleCount > 0` with `samplesWithCandidates == 0`; a mixed batch of all three graph kinds counts 3/2/1; an end-to-end `Kitchen` anchor against the real default grammar; `configSource` reporting `static` with the default path and `variantId` absent; `configSource` reporting `active_variant` with the right `variantId` and path; `configSource` present on empty responses; the artifact and the response reporting identical values; and diagnostics present without artifacts enabled.

Four existing tests asserted exact response shape or key sets and were updated for the additive fields (`test_next_room_api.py` ×2, `test_suggestion_intended_edges.py`, and the artifact fixture in `test_suggestion_debug_artifacts.py`). No guardrail test was modified; the fail-if-called Claude assertions pass untouched.

## Verification

- `python -m graph_layout_synth validate-config --config configs/generic_building.yaml` → `Config is valid`.
- `python -m pytest` → **366 passed** (356 before the branch).
- `git diff --check` → clean.

## Deviation from the task text

The debug artifact's existing `samplesWithMatches` key was **renamed** to `matchedSampleCount` rather than duplicated alongside it. The task asked for the three fields to appear in `aggregation_report.json` with the same values as the response; emitting both names for one number would have left a redundant key in the artifact forever. The key is read only by this module's own `README.md` summary line and one test fixture, both updated. Artifacts are git-ignored debug output, so nothing durable consumed the old name.

## Non-goals

- No request-schema change, no new dependencies, no LLM calls, no frontend code.
- Debug artifacts keep their own richer per-graph detail; only the two summary counts now come from the response.
- `resolve_suggestion_config_source()` remains the single resolver.
