# Add secondary intended edges to next-room suggestions

## Summary

This PR extends `POST /suggest-next-room` responses with optional
`intendedEdges`: secondary, evidence-backed relationships from the suggested
new room to rooms that already exist in the submitted frontend floorplan.

The existing `edgeType` keeps its meaning — the relationship between the
anchor room and the suggested new room. Intended edges are additional
relationships, not replacements. The request schema is unchanged, no LLM is
called, and graph generation behavior is untouched.

## Motivation

NextRoomPredictor now supports corridor auto-extension and pairwise
relationship resolution, and can consume explicit secondary intended edges.
The backend already knew about these relationships — generated samples often
contain them — but the response never exposed them.

Example: the canvas holds `PatientRoom1` and a `Corridor` joined by a door.
The user adds `PatientRoom2` against `PatientRoom1` (wall). Generated samples
frequently contain the local topology:

```txt
PatientRoom1 -- PatientRoom2: wall
PatientRoom1 -- Corridor: door
PatientRoom2 -- Corridor: door
```

The response previously returned only `roomType: PatientRoom` with
`edgeType: wall`. It now can also return:

```json
{
  "intendedEdges": [
    {
      "targetExistingRoomId": "corridor-id",
      "targetRoomType": "Corridor",
      "edgeType": "door",
      "edgeTypeCounts": {"door": 12},
      "confidence": 0.24,
      "sampleCount": 12
    }
  ]
}
```

## Response model

`NextRoomTypeSuggestion` gains an optional `intendedEdges` list of
`SuggestedIntendedEdge`:

- `targetExistingRoomId`: frontend room ID of the existing target room;
  omitted when the target is ambiguous (see below)
- `targetRoomType`: existing target room's type
- `edgeType`: dominant `door`/`wall` connection between the suggested room
  and the target; `door` wins ties, matching the existing convention
- `edgeTypeCounts`, `confidence`, `sampleCount`: per-sample support evidence
  following the existing suggestion counting boundary

All previously existing fields — including `edgeType` and `edgeTypeCounts` —
are unchanged.

## Algorithm

For each generated graph sample and each semantic anchor match:

1. The matched node's generated neighbors are partitioned deterministically
   (ascending `str(node_id)` order) into **known-neighbor correspondents** —
   consuming one slot per known frontend `(room type, edge type)` anchor
   relation — and **extra candidate suggested nodes**. The per-relation slot
   arithmetic is identical to the existing multiset-subtraction extras, so
   suggested room types stay consistent with current aggregation.
2. Correspondence to a known frontend neighbor uses local relationships only:
   the generated neighbor must be adjacent to the matched anchor node, have
   the known neighbor's room type, and connect to the anchor with the known
   anchor edge type. Generated node IDs never need to match frontend IDs.
3. A secondary intended edge is recorded **only** when the generated graph
   itself contains a `door`/`wall` edge between a candidate node and a
   known-neighbor correspondent. Nothing is inferred from room-type rules or
   geometry: `PatientRoom--Corridor = door` is reported only when samples
   actually contain that door edge, and a wall edge is reported as `wall`.

Aggregation across samples mirrors the existing conventions:

- each `(suggested room type, target)` pair counts at most once per generated
  sample for `sampleCount`
- per-sample `door`/`wall` evidence accumulates into `edgeTypeCounts`
- the dominant `edgeType` prefers `door` on ties
- `confidence` is the intended edge's sample support divided by the top-level
  sample count
- the returned list is ordered by descending sample support, then target room
  type, then target room ID, so responses are deterministic

### Ambiguous known neighbors

When several existing frontend rooms share the same room type and anchor edge
type (for example two corridors both door-connected to the anchor), the
generated evidence cannot name one of them. The intended edge is then
aggregated under the room type alone: `targetExistingRoomId` is omitted while
`targetRoomType` and the edge evidence are still returned. This never crashes
and is documented in the API contract.

### Multi-match symmetry

Every semantic anchor match remains a sampling point, consistent with the
existing all-match aggregation philosophy. When the suggested room type equals
the anchor type (patient room beside patient room), a generated candidate node
can itself be a valid anchor match; the symmetric evidence it produces is
de-duplicated per sample.

## Backward compatibility

- The request schema is unchanged.
- `intendedEdges` is omitted from the response JSON when no generated sample
  contains a secondary edge (the serializer drops `None` optional fields, the
  same mechanism that already makes `edgeType` optional on the wire).
- Existing clients that ignore the field keep working; all previous fields
  and their semantics are unchanged.
- One pre-existing debug-artifact test asserting exact `finalSuggestions`
  equality was updated with `"intendedEdges": None`, the same style of update
  the edge-type-aware PR made when it added `edgeType`.

## Debug artifacts

When suggestion debug artifacts are enabled, `matching_report.json` now also
records:

- `knownFrontendNeighborTargets`: the known frontend neighbor mapping
  (neighbor room type, anchor edge type, resolved target room ID or null)
- per-graph `intendedEdgeEvidence`: for every matched anchor node, the
  known-neighbor correspondents, the candidate suggested nodes, and each
  detected secondary edge with its generated node IDs and edge type

Aggregated intended edges appear in the aggregation report through the
serialized `finalSuggestions`. Internal node IDs remain confined to these
private disk artifacts.

## Implementation

- `graph_layout_synth/api/models.py`: adds `SuggestedIntendedEdge` and the
  optional `intended_edges` field on `NextRoomTypeSuggestion`.
- `graph_layout_synth/api/matching_node_neighbor_aggregation.py`: adds
  `known_frontend_neighbor_targets`,
  `intended_edge_details_for_generated_graph`,
  `intended_edge_relations_for_generated_graph`, intended-edge counters on
  `CandidateAggregation`, and intended-edge assembly in
  `build_suggestions_from_counts`.
- `graph_layout_synth/api/predictor.py`: passes the new aggregation evidence
  into suggestion building.
- `graph_layout_synth/api/suggestion_debug_artifacts.py`: extends the
  matching report with the known-neighbor mapping and per-match evidence.

The helpers remain pure, HTTP-independent, and deterministic; the mockable
`GraphSampler` boundary and semantic anchor matching rules are unchanged.

## Documentation

- `docs/contracts/suggest-next-room-api.md`: `SuggestedIntendedEdge`
  TypeScript type, response example, field documentation, a new
  "Secondary intended edges" section, an intended-edge resolution step in the
  `+`-handle flow, and the extended debug-artifact contents.
- `docs/integration/nextroompredictor-api.md`: intended-edge consumption
  guidance and debug artifact updates.
- `README.md`, `AGENTS.md`, `CLAUDE.md`: feature summary and guardrails
  (no invented edges, no hard-coded room-type rules).

## Tests

`tests/test_suggestion_intended_edges.py` covers:

- known-neighbor target mapping keeps unambiguous frontend room IDs
- secondary intended edge found with door evidence
- no hard-coded PatientRoom/Corridor door rule: wall evidence reports `wall`
- no intended edge when the generated graph lacks the secondary edge
- endpoint-level response with anchor `edgeType` preserved alongside the
  intended edge
- backward-compatible fields with the intended-edge field absent when there
  is no evidence
- multi-sample aggregation of door/wall counts, dominant edge type,
  `sampleCount`, and `confidence`
- deterministic `door` tie-breaking
- ambiguous known corridors omit `targetExistingRoomId` without crashing
- candidate edges to generated-only rooms are never reported as intended
  edges
- debug artifacts include the known-neighbor mapping, per-match secondary
  edges, and aggregated intended edges

## Verification

```txt
python -m pytest -q
225 passed, 1 warning

git diff --check
passed
```

## Non-goals

This PR does not:

- change the request schema or `/suggest-next-room` calling conventions
- change semantic anchor matching or suggestion ranking
- call the LLM or change graph generation behavior
- return geometry, side, direction, placement, or collision results
- infer secondary edges from geometry or hard-coded room-type rules
- implement two-hop matching
- modify NextRoomPredictor

## Review checklist

- [x] `edgeType` still describes the anchor/new-room relationship.
- [x] Intended edges come only from actual generated-graph edges between
      candidate nodes and known-neighbor correspondents.
- [x] No healthcare or room-type semantics are hard-coded.
- [x] Ambiguous targets omit the room ID instead of guessing or crashing.
- [x] Aggregation, tie-breaking, and ordering are deterministic and follow
      existing conventions.
- [x] Existing clients and the existing test suite remain compatible.
- [x] Debug artifacts expose the full evidence chain without leaking internal
      node IDs into the public response.
