# Instruction-Guided Config Variants

## Purpose

`propose-instruction-variant` is a research CLI workflow for testing whether
Claude can translate researcher-written design instructions (a markdown or
text file of design rules) into a valid GraphLayoutSynth YAML config variant.

It exists to answer one narrow question: given free-form instructions like
"avoid a single corridor hub" or "clinical support rooms should be near
patient rooms," can Claude express that intent using GraphLayoutSynth's
*existing* config vocabulary (`grammar_rules`, `typed_accessibility_pairs`,
`semantic_node_groups`, `room_mix_targets`, `validation`, `ranking`) well
enough to produce a config that passes deterministic validation?

This is a config-authoring aid, not a new generation, validation, or ranking
path. It does not replace `propose-grammar-variant` (structured
requirements/heuristic instructions) or the program-requirements preflight
(user-facing room-mix validation); it is a separate, narrower experiment
focused on markdown-style design rules.

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

## Artifacts

All artifacts are written under `--output-dir`. These four are always
written, whether or not Claude is called:

```txt
submitted_instructions.md   # the instruction file's exact text
base_config.yaml            # the base config used to build the prompt
llm_prompt.md                # the initial prompt sent (or that would be sent) to Claude
manifest.json                 # run metadata: inputs, status, all attempts, artifact paths
```

If Claude is called (i.e., not `--no-call`), every attempt — the initial
proposal plus any repair attempts — gets its own subdirectory under
`attempts/`:

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
(or `"generated"` once graphs are produced). If `--samples > 0`, graph
samples are generated by directly reusing the existing `generate` CLI
pipeline (`run_generate`) against the top-level `proposed_config.yaml` — no
new generator code is introduced. Outputs use the same conventions as
`generate` (`candidate_<n>.json`, `ranking_report.json`, `best_candidate.json`,
etc.) under `<output-dir>/generated_samples/`.

`manifest.json` also records `repairAttemptsRequested`, `repairAttemptsUsed`,
`generationRan`, and an `attempts` list (one entry per attempt, with its
`index`, `kind` (`"initial"` or `"repair"`), `isValid`, and artifact paths) so
the full repair history is reviewable without re-reading every file.

## Limitations

- This workflow is a config-authoring experiment, not a production feature.
  It does not integrate with `ProgramRequirements`/program-requirements
  preflight, does not activate proposed variants through the grammar-variant
  control plane, and does not affect `/suggest-next-room`.
- Only markdown/plain-text instruction files are supported; there is no PDF
  ingestion.
- There is no separate rule parser — instruction interpretation is entirely
  Claude's proposal, checked only by GraphLayoutSynth's existing
  deterministic validator and `ConfigContract` consistency checks. A
  passing validation result means the config is schema-valid and internally
  consistent, not that it faithfully captures every instruction.
- Repair only gives Claude another chance to pass the same deterministic
  checks; it does not add reasoning, retries with different strategies, or
  guarantee convergence. `--repair-attempts` bounds the number of Claude
  calls, not the quality of the result — a proposal can still be invalid
  after every attempt is exhausted.
- As with `propose-grammar-variant`, tests for this workflow mock the Claude
  call; no live API calls are made in the test suite.

## See also

- `docs/GRAMMAR_CONFIG_SKILLS.md`: the schema reference given to Claude for
  any config-variant proposal.
- `propose-grammar-variant`: the structured-requirements/heuristic-instructions
  workflow this command's prompt-building code is built on.
- `docs/PROGRAM_REQUIREMENTS.md`: the separate, user-facing program-requirements
  preflight (room-mix min/target/max, not free-form design instructions).
