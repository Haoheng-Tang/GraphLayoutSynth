# Add read-only program room-type catalog endpoint

## Summary

This PR adds a small read-only endpoint that exposes the canonical room types
of the active GraphLayoutSynth config:

```http
GET /program-requirements/room-types
```

NextRoomPredictor can populate its room-type dropdown from this catalog
instead of hard-coding backend room type names, and simple program editors
can map user-entered names to canonical IDs before validation or generation.

The endpoint is a catalog only. It never calls the LLM, never generates
graphs, never modifies variant state, does not change `/suggest-next-room`,
and does not require `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS`.

## Motivation

GraphLayoutSynth already owns the canonical room-type vocabulary: configs
define `allowed_node_types` and semantic groups, `ProgramRequirements`
validation checks room types against the live `ConfigContract`, and grammar
variants can change the vocabulary. The frontend had no way to discover that
vocabulary, so dropdowns would need hard-coded names that silently drift from
the active config — especially once an activated variant introduces new room
types.

## Response

```json
{
  "roomTypes": [
    {"id": "ClinicalSupport", "displayName": "Clinical support"},
    {"id": "Corridor", "displayName": "Corridor"},
    {"id": "PatientRoom", "displayName": "Patient room"},
    {"id": "StaffSupport", "displayName": "Staff support"}
  ],
  "source": "default_config",
  "configPath": "configs/generic_building.yaml"
}
```

- `id` is the canonical room type used by configs and `ProgramRequirements`.
- `displayName` is a humanized rendering of the ID (CamelCase split, later
  words lowercased, all-caps acronyms preserved: `ICURoom` → `ICU room`).
- `description` is reserved and currently omitted.
- `roomTypes` is deterministic: de-duplicated and sorted by `id`.
- `source` is `default_config`, `env_config`, `active_variant`, or
  `request_config`.

## Vocabulary source

IDs come from the live `ConfigContract` — the union of the `room_like` and
`corridor` semantic groups — the same contract that program-requirements
validation consumes, so there is no second source of room-type truth.

The catalog deliberately uses those groups rather than raw
`allowed_node_types`: abstract structural node types such as `BuildingFloor`
and `Zone` are not user-facing program room types and would be meaningless in
a dropdown. Catalog IDs are always a subset of `allowed_node_types`, so every
dropdown selection passes vocabulary validation. Grammar rule names are never
exposed.

## Config resolution

Resolution mirrors the `/suggest-next-room` sampler
(`GRAPHLAYOUTSYNTH_GRAMMAR_MODE`):

- `static` (default): `configs/generic_building.yaml`
- `env_config`: the path in `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG`
- `active_variant`: the validated config referenced by
  `outputs/llm_variants/active_variant.json`; a missing or invalid pointer
  fails explicitly with HTTP 400 instead of silently falling back
- mode unset: backward-compatible fallback — `env_config` when
  `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is set, otherwise `static`

So when an activated variant adds a room type, the catalog (like the
suggestion sampler) reflects the variant's vocabulary.

An optional query parameter lets developer tooling inspect a specific config:

```http
GET /program-requirements/room-types?baseConfigPath=configs/generic_building.yaml
```

This returns `source: request_config`. Unreadable or invalid configs return a
controlled HTTP 400 with a clear message.

## Implementation

- `graph_layout_synth/api/models.py`: `ProgramRoomTypeCatalogItem` and
  `ProgramRoomTypeCatalogResponse` (camel-case aliases via the shared
  `ApiModel` base).
- `graph_layout_synth/api/room_type_catalog.py`: config-path resolution
  mirroring the sampler modes, contract-derived catalog building, and the
  display-name helper. Reuses `build_config_contract`,
  `load_raw_config_mapping`, `active_variant_config_path`, and the sampler's
  env-var constants rather than duplicating any of them.
- `server/main.py`: the `GET /program-requirements/room-types` route with
  controlled 400 errors; no feature gate.

## Frontend guidance

- Populate the program editor's room-type dropdown from this endpoint.
- Map any user-entered room type names to these canonical `id` values before
  calling `POST /program-requirements/validate` or submitting requirements
  for grammar-variant proposal; arbitrary free-text names fail vocabulary
  validation.

## Documentation

- `docs/PROGRAM_REQUIREMENTS.md`: new "Room-type catalog endpoint" section
  with the response example, resolution behavior, and frontend guidance.
- `README.md`: endpoint listed in the HTTP API section plus a short summary
  in the program-requirements section.
- `AGENTS.md`: architecture bullet and a read-only guardrail (no LLM calls,
  no generation, no variant-state changes, no second vocabulary source).
- `CLAUDE.md`: endpoint added to the HTTP API layer overview.

## Tests

`tests/test_room_type_catalog.py` (11 tests) covers:

- default catalog returns 200 with non-empty, unique, sorted IDs and the
  expected default-config vocabulary
- humanized display names, including acronym handling
- IDs equal the contract's `room_like` + `corridor` union and are a subset of
  `allowed_node_types`
- endpoint works with `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS` unset
- endpoint never touches Claude code paths (stubbed to raise if called)
- active-variant mode returns the activated variant's vocabulary (a variant
  adding a `Lounge` type appears in the catalog)
- active-variant mode without a valid pointer fails explicitly with 400
- backward-compatible `env_config` fallback when only
  `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is set
- `baseConfigPath` returns that config's room types with
  `source: request_config`
- missing `baseConfigPath` target returns a controlled 400

## Verification

```txt
python -m pytest -q
236 passed, 1 warning

git diff --check
passed
```

## Non-goals

This PR does not:

- call the LLM or generate graphs
- modify active variants or any variant state
- change `/suggest-next-room` or `POST /program-requirements/validate`
- add authentication or caching
- add display-name localization or room-type descriptions
- let users register new room types through the API

## Review checklist

- [x] Catalog IDs are the canonical config/`ProgramRequirements` room types.
- [x] Vocabulary comes from the live `ConfigContract`; no duplicate source of
      truth.
- [x] Abstract structural types and grammar rule names are not exposed.
- [x] Response is deterministic, de-duplicated, and sorted.
- [x] Active-variant, env-config, default, and explicit-path resolution all
      behave like existing project config resolution.
- [x] No feature flag, no LLM calls, no graph generation.
- [x] Controlled HTTP 400 errors for unresolvable or invalid configs.
- [x] Existing tests pass unchanged.
