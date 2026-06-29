# Add strict semantic anchor matching

## Summary

This PR replaces arbitrary generated-anchor selection with strict semantic
matching for `POST /suggest-next-room`.

Generated GraphLayoutSynth candidates can contain many nodes with the same
room type. The previous adapter sorted those nodes by internal ID and selected
one using the sample index modulo the number of matches. That ordering did not
represent a semantic correspondence with the room selected in
NextRoomPredictor.

The new matcher returns every generated node whose room type matches and whose
one-hop neighbor multiset covers the frontend anchor's known one-hop neighbor
multiset.

The public API request and response schemas are unchanged.

## Matching rule

For both the frontend anchor and each generated candidate node, build a
one-hop signature using:

```text
key:   (neighbor room type, edge type)
value: number of neighbors with that relation
```

Example signature:

```text
("Corridor", "door")     -> 1
("PatientRoom", "wall")  -> 2
```

A generated node matches if and only if:

1. Its room type equals the frontend anchor room type.
2. Every frontend signature count is covered:

   ```text
   generated_signature[key] >= frontend_signature[key]
   ```

This is strict one-way multiset containment, not equality.

### Allowed

- Additional generated neighbors
- Higher generated degree
- Additional relation types
- More occurrences of a required relation
- Multiple matching nodes in one generated graph

### Rejected

- Different anchor room type
- Missing required neighbor relation
- Wrong edge type
- Too few occurrences of a repeated relation

If the frontend anchor has no known neighbors, every generated node with the
same room type matches.

## Example

Frontend anchor:

```text
PatientRoom
  -> Corridor via door
  -> PatientRoom via wall
```

Valid generated candidate:

```text
PatientRoom
  -> Corridor via door
  -> PatientRoom via wall
  -> Bathroom via door
  -> StaffSupport via door
```

The extra generated neighbors are allowed.

Invalid generated candidate:

```text
PatientRoom
  -> Corridor via wall
  -> PatientRoom via wall
```

This candidate does not cover the required `Corridor::door` relation.

## Implementation

Adds `graph_layout_synth/api/semantic_anchor_matching.py` with pure helpers:

- `extract_anchor_room_type`
- `build_anchor_neighbor_signature`
- `build_candidate_neighbor_signature`
- `covers_neighbor_signature`
- `is_semantic_anchor_match`
- `find_matching_anchor_nodes`

The helpers:

- use `Counter`-style multiset signatures
- return all matching nodes
- do not mutate either graph
- remain independent of FastAPI
- expose no internal node IDs through the public response

## Sampler integration

The existing generator remains behind the mockable `GraphSampler` boundary.
Each API request still generates local GraphLayoutSynth candidates and does
not call Claude.

The sampler now calls `find_matching_anchor_nodes` instead of sorting
same-type nodes and applying modulo selection.

This branch intentionally does not aggregate possible next-room neighbors
across multiple semantic matches. To keep `/suggest-next-room` stable without
making another arbitrary choice:

| Matches in one generated graph | Current behavior |
| --- | --- |
| Zero | Project no neighborhood for that sample. |
| Exactly one | Project that matching node's one-hop neighborhood. |
| More than one | Project no neighborhood for that sample. |

The matching helper still returns the complete matching-node list. A later
branch can aggregate candidate neighbors across all matches.

## Deliberately not used

Matching does not use:

- internal node ordering
- modulo selection
- random selection
- BFS or DFS
- degree equality or degree similarity
- shortest paths
- graph edit distance
- fuzzy or embedding similarity
- scoring, ranking, or top-k selection

This is only a one-hop multiset-containment operation.

## API compatibility

No public contract fields changed:

- `GET /health` remains available.
- `POST /suggest-next-room` accepts the same request.
- `POST /suggest-next-room` returns the same response shape.
- Frontend room IDs remain external stable IDs.
- Internal GraphLayoutSynth IDs remain private.
- No `side` or direction field is added.
- Geometry remains a frontend responsibility.

The endpoint may return fewer suggestions because samples with multiple
semantic matches are no longer resolved through arbitrary node selection.
NextRoomPredictor should continue using its documented local fallback when the
suggestion list is empty.

## Documentation

Updates:

- `docs/contracts/suggest-next-room-api.md`
- `docs/integration/nextroompredictor-api.md`
- `docs/PR/suggest-next-room-api.md`

The documentation now explains one-way coverage, multiset counts, additional
generated neighbors, edge-type requirements, multiple matching nodes, and the
deferred all-match aggregation behavior.

## Tests

Adds focused tests for:

- room-type and signature extraction
- empty frontend signatures
- different room types
- exact one-hop coverage
- one-way coverage with additional neighbors
- higher generated degree
- missing required neighbors
- incorrect edge types
- insufficient repeated-neighbor counts
- equal and greater repeated-neighbor counts
- multiple matching nodes
- zero matching nodes
- insertion-order independence
- retrieval of all matches without random or modulo selection
- refusal to choose among multiple matches
- projection when exactly one semantic match exists

Verification:

```text
python -m pytest tests/test_semantic_anchor_matching.py -q
16 passed

python -m pytest -q
148 passed

python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs/generic_building.yaml.

GET /health
200 {"status":"ok"}

POST /suggest-next-room
200 with suggestions, sampleCount, and predictorVersion fields

git diff --check
passed
```

## Non-goals

This PR does not:

- aggregate next-room candidates across all semantic matches
- implement fuzzy or relaxed matching
- implement graph edit distance or similarity scoring
- condition the grammar directly on the submitted partial graph
- modify NextRoomPredictor
- add side/direction input
- return geometry or generated graph samples
- add authentication, WebSockets, or streaming
- call Claude during prediction
- change deterministic ranking or diversity behavior

## Review checklist

- [x] Matching uses strict one-way one-hop multiset coverage.
- [x] Room type and edge type are part of the match.
- [x] Repeated neighbor counts are respected.
- [x] Additional generated neighbors are allowed.
- [x] All matching nodes are returned by the helper.
- [x] Arbitrary ordering and modulo selection are removed.
- [x] Multiple matches are not silently reduced to one.
- [x] Matching helpers are pure and HTTP-independent.
- [x] Existing API schemas remain unchanged.
- [x] Existing and new tests pass.
- [x] Documentation describes the current boundary and deferred work.
