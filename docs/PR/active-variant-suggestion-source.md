# Fix: `/suggest-next-room` must actually use the activated variant config

Branch: `feat/active-variant-suggestion-source`

## Problem

After building a config variant from plain-language instructions and
activating it through the grammar-variant control plane, NextRoomPredictor
still received `/suggest-next-room` suggestions sampled from the previously
resolved config rather than the newly activated variant, even with
`GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant`.

## Root cause

`ExistingGeneratorSampler.resolved_config()` cached its first resolution by
writing the loaded config into `self.config` — the same field used for
explicit config injection — and returned that field unconditionally on every
later call. Because `create_app()` builds one `NextRoomPredictor` (and one
sampler) stored in `app.state.predictor` for the whole server process, the
config source was resolved exactly once, at the first suggestion request.
Activating a variant afterwards updated `active_variant.json` on disk but
never reached the sampler until a server restart.

The room-type catalog (`GET /program-requirements/room-types`) resolved its
config fresh on every request through its own copy of the mode-resolution
logic, so the catalog could report the new variant's vocabulary while
suggestions still sampled the old config — the exact "variants appear
inactive" symptom.

The activation pointer machinery itself was correct: `activate_variant`
writes `active_variant.json` with `variantId` + `validatedConfigPath`, and
instruction-generated variants register in the same `registry.json` with
`status: "valid"`, so `POST /grammar-variants/{id}/activate` works uniformly
for both proposal flows. No second registry was involved or added.

## Fix

- `graph_layout_synth/api/sampling.py` now owns one shared resolver,
  `resolve_suggestion_config_source() -> SuggestionConfigSource` (frozen
  dataclass: `mode`, `config_path`, `variant_id`). It is re-evaluated on
  every request. In `active_variant` mode it reads the pointer via the new
  `active_variant_pointer()` control-plane helper (which
  `active_variant_config_path()` now delegates to — same file, same
  registry) and fails explicitly when the pointer is missing, malformed, or
  its validated config file no longer exists. No fallback to the base
  config, ever.
- `ExistingGeneratorSampler` separates explicit injection from caching:
  `config` remains the injected override and always wins; otherwise the
  source is re-resolved per call and the parsed `LayoutConfig` is reused
  only while the resolved source (mode + path + variant) is unchanged.
  Activating a different variant therefore takes effect on the next request
  with no restart, while repeat requests against an unchanged variant do not
  re-parse YAML. `last_resolved_config` / `last_config_source` expose what
  was actually used.
- `room_type_catalog.resolve_catalog_config_path()` now delegates to the
  same resolver and only maps mode names onto its existing public source
  labels (`default_config`/`env_config`/`active_variant`), so catalog and
  sampler can never disagree.
- Observability without touching the public contract: the
  `/suggest-next-room` response schema is **unchanged** (the frontend's
  tolerance for unknown fields is not established, and the contract is
  deliberately narrow). Instead, the predictor logs
  `mode`/`configPath`/`variantId` per request, and debug artifact runs
  record the same triple as a `configSource` block in
  `aggregation_report.json`.

Static and env-config behavior is unchanged: `static` still calls
`load_config()` with no argument (preserving existing test mocks), and the
unset-mode fallback (env-config when `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is
set, else static) is preserved.

## Behavior matrix

| `GRAPHLAYOUTSYNTH_GRAMMAR_MODE` | `/suggest-next-room` config |
| --- | --- |
| unset | `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` if set, else default config (unchanged) |
| `static` | default base config |
| `env_config` | `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` (400 if unset) |
| `active_variant` | the currently activated valid variant, re-checked per request; controlled 400 if no valid active pointer |

`/suggest-next-room` never calls Claude in any mode.

## Tests

New `tests/test_active_variant_suggestion_source.py` (10 tests), all through
one persistent app instance where relevant:

- activated variant config is the one loaded by the sampler in
  `active_variant` mode;
- **restart regression**: activate variant A → suggest → activate variant B
  → suggest, asserting the second request loads B (fails against the old
  permanent cache);
- unchanged active variant reuses the parsed config (no per-request YAML
  reload);
- missing pointer and missing validated-config-file cases return controlled
  400s with no `load_config` call (no silent fallback);
- an instruction-generated variant (`propose-from-instructions`, mocked
  Claude) activates through the shared registry and drives suggestions;
- `/suggest-next-room` never calls Claude in unset/static/env_config/
  active_variant modes;
- catalog and suggestion sampler resolve the same active config path;
- sampler unit tests: pointer re-check per `sample()` call and injected
  `config=` override still winning (with `sampler.config` no longer
  clobbered by resolution — one existing assertion in
  `test_matching_node_neighbor_aggregation.py` updated to the new
  `last_resolved_config` accessor accordingly).

Full suite: 297 passed.

## Non-goals

- No change to the `/suggest-next-room` request or response schema.
- No Claude calls, generation, or variant repair during suggestions.
- No change to program-requirements validation or the room-type catalog's
  public labels.
- No second variant registry, no new generator.
