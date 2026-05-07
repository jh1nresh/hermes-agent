"""Watcher engine — the tick-time poller that runs every watcher whose
interval has elapsed, resolves any new items, and delivers them (verbatim
in ``deliver_only`` mode, or as prompt context to a short-lived agent).

Safe to call from the cron scheduler tick loop: guarded by its own file
lock so concurrent ticks (gateway in-process + standalone daemon) don't
double-fire.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from hermes_constants import get_hermes_home
from watchers.providers import ProviderError, resolve_provider
from watchers.store import (
    WatcherSubscription,
    list_watchers,
    load_watermark,
    save_watcher,
    save_watermark,
)

logger = logging.getLogger(__name__)

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore
try:
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - Unix
    msvcrt = None  # type: ignore


def _lock_path() -> Path:
    d = get_hermes_home() / "watchers"
    d.mkdir(parents=True, exist_ok=True)
    return d / ".tick.lock"


def _is_due(sub: WatcherSubscription, *, now: Optional[float] = None) -> bool:
    """Has enough time elapsed since the last run?"""
    if not sub.enabled:
        return False
    now = now or time.time()
    if sub.last_run_at is None:
        return True
    return (now - sub.last_run_at) >= max(5, int(sub.interval_seconds))


def _render_prompt(template: str, items: List[Dict[str, Any]], sub: WatcherSubscription) -> str:
    """Render the watcher prompt template with the new items.

    If ``template`` is empty, a sensible default is produced so the user
    doesn't have to write a prompt for a quick ``hermes watch add``.
    ``{items_json}`` / ``{name}`` / ``{count}`` are recognized placeholders;
    unknown placeholders are left verbatim so they don't raise at render
    time.
    """
    payload = json.dumps(items, indent=2, default=str)
    if not template:
        return (
            f"{sub.name}: {len(items)} new event(s) from the {sub.provider} watcher.\n\n"
            f"Items:\n{payload}"
        )

    placeholders = {
        "items_json": payload,
        "name": sub.name,
        "count": str(len(items)),
    }

    def _replace(match: "re.Match[str]") -> str:  # type: ignore[name-defined]
        key = match.group(1)
        return placeholders.get(key, match.group(0))

    import re

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _replace, template)


# ---------------------------------------------------------------------------
# Delivery — re-uses cron delivery plumbing so deliver="multi" etc. work
# identically.  We piggyback on cron's ``_deliver_result`` by staging a
# synthetic "job" dict that looks like a no-agent cron job.
# ---------------------------------------------------------------------------


def _deliver_payload(
    sub: WatcherSubscription,
    content: str,
    *,
    adapters: Optional[Dict[str, Any]] = None,
    loop: Any = None,
) -> Optional[str]:
    """Deliver ``content`` using the cron delivery plumbing.

    Returns None on success or an error string.
    """
    try:
        from cron.scheduler import _deliver_result
    except ImportError as e:
        return f"cron delivery unavailable: {e}"

    synthetic_job = {
        "id": f"watcher:{sub.name}",
        "name": f"watch/{sub.name}",
        "deliver": sub.deliver,
        "origin": None,
    }
    try:
        return _deliver_result(synthetic_job, content, adapters=adapters, loop=loop)
    except Exception as e:
        logger.exception("Watcher %s: delivery crashed: %s", sub.name, e)
        return f"delivery crashed: {e}"


# ---------------------------------------------------------------------------
# Agent dispatch — when deliver_only is False, we hand the items to a
# short-lived AIAgent (same pattern cron uses for agent-mode jobs).  In
# deliver_only mode, the rendered prompt is sent verbatim as the message.
# ---------------------------------------------------------------------------


def _run_agent_for_watcher(
    sub: WatcherSubscription,
    prompt: str,
    *,
    adapters: Optional[Dict[str, Any]] = None,
    loop: Any = None,
) -> str:
    """Run a one-shot agent session for the watcher and return its final response.

    Errors are captured and returned as error strings so the tick loop doesn't
    crash on one misbehaving watcher.
    """
    try:
        from run_agent import AIAgent
    except ImportError as e:
        return f"[watcher error: AIAgent unavailable: {e}]"

    try:
        agent = AIAgent(
            quiet_mode=True,
            platform="watcher",
            session_id=f"watcher-{sub.name}-{int(time.time())}",
            skip_context_files=False,
            skip_memory=True,
            save_trajectories=False,
        )
        if sub.skills:
            # Skill loading is best-effort; if a skill is missing we note it
            # and proceed.  The watcher is generally running unattended.
            try:
                from agent.skill_tools import skill_view as _skv  # noqa: F401
            except Exception:
                pass
        return agent.chat(prompt) or ""
    except Exception as e:
        logger.exception("Watcher %s: agent crashed: %s", sub.name, e)
        return f"[watcher error: agent crashed: {e}]"


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def run_watcher(
    sub: WatcherSubscription,
    *,
    now: Optional[float] = None,
    adapters: Optional[Dict[str, Any]] = None,
    loop: Any = None,
) -> Dict[str, Any]:
    """Execute one poll cycle for a single watcher.

    Returns a dict summarizing the outcome::

        {
          "name": "...", "status": "ok"|"error"|"skipped",
          "new_events": N, "error": None|str,
        }

    The caller is responsible for persisting updates — we return rather than
    mutate the global store so callers with custom storage can compose the
    engine piece-by-piece.
    """
    now = now or time.time()
    result = {"name": sub.name, "status": "ok", "new_events": 0, "error": None}

    watermark = load_watermark(sub.name)
    try:
        provider = resolve_provider(sub.provider)
    except KeyError as e:
        result.update(status="error", error=str(e))
        return result

    try:
        new_items, new_watermark = provider(sub.config, watermark)
    except ProviderError as e:
        logger.warning("Watcher %s: provider error: %s", sub.name, e)
        result.update(status="error", error=str(e))
        return result
    except Exception as e:  # defensive — never let a provider crash the tick
        logger.exception("Watcher %s: unexpected provider failure: %s", sub.name, e)
        result.update(status="error", error=f"unexpected: {e}")
        return result

    # Persist the watermark even on empty polls so ``last_polled_at`` etc.
    # advance (useful for observability).
    save_watermark(sub.name, new_watermark)

    if not new_items:
        result["new_events"] = 0
        return result

    result["new_events"] = len(new_items)
    prompt = _render_prompt(sub.prompt, new_items, sub)

    if sub.deliver_only:
        err = _deliver_payload(sub, prompt, adapters=adapters, loop=loop)
        if err:
            result.update(status="error", error=err)
        return result

    # Agent mode: run one-shot agent, then deliver its final output.
    response = _run_agent_for_watcher(sub, prompt, adapters=adapters, loop=loop)
    if response:
        err = _deliver_payload(sub, response, adapters=adapters, loop=loop)
        if err:
            result.update(status="error", error=err)
    return result


def tick(
    *,
    now: Optional[float] = None,
    adapters: Optional[Dict[str, Any]] = None,
    loop: Any = None,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """Poll every due watcher and return per-watcher outcomes.

    Uses a file lock so concurrent ticks (gateway + daemon) don't double-fire.
    Returns an empty list silently if another tick holds the lock.
    """
    lock_file = _lock_path()
    lock_fd = None
    try:
        lock_fd = open(lock_file, "w")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        logger.debug("Watcher tick skipped — another instance holds the lock")
        if lock_fd is not None:
            lock_fd.close()
        return []

    try:
        outcomes: List[Dict[str, Any]] = []
        for sub in list_watchers():
            if not _is_due(sub, now=now):
                continue
            if verbose:
                logger.info("Watcher %s: polling (%s)", sub.name, sub.provider)
            outcome = run_watcher(sub, now=now, adapters=adapters, loop=loop)
            # Persist timing + last error back to the subscription.
            sub.last_run_at = now or time.time()
            sub.last_error = outcome.get("error")
            sub.last_event_count = outcome.get("new_events", 0)
            save_watcher(sub)
            outcomes.append(outcome)
        return outcomes
    finally:
        if lock_fd is not None:
            try:
                if fcntl:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            lock_fd.close()
