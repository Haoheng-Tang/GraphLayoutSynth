# Expose semantic groups on the room-type catalog

Branch: `feat/catalog-semantic-groups` → `main`

## Summary

Adds `groups: list[str]` to `ProgramRoomTypeCatalogItem`, listing every semantic group the active config assigns to each room type, verbatim and sorted. The frontend needs semantic role — circulation, clinical support, staff support — to decide corridor auto-extension and default room depth. Without it, it falls back to matching substrings in the type ID, which works accidentally for `OnStageCorridor`/`OffStageCorridor`, misclassifies `NurseStation` as a full-depth room instead of a support room, and breaks completely for a variant naming circulation `Spine`, `Racetrack`, or `Hallway`. The backend already models roles in `semantic_node_groups`; this publishes them.

This is the third branch closing the same class of bug: structural meaning carried by spelling instead of declaration. `feat/vocabulary-canonicalization` made group membership authoritative for `is_corridor_node_type` inside the backend; this makes the same declaration available across the API boundary.

## The change

`ProgramRoomTypeCatalogItem` gains `groups: list[str]` (default `[]`, never `None`), populated in `build_room_type_catalog` by a new `_groups_by_room_type` helper that inverts the contract's `semantic_node_groups` into a per-type sorted list. It resolves through the same `resolve_suggestion_config_source()` path as `generated`/`tier` and the vocabulary itself, so an activated variant's groups are reflected and the catalog can never disagree with the suggestion sampler.

Deliberately **raw group names, not computed flags**: no `isCirculation` boolean and no collapse into a single `role`. The frontend will add role consumers over time (depth, auto-extension, grouping, badges) and a computed flag would need a sibling for each one. Group names are config-defined rather than a fixed enum, so clients treat unknown names as opaque. A type may belong to several groups and all are returned; the field is additive and existing clients that read only `id`/`displayName` are unaffected.

## Actual output (static mode, default config)

```json
{"id": "OnStageCorridor", "displayName": "On stage corridor", "description": null, "generated": true, "tier": "generated", "groups": ["corridor"]}
{"id": "NurseStation",    "displayName": "Nurse station",     "description": null, "generated": true, "tier": "generated", "groups": ["clinical_support", "room_like", "support"]}
{"id": "PatientRoom",     "displayName": "Patient room",      "description": null, "generated": true, "tier": "generated", "groups": ["patient", "patient_care", "room_like"]}
```

`NurseStation` now carries `clinical_support`/`support`, which is the datum the frontend was missing when it sized nurse stations as full-depth rooms. Corridor types report `corridor` but *not* `room_like` — the default config's `room_like` group excludes circulation, and the catalog is the union of the two, so both still appear as entries.

## Tests

7 new tests in `tests/test_catalog_semantic_groups.py`: exact sorted group lists for `OnStageCorridor`, `OffStageCorridor`, `NurseStation`, `PatientRoom`, `Stair`, `StaffLounge`, and `Kitchen` (drift fails loudly, matching the style of `test_catalog_reports_generated_and_optional_tiers`); every catalog entry has a sorted, de-duplicated, non-empty group list; multi-group types return every group (`StorageRoom` is both `building_service` and `support`); the model default is `[]` rather than `null`; an activated variant renaming circulation to `Spine` still reports `groups: ["corridor"]` — the case substring matching cannot handle, asserted alongside the fact that `"corridor" not in "Spine".lower()`; a variant declaring a custom `isolation_capable` group passes it through verbatim; and a consistency test asserting the set of types the catalog calls circulation is exactly the set `is_corridor_node_type` calls circulation, guarding against a second source of role truth appearing.

## Verification

- `python -m graph_layout_synth validate-config --config configs/generic_building.yaml` → `Config is valid`.
- `python -m pytest` → **356 passed** (349 before the branch).
- `git diff --check` → clean.
- Live server in `static` mode → catalog JSON above, `source: default_config`, 32 entries.
- No LLM-call paths touched; the fail-if-called Claude guardrail tests pass unmodified.

## Docs

`docs/contracts/suggest-next-room-api.md` documents `groups` on the catalog entry shape, states that group names are config-defined rather than a fixed enum and must be treated as opaque when unrecognized, and spells out that roles come from `groups` and never from substrings in the `id` (with the `NurseStation` and `Spine` cases named). `docs/PROGRAM_REQUIREMENTS.md` updates the catalog example with real group lists and the contract paragraph. `docs/ROOM_VOCABULARY.md` gains an explicit statement that `semantic_node_groups` is the declared source of role-based behavior in both repos, with the rule that neither side may infer role from spelling. `AGENTS.md`'s catalog bullet records the field and the no-computed-flags constraint.

## Non-goals

- No `/suggest-next-room` request/response schema change, no new dependencies, no LLM calls, no frontend code.
- No computed role flags or single-`role` collapse; raw group names only.
- `resolve_suggestion_config_source()` remains the single resolver.

## Unrelated change included

`docs/PR/vocabulary-canonicalization.md` was reflowed from hard-wrapped ~72-column prose to one line per paragraph and bullet, at the user's request. Content is unchanged; only line breaks within paragraphs differ.
