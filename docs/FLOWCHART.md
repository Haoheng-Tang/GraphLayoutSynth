Flowchart of the CLAUDE variant proposal workflow.

```mermaid
flowchart LR
  CLI[CLI: propose-grammar-variant] --> PROMPT["Build prompt\n(outputs/grammar_variant_prompt.md)"]
  PROMPT --> BASE[Base config\n[configs/generic_building.yaml]]
  PROMPT --> SKILLS[Grammar skills\n[docs/GRAMMAR_CONFIG_SKILLS.md]]
  PROMPT --> REQ[Variant requirements (optional)\n[docs/PATIENT_SUPPORT_ROOM_MIX_REQUIREMENTS.yaml]]
  CLI --> ASSIST["grammar_variant_assistant.py\n(graph_layout_synth/grammar_variant_assistant.py)"]
  ASSIST --> LOADENV[Load env (.env.local) -> ANTHROPIC_API_KEY]
  ASSIST --> PROMPT
  ASSIST --> CLAUDE[Claude (Anthropic) via SDK]
  CLAUDE --> RAW["Write raw response\noutputs/llm_grammar_variant_raw.md"]
  RAW --> EXTRACT["extract_yaml_from_llm_response"]
  EXTRACT --> VALIDATE["validate_variant_yaml_text\n-> graph_layout_synth.config.validate_config"]
  VALIDATE --> ROOMMIX_CHECK{"require_room_mix_targets?"}
  ROOMMIX_CHECK -- yes --> VALIDATE_ROOMMIX["validate_room_mix_targets\n(grammar_variant_assistant.py)"]
  VALIDATE_ROOMMIX -- pass --> WRITE_OK["Write final YAML + rationale\noutputs/llm_grammar_variant.yaml\noutputs/llm_grammar_variant_rationale.md"]
  VALIDATE_ROOMMIX -- fail --> WRITE_INVALID["Write invalid YAML sidecar\noutputs/llm_grammar_variant.invalid.yaml"]
  ROOMMIX_CHECK -- no --> WRITE_OK
  VALIDATE -- fail --> WRITE_INVALID
  WRITE_OK --> DONE[Done / ready for generate]
```
