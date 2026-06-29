# Suggest Next Room API Contract

## Purpose

`POST /suggest-next-room` returns ranked next-room-type suggestions for a selected anchor room in the current floorplan.

The frontend calls this endpoint only when the user clicks a `+` handle. The backend should not be called on every floorplan edit.

The backend does not need to understand canvas direction such as `"north"`, `"south"`, `"east"`, or `"west"` in v1. Direction is handled locally by the frontend when placing the selected suggestion.

## Identity rule

The API uses NextRoomPredictor room IDs as external stable node IDs.

GraphLayoutSynth may convert these IDs to its own internal graph/node IDs, but the API boundary should remain based on frontend room IDs. The backend must map:

```txt
frontend room.id → internal GraphLayoutSynth node id
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
* `sampleCount`: number of graph samples requested. Default target is `50`.

## Direction handling

The clicked side is intentionally not sent to the backend in v1.

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
      "reason": "Appeared as a new neighbor of the selected Corridor in 30 of 50 generated graph samples."
    },
    {
      "roomType": "StaffSupport",
      "sampleCount": 10,
      "sampleShare": 0.2,
      "confidence": 0.2,
      "reason": "Appeared as a new neighbor of the selected Corridor in 10 of 50 generated graph samples."
    }
  ],
  "sampleCount": 50,
  "predictorVersion": "graphlayoutsynth-v1"
}
```

## Response fields

* `suggestions`: ranked room-type suggestions.
* `suggestions[].roomType`: recommended next room type.
* `suggestions[].sampleCount`: number of generated graph samples supporting this room type.
* `suggestions[].sampleShare`: `sampleCount / total valid samples`.
* `suggestions[].confidence`: initially same as `sampleShare`; may become calibrated later.
* `suggestions[].reason`: optional human-readable explanation.
* `sampleCount`: number of graph samples actually used.
* `predictorVersion`: backend predictor/version label.

## Aggregation rule

The backend should count new candidate neighbors of `anchorRoomId`, not existing neighbors already present in the input floorplan.

For each generated graph:

1. Locate the anchor node corresponding to `anchorRoomId`.
2. Find nodes adjacent to the anchor in the generated graph.
3. Exclude neighbors that already exist in the input floorplan.
4. Count remaining neighbor room types.
5. Aggregate counts across samples.

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
