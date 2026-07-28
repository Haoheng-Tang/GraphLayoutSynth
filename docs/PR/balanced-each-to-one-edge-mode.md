# Add `balanced_each_to_one` edge mode with per-target capacity

Branch: `feat/update-rule-schema` → `main`

## Summary

Adds a new grammar-rule edge mode, `balanced_each_to_one`, that distributes
source nodes across **all** resolved target nodes round-robin under an
explicit per-target capacity (`max_sources_per_target`), instead of
concentrating every source on the first target the way `each_to_one` does.
Infeasible assignments fail rule application with a clear error before any
edge is created. All four existing edge modes are unchanged, the new mode is
advertised to the LLM variant workflows through the live config contract and
the grammar skills doc, and rule-application traces record the capacity.

## Motivation

`each_to_one` connects every source to `targets[0]` by design — the right
behavior for a shared hub such as a single corridor. But for rules like
"assign PatientRooms to ClinicalSupport", it produced one ClinicalSupport
node serving every PatientRoom (`15, 0, 0, 0, 0`) with no way to express
"spread patients across the available support rooms, at most four per
room". Changing `each_to_one` itself was not an option: existing grammar
files (including `configs/generic_building.yaml`) depend on its
first-target semantics.

## New YAML contract

```yaml
create_edges:
  - source: patient
    target: clinical
    edge_type: door
    mode: balanced_each_to_one
    max_sources_per_target: 4
```

`max_sources_per_target` is **required** for this mode and must be a
positive integer (booleans rejected, matching the existing count-spec
validation style). It is rejected on every other mode, preserving the
schema's strict unknown/invalid-combination policy. Normal aliases,
`matched`, and `__neighbors__` work exactly as with other modes.

## Assignment semantics

Round-robin over the resolved target order: source *i* connects to
`targets[i % len(targets)]`. This yields, deterministically for the same
resolved source/target lists (no new randomness is introduced):

- every source connected to exactly one target;
- target loads differing by at most one (15 sources / 5 targets → `3, 3, 3,
  3, 3`; 14 → `3, 3, 3, 3, 2`; 20 with capacity 4 → `4, 4, 4, 4, 4`);
- every target used whenever sources ≥ targets;
- no target ever exceeding `max_sources_per_target` (round-robin's maximum
  load is `ceil(sources/targets)`, which the feasibility check bounds by
  the capacity).

### Infeasibility

Before touching the graph, `_create_balanced_each_to_one_edges` checks
`len(sources) <= len(targets) * max_sources_per_target`. On failure it
raises `RuleSchemaError` naming the rule, source count, target count,
per-target capacity, and total capacity — never a partial or overloaded
assignment. Empty sources create no edges (consistent with the existing
empty-alias semantics of all modes); non-empty sources with zero resolved
targets (e.g. `__neighbors__` on an isolated node) are infeasible and raise
rather than silently no-op, because "assign every source" cannot be
satisfied.

## Design notes

- `rule_schema.py` owns the whole change: `EDGE_MODES` /
  `CREATE_EDGE_KEYS` constants, `validate_grammar_rule` field rules, and a
  dedicated `_create_balanced_each_to_one_edges` helper dispatched before
  the generic empty-list early-return so the feasibility error is not
  swallowed. `apply_grammar_rule` threads `max_sources_per_target` and the
  rule name through for error reporting, and re-validates the capacity
  defensively at application time (the function is public API).
- Tracing follows the existing `sampled_parameters` style:
  `create_edges` trace entries for this mode add `max_sources_per_target`
  alongside the existing `mode`/`edge_type`/`created_edge_count`; entries
  for existing modes gain no new fields, so trace consumers are unaffected.
- `config_contract.py`'s `grammar_rule_schema_summary.supported_edge_modes`
  now lists the mode. Because both LLM proposal paths embed the live
  contract summary and `docs/GRAMMAR_CONFIG_SKILLS.md` verbatim into their
  prompts, Claude learns the new mode with no prompt-builder changes; the
  deterministic validator then accepts what it proposes.

## Documentation

- `docs/GRAMMAR_CONFIG_SKILLS.md`: field rules, the deliberate
  `each_to_one`-vs-`balanced_each_to_one` distinction, a worked example,
  and the 15/5 and 14/5 distribution outcomes (this file is embedded in
  LLM variant prompts, so it doubles as the model-facing spec).
- `README.md` grammar-rules section: mode list plus a balanced example
  with the feasibility rule.
- `AGENTS.md`: capability list updated with the new mode and its
  fail-on-infeasible behavior.

## Tests

20 new tests in `tests/test_rule_schema.py`, using the file's existing
direct-`apply_grammar_rule` conventions plus one end-to-end generation
test:

- load distributions 15/5 → all 3s, 14/5 → `[2, 3, 3, 3, 3]`, 20/5 at
  capacity 4 → all 4s; every source gets exactly one assignment edge;
- 21/5 at capacity 4 raises with rule name, counts, capacity, and total
  capacity in the message, and zero assignment edges in the graph;
- empty sources → no edges; non-empty sources with empty targets → raises;
- schema validation: missing/zero/negative/boolean/float/string capacities
  all rejected; capacity rejected on `each_to_one`; valid rule passes with
  vocabulary checks;
- `each_to_one` regression pin: all sources still connect to the first
  target only;
- determinism at rule level (identical graphs from identical inputs) and
  at generation level (same seed → identical edge lists);
- trace entries record mode/capacity/edge count for the new mode and gain
  no new fields for existing modes;
- end-to-end: a config generating 45 PatientRooms and 15 ClinicalSupport
  nodes through the real pipeline yields exactly 45 assignment edges at 3
  per ClinicalSupport, counting only PatientRoom↔ClinicalSupport door
  edges (corridor door edges excluded from load counting).

## Verification

- `python -m pytest -q` → 317 passed (297 before the branch).
- `python -m graph_layout_synth validate-config --config
  configs/generic_building.yaml` → valid (existing config untouched by the
  schema change).
- `git diff --check` → clean. (No lint/type-check tooling is configured in
  the repo.)
- Prompt-embedding check via the CLI's `--no-call` dry run (no API key, no
  Claude call): the generated `llm_prompt.md` contains
  `balanced_each_to_one` five times from the skills doc, and the embedded
  live-contract JSON lists it in `supported_edge_modes`.

## Backward compatibility

- `each_to_one`, `one_to_each`, `one_to_one`, and `adjacent_pairs`
  semantics are byte-for-byte unchanged and pinned by a regression test.
- Existing configs validate unchanged; `max_sources_per_target` remains an
  unknown-field error everywhere except the new mode.
- Trace shape for existing modes is unchanged.
- No generator, ranking, API, or frontend behavior is touched; the new
  mode only takes effect where a config opts in.

## Non-goals

- No change to existing edge-mode semantics.
- No seeded-random or load-aware assignment strategies beyond round-robin
  (round-robin already satisfies the balance and capacity requirements
  deterministically).
- No frontend or `/suggest-next-room` changes.
- No relaxation of the strict unknown-field validation policy.

## Review checklist

- [ ] `EDGE_MODES` / `CREATE_EDGE_KEYS` are the only new schema surface;
  no other field or mode changed.
- [ ] Feasibility check runs before any `graph.add_edge` call in the
  balanced path; the error message contains rule name, both counts,
  capacity, and total capacity.
- [ ] `max_sources_per_target` on non-balanced modes fails validation.
- [ ] Trace entries for existing modes are unchanged (no capacity key).
- [ ] `supported_edge_modes` in the contract summary matches `EDGE_MODES`.
- [ ] The 45/15 end-to-end test counts only PatientRoom↔ClinicalSupport
  door edges, not corridor edges.
