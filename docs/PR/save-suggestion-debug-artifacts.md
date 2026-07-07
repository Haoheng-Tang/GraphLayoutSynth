# Save next-room suggestion debug artifacts

## Summary

This PR adds optional server-side debug artifact saving for
`POST /suggest-next-room`.

Normal NextRoomPredictor requests are unchanged: by default the endpoint does
not write files and still returns the existing public response shape:

```json
{
  "suggestions": [],
  "sampleCount": 50,
  "predictorVersion": "graphlayoutsynth-v1"
}
```

When debug saving is enabled, GraphLayoutSynth writes the generated graph
samples plus matching and aggregation reports to a timestamped output folder.
This makes it possible to inspect why a room type was suggested, why a graph
matched the selected anchor, or why no candidates were found.

## Motivation

The suggestion endpoint uses generated graph samples internally, but those
samples were previously invisible during API calls. That made it difficult to
debug the full path:

1. convert the frontend floorplan into a NetworkX graph
2. generate backend graph samples
3. find semantic anchor matches
4. subtract known frontend neighbor relations
5. aggregate extra neighbor room types into suggestions

The new artifact writer keeps this debugging information on disk without
expanding the normal HTTP response or requiring frontend changes.

## Enablement

Debug saving remains opt-in.

For a single request, NextRoomPredictor may send:

```json
{
  "includeDebugArtifacts": true,
  "includeDebugVisualizations": true
}
```

Both fields are optional strict booleans. Existing requests that omit them keep
the same behavior.

Server-wide saving can also be enabled with environment variables:

```powershell
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS = "true"
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_PNGS = "true"
```

The artifact base directory defaults to:

```txt
outputs/nextroom_suggestions
```

It can be changed with:

```powershell
$env:GRAPHLAYOUTSYNTH_SUGGESTION_ARTIFACT_DIR = "outputs/nextroom_suggestions"
```

Each enabled request creates a separate timestamp-and-random-ID directory, for
example:

```txt
outputs/nextroom_suggestions/20260707T183500.027705Z-9c7c0e95/
```

## Saved artifacts

Each enabled run writes:

- `request.json`: validated camel-case request snapshot, including debug flags
- `generated_graph_000.json`, `generated_graph_001.json`, ...: raw generated
  NetworkX node-link graph samples with node, edge, and graph attributes
- `matching_report.json`: per-generated-graph semantic anchor matching details
- `aggregation_report.json`: counts and final suggestions returned by the API
- `README.md`: compact human-readable summary
- `generated_graph_000.png`, `generated_graph_001.png`, ...: optional PNG
  visualizations when PNG saving is enabled

Internal generated node IDs may appear in the private disk reports. They are
not added to the normal public API response.

## Matching report

`matching_report.json` records:

- the frontend anchor neighbor signature used for containment checking
- each generated graph index
- each graph's matching-node count
- matching internal node IDs
- matching node room types
- generated one-hop neighbor signatures
- extra neighbor signatures after subtracting known frontend relations
- candidate room types produced by each match and each graph

This is intentionally diagnostic. It does not change the semantic anchor
matching rules.

## Aggregation report

`aggregation_report.json` records:

- frontend anchor room ID
- frontend anchor room type
- frontend known neighbor signature
- generated sample count
- number of samples with matches
- total matching-node count
- number of samples with candidates
- candidate counts by room type
- final suggestions returned by the endpoint
- predictor version

This report makes the returned `sampleShare` values traceable back to graph
sample evidence.

## PNG visualization behavior

PNG saving is optional because it is slower than JSON/report export.

Visualization failures do not fail prediction. GraphLayoutSynth logs a warning
and continues returning suggestions and saving the other JSON artifacts.

The artifact writer now passes the active generation config into the existing
GraphLayoutSynth visualization helper. This prevents valid configured room
types from being rendered with fallback unknown styling in debug PNGs.

## Suggestion grammar config selection

The API sampler still uses `configs/generic_building.yaml` by default.

This PR adds an explicit environment hook for using a generated or experimental
grammar config during `/suggest-next-room`:

```powershell
$env:GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG = "outputs/llm_grammar_variant.yaml"
python -m uvicorn server.main:app --reload --port 8000
```

This keeps GraphLayoutSynth independent from NextRoomPredictor while making the
server's suggestion grammar easy to align with local experiments.

## Implementation notes

- Adds optional request fields to `SuggestNextRoomRequest`:
  - `includeDebugArtifacts`
  - `includeDebugVisualizations`
- Adds `SuggestionArtifactWriter` as a separate best-effort artifact boundary.
- Keeps artifact writing out of suggestion aggregation semantics.
- Reuses existing graph JSON export and PNG visualization helpers.
- Logs artifact save paths and failures server-side.
- Catches artifact and PNG failures so successful prediction responses are not
  turned into API errors.
- Adds `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` support in the API sampler.

No `debugArtifacts` response field is added, so frontend response validation
does not need to change.

## Documentation

Updated docs cover:

- request-body debug flags
- server-wide artifact and PNG environment variables
- artifact output directory
- saved file contents
- optional grammar config selection for suggestions
- production warning about file volume and PNG latency

Files updated:

- `README.md`
- `docs/contracts/suggest-next-room-api.md`
- `docs/integration/nextroompredictor-api.md`

## Tests

New and updated tests cover:

- artifacts are not saved by default
- artifacts are saved with request flags
- artifacts are saved with environment flags
- request snapshots are written
- generated graph JSON files are written
- matching reports are written
- aggregation reports are written
- summary README files are written
- PNG visualizations are written when enabled
- PNG failures warn and do not fail prediction
- artifact save failures warn and do not fail prediction
- default response compatibility when debug saving is disabled
- sampler config selection through `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG`
- debug PNG visualization receives the active generation config

Verification:

```txt
python -m pytest -q
165 passed, 1 warning

python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs\generic_building.yaml.
```

## Non-goals

This PR does not:

- add a frontend artifact browser
- return all generated graphs in the API response
- add WebSockets or streaming
- change semantic anchor matching rules
- change suggestion aggregation semantics
- add fuzzy matching or graph edit distance
- regenerate more graphs when no match is found
- add authentication
- make Claude part of the `/suggest-next-room` request path

## Manual smoke test

Start the server with optional artifact and PNG saving:

```powershell
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_ARTIFACTS = "true"
$env:GRAPHLAYOUTSYNTH_SAVE_SUGGESTION_PNGS = "true"
python -m uvicorn server.main:app --reload --port 8000
```

Send a valid `/suggest-next-room` request and inspect the newest directory
under:

```txt
outputs/nextroom_suggestions/
```

Expected output includes generated graph JSON files, optional PNGs,
`matching_report.json`, `aggregation_report.json`, `request.json`, and
`README.md`.
