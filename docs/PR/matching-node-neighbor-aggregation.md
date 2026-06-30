# Aggregate neighbors across all semantic anchor matches

## Summary

This PR completes the next-room aggregation path for generated graphs that
contain more than one semantic match for the frontend anchor.

Previously, the sampler projected a generated neighborhood only when exactly
one node matched. Generated graphs with zero or multiple matches contributed
no suggestions. The predictor now consumes every semantic match, subtracts
the frontend anchor's known one-hop relations, and aggregates the remaining
room types at the generated-graph level.

The public request and response schemas are unchanged. The suggestion
`reason` text now describes an extra neighbor of a semantically matched anchor
rather than a new neighbor of the selected node.

## Motivation

Strict semantic anchor matching intentionally returns all generated nodes that
cover the frontend anchor's room type and one-hop neighbor multiset. Reducing
that set to one node would reintroduce arbitrary selection based on node
ordering, randomness, or another unsupported tie-break.

Ignoring generated graphs with multiple matches also discards useful evidence.
This change uses the complete matching-node set while preserving graph-level
support semantics: one generated graph contributes at most one count for a
given suggested room type.

## Aggregation rule

For each generated graph:

1. Find every node that semantically matches the frontend anchor.
2. Build each match's one-hop relation multiset, keyed by:

   ```text
   (neighbor room type, edge type)
   ```

3. Subtract the frontend anchor's known relation multiset from each matching
   node's generated relation multiset.
4. Keep only positive remaining counts as extra neighbor relations.
5. Combine extras from all matching nodes.
6. Reduce those extras to a set of room types for the generated graph.
7. Increment each room type's support count once for that graph.

Across the generated-graph pool:

```text
sampleCount(room type) = number of generated graphs supporting that room type

sampleShare = sampleCount(room type) / generated graphs actually returned

confidence = sampleShare
```

Suggestions are sorted by descending support count and then alphabetically by
room type for deterministic ties.

## Multiset subtraction

Known frontend relations are excluded by count rather than by node identity:

```text
extra relations = generated relations - known frontend relations
```

Only positive counts remain. For example:

```text
frontend:
  ("PatientRoom", "wall") -> 1

generated match:
  ("PatientRoom", "wall") -> 3
  ("StaffSupport", "door") -> 1

extras:
  ("PatientRoom", "wall") -> 2
  ("StaffSupport", "door") -> 1
```

Multiplicity is preserved during subtraction so repeated generated relations
are handled correctly. The v1 response still reduces extra relations to room
types, so repeated occurrences and different edge types for the same room type
count once per generated graph.

## Multiple-match example

Given a frontend `Corridor` with one known `PatientRoom::door` relation, one
generated graph contains:

```text
matching Corridor A:
  PatientRoom via door
  StaffSupport via door

matching Corridor B:
  PatientRoom via door
  StaffSupport via wall
  ClinicalSupport via door
```

Both matching nodes contribute. After subtracting the known patient-room
relation and de-duplicating room types, the graph supports:

```text
ClinicalSupport
StaffSupport
```

`StaffSupport` counts once for this graph even though it appears at two
matching nodes with different edge types.

## Implementation

Adds
`graph_layout_synth/api/matching_node_neighbor_aggregation.py` with pure,
HTTP-independent helpers:

- `subtract_neighbor_signature`
- `extract_extra_neighbor_candidates`
- `candidate_room_types_for_generated_graph`
- `aggregate_candidates_from_matching_nodes`
- `build_suggestions_from_counts`

These helpers are exported from `graph_layout_synth.api`.

`NextRoomPredictor` now:

- adapts the submitted floorplan as before
- requests raw generated graphs from the sampler
- aggregates candidates across every semantic match
- builds suggestions from graph-level support counts
- reports the number of generated graphs actually returned

`ExistingGeneratorSampler` no longer projects generated neighborhoods into
copies of the frontend graph or requires exactly one semantic match. It returns
the raw graphs produced by `generate_candidates`, leaving semantic matching
and aggregation in the predictor layer.

The obsolete node-identity helper `existing_neighbor_ids` and the
unique-match projection helper are removed.

## Edge cases

- A generated graph with no semantic matches contributes no candidates.
- A matching node with no extra relations contributes no candidates.
- Multiple matching nodes can contribute different room types.
- The same room type from multiple matches counts once per generated graph.
- The same room type in different generated graphs increments its support.
- Wrong edge types fail semantic matching and do not create misleading extras.
- Zero generated graphs returns an empty suggestion list without division.
- If every graph contributes no candidates, the endpoint returns
  `suggestions: []` with the actual top-level sample count.

The frontend can continue using its documented local fallback for empty
suggestion lists.

## API compatibility

No public contract fields changed:

- `GET /health` remains available.
- `POST /suggest-next-room` accepts the same request shape.
- `POST /suggest-next-room` returns the same response shape.
- Frontend room IDs remain external and stable.
- Internal GraphLayoutSynth node IDs remain private.
- Direction and geometry remain frontend responsibilities.

Suggestion behavior changes because generated graphs with multiple matches can
now contribute evidence. The human-readable `reason` value changes from:

```text
Appeared as a new neighbor of the selected ...
```

to:

```text
Appeared as an extra neighbor of a semantically matched ...
```

Clients should continue treating `reason` as display text rather than a stable
machine-readable value.

## Documentation

Updates:

- `docs/contracts/suggest-next-room-api.md`
- `docs/integration/nextroompredictor-api.md`
- `docs/PR/suggest-next-room-api.md`

The documentation now defines multiset subtraction, aggregation across all
matches, per-graph room-type de-duplication, graph-level sample support, empty
match behavior, and the raw-generator boundary.

## Tests

Adds focused tests for:

- known-neighbor subtraction
- rejection of candidates with the wrong edge type
- preservation of extra multiset counts
- candidate collection from every matching node
- per-graph room-type de-duplication
- support accumulation across generated graphs
- graph-level shares and deterministic sorting
- graphs with no matching nodes
- empty counts and zero generated samples
- raw generated-graph sampling without node selection
- endpoint aggregation across multiple semantic matches

The existing API test sampler now returns generated graphs with semantic
anchors instead of projected copies of the frontend graph. Obsolete
unique-match projection tests are removed because projection is no longer part
of the sampler contract.

Verification:

```text
python -m pytest -q
158 passed

python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs/generic_building.yaml.

git diff --check
passed
```

## Non-goals

This PR does not:

- relax or fuzz semantic anchor matching
- choose or rank one matching node
- implement true partial-graph-conditioned generation
- change the public API schema
- return relation multiplicity or edge type in suggestions
- add direction or geometry to prediction
- expose generated graphs or internal node IDs
- modify NextRoomPredictor frontend code
- call Claude during prediction
- change deterministic layout ranking
- use diversity or novelty metrics for suggestion ranking
- claim generated graphs are code-compliant or life-safety certified

## Review checklist

- [x] Every semantic anchor match can contribute candidates.
- [x] Known frontend relations are removed by multiset subtraction.
- [x] Relation multiplicity is preserved during subtraction.
- [x] Room types are counted at most once per generated graph.
- [x] Support accumulates across generated graphs.
- [x] No matching node is selected arbitrarily.
- [x] The sampler returns raw generated graphs.
- [x] Suggestion ordering remains deterministic.
- [x] Empty and zero-sample cases remain valid.
- [x] Existing request and response schemas remain unchanged.
- [x] Documentation describes the current generator-adapter boundary.
- [x] Existing and new tests pass.
