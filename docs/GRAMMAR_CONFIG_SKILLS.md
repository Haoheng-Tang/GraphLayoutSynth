# Grammar Config Skills

Instructions for Claude or other LLMs that propose GraphLayoutSynth YAML config variants.

## Purpose

A GraphLayoutSynth config describes how to generate, validate, rank, and visualize stochastic building layout graphs. The config is YAML, and generated variants must be complete files that can be validated before generation.

Do not invent unsupported fields. Creative variants are welcome only when they preserve the current schema.

## Required Top-Level Structure

Use the same top-level sections as `configs/generic_building.yaml`:

```yaml
project:
  name: Example config
  building_type: GenericBuilding

random_seed_default: 42

generation:
  num_candidates: 5

allowed_node_types:
  - BuildingFloor
  - Zone
  - Corridor
  - PatientRoom
  - ClinicalSupport
  - StaffSupport

allowed_edge_types:
  - door
  - wall

zone_types:
  - public
  - private
  - service

room_type_counts:
  PatientRoom: 3
  ClinicalSupport: 1
  StaffSupport: 1

stochastic:
  min_zone_count: 2
  max_zone_count: 3
  min_cluster_size: 2
  max_cluster_size: 4
  corridor_pattern_choices:
    - linear
    - hub
  support_room_choices:
    - ClinicalSupport
    - StaffSupport

validation:
  require_connected_graph: true
  require_corridor_access: true
  allow_abstract_nodes_final: false

grammar_rules: []

ranking:
  weights: {}
  targets: {}

visualization:
  node_colors: {}
  unknown_node_color: "#c7c7c7"
```

The current required graph settings are `allowed_node_types`, `allowed_edge_types`, `zone_types`, and `room_type_counts`. The current program settings are `random_seed_default`, `generation`, `stochastic`, `validation`, `ranking`, and `visualization`.

## Grammar Rule Structure

`grammar_rules` is optional, but when present it must be a list. Each rule must have:

```yaml
- name: rule_name
  match:
    type: Zone
    is_abstract: true
  action:
    create_nodes: []
    create_edges: []
```

Supported rule fields:

- `name`
- `match`
- `action`

Supported `match` keys:

- `type`
- `zone`
- `zone_type`
- `is_abstract`

Match values are exact node-attribute matches. Do not use stochastic choices in `match`.

Supported `action` keys:

- `remove_matched_node`
- `update_matched_node_attributes`
- `create_nodes`
- `create_edges`

## Node Attributes

Current supported node attributes in grammar config are:

- `type`
- `zone`
- `zone_type`
- `is_abstract`

Node `type` values must be listed in `allowed_node_types` when that list is present. Typical current types are `BuildingFloor`, `Zone`, `Corridor`, `PatientRoom`, `ClinicalSupport`, and `StaffSupport`.

## Edge Attributes

Edges use:

- `edge_type`

`edge_type` values must be listed in `allowed_edge_types`. The current default config supports:

- `door`
- `wall`

## create_nodes Format

Each `create_nodes` entry must include:

- `alias`
- `type`

Optional fields:

- `count`
- `attributes`

Example:

```yaml
create_nodes:
  - alias: corridor
    type: Corridor
    count: 1
    attributes:
      is_abstract: false
  - alias: room
    type:
      choices:
        - PatientRoom
        - ClinicalSupport
        - StaffSupport
    count:
      min: 3
      max: 5
    attributes:
      is_abstract: false
```

Created-node aliases are used by `create_edges`. Do not use reserved aliases as created-node aliases:

- `matched`
- `__neighbors__`

## Count Formats

Fixed positive integer:

```yaml
count: 3
```

Stochastic min/max object:

```yaml
count:
  min: 3
  max: 6
```

Both `min` and `max` must be positive integers, and `min` must be less than or equal to `max`.

## Stochastic Choices

The supported stochastic choice format is:

```yaml
type:
  choices:
    - PatientRoom
    - ClinicalSupport
```

Use choices for created values such as created node `type`. Do not use choices in `match`.

## create_edges Format

Each `create_edges` entry must include:

- `source`
- `target`
- `edge_type`

Optional field:

- `mode`

Supported edge modes:

- `one_to_one`
- `each_to_one`
- `one_to_each`
- `adjacent_pairs`

`source` and `target` must reference a created-node alias or one of these special aliases:

- `matched`
- `__neighbors__`

Example:

```yaml
create_edges:
  - source: room
    target: corridor
    edge_type: door
    mode: each_to_one
  - source: room
    target: room
    edge_type: wall
    mode: adjacent_pairs
```

## Compact Valid Rule Example

```yaml
grammar_rules:
  - name: expand_zone_to_room_cluster
    match:
      type: Zone
      is_abstract: true
    action:
      remove_matched_node: false
      create_nodes:
        - alias: corridor
          type: Corridor
          count: 1
          attributes:
            is_abstract: false
        - alias: room
          type: PatientRoom
          count:
            min: 3
            max: 6
          attributes:
            is_abstract: false
      create_edges:
        - source: room
          target: corridor
          edge_type: door
          mode: each_to_one
```

## Invalid Patterns To Avoid

Do not use unknown top-level grammar fields:

```yaml
grammar_rules:
  - name: bad_rule
    matcher:
      type: Zone
```

Do not use choices in `match`:

```yaml
match:
  type:
    choices:
      - Zone
      - Corridor
```

Do not use unsupported action fields:

```yaml
action:
  delete_node: true
```

Do not reference aliases that are not created in the same rule or reserved:

```yaml
create_edges:
  - source: patient_room
    target: corridor
    edge_type: door
```

Do not use unknown edge types:

```yaml
create_edges:
  - source: room
    target: corridor
    edge_type: adjacency
```

Do not use count values that are zero, negative, non-integers, or unordered:

```yaml
count:
  min: 6
  max: 3
```

## Validation Before Use

Generated YAML must be complete and directly validateable:

```bash
python -m graph_layout_synth validate-config --config outputs/llm_grammar_variant.yaml
```

Only use a generated config for graph generation after it passes validation.
