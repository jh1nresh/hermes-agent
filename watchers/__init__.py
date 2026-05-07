"""Watchers — interval-polling with watermark dedup, inspired by Vellum Assistant's watcher system.

A watcher periodically fetches data from an external source, compares new items
against a stored watermark, and — if new items exist — hands them to the agent
(or delivers them verbatim, in no-agent mode) as the prompt context.

Watchers are the **pull-based** sibling of webhooks. Webhooks: source pushes →
agent reacts. Watchers: scheduler pulls on an interval → agent reacts to what's
new. Cron is the time-based sibling: scheduler fires → agent runs a prompt,
regardless of whether anything changed.

Key design choices:
- Subscriptions live in ``~/.hermes/watchers.json`` (parallels webhooks).
- Watermarks live in ``~/.hermes/watchers/<name>.watermark.json`` so provider
  state can be inspected / reset per-watcher without touching the subscription
  file.
- Providers implement ``fetch_new(config, watermark) -> (items, new_watermark)``
  and are kept deliberately stateless so the scheduler can call them from any
  thread.
- Delivery reuses the cron ``deliver`` plumbing: ``local`` / ``origin`` /
  platform targets / ``multi`` / ``all``.

Ship-with providers: ``http_json``, ``rss``, ``github``.  Custom providers can
be registered via ``watchers.providers.register()``.
"""

from watchers.providers import (  # noqa: F401
    PROVIDERS,
    ProviderError,
    register,
    resolve_provider,
)
from watchers.store import (  # noqa: F401
    WatcherSubscription,
    delete_watcher,
    get_watcher,
    list_watchers,
    load_watermark,
    save_watcher,
    save_watermark,
)

__all__ = [
    "WatcherSubscription",
    "PROVIDERS",
    "ProviderError",
    "delete_watcher",
    "get_watcher",
    "list_watchers",
    "load_watermark",
    "register",
    "resolve_provider",
    "save_watcher",
    "save_watermark",
]
