# GraphLayoutSynth

Stochastic graph-grammar generation and evaluation for building layout graphs.

## Overview

GraphLayoutSynth is an early-stage research prototype for generating building layout graphs using procedural graph-grammar rules, stochastic sampling, and rule-based validation.

The project represents building layouts as attributed graphs:

* Nodes represent spaces such as rooms, corridors, zones, or service areas.
* Edges represent spatial relationships such as door connections or wall adjacencies.
* Node attributes may include room type, area, aspect ratio, orientation, zone, and other spatial or functional properties.
* Edge attributes may include connection type, adjacency type, or circulation relationship.

The initial goal is not to train a deep graph generative model. Instead, the project explores a small-data, rule-guided approach where candidate layout graphs are generated through stochastic procedural rules and evaluated using explicit constraints and metrics.

## First Milestone

**Minimal stochastic graph-grammar prototype**

The first milestone focuses on generating small valid building layout graphs from structured grammar rules.

Planned features:

* Define a seed graph such as `BuildingFloor`.
* Expand abstract nodes into zones, room clusters, corridors, and rooms.
* Add stochastic rule parameters such as cluster size, corridor pattern, and room-type mix.
* Validate generated graphs using basic constraints.
* Score and rank feasible candidates.
* Export generated graphs as JSON.

## Initial Scope

The first prototype will support generic building layout graphs rather than one specific building type.

Example room or space types may include:

* Room
* Corridor
* SupportRoom
* ServiceRoom
* PublicZone
* PrivateZone
* VerticalCore

Example edge types may include:

* Door connection
* Wall adjacency

## Method

The initial generation pipeline is:

```text
Structured grammar rules
        ↓
Stochastic graph-rewrite generator
        ↓
Rule-based constraint checker
        ↓
Metric-based candidate scoring
        ↓
Candidate export and interpretation
```

Large language models may be used later as auxiliary tools for rule formalization, candidate interpretation, and ranking, but the core generation engine is intended to remain explicit and inspectable.

## Branch

Initial development branch:

```text
m1_stochastic_grammar
```

## Status

Early research prototype.
