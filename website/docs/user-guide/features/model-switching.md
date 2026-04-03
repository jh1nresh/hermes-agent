---
sidebar_position: 3
---

# Switching Models

Change models mid-conversation without losing your chat history.

```
/model sonnet
```

That's it. Your conversation continues with the new model. Hermes formats the name correctly for whatever provider you're on — you don't need to think about it.

## Quick Reference

| You type | You get |
|----------|---------|
| `/model sonnet` | Claude Sonnet 4.6 |
| `/model opus` | Claude Opus 4.6 |
| `/model haiku` | Claude Haiku 4.5 |
| `/model gpt5` | GPT-5.4 |
| `/model gpt5-mini` | GPT-5.4 Mini |
| `/model gpt5-pro` | GPT-5.4 Pro |
| `/model codex` | GPT-5.3 Codex |
| `/model gemini` | Gemini 3 Pro |
| `/model gemini-flash` | Gemini 3 Flash |
| `/model deepseek` | DeepSeek Chat |
| `/model grok` | Grok 4.20 |
| `/model qwen` | Qwen 3.6 Plus |
| `/model minimax` | MiniMax M2.7 |

These aliases **stay on your current provider**. If you're on OpenRouter, you stay on OpenRouter. If you're on native Anthropic, you stay on native Anthropic. The model name is formatted correctly for each — `anthropic/claude-sonnet-4.6` on OpenRouter becomes `claude-sonnet-4-6` on native Anthropic automatically.

If the model isn't available on your current provider (like `/model gpt5` on native Anthropic), Hermes will switch to a provider that has it and tell you.

Type `/model` with no arguments to see the full alias list and your current model.

## Full Model Names

Aliases cover the most popular models. For anything else, use the full name in your provider's format:

```
/model anthropic/claude-sonnet-4.5
/model openai/gpt-5.4-nano
/model nvidia/nemotron-3-super-120b-a12b
```

On OpenRouter these are the standard model IDs from [openrouter.ai/models](https://openrouter.ai). On other providers, use whatever model name that provider expects.

If you're not sure of the exact name, type something close. Hermes will suggest corrections:

```
> /model claude-sonet
  Note: Not in catalog — did you mean: anthropic/claude-sonnet-4.6?
```

## Switching Providers

Aliases and bare model names keep you on your current provider. To explicitly switch to a different provider, use the provider prefix with a colon:

```
/model anthropic:claude-opus-4
/model deepseek:deepseek-chat
/model nous:anthropic/claude-opus-4.6
```

The part before the colon is the Hermes provider name (the same names from `hermes setup`). The part after is the model name as that provider knows it.

To see which providers you have configured: `/provider`

:::tip
On OpenRouter, you can also use `openai:gpt-5.4` — Hermes knows "openai" is a vendor name on OpenRouter (not a separate Hermes provider) and converts it to `openai/gpt-5.4` automatically.
:::

## Custom / Local Endpoints

If you've set up a local model server (Ollama, vLLM, LM Studio, etc.):

```
/model custom
```

This auto-detects the model running on your custom endpoint. If you have multiple models or want to specify one:

```
/model custom:llama-3.3-70b
```

Custom endpoints are configured in `~/.hermes/config.yaml` under `model.base_url`, or via the `OPENAI_BASE_URL` environment variable.

## What Happens When You Switch

- **Conversation history is preserved.** The new model picks up where the old one left off.
- **Prompt cache resets.** The new model builds a fresh cache. This is unavoidable — different models have different cache keys.
- **System prompt rebuilds.** Some models get tailored guidance (tool use patterns, etc.). The system prompt updates automatically.
- **Config is saved.** The new model becomes your default for future sessions too.

## Where It Works

`/model` works everywhere Hermes runs:

- CLI (`hermes chat`)
- Telegram
- Discord
- Slack
- Matrix
- WhatsApp
- Signal
- Home Assistant
- All other gateway platforms

On messaging platforms, if the agent is currently processing a message, `/model` will ask you to wait or `/stop` first.
