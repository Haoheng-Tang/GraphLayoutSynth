# Add NextRoomPredictor next-room suggestion API

## Summary

This PR adds a lightweight FastAPI boundary between GraphLayoutSynth and
NextRoomPredictor.

NextRoomPredictor can now submit its current floorplan and an anchor room ID
when the user clicks a `+` handle. GraphLayoutSynth generates local graph
samples, aggregates newly predicted neighbor room types, and returns ranked
semantic suggestions.

The API does not accept a required placement direction and does not return
geometry. NextRoomPredictor remains responsible for the clicked side, room
placement, dimensions, overlap checks, and frontend state updates.

## Motivation

GraphLayoutSynth and NextRoomPredictor are developed as separate applications.
This change gives them a stable HTTP integration boundary without importing
code from either repository into the other.

The boundary keeps:

- NextRoomPredictor room IDs stable and external
- GraphLayoutSynth internal node IDs private
- deterministic validation and grammar generation inside GraphLayoutSynth
- semantic prediction separate from frontend geometry
- the generator mockable for deterministic API tests

## API

### Health

```http
GET /health
```

```json
{"status":"ok"}
```

### Suggest next room

```http
POST /suggest-next-room
Content-Type: application/json
```

Example request:

```json
{
  "floorplan": {
    "schemaVersion": 1,
    "rooms": [
      {
        "id": "room-1",
        "type": "Corridor",
        "x": 100,
        "y": 100,
        "width": 150,
        "height": 80
      },
      {
        "id": "room-2",
        "type": "PatientRoom",
        "x": 250,
        "y": 100,
        "width": 150,
        "height": 110
      }
    ],
    "edges": [
      {
        "id": "edge-1",
        "sourceRoomId": "room-1",
        "targetRoomId": "room-2",
        "edgeType": "door"
      }
    ],
    "selectedRoomId": "room-1"
  },
  "anchorRoomId": "room-1",
  "sampleCount": 50
}
```

Example response:

```json
{
  "suggestions": [
    {
      "roomType": "PatientRoom",
      "sampleCount": 30,
      "sampleShare": 0.6,
      "confidence": 0.6,
      "reason": "Appeared as a new neighbor of the selected Corridor in 30 of 50 generated graph samples."
    }
  ],
  "sampleCount": 50,
  "predictorVersion": "graphlayoutsynth-v1"
}
```

## Implementation

### FastAPI server

- Adds `server.main:app`.
- Enables CORS for `http://localhost:5173`.
- Supports additional comma-separated origins through
  `NEXT_ROOM_ALLOWED_ORIGINS`.
- Converts request-validation failures to HTTP 400.
- Returns a controlled HTTP 500 without exposing internal stack traces when
  generation fails.

Start locally with:

```bash
python -m uvicorn server.main:app --reload --port 8000
```

### Request and response validation

Pydantic models define:

- `Room`
- `DoorOrAdjacency`
- `FloorplanState`
- `SuggestNextRoomRequest`
- `NextRoomTypeSuggestion`
- `SuggestNextRoomResponse`

Validation includes:

- schema version 1
- non-empty, unique room IDs
- positive room dimensions
- wall/door edge types
- valid edge endpoint references
- an anchor ID present in the submitted rooms
- a strict integer sample count from 1 through 200

Unknown fields are ignored for compatibility. Existing edges may contain an
optional `side`, but the clicked handle side is not required or used.

### External/internal ID adapter

The floorplan adapter creates a reversible mapping:

```text
frontend room ID <-> internal integer node ID
```

It copies room type, geometry, rotation, edge type, and optional existing-edge
side into a NetworkX graph without mutating the request. API responses never
expose internal IDs.

### Sampling boundary

The sampler is defined behind a protocol so tests and future generator
implementations can replace it without changing the HTTP layer or aggregation
logic.

The default implementation calls the existing local GraphLayoutSynth
`generate_candidates` function for the requested number of samples.

This request path does **not** call Claude, does not require an Anthropic API
key, and does not incur an LLM API cost. Claude remains limited to the explicit
grammar-variant and report-interpretation CLI workflows.

### Semantic anchor matching

Generated graphs can contain many nodes with the anchor room type. Matching no
longer chooses one through internal string ordering or sample-index modulo.

Pure helpers now:

- extract the frontend anchor room type
- build frontend and generated one-hop neighbor multisets
- check strict one-way multiset coverage
- test one generated node for a semantic match
- return every matching node in a generated graph

Signature keys are `(neighbor room type, edge type)`. A generated node matches
when its room type is equal and every required frontend relation count is
covered. Additional generated neighbors and higher degree are allowed.
Multiset counts and edge types must match.

The matcher does not use ordering, randomness, BFS, DFS, degree similarity,
ranking, scoring, fuzzy matching, or graph edit distance.

### Aggregation

For each sample, the predictor:

1. Locates the preserved frontend anchor through the internal ID mapping.
2. Finds its immediate predicted neighbors.
3. Excludes neighbor node identities already connected in the input graph.
4. Counts each new room type at most once per sample.
5. Computes `sampleShare` and initial `confidence`.
6. Sorts by descending share, then alphabetically for deterministic ties.

An empty candidate set returns a valid response with `suggestions: []`.
Diversity and novelty metrics do not affect this ranking.

## Current generator-adapter boundary

The current grammar begins from its own abstract seed and cannot directly
continue an arbitrary concrete partial floorplan.

The v1 sampler uses strict semantic coverage to retrieve all nodes that can
correspond to the frontend anchor. This branch deliberately does not implement
next-room candidate aggregation across those multiple matches.

To keep `/suggest-next-room` stable without choosing arbitrarily:

- exactly one match: project that node's one-hop neighborhood
- zero matches: project nothing for that generated graph
- multiple matches: project nothing for that generated graph

The pure matching helper still returns all matches. A later branch can consume
that complete list as the basis for next-room aggregation.

This remains an adapter rather than true partial-graph conditioning. A future
conditional grammar can replace it while preserving the API, ID mapping, and
matching contract.

## Frontend integration behavior

The expanded API contract gives NextRoomPredictor:

- exact TypeScript request and response types
- a reusable `fetch` client
- configurable base URL handling
- timeout and cancellation behavior
- stale-request protection
- HTTP and network fallback handling
- the complete `+`-handle interaction flow
- local verification commands
- an implementation checklist

NextRoomPredictor should call this endpoint only on a `+`-handle click and
retain the clicked side locally. Prediction failures or empty results should
fall back to local rule-based suggestions and must not block manual placement.

## Dependencies

Adds the lightweight API runtime dependencies:

- FastAPI `>=0.115,<0.116`
- Pydantic `>=2.8`
- Starlette `>=0.40,<0.46`
- Uvicorn `>=0.30`

The explicit FastAPI/Starlette compatibility range prevents pre-populated
environments from retaining an incompatible Starlette major version.

The development extra adds HTTPX for endpoint tests.

## Documentation

- Adds the detailed API contract at
  `docs/contracts/suggest-next-room-api.md`.
- Adds the backend integration and local-operation guide at
  `docs/integration/nextroompredictor-api.md`.
- Updates the README with installation, startup, endpoint, CORS, and
  integration links.

## Testing

The API tests cover:

- health endpoint
- valid suggestion requests
- camel-case response serialization
- requests with no `side`
- accidental `side` compatibility
- invalid anchor IDs
- invalid edge references
- missing floorplans
- invalid sample counts
- CORS for the local frontend origin
- frontend/internal ID mapping
- aggregation and deterministic ordering
- duplicate room types within one sample
- existing-neighbor exclusion
- empty sample results
- controlled generator failures

The semantic matching tests cover:

- anchor room-type and one-hop signature extraction
- empty frontend signatures
- room-type equality
- exact and one-way coverage
- additional generated neighbors and higher degree
- missing required relations
- incorrect edge types
- multiset count failures and passes
- multiple and zero matches in one graph
- node insertion-order independence
- retrieval of all matches without random or modulo selection
- unique-match projection and refusal to choose among multiple matches

Verification:

```text
python -m pytest -q
148 passed

python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs/generic_building.yaml.

git diff --check
passed
```

## Non-goals

This PR does not:

- implement or modify NextRoomPredictor UI code
- call the backend continuously on frontend edits
- accept a required north/south/east/west prediction input
- return or mutate room geometry
- expose generated graph samples by default
- add WebSocket streaming or authentication
- replace deterministic ranking with LLM ranking
- use diversity or novelty to rank suggestions
- claim that generated graphs are code-compliant or life-safety certified

## Review checklist

- [x] HTTP boundary keeps both repositories independent.
- [x] Frontend IDs remain stable and internal IDs remain private.
- [x] Existing neighbors are excluded by node identity.
- [x] Direction and geometry remain frontend responsibilities.
- [x] Generator calls are mockable.
- [x] API errors are controlled.
- [x] CORS supports local NextRoomPredictor development.
- [x] API contract and integration instructions are documented.
- [x] Full test suite passes.
- [x] Default grammar config validates.
