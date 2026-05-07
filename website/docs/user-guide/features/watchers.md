---
title: Watchers
description: Poll external sources on an interval and trigger the agent when new items appear.
---

# Watchers

Watchers poll an external source on an interval, detect new items via watermark-based dedup, and either deliver those items verbatim or hand them to a short-lived agent for reasoning. They are the **pull-based sibling** of webhooks:

| Trigger | Source | When |
|---|---|---|
| Webhook | external push | whenever the source calls you |
| Cron | time-based | on a schedule, regardless of whether anything changed |
| **Watcher** | **pull on interval** | **only when new data is detected** |

Inspired by Vellum Assistant's watcher system.

## Quick start

```bash
# Watch a GitHub repo for new issues
hermes watch add my-repo \
  --provider github --repo NousResearch/hermes-agent --scope issues \
  --interval 300 --deliver origin

# Watch an RSS feed, deliver raw (no agent)
hermes watch add hn \
  --provider rss --url https://news.ycombinator.com/rss \
  --interval 900 --deliver telegram --deliver-only

# Watch any JSON endpoint
hermes watch add api-changes \
  --provider http_json --url https://api.example.com/events \
  --arg id_field=event_id --arg items_path=data.events \
  --interval 60
```

Watchers piggyback on the cron scheduler's tick loop — if cron is running (gateway or `hermes cron tick`), watchers run automatically.

## How dedup works

The first poll of a new watcher **records a baseline** and does NOT emit any events. This prevents the first tick from replaying the entire feed. Only items that appear after the baseline are delivered.

Each watcher keeps a **watermark file** at `~/.hermes/watchers/<name>.watermark.json` containing a bounded set of seen IDs. To force a replay (treat the next poll as first-run), use `hermes watch reset <name>`.

## Providers

### `http_json`

Polls a JSON endpoint and dedups by a configurable ID field.

| Config key | Default | Purpose |
|---|---|---|
| `url` | *(required)* | Endpoint to GET |
| `id_field` | `id` | Field used to dedup items |
| `items_path` | *(empty)* | Dotted path to the list (`data.events` etc.) if the response isn't a top-level list |
| `headers` | `{}` | Dict of HTTP headers |
| `max_seen` | `500` | Cap on the watermark ID set |
| `timeout` | `20.0` | Request timeout (seconds) |

### `rss`

Parses RSS 2.0 or Atom feeds. Dedups by `<guid>` / `<id>`.

| Config key | Default | Purpose |
|---|---|---|
| `url` | *(required)* | Feed URL |
| `headers` | `{}` | Optional HTTP headers |
| `max_seen` | `500` | Cap on the watermark GUID set |

### `github`

Polls GitHub's REST API. Auth via `GITHUB_TOKEN` / `GH_TOKEN` environment variable (or `token` in config).

| Config key | Default | Purpose |
|---|---|---|
| `repo` | — | `owner/name` (one of `repo` or `search` is required) |
| `scope` | `issues` | `issues` / `pulls` / `releases` / `commits` |
| `search` | — | GitHub issues search query (alternative to `repo`) |
| `per_page` | `30` | Results per page |

## Delivery

Uses the same delivery plumbing as cron — including the new `multi` / `all` routing intents. See [cron delivery options](./cron#delivery-options) for the full list of targets.

- `--deliver-only` sends the rendered prompt verbatim, skipping the agent (zero LLM cost). Good for digests / notifications.
- Without `--deliver-only`, the prompt is handed to a short-lived agent (skills optional via `--skills`), and the agent's final response is delivered.

## Prompt template

`--prompt` supports three placeholders (unknown ones pass through verbatim):

- `{name}` — the watcher name
- `{count}` — number of new items
- `{items_json}` — JSON array of new items

If `--prompt` is omitted, a default like `"{name}: {count} new event(s) from the {provider} watcher.\n\nItems:\n{items_json}"` is used.

## Custom providers

Plugins can register additional providers at import time:

```python
from watchers.providers import register

def my_provider(config, watermark):
    # Fetch, dedup, return (new_items, new_watermark).
    return [], watermark

register("my_provider", my_provider)
```

## Subcommands

| Command | Purpose |
|---|---|
| `hermes watch add <name> --provider <p> ...` | Create or update a watcher |
| `hermes watch list [--verbose]` | Show all watchers and their status |
| `hermes watch remove <name>` | Delete a watcher + its watermark |
| `hermes watch run <name>` | Fire one watcher out of band (respects dedup) |
| `hermes watch reset <name>` | Clear the watermark — next run treats it as first poll |
| `hermes watch tick` | Poll every due watcher now |
