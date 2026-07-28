# Make `/suggest-next-room` actually use the activated variant config

Branch: `feat/active-variant-suggestion-source` → `main`

## Summary

Fixes the bug where `/suggest-next-room` kept sampling from the first config
it ever resolved, so a grammar variant activated after the first suggestion
request — including instruction-generated variants — silently never reached
the suggestion sampler until a server restart. Config-source resolution is
now re-evaluated on every request through one shared resolver used by both
the suggestion sampler and the room-type catalog, activation takes effect on
the next request with no restart, and a missing/broken active variant fails
with a controlled 400 instead of silently falling back to the base config.
The public request/response schema is unchanged, and `/suggest-next-room`
still never calls Claude in any mode.

Also updates the frontend-facing API contract, the integration and
instruction-variant docs, and expands the README route list into a
method/usage table.

## Motivation

After building a config variant from plain-language instructions and
activating it through the grammar-variant control plane, NextRoomPredictor
still received `/suggest-next-room` suggestions sampled from the previously
resolved config rather than the newly activated variant, even with
`GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant`. Because the room-type
catalog *did* reflect the new variant, the system looked inconsistent:
variants appeared registered, valid, and active, yet had no effect on
suggestions.

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

## Design

### One shared resolver, re-evaluated per request

`graph_layout_synth/api/sampling.py` now owns
`resolve_suggestion_config_source() -> SuggestionConfigSource` (frozen
dataclass: `mode`, `config_path`, `variant_id`). It is re-evaluated on every
request. In `active_variant` mode it reads the pointer via the new
`active_variant_pointer()` control-plane helper (which
`active_variant_config_path()` now delegates to — same file, same registry)
and fails explicitly when the pointer is missing, malformed, or its
validated config file no longer exists. No fallback to the base config,
ever.

`room_type_catalog.resolve_catalog_config_path()` delegates to the same
resolver and only maps mode names onto its existing public source labels
(`default_config`/`env_config`/`active_variant`), so the catalog and the
suggestion sampler can never disagree about the active config.

### Caching without staleness

`ExistingGeneratorSampler` separates explicit injection from caching:
`config` remains the injected override and always wins; otherwise the source
is re-resolved per call and the parsed `LayoutConfig` is reused only while
the resolved source (mode + path + variant) is unchanged. Activating a
different variant therefore takes effect on the next request with no
restart, while repeat requests against an unchanged variant do not re-parse
YAML. `last_resolved_config` / `last_config_source` expose what was actually
used.

Static and env-config behavior is unchanged: `static` still calls
`load_config()` with no argument (preserving existing test mocks), and the
unset-mode fallback (env-config when `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is
set, else static) is preserved.

### Observability without touching the public contract

The `/suggest-next-room` response schema is **unchanged** (the frontend's
tolerance for unknown fields is not established, and the contract is
deliberately narrow). Instead, the predictor logs
`mode`/`configPath`/`variantId` per request, and debug artifact runs record
the same triple as a `configSource` block in `aggregation_report.json`.

## Behavior matrix

| `GRAPHLAYOUTSYNTH_GRAMMAR_MODE` | `/suggest-next-room` config |
| --- | --- |
| unset | `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` if set, else default config (unchanged) |
| `static` | default base config |
| `env_config` | `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` (400 if unset) |
| `active_variant` | the currently activated valid variant, re-checked per request; controlled 400 if no valid active pointer |

`/suggest-next-room` never calls Claude in any mode.

## Documentation

- `docs/contracts/suggest-next-room-api.md` (the file copied into the
  frontend project): new dedicated "Grammar variant config source" section
  leading with the never-calls-Claude guarantee, the mode table, the
  no-restart activation behavior, the explicit-400/no-fallback rule with its
  exact `detail` string, and the new config-source causes in the HTTP 400
  list (flagged as backend deployment issues handled by the frontend's
  existing local-fallback path). Request/response types untouched.
- `docs/integration/nextroompredictor-api.md` and
  `docs/INSTRUCTION_GUIDED_VARIANTS.md`: the required
  `GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant` env var, the
  propose → validate/repair → activate → suggest flow, the no-restart note,
  and the explicit warning that non-`active_variant` modes ignore activated
  variants.
- `README.md`: the HTTP API endpoint bullet list is now a method/route/usage
  table covering every route in `server/main.py`, plus the 403 feature gate
  on `/grammar-variants/*` and OPTIONS CORS preflight behavior.

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

No test makes a live Anthropic API call; the Claude boundary is mocked via
`assistant.propose_grammar_variant_with_claude` throughout.

## Verification

- `python -m pytest -q` → 297 passed.
- `git diff --check` → clean.
- `python -m graph_layout_synth validate-config --config
  configs/generic_building.yaml` → valid (schema untouched).
- Live-server smoke test in `active_variant` mode with two hand-validated
  variants (distinctive `Lounge`/`Studio` room types), zero Claude calls:
  suggest before activation → controlled 400 `"No active grammar variant is
  configured."`; activate `variant-lounge` → catalog shows `Lounge` with
  `source: active_variant` and the suggestion run's
  `aggregation_report.json` records `configSource.variantId:
  variant-lounge`; activate `variant-studio` with **no restart** → catalog
  vocabulary switches and the next suggestion's `configSource` records
  `variant-studio`.

## Non-goals

- No change to the `/suggest-next-room` request or response schema.
- No Claude calls, generation, or variant repair during suggestions.
- No change to program-requirements validation or the room-type catalog's
  public labels.
- No second variant registry, no new generator.

## Commits

1. `5ca32b8` fix: make `/suggest-next-room` use the activated variant per
   request
2. `01f48c4` docs: update suggest-next-room contract for per-request
   active-variant resolution
3. `7c7712e` docs: give the suggestion config source its own contract
   section
4. `9da7798` docs: expand README route list into a method/usage table

## Review checklist

- [ ] `resolve_suggestion_config_source()` is the only mode-resolution
  logic; sampler and catalog both consume it (no second copy remains).
- [ ] `ExistingGeneratorSampler.config` is never written by resolution;
  injected configs still win and mock samplers in existing tests are
  unaffected.
- [ ] `active_variant` failures (no pointer, malformed pointer, deleted
  config file) surface as controlled 400s with no `load_config` call.
- [ ] Response schema and `docs/contracts/suggest-next-room-api.md`
  TypeScript types are byte-identical in shape to `main`.
- [ ] No live Claude calls in tests; `tests/conftest.py` env isolation still
  covers every service env var used here.
