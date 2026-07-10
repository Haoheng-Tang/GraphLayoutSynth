# Add deterministic program-requirements preflight validation

## Summary

This PR prepares GraphLayoutSynth for user-supplied program data by
separating user-facing program inputs from backend/internal generation
constraints, and by adding a deterministic, LLM-independent preflight
validator that checks whether a requested program can be satisfied under the
active backend constraints and the active config vocabulary.

Infeasible or invalid requirements now fail with clear structured errors
before any LLM call or generation attempt.

This PR validates only. Graph generation behavior, deterministic ranking,
and `/suggest-next-room` semantics are unchanged.

## Motivation

Users should express real design/program decisions: room types, minimum,
target, and maximum room counts, and optional high-level adjacency
preferences. They should not be asked for procedural search parameters such
as cluster counts, cluster sizes, max patient rooms per cluster, or corridor
degree limits. Those concepts are internal and do not yet have a stable
user-facing semantic meaning such as zone, department, pod, or nursing unit.

Without a preflight, impossible programs would reach the LLM grammar-variant
assistant or future program-conditioned generation and fail late, expensively,
and with unclear errors.

## Design

### User-facing `ProgramRequirements`

`graph_layout_synth/program_requirements.py` defines the canonical v1 schema:

```yaml
schemaVersion: 1

program:
  roomMix:
    PatientRoom:
      min: 50
      target: 56
      max: 60

adjacencyPreferences:
  - source: PatientRoom
    target: Corridor
    edgeType: door
    priority: required
```

- `edgeType` is `door` or `wall`; `priority` is `required`, `preferred`, or
  `avoid`.
- YAML and JSON files parse into the same model.
- Unknown fields are rejected with `UNSUPPORTED_FIELD` errors. In particular,
  `area`, `width`, `height`, and cluster/group/degree fields are not part of
  the v1 user-facing schema: v1 is graph-only (geometry stays with the
  frontend) and internal search parameters are not user vocabulary.

### Backend/internal `GenerationConstraintProfile`

`graph_layout_synth/generation_constraint_profile.py` defines internal
constraints with preferred and hard bounds:

```yaml
schemaVersion: 1

locality:
  patientRoomGroupSize: {min: 4, preferredMax: 8, hardMax: 12}
  localGroupCount: {min: 1, preferredMax: 8, hardMax: 20}

corridors:
  avoidSingleHubCorridor: true
  corridorDegree: {preferredMax: 8, hardMax: 16}
  allowCorridorChains: true

generation:
  maxRelaxationSteps: 3
```

Partial profiles overlay built-in defaults. The repository default lives at
`configs/program_constraint_profiles/default_healthcare.yaml`. Setting
`maxRelaxationSteps: 0` disables internal relaxation, turning preferred-bound
violations into hard errors. The corridor booleans are carried for future
generation algorithms.

Config-reachable room-count ranges are deliberately not duplicated in the
profile. They remain derived from the active YAML config through
`ConfigContract`: the existing private room-mix range computation was
generalized into public `reachable_room_count_ranges` and
`grammar_created_node_types` helpers in `config_contract.py`, and the
existing behavior delegates to them unchanged.

### Preflight validator

`graph_layout_synth/program_preflight.py` combines:

1. local field validation (schema version, count windows, adjacency fields)
2. config vocabulary validation (room types and edge types against the live
   contract)
3. arithmetic feasibility against internal bounds (patient-group capacity,
   corridor connection capacity)
4. reachability against the current static grammar config

The result is structured:

```json
{
  "valid": true,
  "feasibility": "feasible_with_relaxation",
  "errors": [],
  "warnings": [
    {
      "code": "PREFERRED_PATIENT_GROUP_SIZE_EXCEEDED",
      "severity": "warning",
      "message": "PatientRoom count 56 requires larger local groups than preferred, but remains feasible under hard backend bounds.",
      "path": "program.roomMix.PatientRoom",
      "suggestion": "Reduce PatientRoom count or allow a larger layout/generation scope.",
      "debugDetails": {"preferredCapacity": 64, "hardCapacity": 240}
    }
  ]
}
```

Feasibility states:

- `feasible`: satisfiable within preferred internal bounds.
- `feasible_with_relaxation`: satisfiable only after relaxing preferred
  internal bounds, but still within hard bounds.
- `infeasible`: not satisfiable under hard internal bounds, or the
  requirements themselves are invalid.

Non-solution detection is arithmetic, not a constraint solver. For example,
`PatientRoom.min = 51` against `patientRoomGroupSize.hardMax = 7` and
`localGroupCount.hardMax = 4` fails with:

```txt
PatientRoom minimum count is 51, but backend hard constraints allow at most
28 PatientRoom rooms. The system cannot generate a valid solution under
current constraints.
```

User-facing messages and suggestions avoid internal cluster/group language;
internal capacities appear only in `debugDetails`.

Reachability is reported as warnings, not errors: a program can be feasible
in principle under backend constraints while the current static grammar
cannot reach it. That is exactly the case an LLM-proposed config variant is
for, so reachability warnings do not block variant proposal.

## CLI

```bash
python -m graph_layout_synth validate-program-requirements \
  --requirements docs/program_requirements/example_healthcare_program.yaml \
  --base-config configs/generic_building.yaml \
  --constraints configs/program_constraint_profiles/default_healthcare.yaml \
  --output outputs/program_requirements_validation_report.json
```

`--constraints` and `--output` are optional; without `--constraints` the
built-in default profile is used. The command exits nonzero when validation
has errors.

## HTTP

```http
POST /program-requirements/validate
```

Request: `programRequirements` (required), optional `baseConfigPath`,
optional inline `constraintProfile`. Response: `valid`, `feasibility`,
`errors`, `warnings`.

The endpoint never calls the LLM, never generates graphs, and requires no
feature flag, so it is safe for frontend preflight validation.

## Grammar-variant proposal integration

Both proposal paths run the preflight before any Claude call when program
requirements are supplied:

- CLI: `propose-grammar-variant --program-requirements <file>` with optional
  `--program-constraints <file>`.
- HTTP: `POST /grammar-variants/propose` with `programRequirements` and
  optional `constraintProfile`.

Behavior:

- validation errors produce a controlled failure and no LLM call (the HTTP
  path records a `failed` registry entry; the CLI exits nonzero);
- warnings are saved and the proposal continues;
- validated requirements are added to the prompt as deterministic
  design-intent text;
- program requirements remain optional, so heuristic-only proposals are
  unchanged.

Variant artifact directories gain, when program requirements are used:

- `submitted_program_requirements.json`
- `program_requirements.yaml` (normalized)
- `program_constraint_profile.yaml` (profile used)
- `program_requirements_validation.json`
- a compact `programRequirementsValidation` summary in `metadata.json`

The CLI writes sibling artifacts next to `--output-config`
(`<stem>_program_requirements.yaml`, `<stem>_program_validation.json`).

## Test isolation fix

The audit surfaced a pre-existing cross-test pollution bug: tests that run
the proposal flow with the default `env_path` load the developer's real
`.env.local` through `load_llm_environment`, which writes keys directly into
`os.environ`. With `GRAPHLAYOUTSYNTH_GRAMMAR_MODE` present in `.env.local`,
later sampler tests failed. This reproduces without this branch's changes.

`tests/conftest.py` now clears the nine service environment variables
(`ANTHROPIC_API_KEY`, the `GRAPHLAYOUTSYNTH_*` variables, and
`NEXT_ROOM_ALLOWED_ORIGINS`) before every test. Tests are hermetic against
the real shell environment and `.env.local`; runtime behavior is untouched.

## Documentation

- `docs/PROGRAM_REQUIREMENTS.md`: the user/internal split, v1 fields and
  exclusions (why area/width/height and cluster fields are excluded), YAML
  and JSON examples, CLI and HTTP usage, feasibility-state semantics, and the
  no-generation-change warning.
- `docs/program_requirements/example_healthcare_program.yaml` and `.json`:
  runnable examples. The corridor counts intentionally trigger a
  reachability warning against the default config to illustrate the variant
  workflow.
- README, AGENTS.md, and CLAUDE.md updated with the new command, endpoint,
  modules, and guardrails.

## Tests

New coverage (33 tests):

- valid YAML and JSON requirements parse into identical models
- invalid schema version, negative counts, and min/target/max inconsistency
  fail
- `area`, `width`, `height`, and other unsupported fields are rejected
- invalid adjacency edge types, priorities, and conflicting preferences fail
- unknown room types fail against the base config vocabulary (roomMix and
  adjacency source/target)
- feasible within preferred bounds; feasible only with relaxation; infeasible
  under hard bounds, including the 51-versus-28 capacity example with
  cluster-free messaging
- `maxRelaxationSteps: 0` escalates relaxation warnings to errors
- corridor connection hard-capacity errors
- reachable-range and not-created-by-grammar warnings
- constraint profile defaults, file round-trip, partial overlay, unknown
  fields, and inconsistent bounds
- CLI validation command (report writing, constraints file, nonzero exit)
- HTTP validation endpoint (feasible, infeasible, bad config path, bad
  profile)
- CLI and HTTP variant proposal block the LLM call on preflight errors and
  save preflight artifacts; successful proposals embed program design intent

## Verification

```txt
python -m pytest -q
209 passed, 1 warning

python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs\generic_building.yaml.

python -m graph_layout_synth validate-program-requirements \
  --requirements docs/program_requirements/example_healthcare_program.yaml \
  --base-config configs/generic_building.yaml
Feasibility: feasible. (two expected reachability warnings; exit 0)

git diff --check
passed; only LF-to-CRLF working-copy warnings were printed
```

An additional in-process FastAPI smoke script verified:

- `GET /health`
- `POST /suggest-next-room` unchanged response shape
- feasible and infeasible `POST /program-requirements/validate` responses
- HTTP 400 for an invalid inline constraint profile
- heuristic-only dry-run proposal unchanged
- infeasible `programRequirements` blocking the Claude call with a stubbed
  LLM boundary that raises when touched

## Non-goals

This PR does not:

- implement frontend UI
- expose area, width, or height in v1 user-facing requirements
- expose cluster-count or cluster-size fields as user-facing inputs
- support CSV or Excel input
- change graph generation, ranking, or diversity behavior
- change `/suggest-next-room` requests, responses, or edge-type-aware
  suggestions
- implement a full constraint solver
- make the LLM solve impossible requirements
- implement square-room geometry or two-hop matching

## Review checklist

- [x] User-facing schema contains only room types, count windows, and
      adjacency preferences.
- [x] Internal constraint concepts never appear in user-facing fields or
      user-facing error text.
- [x] Preflight runs before every LLM variant proposal that supplies
      program requirements; errors block, warnings continue.
- [x] Validation endpoint is LLM-free, generation-free, and ungated.
- [x] Reachability reuses `ConfigContract` instead of duplicating grammar
      assumptions.
- [x] Heuristic-only proposals and `/suggest-next-room` are unchanged.
- [x] Tests are hermetic against the developer's real environment.
- [x] Full test suite passes.
