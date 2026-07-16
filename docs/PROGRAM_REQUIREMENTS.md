# Program Requirements and Preflight Validation

GraphLayoutSynth separates user-facing program inputs from backend/internal
generation constraints, and validates user programs deterministically before
any LLM call or generation attempt.

Important: this feature does not change graph generation behavior yet. It
adds validation only. Generation still uses the normal YAML config and
grammar rules; program-conditioned generation is future work.

## Two separate models

### `ProgramRequirements` (user-facing)

Real design/program decisions:

- room type
- minimum room count
- target room count
- maximum room count
- optional high-level adjacency preferences

V1 user-facing fields:

- `schemaVersion` (must be `1`)
- `program.roomMix`
- `roomMix.<roomType>.min`
- `roomMix.<roomType>.target`
- `roomMix.<roomType>.max`
- `adjacencyPreferences[]` with `source`, `target`, `edgeType` (`door` or
  `wall`), and `priority` (`required`, `preferred`, or `avoid`)

Deliberately excluded from v1:

- **Area, width, and height.** V1 prediction and generation are graph-only;
  geometry stays with the frontend. Accepting dimensional fields would imply
  geometric semantics the backend does not have, so they are rejected as
  unsupported fields.
- **Cluster or local-group fields.** Concepts like cluster count, cluster
  size, max patient rooms per cluster, or corridor degree limits are
  procedural search parameters. They do not yet have a stable user-facing
  semantic meaning such as zone, department, pod, or nursing unit, so users
  are never asked for them.
- **Generation-search parameters** such as relaxation limits.

### `GenerationConstraintProfile` (backend/internal)

Procedural search/generation parameters used by the preflight validator and
future generation algorithms:

- `locality.patientRoomGroupSize` (`min`, `preferredMax`, `hardMax`)
- `locality.localGroupCount` (`min`, `preferredMax`, `hardMax`)
- `corridors.avoidSingleHubCorridor`
- `corridors.corridorDegree` (`preferredMax`, `hardMax`)
- `corridors.allowCorridorChains`
- `generation.maxRelaxationSteps`

The built-in default profile is mirrored in
`configs/program_constraint_profiles/default_healthcare.yaml`. Partial
profiles are valid; omitted sections use built-in defaults. Setting
`generation.maxRelaxationSteps: 0` disables internal relaxation, which turns
preferred-bound violations into hard errors. The corridor booleans are
carried for future generation algorithms.

Config-reachable room-count ranges are deliberately not duplicated here:
they remain derived from the active YAML config through `ConfigContract`
(`room_mix_reachable_ranges`), and the preflight reuses that logic.

## Feasibility states

The preflight distinguishes:

- `feasible`: satisfiable within preferred internal bounds.
- `feasible_with_relaxation`: satisfiable only after relaxing preferred
  internal bounds, but still within hard bounds. Reported through warnings
  such as `PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED`.
- `infeasible`: not satisfiable under hard internal bounds (or the
  requirements themselves are invalid). For example, a `PatientRoom` minimum
  of 51 against a hard backend capacity of 28 fails with a clear error
  before any LLM call.

Config reachability is reported separately as warnings (for example
`ROOM_COUNT_NOT_REACHABLE_BY_CURRENT_CONFIG`): a program can be feasible in
principle under backend constraints while the current static grammar cannot
reach it. That is exactly the case an LLM-proposed config variant is for, so
reachability warnings do not block variant proposal; errors do.

User-facing error messages avoid internal cluster/group language; suggestions
read like "Reduce PatientRoom count or allow a larger layout/generation
scope." Internal capacities appear only in `debugDetails`.

## YAML example

See `docs/program_requirements/example_healthcare_program.yaml`:

```yaml
schemaVersion: 1

program:
  roomMix:
    PatientRoom:
      min: 50
      target: 56
      max: 60
    ClinicalSupport:
      min: 6
      target: 8
      max: 10

adjacencyPreferences:
  - source: PatientRoom
    target: Corridor
    edgeType: door
    priority: required
```

## JSON example

JSON parses into the same model; see
`docs/program_requirements/example_healthcare_program.json`:

```json
{
  "schemaVersion": 1,
  "program": {
    "roomMix": {
      "PatientRoom": { "min": 50, "target": 56, "max": 60 }
    }
  },
  "adjacencyPreferences": [
    { "source": "PatientRoom", "target": "Corridor", "edgeType": "door", "priority": "required" }
  ]
}
```

## CLI validation

```bash
python -m graph_layout_synth validate-program-requirements \
  --requirements docs/program_requirements/example_healthcare_program.yaml \
  --base-config configs/generic_building.yaml
```

With an explicit internal constraint profile and a JSON report:

```bash
python -m graph_layout_synth validate-program-requirements \
  --requirements docs/program_requirements/example_healthcare_program.yaml \
  --base-config configs/generic_building.yaml \
  --constraints configs/program_constraint_profiles/default_healthcare.yaml \
  --output outputs/program_requirements_validation_report.json
```

The command exits nonzero when validation has errors. Room types and edge
types are validated against the base config vocabulary through the live
config contract; unknown types are errors, never silently invented.

## HTTP validation endpoint

```http
POST /program-requirements/validate
```

Request fields: `programRequirements` (required), optional `baseConfigPath`,
optional inline `constraintProfile`. Response fields: `valid`, `feasibility`,
`errors`, `warnings`, where each issue has `code`, `severity`, `message`, and
optional `path`, `suggestion`, and `debugDetails`.

This endpoint never calls the LLM and never generates graphs, so it is safe
for frontend preflight validation. It requires no feature flag.

## Room-type catalog endpoint

```http
GET /program-requirements/room-types
```

A read-only catalog of the canonical user-facing room types derived from the
active config vocabulary, so frontend dropdowns never hard-code room type
names. IDs come from the live `ConfigContract` (the `room_like` and
`corridor` semantic groups) — the same vocabulary source program-requirements
validation uses — so there is no second source of room-type truth. Abstract
structural node types such as `BuildingFloor` and `Zone` are not included.

Example response:

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

`roomTypes` is deterministic, de-duplicated, and sorted by `id`. `description`
is reserved and currently omitted. `source` is one of `default_config`,
`env_config`, `active_variant`, or `request_config`.

Config resolution follows the `/suggest-next-room` sampler: with
`GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant` and a valid active pointer the
catalog reflects the activated variant's vocabulary; `env_config` uses
`GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG`; otherwise the default base config is
used, including the backward-compatible fallback when the mode is unset. In
active-variant mode a missing pointer fails explicitly with HTTP 400. An
optional `?baseConfigPath=...` query parameter lets developer tooling inspect
a specific config (`source: request_config`); an unreadable config returns a
controlled HTTP 400.

Frontend guidance: populate the program editor's room-type dropdown from this
endpoint, and map any user-entered room type names to these canonical `id`
values before calling `POST /program-requirements/validate` or submitting
requirements for variant proposal. Arbitrary free-text room types will fail
vocabulary validation.

Like the validation endpoint, the catalog never calls the LLM, never
generates graphs, never modifies variant state, and requires no feature flag.

## Integration with grammar-variant proposal

Both the CLI (`propose-grammar-variant --program-requirements ...`, with
optional `--program-constraints`) and the HTTP control plane
(`POST /grammar-variants/propose` with `programRequirements` and optional
`constraintProfile`) run the preflight before any Claude call:

- validation errors produce a controlled failure and no LLM call;
- warnings are saved and the proposal continues;
- validated requirements are added to the prompt as deterministic
  design-intent text.

Variant proposal artifacts then include the submitted requirements, the
normalized requirements YAML, the constraint profile used, and the validation
report (`submitted_program_requirements.json`, `program_requirements.yaml`,
`program_constraint_profile.yaml`, `program_requirements_validation.json`),
plus a compact `programRequirementsValidation` summary in `metadata.json`.
Program requirements remain optional; heuristic-only proposals are unchanged.
