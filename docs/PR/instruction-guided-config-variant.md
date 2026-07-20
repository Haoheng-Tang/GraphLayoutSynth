# Add instruction-guided config variants with a validation-guided repair loop

## Summary

This PR adds `propose-instruction-variant`, a research CLI workflow that
tests whether Claude can translate a markdown/text file of researcher-written
design instructions into a valid GraphLayoutSynth YAML config variant. It
also adds an optional validation-guided repair loop: when a proposal fails
deterministic validation, the invalid YAML and its validation errors are sent
back to Claude for correction, up to a configurable number of attempts,
stopping at the first attempt that validates.

Claude proposes and, on repair attempts, revises YAML config variants only.
It never generates graph JSON, and it never validates, ranks, repairs, or
certifies layouts itself. GraphLayoutSynth's deterministic validator remains
the sole authority on acceptance, and the existing generation pipeline runs
only after some attempt actually passes validation.

## Purpose

`propose-instruction-variant` exists to answer one narrow question: given
free-form instructions like "avoid a single corridor hub" or "clinical
support rooms should be near patient rooms," can Claude express that intent
using GraphLayoutSynth's *existing* config vocabulary (`grammar_rules`,
`typed_accessibility_pairs`, `semantic_node_groups`, `room_mix_targets`,
`validation`, `ranking`) well enough to produce a config that passes
deterministic validation?

This is a config-authoring aid, not a new generation, validation, or ranking
path. It does not replace `propose-grammar-variant` (structured
requirements/heuristic instructions) or the program-requirements preflight
(user-facing room-mix validation); it is a separate, narrower experiment
focused on markdown-style design rules.

## Motivation for the repair loop

The first live run against `configs/generic_building.yaml` successfully
called Claude and saved `proposed_config.yaml`, but deterministic validation
failed, so no graph samples were generated. That is the correct safety
behavior, but a single-shot workflow gives Claude no way to see *why* it
failed and try again. The repair loop closes that gap without weakening any
safety property: every attempt, initial or repaired, is validated the same
deterministic way, and generation still requires a validated config.

## Basic command

```bash
python -m graph_layout_synth propose-instruction-variant \
  --instructions docs/design_instructions/inpatient_unit_rules.md \
  --base-config configs/generic_building.yaml \
  --output-dir outputs/instruction_variants/inpatient_unit_v1 \
  --samples 50
```

Required arguments: `--instructions`, `--base-config`, `--output-dir`.
`--samples` defaults to `0` (validate only, no generation). `--no-call`
writes the prompt and inputs without calling Claude or making any repair
calls, regardless of `--repair-attempts`.

## Command with repair attempts

```bash
python -m graph_layout_synth propose-instruction-variant \
  --instructions docs/design_instructions/inpatient_unit_rules.md \
  --base-config configs/generic_building.yaml \
  --output-dir outputs/instruction_variants/inpatient_unit_live_02 \
  --samples 25 \
  --repair-attempts 2
```

`--repair-attempts` defaults to `0`, which preserves the original one-shot
behavior exactly: no repair call is made, and an invalid initial proposal
ends the run without generating graphs. With `--repair-attempts N > 0`, an
invalid initial proposal is sent back to Claude with its validation errors,
up to `N` times, stopping at the first attempt that validates.

## Claude proposes and repairs YAML only

Both the initial prompt and every repair prompt ask Claude for a complete
YAML config variant, never a patch, and explicitly instruct it not to
generate graph samples, node-link JSON, or any other raw graph output. The
repair prompt additionally includes:

- the original design instructions, unchanged;
- the base config and the same live `ConfigContract` schema guidance used in
  the initial prompt;
- the previous attempt's invalid YAML proposal, verbatim;
- the deterministic validation errors from that attempt's
  `config_validation_report.json`;
- a strict instruction to return a complete corrected YAML config, never a
  patch/diff, and never an invented/unsupported schema field;
- a reminder that deterministic GraphLayoutSynth validation, not Claude's own
  response, decides whether the corrected config is accepted.

## Deterministic validation remains authoritative

Every attempt — initial and repaired — is validated the same way, by the
existing `validate_config_file` (`validate_config` + `ConfigContract`
consistency checks), with no new validator introduced. The attempt loop
tracks a `final_valid_config_path` that stays `None` until some attempt's
report says `is_valid: true`; generation is gated directly on that variable
being set, so an invalid config can never reach the generation pipeline
regardless of what Claude claims about its own output.

## Generation runs only after a valid config

If any attempt (initial or repaired) validates, the loop stops immediately.
If `--samples > 0`, graph samples are then generated by directly reusing the
existing `generate` CLI pipeline (`run_generate`) against the validated
config — no new generator code is introduced. Outputs use the same
conventions as `generate` (`candidate_<n>.json`, `ranking_report.json`,
`best_candidate.json`, etc.) under `<output-dir>/generated_samples/`. If
every attempt remains invalid, generation never runs, regardless of
`--samples`.

## Invalid configs remain inspectable

Nothing is discarded. Every attempt's raw Claude response, extracted YAML,
and validation report are saved to disk, whether or not that attempt
succeeded, so a researcher can review exactly what was tried and why each
attempt failed.

## Artifact structure

All artifacts are written under `--output-dir`. Four files are always
written, whether or not Claude is called:

```txt
submitted_instructions.md   # the instruction file's exact text
base_config.yaml            # the base config used to build the prompt
llm_prompt.md               # the initial prompt sent (or that would be sent) to Claude
manifest.json                # run metadata: inputs, status, all attempts, artifact paths
```

If Claude is called, every attempt gets its own subdirectory:

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
later `attempt_N_repair/` directories only exist if earlier attempts were
still invalid.

Top-level convenience copies always reflect the **latest** attempt made
(valid or not), and the review summary covers **every** attempt:

```txt
proposed_config.yaml           # copy of the latest attempt's extracted YAML
config_validation_report.json  # the latest attempt's validation report
review_summary.md              # table of every attempt plus the final outcome
```

`manifest.json` records `repairAttemptsRequested`, `repairAttemptsUsed`,
`generationRan`, and an `attempts` list (each with `index`, `kind`
(`"initial"`/`"repair"`), `isValid`, and artifact paths). Final `status` is
one of `proposed_valid`, `generated`, `proposed_invalid`, or `failed` (plus
`dry_run` for `--no-call`).

## Live smoke behavior

A live, end-to-end smoke test through the actual CLI entry point (mocked
Claude boundary, real filesystem, real deterministic validation, real
generation pipeline) reproduced exactly the intended repair flow:

1. **Initial proposal** — Claude's first response fails deterministic
   validation; `attempt_0_initial/config_validation_report.json` records the
   errors.
2. **Repair prompt built** — the invalid YAML and those validation errors are
   sent back to Claude as `attempts/attempt_1_repair/repair_prompt.md`.
3. **Repaired config validates** — the corrected response passes
   deterministic validation; the loop stops.
4. **Generation ran** — with `--samples > 0`, the existing `generate`
   pipeline ran against the now-valid `proposed_config.yaml` and wrote the
   usual candidate/ranking/report artifacts under `generated_samples/`, and
   `manifest.json` recorded `status: "generated"`.

## Non-goals

This PR does not:

- implement PDF ingestion — only markdown/plain-text instruction files are
  supported
- implement frontend UI
- change `/suggest-next-room` in any way
- integrate with `ProgramRequirements`/program-requirements preflight
- activate proposed variants through the grammar-variant control plane
- implement a new validator or a new generator — both stages reuse existing
  deterministic GraphLayoutSynth code unchanged
- let Claude override validation, or continue generation with an invalid
  config
- add reasoning, alternate strategies, or convergence guarantees to repair —
  `--repair-attempts` bounds the number of Claude calls, not the quality of
  the result; a proposal can still be invalid after every attempt

## Tests

`tests/test_instruction_variant.py` covers, with mocked Claude calls only
(no live API calls):

- prompt builder unit tests: instruction text and base config present in the
  initial prompt; repair prompt includes the invalid YAML, the validation
  errors, and the original instructions
- `--no-call` writes the four always-on artifacts and never calls Claude,
  including with `--repair-attempts` set
- missing instructions file returns a controlled error
- a valid initial response writes the full attempt-0 artifact set with no
  generation requested
- an invalid initial proposal with `--repair-attempts 0` calls Claude exactly
  once, does not call repair, and does not generate
- a valid config with `--samples N` calls the existing generation pipeline
  with sample count `N`
- `--samples 0` validates without generating
- an invalid initial proposal with `--repair-attempts 1` calls repair exactly
  once
- the repair prompt sent to Claude contains the invalid YAML and the
  validation errors
- repair success triggers generation when `--samples > 0`
- repair that remains invalid writes a second validation report and does not
  generate
- multiple repair attempts stop at the first valid config (verified with more
  allowed attempts than actually used, confirming early stop rather than
  exhaustion)
- manifest records each attempt's index, kind, validity, and artifact paths
- review summary lists the initial proposal and repair outcomes in a table

## Verification

```txt
python -m pytest -q
253 passed, 1 warning

git diff --check
passed
```

## Review checklist

- [x] Claude proposes and repairs YAML only; it is never asked to generate,
      validate, rank, or certify graphs.
- [x] Deterministic validation runs identically on every attempt, initial and
      repaired.
- [x] Generation is gated on an actually-validated attempt, not on Claude's
      claims about its own output.
- [x] Every attempt's response, config, and validation report are saved,
      valid or not.
- [x] The repair loop stops at the first valid attempt.
- [x] `--repair-attempts 0` reproduces the original one-shot behavior.
- [x] No new validator or generator was introduced.
- [x] `/suggest-next-room` is unchanged.
- [x] Full test suite passes; `git diff --check` is clean.
