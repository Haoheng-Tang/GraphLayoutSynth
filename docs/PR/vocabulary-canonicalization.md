# Canonicalize room vocabulary, remove legacy types, add catalog tier flags

Branch: `feat/vocabulary-canonicalization` → `main`

## Summary

Three related changes. **(1)** Circulation becomes a declared property: the
config's `corridor` semantic group is now the source of truth for "is this a
corridor?" across validators, ranking, scoring, and review summaries, with
the old name-token rule kept only as a fallback for configs that declare no
group. **(2)** The legacy `Corridor`, `ClinicalSupport`, and `StaffSupport`
types are removed entirely from the canonical vocabulary — allowed types,
semantic groups, visualization colors, accessibility-pair defaults, and all
downstream metric plumbing now use the healthcare vocabulary, with support
metrics and diversity features derived from semantic groups and
config-derived pairs instead of literal type names. **(3)** The room-type
catalog gains additive `generated: bool` and
`tier: "generated" | "optional"` fields so the frontend can tell which of
the 32 canonical types the active grammar actually creates (16 for the
default config) instead of discovering guaranteed-empty suggestions at
request time.

## Change 1 — Group-declared circulation

- `is_corridor_node_type(node_type, corridor_types=None)`
  (`config_contract.py`): membership in the passed group is authoritative;
  the `"corridor"` token rule applies only without group context, so
  configs without the group keep working. A token-named type *outside* a
  declared group is not circulation.
- `LayoutConfig` now carries contract-derived `corridor_node_types` and
  `support_node_types`, populated in `validate_config` (which already built
  the contract), so graph-level consumers get group context without a
  second resolution path.
- `validate_graph` threads the declared group into
  `rooms_have_corridor_access`/`room_has_corridor_access`;
  `rank_candidates` → `compute_candidate_metrics` accept optional
  `corridor_types`/`support_types`, passed from the CLI's config. Bare
  calls (existing tests, ad-hoc graphs) fall back to the token rule via the
  same helper — no literal comparisons remain anywhere.
- The existing `corridor` group name is kept (the catalog and preflight
  already consume it); no `circulation` group was introduced.

## Change 2 — Legacy types removed

`configs/generic_building.yaml` drops the three legacy types from
`allowed_node_types` (35 → 32 catalog types), every semantic group, and the
visualization palette. Blockers handled:

- **`config.py`'s `Corridor` requirement** is replaced by: the contract's
  corridor group must be non-empty and every member must be in
  `allowed_node_types` (declared-group membership is validated; the group
  itself may still come from the token fallback for old-style configs, so
  compact test configs with a literal `Corridor` type keep validating).
- **Accessibility defaults remapped**: the fallback pair constants are now
  `PatientRoom → NurseStation` (primary) and `PatientRoom → MedicationRoom`
  (both in the default generated mix), in `config_contract.py` and
  `review_summary.DEFAULT_TYPED_ACCESSIBILITY_PAIRS`. The default config
  declares three pairs explicitly (NurseStation, MedicationRoom,
  OnStageCorridor — the corridor pair was already present and kept).
  Verified non-empty: 21/21 patient rooms reach a NurseStation at distance
  1 in the seed-42 artifacts.
- **Review summaries are group-derived**: `support_type_summary` takes the
  config's `clinical_support`/`staff_support` groups, reporting per-type
  counts plus per-category rollups (`support_group_counts`/`_ratios`,
  e.g. `{"clinical_support": 10, "staff_support": 2}` for a default
  candidate). Distinct categories are never collapsed; the CLAUDE.md and
  AGENTS.md guardrails were rewritten to state the group-based rule. The
  token fallback remains for group-less calls.
- **`support_mix` made group-aware (option chosen: group-aware, not
  weight 0)**: `_support_room_count` accepts the config's `support` group,
  threaded from the CLI. The metric is now meaningful again
  (~15 support rooms/graph, ratio ≈ 0.37) rather than silently zero;
  the fallback token set remains for bare calls. Group-aware was chosen
  because the threading cost was two optional parameters and it keeps a
  real, documented ranking signal instead of amputating one.
- **`diversity.py` follows config-derived pairs**: features are extracted
  for *every* typed-accessibility pair recorded in a review summary (the
  pairs are contract-derived at summary-build time), not one hardcoded
  name; `support_group_ratio.*` features were added; `DEFAULT_BIN_CONFIG`
  now bins on `support_group_ratio.clinical_support`/`staff_support` and
  `typed_access.PatientRoom_to_NurseStation.distance_mean`, and the
  corridor-fraction bin edges were retuned to the current grammar's scale
  (bins are skipped when a dimension is absent, so other configs degrade
  gracefully).
- `docs/ROOM_VOCABULARY.md` no longer has a legacy tier; it documents the
  removal, the declared-circulation rule, and the imported-floorplan
  behavior (unknown labels accepted, never matched — no migration/alias
  layer, verified by test).

## Change 3 — Catalog tier flags

`ProgramRoomTypeCatalogItem` gains `generated: bool` and
`tier: Literal["generated", "optional"]`, populated in
`build_room_type_catalog` from `grammar_created_node_types(raw_config)` —
the same raw config the catalog already loads through the shared
`resolve_suggestion_config_source()` path, so an activated variant that
generates different types flips the flags together with the vocabulary
(covered by a test that activates a Kitchen-generating variant). Both
fields are additive; `description` stays reserved/null.

Default config: **16 `generated: true` of 32 total.** (The task predicted
"16 of 35"; 35 was the pre-removal count — removing the three legacy types
is what Change 2 mandates, leaving 32.)

## Contract and docs (Change 4)

`docs/contracts/suggest-next-room-api.md`: catalog entry shape with
`generated`/`tier` and guidance to badge/de-emphasize optional types; all
request examples, the response-example `reason` strings, the semantic
matching walkthrough, and the PowerShell verification block now use
`OnStageCorridor` (and `StorageRoom` replaces a `StaffSupport` example);
the legacy-compatibility paragraph is gone, with old-export labels covered
by the unknown-labels rule. Also updated: `docs/PROGRAM_REQUIREMENTS.md`
(catalog example with flags, 32/16 counts), `README.md` (route table,
configuration section, grammar example, review-summary/diversity
descriptions), `CLAUDE.md` and `AGENTS.md` guardrails.

## Tests

12 new tests in `tests/test_vocabulary_canonicalization.py`: group-first
helper semantics (declared group beats token, token-named type outside the
group is not circulation); a `Gallery` circulation type validating
end-to-end through a declared group; ConfigError when no circulation type
exists; no legacy types in default generation; legacy floorplan labels
still accepted by `/suggest-next-room` (200, empty suggestions);
group-aware ranking support metric; review summaries keeping
clinical/staff categories separate plus token fallback; accessibility
defaults targeting NurseStation/MedicationRoom with real distances;
diversity features following config-derived pairs with no legacy keys;
catalog 16-of-32 tier flags with per-item consistency; tier flags
following an activated variant.

Updated existing tests: hand-built fixtures moved to canonical types
(`test_validators`, `test_ranking`, `test_visualize`,
`test_review_summary`'s two typed-accessibility tests,
`test_program_requirements` adjacency/corridor-capacity payloads), catalog
expectations (32 entries, no legacy), healthcare-vocabulary assertions
inverted for legacy removal. Tests that exercise the token fallback with
legacy-named graphs were deliberately kept (fallback regression coverage).

## Verification

- `python -m graph_layout_synth validate-config --config
  configs/generic_building.yaml` → `Config is valid`.
- `python -m pytest` → **349 passed** (337 before the branch).
- `python -m graph_layout_synth generate --config
  configs/generic_building.yaml --num-candidates 5 --top-k 2 --seed 42
  --output-dir outputs` → best 46 nodes / 86 edges, top candidates score
  142.0, corridor_access 1.00.
- Artifacts: all five fresh candidates contain no legacy node type (stale
  June files in git-ignored `outputs/` do — left untouched);
  `typed_accessibility_summary` has `PatientRoom → NurseStation` with
  21/21 reachable at distance 1 (plus MedicationRoom and OnStageCorridor
  pairs); `contract_summary.corridor_node_types` is
  `[OnStageCorridor, OffStageCorridor]` with reachable ranges
  PatientRoom 20–24 / OnStageCorridor 4–4; the catalog returns 16
  `generated: true` of 32 with consistent tiers.
- `git diff --check` clean. No LLM-call paths touched; the fail-if-called
  Claude guardrail tests pass unmodified.

## Deviations from the task text

- **Catalog totals**: 16 of **32**, not "16 of 35" — the 35 figure
  described the pre-removal state and is arithmetically incompatible with
  removing three legacy types.
- The default config keeps its pre-existing `PatientRoom → OnStageCorridor`
  accessibility pair alongside the two remapped pairs (removing it was not
  requested, and it carries real signal).
- `grammar_variant_assistant.py`'s room-mix fallback aliases
  (`FALLBACK_CLINICAL_TYPE`/`FALLBACK_STAFF_TYPE = ClinicalSupport/
  StaffSupport`) were left: they activate only for explicit structured
  room-mix variant requests, which may legitimately target configs that
  still declare those types. Nothing else consumes them.
- `grammar.py`'s legacy built-in expansion path still creates a literal
  `Corridor` node; it runs only for configs with no `grammar_rules` (none
  in the repo) and was left as-is — its output still validates through the
  token fallback since such configs declare no corridor group.

## Non-goals

- No `/suggest-next-room` request/response schema changes, no new
  dependencies, no LLM calls, no migration/alias layer for legacy labels,
  no frontend code.
