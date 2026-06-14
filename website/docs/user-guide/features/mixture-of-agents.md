---
sidebar_position: 7
title: "Mixture of Agents"
description: "Use /moa to run one prompt through multiple configured models inside the normal Hermes agent loop"
---

# Mixture of Agents

Mixture of Agents is now a slash-command mode, not a model tool.

Use it when a hard prompt benefits from multiple model perspectives but still needs Hermes' normal agent loop: tool calls, follow-up iterations, interrupts, transcript persistence, and the same session context as any other message.

```bash
/moa design and implement a migration plan for this flaky test cluster
```

Hermes runs the configured reference models first, asks the configured aggregator to synthesize their guidance, then injects that private guidance into the next normal agent iteration. If the main model calls a tool, Hermes continues the usual tool loop and refreshes MoA guidance on the next model iteration.

## Configure the model set

You can configure MoA from:

- Dashboard → Models → Model Settings → Mixture of Agents
- Desktop app → Settings → Model → Mixture of Agents
- `config.yaml`

The config stores explicit provider/model pairs, so you can mix providers and use multiple models from the same provider:

```yaml
moa:
  reference_models:
    - provider: openai-codex
      model: gpt-5.5
    - provider: openrouter
      model: deepseek/deepseek-v4-pro
  aggregator:
    provider: openrouter
    model: anthropic/claude-opus-4.8
  reference_temperature: 0.6
  aggregator_temperature: 0.4
  max_tokens: 4096
```

OpenRouter Fusion presets are valid model choices here too. Define any number
of custom Fusion slugs under `fusion.presets`; those slugs appear under the
configured provider (`openrouter` or `nous`) in `hermes model`, `/model`, and
the dashboard picker.

```yaml
fusion:
  presets:
    - provider: openrouter      # or nous
      slug: research-panel      # selectable as fusion/research-panel
      analysis_models:
        - anthropic/claude-opus-4.8
        - openai/gpt-5.5
        - google/gemini-3.5-flash
      judge_model: openai/gpt-5.5   # optional
      max_tool_calls: 8             # optional
      max_completion_tokens: 4096   # optional

moa:
  reference_models:
    - provider: openrouter
      model: fusion/research-panel
  aggregator:
    provider: openrouter
    model: fusion/research-panel
```

At request time Hermes sends the preset as `model: openrouter/fusion` plus the
configured `openrouter:fusion` server-tool payload, so the user-facing slug is
stable while the analysis panel stays configurable.

Defaults use one Codex OAuth model and two OpenRouter-hosted models for high-signal testing:

- `openai-codex:gpt-5.5`
- `openrouter:deepseek/deepseek-v4-pro`
- aggregator: `openrouter:anthropic/claude-opus-4.8`

## Notes

- `/moa` is per-turn. A regular message after it is not automatically MoA-routed.
- MoA is no longer listed under `hermes tools`; there is no `moa` toolset to enable.
- Credential failures on one reference model do not abort the turn. Hermes includes the failure in the synthesis context and continues with whatever models returned.
