# Suggest Next Room API Contract

## Purpose

`POST /suggest-next-room` returns ranked next-room-type suggestions for a selected anchor room in the current floorplan.

The frontend calls this endpoint only when the user clicks a `+` handle. The backend should not be called on every floorplan edit.

The backend does not need to understand canvas direction such as `"north"`, `"south"`, `"east"`, or `"west"` in v1. Direction is handled locally by the frontend when placing the selected suggestion.

## Service setup

For local development:

```text
Base URL: http://127.0.0.1:8000
Health:   GET  /health
Suggest:  POST /suggest-next-room
Docs:     GET  /docs
```

NextRoomPredictor should configure the backend URL rather than hard-code a
deployment address. For Vite:

```env
VITE_GRAPHLAYOUTSYNTH_API_URL=http://127.0.0.1:8000
```

GraphLayoutSynth allows `http://localhost:5173` by default. If
NextRoomPredictor uses another origin, start the backend with it in
`NEXT_ROOM_ALLOWED_ORIGINS`. Multiple origins are comma-separated:

```powershell
$env:NEXT_ROOM_ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
python -m uvicorn server.main:app --reload --port 8000
```

## When to call

Call the endpoint after the user clicks a room's `+` handle and the frontend
has both the room's stable ID and the latest floorplan export. Retain the
clicked side locally for placement after the user chooses a suggestion.

Do not call the endpoint:

* on every drag, resize, selection, or floorplan state update
* before a room has a stable frontend ID
* to perform geometry or overlap validation
* a second time merely to place the selected suggestion

If another handle is clicked while a request is pending, cancel or ignore the
older request so its response cannot replace suggestions for the newer anchor.

## TypeScript types

NextRoomPredictor may use these directly or map equivalent existing editor
types to this shape:

```ts
export type FloorplanRoom = {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number | null;
};

export type FloorplanEdge = {
  id: string;
  sourceRoomId: string;
  targetRoomId: string;
  edgeType: "wall" | "door";
  side?: string | null; // accepted on existing edges; unused for prediction
};

export type FloorplanState = {
  schemaVersion: 1;
  rooms: FloorplanRoom[];
  edges: FloorplanEdge[];
  selectedRoomId?: string | null;
};

export type SuggestNextRoomRequest = {
  floorplan: FloorplanState;
  anchorRoomId: string;
  sampleCount: number;
};

export type NextRoomTypeSuggestion = {
  roomType: string;
  sampleCount: number;
  sampleShare: number;
  confidence: number;
  reason?: string | null;
};

export type SuggestNextRoomResponse = {
  suggestions: NextRoomTypeSuggestion[];
  sampleCount: number;
  predictorVersion: string;
};
```

## Reusable frontend client

This client provides a configurable URL, request timeout, cancellation for
stale UI work, HTTP error parsing, and minimal response validation:

```ts
const API_BASE_URL = (
  import.meta.env.VITE_GRAPHLAYOUTSYNTH_API_URL ??
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

const DEFAULT_SAMPLE_COUNT = 50;
const DEFAULT_TIMEOUT_MS = 15_000;

export class GraphLayoutSynthApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(`GraphLayoutSynth request failed with HTTP ${status}`);
    this.name = "GraphLayoutSynthApiError";
  }
}

function isSuggestNextRoomResponse(
  value: unknown,
): value is SuggestNextRoomResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<SuggestNextRoomResponse>;

  return (
    Array.isArray(candidate.suggestions) &&
    typeof candidate.sampleCount === "number" &&
    typeof candidate.predictorVersion === "string" &&
    candidate.suggestions.every(
      (suggestion) =>
        typeof suggestion?.roomType === "string" &&
        typeof suggestion.sampleCount === "number" &&
        typeof suggestion.sampleShare === "number" &&
        typeof suggestion.confidence === "number",
    )
  );
}

export async function suggestNextRoom(
  floorplan: FloorplanState,
  anchorRoomId: string,
  options: {
    sampleCount?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<SuggestNextRoomResponse> {
  const sampleCount = options.sampleCount ?? DEFAULT_SAMPLE_COUNT;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();

  const relayCallerAbort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) {
    relayCallerAbort();
  } else {
    options.signal?.addEventListener("abort", relayCallerAbort, { once: true });
  }

  const timeoutId = window.setTimeout(
    () =>
      controller.abort(
        new DOMException("GraphLayoutSynth request timed out", "TimeoutError"),
      ),
    timeoutMs,
  );

  const request: SuggestNextRoomRequest = {
    floorplan,
    anchorRoomId,
    sampleCount,
  };

  try {
    const response = await fetch(`${API_BASE_URL}/suggest-next-room`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });

    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // A non-JSON body is handled below as an invalid/error response.
    }

    if (!response.ok) {
      const detail =
        typeof payload === "object" && payload !== null && "detail" in payload
          ? (payload as { detail: unknown }).detail
          : payload;
      throw new GraphLayoutSynthApiError(response.status, detail);
    }

    if (!isSuggestNextRoomResponse(payload)) {
      throw new Error("GraphLayoutSynth returned an invalid response shape.");
    }

    return payload;
  } finally {
    window.clearTimeout(timeoutId);
    options.signal?.removeEventListener("abort", relayCallerAbort);
  }
}
```

No authentication header is required in v1.

## Identity rule

The API uses NextRoomPredictor room IDs as external stable node IDs.

GraphLayoutSynth may convert these IDs to its own internal graph/node IDs, but the API boundary should remain based on frontend room IDs. The backend must map:

```txt
frontend room.id <-> internal GraphLayoutSynth node id
```

The frontend should never need to know GraphLayoutSynth internal node IDs.

## Request

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
        "id": "room-1-east-room-2",
        "sourceRoomId": "room-1",
        "targetRoomId": "room-2",
        "side": "east",
        "edgeType": "door"
      }
    ],
    "selectedRoomId": "room-1"
  },
  "anchorRoomId": "room-1",
  "sampleCount": 50
}
```

## Request fields

* `floorplan`: current editor floorplan JSON from NextRoomPredictor.
* `anchorRoomId`: frontend room ID for the selected/anchor room where the user clicked a `+` handle.
* `sampleCount`: number of graph samples requested. Use `50` as the normal UI default.

### Request validation rules

| Field | Rule |
| --- | --- |
| `floorplan.schemaVersion` | Must be exactly `1`. |
| `floorplan.rooms` | Must contain at least one room. |
| `rooms[].id` | Must be non-empty and unique. Keep it stable across calls. |
| `rooms[].type` | Must be a non-empty semantic room-type label. |
| `rooms[].x`, `rooms[].y` | Must be JSON numbers. Geometry is preserved but not used for v1 prediction. |
| `rooms[].width`, `rooms[].height` | Must be positive JSON numbers. |
| `rooms[].rotation` | Optional number or `null`. |
| `floorplan.edges` | May be empty. Every endpoint must reference an ID in `rooms`. |
| `edges[].edgeType` | Must be `"wall"` or `"door"`. |
| `edges[].side` | Optional compatibility field on an existing edge; unused for prediction. |
| `floorplan.selectedRoomId` | Optional. If supplied, it must reference an existing room. |
| `anchorRoomId` | Required and must match one `rooms[].id`; it is authoritative even if `selectedRoomId` differs. |
| `sampleCount` | Required strict integer from `1` through `200`. |

The request is a snapshot. Send the latest rooms and edges on every handle
click. GraphLayoutSynth does not retain or mutate it.

## Direction handling

The clicked handle side is intentionally not sent to the backend in v1.
`edges[].side` may still appear on existing floorplan edges, but it does not
describe the current click and is not used for prediction.

Do not add a top-level `side`, `direction`, `north`, `south`, `east`, or `west`
field. Unknown fields are currently ignored, but frontend code must not depend
on that permissive behavior.

The frontend owns placement direction:

* user clicks `+` on north/south/east/west
* backend recommends likely adjacent room types
* frontend places the selected room on the clicked side
* frontend validates placement using local geometry and overlap rules

The backend owns semantic/topological prediction:

* given current graph
* given anchor room
* predict likely new neighbor room types

## Response

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

## Response fields

* `suggestions`: ranked room-type suggestions.
* `suggestions[].roomType`: recommended next room type.
* `suggestions[].sampleCount`: number of generated samples containing this new neighboring type; a type is counted at most once per sample.
* `suggestions[].sampleShare`: suggestion `sampleCount` divided by the top-level actual `sampleCount`.
* `suggestions[].confidence`: currently the same as `sampleShare`; it is not a calibrated safety or compliance probability.
* `suggestions[].reason`: optional human-readable explanation. Do not parse it for application logic.
* `sampleCount`: number of graph samples actually returned. It may be lower than the requested count.
* `predictorVersion`: diagnostic backend predictor/version label.

Suggestions are ordered by descending `sampleShare`, with alphabetical
room-type ordering for ties.

An empty result is successful:

```json
{
  "suggestions": [],
  "sampleCount": 50,
  "predictorVersion": "graphlayoutsynth-v1"
}
```

Use local fallback suggestions when the returned list is empty.

## Semantic anchor matching

A generated graph may contain many nodes with the same room type. The backend
does not select one using internal node order, modulo sampling, randomness, or
graph traversal order.

Instead, it represents the frontend anchor's known one-hop neighborhood as a
multiset. Each key contains:

```text
(neighbor room type, edge type)
```

The value is the number of neighbors with that relation. Generated candidate
nodes receive the same signature.

A generated node is a semantic anchor match if and only if:

1. Its room type equals the frontend anchor room type.
2. For every relation in the frontend signature:

   ```text
   generated signature count >= frontend required count
   ```

This is strict one-way coverage, not equality. A generated candidate may have
additional neighbors and a higher degree. Extra generated neighbors are
allowed and are not penalized.

For example, a frontend `PatientRoom` with:

* one `Corridor` neighbor through a `door`
* two `PatientRoom` neighbors through `wall` edges

requires a generated `PatientRoom` with at least those counts. A generated
`Corridor` neighbor through a `wall` cannot satisfy the required
`Corridor::door` relation.

Multiset counts matter: one `PatientRoom::wall` relation cannot cover a
requirement for two. If the frontend anchor has no known neighbors, every
generated node with the same room type matches.

The matching helper returns all valid nodes from each generated graph. It does
not rank, score, sort, or choose among them. It does not use BFS, DFS, degree
equality, graph edit distance, fuzzy matching, or top-k selection because this
is only a one-hop containment check.

### Current endpoint integration

Every semantic match is used as a sampling point. For each matching node, the
backend subtracts the frontend anchor signature from the generated candidate
signature using multiset subtraction:

```text
extra relations = generated candidate relations - known frontend relations
```

Positive remaining `(neighbor room type, edge type)` counts are candidate
next-room relations. This correctly preserves multiplicity: if the frontend
has one `PatientRoom::wall` relation and a generated match has three, two
remain as extra relations.

All matching nodes in one generated graph are considered. Their extra
relations are then reduced to a set of room types for that graph. Consequently:

* the same room type from multiple matching nodes counts once for that graph
* the same room type with multiple edge types counts once for that graph
* the same room type in multiple generated graphs increments its support count
* a graph with no semantic matches contributes no candidate types

`suggestions[].sampleCount` is therefore the number of generated graph samples
supporting that extra room type, not the raw number of matching nodes or
relations. `sampleShare` divides that count by the top-level number of
generated graph samples actually returned.

If no matching nodes are found, the backend returns an empty suggestion list.
It does not relax matching, regenerate graphs, or fall back to internal node
selection.

## Important behavior

The backend should aggregate generated graphs and return ranked suggestions. The frontend should not need to receive all generated graphs for normal UI behavior.

The frontend remains geometry-authoritative:

* backend suggests room types
* frontend uses clicked side for placement
* frontend chooses default dimensions
* frontend validates whether the candidate room can be placed
* frontend disables or hides suggestions that overlap existing rooms

The frontend should fall back to local rule-based suggestions if:

* the backend is unavailable
* the request times out
* the backend returns no valid suggestions

## `+` handle integration flow

The frontend retains `clickedSide`; only `floorplan` and `anchorRoomId` cross
the API boundary.

```ts
let activeSuggestionRequest: AbortController | null = null;

async function onPlusHandleClick(
  anchorRoomId: string,
  clickedSide: "north" | "south" | "east" | "west",
) {
  activeSuggestionRequest?.abort();
  const requestController = new AbortController();
  activeSuggestionRequest = requestController;

  openSuggestionPanel({
    anchorRoomId,
    clickedSide,
    status: "loading",
    suggestions: [],
  });

  try {
    const result = await suggestNextRoom(
      exportCurrentFloorplan(),
      anchorRoomId,
      {
        sampleCount: 50,
        signal: requestController.signal,
      },
    );

    if (activeSuggestionRequest !== requestController) return;

    if (result.suggestions.length === 0) {
      showLocalFallbackSuggestions(anchorRoomId, clickedSide);
      return;
    }

    updateSuggestionPanel({
      status: "ready",
      suggestions: result.suggestions,
      predictorVersion: result.predictorVersion,
      actualSampleCount: result.sampleCount,
    });
  } catch (error) {
    if (requestController.signal.aborted) return;

    console.warn("GraphLayoutSynth suggestions unavailable", error);
    showLocalFallbackSuggestions(anchorRoomId, clickedSide);
  } finally {
    if (activeSuggestionRequest === requestController) {
      activeSuggestionRequest = null;
    }
  }
}
```

If this logic lives in a component, abort the active request during component
cleanup/unmount.

When the user selects a returned `roomType`, NextRoomPredictor must:

1. Use the locally retained `clickedSide`.
2. Choose local default dimensions for that room type.
3. Calculate candidate rectangle geometry.
4. Reject, disable, or adjust overlapping placements.
5. Create a new stable frontend room ID.
6. Add the room and its wall/door edge to frontend state.
7. Include the updated state in the next API request.

Do not send another prediction request merely to perform placement. This
endpoint returns recommendations, not a placement transaction.

## Errors and fallback

The server returns JSON errors with a `detail` field.

### HTTP 400

The request is invalid. Common causes include:

* missing `floorplan`, `anchorRoomId`, or `sampleCount`
* empty or duplicate room IDs
* an `anchorRoomId` not present in `rooms`
* an edge referencing a missing room
* non-positive room width or height
* an unsupported `edgeType`
* `sampleCount` outside `1..200` or sent as a string/decimal

`detail` may be a string or an array of validation objects. Log it during
development, but present a short fallback message to users.

### HTTP 500

An unexpected generation failure returns a controlled response:

```json
{"detail":"Next-room prediction failed."}
```

Recommended frontend handling:

| Condition | Behavior |
| --- | --- |
| HTTP 400 | Log request details during development; show local fallback suggestions. |
| HTTP 500 | Show local fallback suggestions; optionally offer retry. |
| Network failure | Show local fallback suggestions. |
| Timeout | Show local fallback suggestions; optionally offer retry. |
| Superseded request or unmounted component | Silently discard the stale result. |
| HTTP 200 with an empty list | Show local fallback suggestions. |

Prediction failure must never block manual or local room placement.

## Local verification

Start GraphLayoutSynth from its repository root:

```powershell
mamba activate musa-550-fall-2024
python -m pip install -e ".[dev]"
python -m uvicorn server.main:app --reload --port 8000
```

Check health from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected body:

```json
{"status":"ok"}
```

Call the endpoint:

```powershell
$body = @{
    floorplan = @{
        schemaVersion = 1
        rooms = @(
            @{
                id = "room-1"
                type = "Corridor"
                x = 100
                y = 100
                width = 150
                height = 80
            },
            @{
                id = "room-2"
                type = "PatientRoom"
                x = 250
                y = 100
                width = 150
                height = 110
            }
        )
        edges = @(
            @{
                id = "edge-1"
                sourceRoomId = "room-1"
                targetRoomId = "room-2"
                edgeType = "door"
            }
        )
        selectedRoomId = "room-1"
    }
    anchorRoomId = "room-1"
    sampleCount = 10
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Uri http://127.0.0.1:8000/suggest-next-room `
    -Method Post `
    -ContentType "application/json" `
    -Body $body |
    ConvertTo-Json -Depth 10
```

Generated counts depend on the active grammar. NextRoomPredictor end-to-end
tests should assert the response shape, ordering, stale-request behavior, and
fallback behavior rather than fixed counts.

GraphLayoutSynth's endpoint contract tests run with:

```powershell
python -m pytest tests/test_next_room_api.py -q
```

## NextRoomPredictor implementation checklist

* [ ] Read the backend URL from `VITE_GRAPHLAYOUTSYNTH_API_URL`.
* [ ] Call the API only from the `+`-handle interaction.
* [ ] Send the latest floorplan snapshot.
* [ ] Use the stable ID of the room owning the clicked handle as `anchorRoomId`.
* [ ] Send an integer `sampleCount` in `1..200` (normally `50`).
* [ ] Keep clicked side local and omit it from the top-level request.
* [ ] Scope loading state to the active anchor/handle.
* [ ] Cancel or ignore stale requests.
* [ ] Use local rule-based suggestions on empty/error/timeout paths.
* [ ] Never parse returned `reason` text as application data.
* [ ] Keep geometry and collision validation in the frontend.
* [ ] Never request or expect an internal GraphLayoutSynth node ID.

## Non-goals

This API does not:

* mutate the floorplan
* create rooms directly
* return final geometry
* handle north/south/east/west placement direction in v1
* run on every edit
* return all generated graph samples by default
* replace local collision detection
* replace local rule-based fallback suggestions
