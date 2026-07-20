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

**Claude may propose a complete YAML config variant. It never generates graph
JSON, and it never validates, ranks, repairs, or certifies layouts.**
Deterministic GraphLayoutSynth code — `validate_config`, `ConfigContract`,
and the existing `generate` pipeline — remains solely responsible for
validating the proposal and, optionally, generating graphs from it. If the
proposed config fails deterministic validation, it is saved for inspection
but never used for generation.

## Example command

```bash
python -m graph_layout_synth propose-instruction-variant \
  --instructions docs/design_instructions/inpatient_unit_rules.md \
  --base-config configs/generic_building.yaml \
  --output-dir outputs/instruction_variants/inpatient_unit_v1 \
  --samples 50
```

Required arguments: `--instructions`, `--base-config`, `--output-dir`.

Optional arguments:

- `--samples` (default `0`): generate this many graph samples with the
  existing `generate` pipeline, but only if the proposed config passes
  validation. `0` validates the proposal without generating any graphs.
- `--no-call`: write the prompt and inputs without calling Claude (see
  below).
- `--model`: override the Claude model (same override already supported by
  `propose-grammar-variant`).
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

The prompt reuses the existing grammar-variant assistant infrastructure
(`build_grammar_variant_prompt`): it embeds the base config, the live
`ConfigContract` vocabulary summary, and the standard complete-YAML output
instructions. On top of that, the instruction-guided variant prompt adds:

- the full instruction text, verbatim, under a `# Design Instructions`
  section;
- an explicit instruction to translate each design rule into supported
  existing config concepts (grammar rules, adjacency preferences, edge-type
  probabilities via stochastic counts/choices, validation settings, ranking
  weights) rather than inventing new schema fields;
- an explicit instruction not to generate graph samples, node-link JSON, or
  any other raw graph output — only a YAML config variant.

## Artifacts

All artifacts are written under `--output-dir`. These four are always
written, whether or not Claude is called:

```txt
submitted_instructions.md   # the instruction file's exact text
base_config.yaml            # the base config used to build the prompt
llm_prompt.md                # the full prompt sent (or that would be sent) to Claude
manifest.json                 # run metadata: inputs, status, artifact paths
```

If Claude is called (i.e., not `--no-call`), these are also written:

```txt
raw_llm_response.md            # Claude's raw response text
proposed_config.yaml           # the extracted YAML, if YAML could be extracted
config_validation_report.json  # deterministic validation result (same shape as `validate-config`)
review_summary.md              # human-readable pass/fail summary
```

If YAML cannot be extracted from the response at all, `proposed_config.yaml`
is not written, `manifest.json` records `status: "failed"`, and the CLI
exits nonzero.

If the extracted config fails deterministic validation, `proposed_config.yaml`
is still saved (so the invalid proposal can be inspected) along with its
validation errors, `manifest.json` records `status: "invalid"`, and **no
graphs are generated** regardless of `--samples`. The CLI exits nonzero.

If validation passes and `--samples > 0`, graph samples are generated by
directly reusing the existing `generate` CLI pipeline (`run_generate`) with
the proposed config — no new generator code is introduced. Outputs use the
same conventions as `generate` (`candidate_<n>.json`, `ranking_report.json`,
`best_candidate.json`, etc.) under `<output-dir>/generated_samples/`.

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
- As with `propose-grammar-variant`, tests for this workflow mock the Claude
  call; no live API calls are made in the test suite.

## See also

- `docs/GRAMMAR_CONFIG_SKILLS.md`: the schema reference given to Claude for
  any config-variant proposal.
- `propose-grammar-variant`: the structured-requirements/heuristic-instructions
  workflow this command's prompt-building code is built on.
- `docs/PROGRAM_REQUIREMENTS.md`: the separate, user-facing program-requirements
  preflight (room-mix min/target/max, not free-form design instructions).
