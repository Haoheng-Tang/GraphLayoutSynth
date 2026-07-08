# Add LLM grammar variant control plane

## Summary

This PR adds an optional HTTP control plane for proposing, recording,
inspecting, and activating LLM-proposed GraphLayoutSynth grammar/config
variants.

The existing CLI workflow remains intact:

```bash
python -m graph_layout_synth propose-grammar-variant
```

The new HTTP workflow reuses the existing grammar-variant assistant logic for
prompt building, Claude calls, YAML extraction, and validation. The LLM still
only proposes complete YAML configs. It does not generate raw NetworkX graphs,
does not bypass validation, and is never called by normal
`POST /suggest-next-room` requests.

Static suggestion mode remains the default.

## Motivation

The previous implementation had the core LLM grammar/config proposal machinery
but no backend control plane around it. An audit confirmed these pieces already
existed:

- CLI proposal and dry-run support
- heuristic/design instructions
- prompt building
- Claude call boundary
- YAML extraction
- YAML/config validation
- valid YAML output
- invalid YAML sidecar output
- `/suggest-next-room` config-path support
- static config as the default
- no LLM calls during normal suggestion requests

This PR adds the missing backend layer:

- HTTP endpoints
- structured artifact directories
- registry records
- active variant pointer
- valid-only activation
- active-variant suggestion mode

## Feature gate

The grammar variant endpoints are disabled by default.

Enable them with:

```powershell
$env:GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS = "true"
$env:GRAPHLAYOUTSYNTH_LLM_VARIANT_DIR = "outputs/llm_variants"
python -m uvicorn server.main:app --reload --port 8000
```

If the feature flag is unset or false, the grammar variant endpoints return a
controlled disabled response. `GET /health` and `POST /suggest-next-room` keep
working.

## Endpoints

### Propose variant

```http
POST /grammar-variants/propose
Content-Type: application/json
```

Request body:

```json
{
  "heuristicInstructions": "Increase patient/support room mix using the existing schema.",
  "baseConfigPath": "configs/generic_building.yaml",
  "variantRequirements": null,
  "activateIfValid": false,
  "dryRun": true,
  "model": "claude-sonnet-4-6"
}
```

Behavior:

- creates a structured variant artifact directory
- writes heuristic instructions and prompt artifacts
- in dry-run mode, does not call Claude and does not require an API key
- in live mode, calls the existing Claude proposal boundary
- extracts YAML
- validates YAML/config before marking the record valid
- records invalid or failed proposals without activation
- optionally activates a valid proposal when `activateIfValid` is true

### List variants

```http
GET /grammar-variants
```

Returns compact registry records and active status. It does not return the full
raw LLM response.

### Inspect variant

```http
GET /grammar-variants/{variant_id}
```

Returns registry metadata, artifact paths, stored metadata, and validated YAML
content when present and small enough to return safely.

### Activate variant

```http
POST /grammar-variants/{variant_id}/activate
```

Only variants with status `valid` can be activated. Dry-run, invalid, and
failed variants are rejected.

Activation writes the active pointer and updates registry records so exactly
one variant is active.

## Artifacts and registry

Each proposal is saved under:

```txt
outputs/llm_variants/<variant_id>/
```

Possible files:

- `metadata.json`
- `heuristic_instructions.md`
- `base_config_path.txt`
- `prompt.md`
- `raw_llm_response.md`
- `extracted_variant.yaml`
- `validated_variant.yaml`
- `invalid_variant.yaml`
- `validation_report.json`
- `rationale.md`

The registry is:

```txt
outputs/llm_variants/registry.json
```

The active pointer is:

```txt
outputs/llm_variants/active_variant.json
```

Records include:

- `variantId`
- `createdAt`
- `status`: `dry_run`, `valid`, `invalid`, or `failed`
- `active`
- `baseConfigPath`
- `validatedConfigPath`, when valid
- `artifactDir`
- `heuristicSummary`
- optional model name
- optional error summary
- compact validation summary
- artifact paths

No API keys or secrets are saved.

## Suggestion config modes

`POST /suggest-next-room` keeps static behavior by default and still does not
call the LLM.

Config selection is controlled with:

```powershell
$env:GRAPHLAYOUTSYNTH_GRAMMAR_MODE = "static"
$env:GRAPHLAYOUTSYNTH_GRAMMAR_MODE = "env_config"
$env:GRAPHLAYOUTSYNTH_GRAMMAR_MODE = "active_variant"
```

Modes:

- `static`: always use `configs/generic_building.yaml`
- `env_config`: use `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG`
- `active_variant`: use `outputs/llm_variants/active_variant.json`

Backward-compatible behavior is preserved when `GRAPHLAYOUTSYNTH_GRAMMAR_MODE`
is omitted:

- if `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is set, suggestions use that config
- otherwise suggestions use the static default config

If `active_variant` mode is selected without a valid active pointer, suggestion
generation fails explicitly instead of silently falling back to static config.

## Implementation notes

### New service layer

Adds:

```txt
graph_layout_synth/grammar_variant_control_plane.py
```

This module owns:

- feature flag helpers
- artifact directory creation
- registry read/write
- active pointer read/write
- dry-run proposal flow
- live proposal flow wrapping the existing assistant
- validation report writing
- valid-only activation
- active variant config path resolution

The module reuses:

- `build_grammar_variant_prompt`
- `propose_grammar_variant_with_claude`
- `extract_yaml_from_llm_response`
- `validate_variant_yaml_text`
- `validate_room_mix_targets`
- `extract_rationale_from_llm_response`
- `validate_config_file`

### API models/routes

Adds `GrammarVariantProposeRequest` and the four control-plane endpoints in
`server.main`.

### Suggestion sampler

Updates `ExistingGeneratorSampler.resolved_config()` to support:

- static mode
- env-config mode
- active-variant mode

The sampler still calls the normal deterministic graph generator. It does not
call Claude.

## Error handling

The control plane records and returns controlled failures for:

- disabled feature flag
- missing API key during live proposal
- missing optional Anthropic SDK
- LLM request failure
- YAML extraction failure
- config validation failure
- activation attempts against non-valid variants
- active-variant mode without an active pointer

Static `/suggest-next-room` is unaffected by proposal failures.

## Tests

Adds:

```txt
tests/test_grammar_variant_control_plane.py
```

Coverage includes:

- endpoints disabled unless `GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true`
- dry-run proposal writes artifacts and registry without API key
- dry-run proposal does not call Claude
- mocked valid proposal writes structured artifacts
- invalid YAML produces an invalid record
- mocked LLM failure produces a failed record
- invalid, failed, and dry-run variants cannot activate
- valid variants can activate
- `active_variant.json` is written
- registry marks exactly one variant active
- static suggestion mode remains default
- env-config mode uses `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG`
- active-variant mode uses the active validated config
- active-variant mode without a pointer fails explicitly
- missing API key returns a controlled error

## Verification

Pre-merge verification passed:

```txt
python -m pytest -q
176 passed, 1 warning
```

```txt
python -m graph_layout_synth validate-config --config configs/generic_building.yaml
Config is valid: configs\generic_building.yaml.
```

```txt
git diff --check
passed; only LF-to-CRLF working-copy warnings were printed
```

An additional in-process FastAPI smoke script verified:

- static `GET /health`
- static `POST /suggest-next-room`
- disabled endpoint behavior
- dry-run artifacts and registry
- mocked valid proposal artifacts
- activation and active pointer
- invalid/failed/dry-run activation rejection
- active-variant suggestion config selection
- explicit failure when active-variant mode lacks a pointer
- env-config compatibility
- old behavior when only `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` is set

## Manual smoke commands

Enable the control plane:

```powershell
$env:GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS = "true"
$env:GRAPHLAYOUTSYNTH_LLM_VARIANT_DIR = "outputs/llm_variants"
python -m uvicorn server.main:app --reload --port 8000
```

Dry-run proposal:

```bash
curl -X POST http://localhost:8000/grammar-variants/propose \
  -H "Content-Type: application/json" \
  -d '{
    "heuristicInstructions": "Increase patient/support room mix using the existing schema.",
    "dryRun": true
  }'
```

List, inspect, activate:

```bash
curl http://localhost:8000/grammar-variants
curl http://localhost:8000/grammar-variants/<variant_id>
curl -X POST http://localhost:8000/grammar-variants/<variant_id>/activate
```

Use the active variant for suggestions:

```powershell
$env:GRAPHLAYOUTSYNTH_GRAMMAR_MODE = "active_variant"
python -m uvicorn server.main:app --reload --port 8000
```

Use an explicit config path:

```powershell
$env:GRAPHLAYOUTSYNTH_GRAMMAR_MODE = "env_config"
$env:GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG = "outputs/llm_grammar_variant.yaml"
python -m uvicorn server.main:app --reload --port 8000
```

## Non-goals

This PR does not:

- add a frontend UI
- change NextRoomPredictor
- make `/suggest-next-room` call the LLM directly
- call the LLM once per graph sample
- change semantic matching
- change neighbor aggregation
- add two-hop matching
- return raw generated graphs from normal suggestion responses
- expose API keys or secrets
