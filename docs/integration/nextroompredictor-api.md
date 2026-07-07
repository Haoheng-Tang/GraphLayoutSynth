# NextRoomPredictor API Integration

GraphLayoutSynth and NextRoomPredictor remain separate applications. Their only integration boundary is this HTTP API.

NextRoomPredictor calls `POST /suggest-next-room` when a user clicks a `+` handle. It should not call the endpoint continuously after every canvas edit. The backend recommends semantic/topological room types; the frontend owns direction, geometry, placement, and collision checks.

## Install and run

From the GraphLayoutSynth repository:

```bash
python -m pip install -e ".[dev]"
python -m uvicorn server.main:app --reload --port 8000
```

By default, suggestions sample from `configs/generic_building.yaml`. To use a
generated or experimental grammar config, set the config path before starting
the server:

```powershell
$env:GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG = "outputs/llm_grammar_variant.yaml"
python -m uvicorn server.main:app --reload --port 8000
```

If the server fails while constructing `FastAPI` with an error mentioning
`Router.__init__`, reinstall the project with the command above. The project
pins FastAPI and Starlette to a compatible range; `python -m pip check` can
confirm whether the active environment contains conflicting packages.

The default browser origin is `http://localhost:5173`. To allow additional origins, provide a comma-separated environment variable before starting the server:

```bash
NEXT_ROOM_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:5173
```

On PowerShell:

```powershell
$env:NEXT_ROOM_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:5173"
```

## Health check

```bash
curl http://localhost:8000/health
```

Response:

```json
{"status":"ok"}
```

## Suggest a next room

```bash
curl -X POST http://localhost:8000/suggest-next-room \
  -H "Content-Type: application/json" \
  -d '{
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
          "id": "room-1-east-room-2",
          "sourceRoomId": "room-1",
          "targetRoomId": "room-2",
          "edgeType": "door"
        }
      ],
      "selectedRoomId": "room-1"
    },
    "anchorRoomId": "room-1",
    "sampleCount": 50
  }'
```

`side` is not part of the v1 prediction contract. It is optional on existing floorplan edges for compatibility, and an accidental extra request field is ignored. No north/south/east/west value is required or inferred.

Example response:

```json
{
  "suggestions": [
    {
      "roomType": "PatientRoom",
      "sampleCount": 30,
      "sampleShare": 0.6,
      "confidence": 0.6,
      "reason": "Appeared as an extra neighbor of a semantically matched Corridor in 30 of 50 generated graph samples."
    },
    {
      "roomType": "StaffSupport",
      "sampleCount": 10,
      "sampleShare": 0.2,
      "confidence": 0.2,
      "reason": "Appeared as an extra neighbor of a semantically matched Corridor in 10 of 50 generated graph samples."
    }
  ],
  "sampleCount": 50,
  "predictorVersion": "graphlayoutsynth-v1"
}
```

Each room type is counted at most once per generated sample, so `sampleShare`
remains between zero and one. Known input relations are excluded through
multiset subtraction over `(neighbor room type, edge type)`. A generated match
with a higher count of a known relation can therefore still produce that room
type as an extra candidate.

The top-level `sampleCount` reports the number of samples actually returned by the sampler. If no candidate neighbor types are found, `suggestions` is an empty array.

## Validation and errors

Requests must contain:

- a schema-version-1 floorplan with at least one room;
- unique, non-empty room IDs;
- positive room width and height;
- edges using `wall` or `door` and referencing existing rooms;
- an `anchorRoomId` present in `floorplan.rooms`;
- a `sampleCount` from 1 through 200.

Invalid requests return HTTP 400. Unexpected generation failures return a controlled HTTP 500 response without an internal stack trace.

## Suggestion debug artifacts

The endpoint does not save generated graphs during normal requests. For a
single diagnostic request, add either or both optional booleans:

```json
{
  "includeDebugArtifacts": true,
  "includeDebugVisualizations": true
}
```

`includeDebugArtifacts` saves JSON reports. `includeDebugVisualizations` saves
PNGs and also enables the JSON artifact run. Existing frontend requests can
omit both fields and retain the same behavior and response shape.

To enable artifacts for all requests handled by a server process, set:

```powershell
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS = "true"
```

Configure the base directory and optional PNGs with:

```powershell
$env:GRAPHLAYOUTSYNTH_SUGGESTION_ARTIFACT_DIR = "outputs/nextroom_suggestions"
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_PNGS = "true"
```

Truth values accepted for environment flags are `1`, `true`, `yes`, and `on`,
case-insensitively. The default base directory is
`outputs/nextroom_suggestions`. Every enabled request creates a separate
timestamp-and-short-ID directory:

```text
outputs/nextroom_suggestions/<timestamp>-<id>/
  README.md
  request.json
  generated_graph_000.json
  generated_graph_001.json
  matching_report.json
  aggregation_report.json
  generated_graph_000.png       # optional
  generated_graph_001.png       # optional
```

The files contain:

- `request.json`: validated camel-case request snapshot, including debug flags
- `generated_graph_*.json`: raw generated NetworkX node-link graphs with node,
  edge, and graph attributes
- `matching_report.json`: frontend anchor signature and, for every generated
  graph, matching internal node IDs, one-hop signatures, subtracted extras,
  and candidate room types
- `aggregation_report.json`: anchor identity/type, sample and match totals,
  per-room candidate counts, returned suggestions, and predictor version
- `README.md`: concise run totals, suggestions, and pointers to key files
- `generated_graph_*.png`: optional renderings using the existing
  GraphLayoutSynth visualization utility

Internal generated node IDs are allowed only in these private disk artifacts;
they are never added to the normal response. The server logs the saved
directory. It also logs artifact or PNG failures and continues returning the
computed suggestions without exposing a stack trace to the client.

Debug saving can create many files, and PNG rendering adds work to the request
path. Keep these settings disabled by default in production and use an
external retention policy if server-wide saving is enabled.

## Identity and graph conversion

Frontend room IDs are stable external IDs. The adapter maps them to internal integer node IDs and maintains the reverse mapping:

```text
frontend room ID <-> internal graph node ID
```

Geometry, rotation, room type, edge type, and optional existing-edge side are copied into the internal graph. The prediction response contains room types only and never exposes an internal node ID.

## Semantic anchor matching boundary

The current GraphLayoutSynth grammar starts from its own abstract seed and
cannot yet rewrite an arbitrary concrete partial floorplan. Generated graphs
can also contain many nodes with the same room type, so room type alone is not
enough to select an anchor.

For each generated graph, GraphLayoutSynth builds one-hop neighbor multisets
whose keys are:

```text
(neighbor room type, edge type)
```

A generated node matches the frontend anchor only when:

1. Its room type equals the frontend anchor's room type.
2. Its one-hop signature covers every count in the frontend anchor signature.

This is strict one-way containment:

```text
frontend known one-hop relations <= generated candidate one-hop relations
```

For example, if the frontend anchor has one `Corridor` neighbor through a
`door` and two `PatientRoom` neighbors through `wall` edges, a generated
candidate needs at least those same relation counts. A `Corridor` through a
`wall` does not cover a `Corridor` through a `door`.

Generated candidates may have additional neighbors. Extra neighbors and a
higher degree do not prevent a match. When the frontend anchor has no known
neighbors, every generated node with the same room type matches.

Matching returns all valid generated nodes. It does not sort, rank, score, or
choose among them, and it does not use internal ID order, modulo selection,
randomness, BFS, DFS, degree equality, or graph edit distance.

Every matching node is used as a sampling point. For each match, GraphLayoutSynth
subtracts the frontend anchor's known relation multiset from the generated
node's relation multiset. Positive remaining counts are extra candidate
relations.

All matches in the generated graph contribute, but room types are de-duplicated
within that graph before counting. Thus, if three matching nodes all produce
an extra `StaffSupport`, that generated graph contributes one unit of support
for `StaffSupport`. If `StaffSupport` appears in 18 different generated graphs,
its suggestion `sampleCount` is 18.

The top-level `sampleCount` remains the number of generated graphs actually
processed. Suggestion `sampleShare` is its graph-level support count divided by
that top-level count.

A graph with no matching nodes contributes nothing. If no generated graphs
contain a match, the endpoint returns an empty suggestion list so the frontend
can use its local fallback. Matching is not relaxed and graphs are not
regenerated.

The boundary remains replaceable: a future true conditional generator can
implement the same sampler interface without changing the HTTP contract, ID
adapter, matching rule, or frontend.
