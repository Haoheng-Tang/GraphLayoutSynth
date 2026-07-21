# Instruction-Guided Config Variants

## Purpose

`propose-instruction-variant` (CLI) and `POST /grammar-variants/propose-from-instructions`
(HTTP) let a researcher or NextRoomPredictor submit free-form design
instructions and test whether Claude can translate them into a valid
GraphLayoutSynth YAML config variant.

They exist to answer one narrow question: given free-form instructions like
"avoid a single corridor hub" or "clinical support rooms should be near
patient rooms," can Claude express that intent using GraphLayoutSynth's
*existing* config vocabulary (`grammar_rules`, `typed_accessibility_pairs`,
`semantic_node_groups`, `room_mix_targets`, `validation`, `ranking`) well
enough to produce a config that passes deterministic validation?

This is a config-authoring aid, not a new generation, validation, or ranking
path. It does not replace `propose-grammar-variant` (structured
requirements/heuristic instructions) or the program-requirements preflight
(user-facing room-mix validation); it is a separate, narrower workflow
focused on markdown/plain-text design rules. The CLI and HTTP entry points
share one attempt/repair engine (`instruction_variant_workflow.py`) and, over
HTTP, one variant registry (`grammar_variant_control_plane.py`) — there is no
separate implementation or separate bookkeeping between the two.

## Claude proposes YAML only

**Claude may propose and, on repair attempts, revise a complete YAML config
variant. It never generates graph JSON, and it never validates, ranks,
repairs, or certifies layouts itself.** Deterministic GraphLayoutSynth code —
`validate_config`, `ConfigContract`, and the existing `generate` pipeline —
remains solely responsible for validating every proposal (initial and
repaired) and, optionally, generating graphs from the first one that
validates. If no attempt produces a valid config, every proposal is saved for
inspection but none is ever used for generation.

## Example command

```bash
python -m graph_layout_synth propose-instruction-variant \
  --instructions docs/design_instructions/inpatient_unit_rules.md \
  --base-config configs/generic_building.yaml \
  --output-dir outputs/instruction_variants/inpatient_unit_live_02 \
  --samples 25 \
  --repair-attempts 2
```

Required arguments: `--instructions`, `--base-config`, `--output-dir`.

Optional arguments:

- `--samples` (default `0`): generate this many graph samples with the
  existing `generate` pipeline, but only if some attempt's proposed config
  passes validation. `0` validates without generating any graphs.
- `--repair-attempts` (default `0`): if the initial proposal fails
  deterministic validation, send it back to Claude — together with the
  validation errors — up to this many times, stopping at the first attempt
  that validates. `0` preserves the original one-shot behavior: no repair
  call is made, and an invalid initial proposal ends the run without
  generating graphs.
- `--no-call`: write the prompt and inputs without calling Claude (see
  below). No repair calls are made either, regardless of `--repair-attempts`.
- `--model`: override the Claude model (same override already supported by
  `propose-grammar-variant`); used for both the initial and repair calls.
- `--max-tokens`, `--env-path`, `--seed`: same meaning as the equivalent
  `propose-grammar-variant`/`generate` flags.

An example instruction file is included at
`docs/design_instructions/inpatient_unit_rules.md`:

```md
# Inpatient unit design instructions

- Patient rooms should connect to corridors with door edges.
- Patient rooms may share wall edges with adjacent patient rooms.
- Clinical support rooms should be near patient rooms.
- Staff support rooms should connect to corridors but should be less frequent than patient rooms.
- Avoid a single corridor hub connecting to every patient room.
- Prefer distributed corridor segments serving local groups of patient rooms.
```

## Dry-run mode

Use `--no-call` to inspect exactly what would be sent to Claude without
making an API call or requiring `ANTHROPIC_API_KEY`:

```bash
python -m graph_layout_synth propose-instruction-variant \
  --instructions docs/design_instructions/inpatient_unit_rules.md \
  --base-config configs/generic_building.yaml \
  --output-dir outputs/instruction_variants/inpatient_unit_v1 \
  --no-call
```

This writes the submitted instructions, the base config, and the full prompt
(`llm_prompt.md`) so a researcher can read exactly what Claude would see
before spending an API call. No graphs are generated in dry-run mode
regardless of `--samples`.

## Prompt contents

The initial prompt reuses the existing grammar-variant assistant
infrastructure (`build_grammar_variant_prompt`): it embeds the base config,
the live `ConfigContract` vocabulary summary, and the standard complete-YAML
output instructions. On top of that, the instruction-guided variant prompt
adds:

- the full instruction text, verbatim, under a `# Design Instructions`
  section;
- an explicit instruction to translate each design rule into supported
  existing config concepts (grammar rules, adjacency preferences, edge-type
  probabilities via stochastic counts/choices, validation settings, ranking
  weights) rather than inventing new schema fields;
- an explicit instruction not to generate graph samples, node-link JSON, or
  any other raw graph output — only a YAML config variant.

## Validation-guided repair

If `--repair-attempts N` (`N > 0`) is set and the initial proposal fails
deterministic validation, GraphLayoutSynth sends a **repair prompt** back to
Claude asking it to correct the previous proposal. The corrected config is
validated the same way, and the loop stops at the first attempt that
validates — up to `N` repair attempts total. If every attempt (initial plus
all repairs) remains invalid, no graphs are generated and every attempt stays
saved for inspection.

Each repair prompt includes:

- the original design instructions, unchanged from the initial prompt;
- the base config and the same live `ConfigContract` schema guidance used in
  the initial prompt;
- the previous attempt's invalid YAML proposal, verbatim;
- the deterministic validation errors from that attempt's
  `config_validation_report.json`;
- a strict instruction to return a complete corrected YAML config, never a
  patch or diff, and never an invented/unsupported schema field;
- a strict instruction not to generate graph samples, node-link JSON, or any
  other raw graph output;
- a reminder that deterministic GraphLayoutSynth validation, not Claude's own
  response, decides whether the corrected config is accepted.

Repair never changes what Claude is allowed to do: it may only propose a
revised YAML config. GraphLayoutSynth's deterministic validator remains the
sole authority on acceptance, and generation still runs only after some
attempt actually validates.

## HTTP endpoint

```http
POST /grammar-variants/propose-from-instructions
```

This exposes the same instruction-guided proposal workflow to
NextRoomPredictor over HTTP, so the frontend can submit plain-language design
instructions directly. It reuses the CLI's exact attempt/repair engine
(`instruction_variant_workflow.py`) and, on a valid proposal, registers the
result as a normal entry in the *existing* grammar-variant registry
(`grammar_variant_control_plane.py`) — there is no second, independent
variant registry. A valid instruction-guided proposal is immediately visible
via `GET /grammar-variants`, inspectable via `GET /grammar-variants/{id}`,
and activatable via `POST /grammar-variants/{id}/activate`, exactly like a
variant proposed from structured requirements or heuristic instructions.

**Gating**: like every other `/grammar-variants/*` endpoint, this one
(including dry runs) requires `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true` and
returns HTTP 403 otherwise — matching the existing `POST /grammar-variants/propose`
convention, where dry runs are gated identically. `GET /health`,
`POST /suggest-next-room`, `GET /program-requirements/room-types`,
`POST /program-requirements/validate`, and server startup are entirely
unaffected by this flag and never call Claude, gated or not.

**Explicit rule: Claude is called only for this one endpoint, only when
`dryRun` is not set, and only with non-empty `instructionText`.** No other
endpoint in this service calls the LLM. In particular:

- structured program-requirement validation (`POST /program-requirements/validate`)
  is purely deterministic and never touches Claude;
- the room-type catalog (`GET /program-requirements/room-types`) is a
  read-only, config-derived lookup;
- variant listing, inspection, and activation
  (`GET /grammar-variants`, `GET /grammar-variants/{id}`,
  `POST /grammar-variants/{id}/activate`) only read and update
  `registry.json`/config files, never call Claude;
- `POST /suggest-next-room` always uses the deterministic generator and
  never calls Claude, regardless of this feature flag.

### Request

```json
{
  "name": "inpatient-unit-rules-v1",
  "instructionText": "# Inpatient unit rules\n\n- PatientRoom should connect to Corridor with door edges.\n- ClinicalSupport should be near PatientRoom groups.\n- Avoid a single Corridor node connected to every PatientRoom.",
  "repairAttempts": 2,
  "samples": 0,
  "dryRun": false
}
```

- `instructionText` (required): must be non-empty after trimming whitespace;
  empty or whitespace-only text returns a controlled HTTP 400.
- `name` (optional): a short human-readable label used for the registry's
  `heuristicSummary` instead of a truncated instruction-text summary.
- `baseConfigPath` (optional): defaults to `configs/generic_building.yaml`,
  validated with the same safe YAML loading the existing
  `POST /grammar-variants/propose` endpoint already uses for this field.
- `repairAttempts` (optional, default `0`, capped at `3`): out-of-range
  values return HTTP 400 rather than being silently clamped, matching how
  `sampleCount` is already bounded on `POST /suggest-next-room`.
- `samples` (optional, default `0`, capped at `25`): same bounded-and-rejected
  convention.
- `dryRun` (optional, default `false`): see below.

### Response

```json
{
  "status": "proposed_valid",
  "variantId": "20260721T090501123456Z-3f9a1c2d",
  "valid": true,
  "repairAttemptsUsed": 1,
  "generationRan": false,
  "artifactDir": "outputs/llm_variants/20260721T090501123456Z-3f9a1c2d",
  "attempts": [
    {"attemptIndex": 0, "kind": "initial", "valid": false, "validationErrorCount": 2, "artifactDir": "outputs/llm_variants/20260721T090501123456Z-3f9a1c2d/attempts/attempt_0_initial"},
    {"attemptIndex": 1, "kind": "repair", "valid": true, "validationErrorCount": 0, "artifactDir": "outputs/llm_variants/20260721T090501123456Z-3f9a1c2d/attempts/attempt_1_repair"}
  ],
  "errors": [],
  "warnings": []
}
```

`status` is one of `dry_run`, `proposed_valid`, `generated`,
`proposed_invalid`, or `failed`, mirroring the CLI manifest's status
vocabulary. `variantId` is the registry ID used with the existing
`GET /grammar-variants/{id}` and `POST /grammar-variants/{id}/activate`
endpoints — except for dry runs, where it is always `null` (see below).

### Dry-run behavior

With `dryRun: true`, the endpoint trims and validates the instruction text,
builds the same prompt the CLI would send, and writes
`submitted_instructions.md`, `base_config.yaml`, `llm_prompt.md`, and
`manifest.json` under a server-assigned artifact directory — **without
calling Claude and without running any repair attempts or graph
generation**. The response is always:

```json
{
  "status": "dry_run",
  "variantId": null,
  "valid": false,
  "repairAttemptsUsed": 0,
  "generationRan": false,
  "artifactDir": "outputs/llm_variants/<id>",
  "attempts": [],
  "errors": [],
  "warnings": []
}
```

`variantId` is intentionally `null` in the response even though the
artifacts live under a real, server-assigned directory: dry runs are cheap
enough that a frontend may trigger many of them while a user drafts
instructions, and they are not meant to be treated as addressable variants.

### Repair attempts over HTTP

`repairAttempts` works exactly like the CLI's `--repair-attempts`: if the
initial proposal fails deterministic validation, the invalid YAML and its
validation errors are sent back to Claude, up to `repairAttempts` times,
stopping at the first attempt that validates. If every attempt remains
invalid, the response has `valid: false` and `status: "proposed_invalid"`,
and the config is never used for generation or made activatable.

### Activation flow

1. `POST /grammar-variants/propose-from-instructions` with `dryRun: false`
   and a valid resulting config.
2. The response's `variantId` and `status: "proposed_valid"` (or
   `"generated"`) confirm the variant is registered and activatable.
3. `POST /grammar-variants/{variantId}/activate` — identical to activating
   any other variant; this call never touches Claude.
4. With `GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant`, `/suggest-next-room`
   now samples from the instruction-guided config — still without ever
   calling Claude itself.

An invalid proposal (initial or after exhausting repairs) is still saved
with a `variantId` and appears in `GET /grammar-variants` for inspection, but
`POST /grammar-variants/{variantId}/activate` returns HTTP 400 for it, the
same as any other non-`valid` record.

## Artifacts

All artifacts are written under `--output-dir` (CLI) or the server-assigned
directory under the configured variant root, typically
`outputs/llm_variants/<variantId>/` (HTTP) — the layout is identical either
way. These four are always written, whether or not Claude is called:

```txt
submitted_instructions.md   # the instruction file's exact text
base_config.yaml            # the base config used to build the prompt
llm_prompt.md                # the initial prompt sent (or that would be sent) to Claude
manifest.json                 # run metadata: inputs, status, all attempts, artifact paths
```

If Claude is called (i.e., not `--no-call` on the CLI, or `dryRun: false`
over HTTP), every attempt — the initial proposal plus any repair attempts —
gets its own subdirectory under `attempts/`:

```txt
attempts/
  attempt_0_initial/
    raw_llm_response.md
    proposed_config.yaml
    config_validation_report.json

  attempt_1_repair/
    repair_prompt.md
    raw_llm_response.md
    proposed_config.yaml
    config_validation_report.json

  attempt_2_repair/
    repair_prompt.md
    raw_llm_response.md
    proposed_config.yaml
    config_validation_report.json
```

Only repair attempts have `repair_prompt.md`; the initial attempt's prompt is
the top-level `llm_prompt.md`. The loop stops at the first valid attempt, so
later `attempt_N_repair/` directories are only created if earlier attempts
were still invalid.

Alongside the per-attempt directories, these top-level convenience copies
always reflect the **latest** attempt made (valid or not), and a summary
covering **every** attempt:

```txt
proposed_config.yaml           # copy of the latest attempt's extracted YAML
config_validation_report.json  # the latest attempt's validation report
review_summary.md              # table of every attempt plus the final outcome
```

If YAML cannot be extracted from a response at all, the run stops
immediately — no further repair attempts are made — `manifest.json` records
`status: "failed"`, and the CLI exits nonzero.

If every attempt (initial plus all repairs, if any) fails deterministic
validation, `manifest.json` records `status: "proposed_invalid"`, and **no
graphs are generated** regardless of `--samples`. The CLI exits nonzero.

If some attempt validates, `manifest.json` records `status: "proposed_valid"`
(or `"generated"` once graphs are produced). Graph samples are always
optional and deterministic: they are requested explicitly (`--samples` /
`samples`, both defaulting to `0`), never generated unless a proposal
actually validates, and produced by the same seeded, deterministic `generate`
pipeline used everywhere else in GraphLayoutSynth — Claude has no part in
generation or in deciding whether it runs. If `--samples > 0` (CLI) or
`samples > 0` (HTTP), graph samples are generated by directly reusing the
existing `generate` CLI pipeline (`run_generate`) against the top-level
`proposed_config.yaml` — no new generator code is introduced. Outputs use
the same conventions as `generate` (`candidate_<n>.json`, `ranking_report.json`,
`best_candidate.json`, etc.) under `<output-dir>/generated_samples/` (CLI) or
`<artifactDir>/generated_samples/` (HTTP).

`manifest.json` also records `repairAttemptsRequested`, `repairAttemptsUsed`,
`generationRan`, and an `attempts` list (one entry per attempt, with its
`index`, `kind` (`"initial"` or `"repair"`), `isValid`, and artifact paths) so
the full repair history is reviewable without re-reading every file.

## Limitations

- This workflow is a config-authoring aid. It does not integrate with
  `ProgramRequirements`/program-requirements preflight (that remains a
  separate, user-facing room-mix validation flow), and it does not implement
  any frontend UI — this branch is backend-only; NextRoomPredictor's own
  instruction-submission UI, if any, is out of scope here.
- Only markdown/plain-text instruction files (CLI) or plain-text
  `instructionText` (HTTP) are supported; there is no PDF ingestion.
- There is no separate rule parser — instruction interpretation is entirely
  Claude's proposal, checked only by GraphLayoutSynth's existing
  deterministic validator and `ConfigContract` consistency checks. A
  passing validation result means the config is schema-valid and internally
  consistent, not that it faithfully captures every instruction.
- Repair only gives Claude another chance to pass the same deterministic
  checks; it does not add reasoning, retries with different strategies, or
  guarantee convergence. `--repair-attempts`/`repairAttempts` bounds the
  number of Claude calls, not the quality of the result — a proposal can
  still be invalid after every attempt is exhausted.
- The HTTP endpoint is synchronous: a live request blocks until Claude
  responds (and, if requested, until generation finishes). There is no
  background job queue or polling in this branch.
- As with `propose-grammar-variant`, tests for this workflow mock the Claude
  call; no live API calls are made in the test suite.

## See also

- `docs/GRAMMAR_CONFIG_SKILLS.md`: the schema reference given to Claude for
  any config-variant proposal.
- `propose-grammar-variant` / `POST /grammar-variants/propose`: the
  structured-requirements/heuristic-instructions workflow this feature's
  prompt-building code is built on, and the registry this feature's valid
  proposals become normal entries in.
- `docs/PROGRAM_REQUIREMENTS.md`: the separate, user-facing program-requirements
  preflight (room-mix min/target/max, not free-form design instructions).
- `docs/contracts/suggest-next-room-api.md`: how an activated variant (from
  either grammar-variant proposal flow) affects `/suggest-next-room` sampling
  once `GRAPHLAYOUTSYNTH_GRAMMAR_MODE=active_variant` is set — that endpoint
  itself never calls Claude.
