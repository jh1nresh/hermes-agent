"""hermes watch — manage polling watchers from the CLI.

Usage:
    hermes watch add <name> --provider <p> --url <url> [--interval 300] ...
    hermes watch list
    hermes watch remove <name>
    hermes watch run <name>          # fire the watcher once, out of band
    hermes watch reset <name>        # clear the watermark; next run replays all

Watchers poll an external source on an interval, detect new items via
watermark-based dedup, and deliver the result (verbatim or via agent).
Subscriptions persist to ~/.hermes/watchers.json.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List

from hermes_constants import display_hermes_home
from watchers.engine import run_watcher, tick
from watchers.providers import PROVIDERS
from watchers.store import (
    WatcherSubscription,
    delete_watcher,
    get_watcher,
    list_watchers,
    load_watermark,
    save_watcher,
    save_watermark,
)


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _parse_kv_pairs(items: List[str], *, label: str) -> Dict[str, str]:
    """Parse a list of ``k=v`` strings into a dict; errors out cleanly."""
    out: Dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(
                f"--{label} expects key=value pairs; got {item!r}"
            )
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_config_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"--config must be valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"--config must be a JSON object; got {type(parsed).__name__}")
    return parsed


def watch_command(args) -> None:
    """Entry point for 'hermes watch' subcommand."""
    sub = getattr(args, "watch_action", None)

    if not sub:
        print("Usage: hermes watch {add|list|remove|run|reset}")
        print("Run 'hermes watch --help' for details.")
        return

    if sub in ("add", "subscribe"):
        _cmd_add(args)
    elif sub in ("list", "ls"):
        _cmd_list(args)
    elif sub in ("remove", "rm"):
        _cmd_remove(args)
    elif sub == "run":
        _cmd_run(args)
    elif sub == "reset":
        _cmd_reset(args)
    elif sub == "tick":
        _cmd_tick(args)
    else:
        print(f"Unknown watch subcommand: {sub}")


def _cmd_add(args) -> None:
    name = (args.name or "").strip().lower().replace(" ", "-")
    if not _NAME_RE.match(name):
        print(f"Error: name must be lowercase alphanumerics + '-'/'_' (got {args.name!r})")
        return

    provider = (args.provider or "").lower()
    if provider not in PROVIDERS:
        print(f"Error: unknown provider {args.provider!r}.")
        print(f"       Known providers: {sorted(PROVIDERS)}")
        return

    # Config assembly: --config JSON + individual --arg flags merged.
    try:
        cfg = _parse_config_json(getattr(args, "config", "") or "")
        cfg.update(_parse_kv_pairs(getattr(args, "arg", None) or [], label="arg"))
    except ValueError as e:
        print(f"Error: {e}")
        return

    # Convenience flags — providers that commonly need these get dedicated args.
    if getattr(args, "url", None):
        cfg.setdefault("url", args.url)
    if getattr(args, "repo", None):
        cfg.setdefault("repo", args.repo)
    if getattr(args, "scope", None):
        cfg.setdefault("scope", args.scope)

    existing = get_watcher(name)
    sub = WatcherSubscription(
        name=name,
        provider=provider,
        config=cfg,
        interval_seconds=int(args.interval),
        prompt=args.prompt or "",
        skills=[s.strip() for s in (args.skills or "").split(",") if s.strip()],
        deliver=args.deliver or "origin",
        deliver_only=bool(args.deliver_only),
        enabled=True,
        created_at=(existing.created_at if existing else time.time()),
    )
    save_watcher(sub)

    verb = "Updated" if existing else "Added"
    print(f"{verb} watcher '{name}':")
    print(f"  provider       = {sub.provider}")
    print(f"  interval       = {sub.interval_seconds}s")
    print(f"  deliver        = {sub.deliver}")
    print(f"  deliver_only   = {sub.deliver_only}")
    print(f"  config         = {json.dumps(sub.config, indent=2)}")
    if not existing:
        print()
        print(f"The watermark file will be created at {display_hermes_home()}/watchers/{name}.watermark.json")
        print("on first successful poll.  The first poll records baseline state and")
        print("does NOT fire; only items that appear AFTER the baseline are delivered.")


def _cmd_list(args) -> None:
    subs = list_watchers()
    if not subs:
        print("No watchers registered.")
        print()
        print("Add one with: hermes watch add <name> --provider <p> --url <url>")
        return

    print(f"{'NAME':<20} {'PROVIDER':<12} {'INTERVAL':<10} {'LAST RUN':<21} {'EVENTS':<7} {'STATUS'}")
    for sub in sorted(subs, key=lambda s: s.name):
        last_run = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sub.last_run_at))
            if sub.last_run_at
            else "never"
        )
        status = "disabled" if not sub.enabled else ("error" if sub.last_error else "ok")
        print(
            f"{sub.name:<20} {sub.provider:<12} {sub.interval_seconds!s:<10} "
            f"{last_run:<21} {sub.last_event_count:<7} {status}"
        )
        if sub.last_error and getattr(args, "verbose", False):
            print(f"    ↳ last_error: {sub.last_error}")


def _cmd_remove(args) -> None:
    name = args.name.strip().lower()
    if delete_watcher(name):
        print(f"Removed watcher '{name}' and cleared its watermark.")
    else:
        print(f"No watcher named '{name}'.")


def _cmd_run(args) -> None:
    name = args.name.strip().lower()
    sub = get_watcher(name)
    if not sub:
        print(f"No watcher named '{name}'.")
        return
    print(f"Polling watcher '{name}' ({sub.provider})...")
    outcome = run_watcher(sub)
    sub.last_run_at = time.time()
    sub.last_error = outcome.get("error")
    sub.last_event_count = outcome.get("new_events", 0)
    save_watcher(sub)
    print(f"  status      = {outcome.get('status')}")
    print(f"  new events  = {outcome.get('new_events')}")
    if outcome.get("error"):
        print(f"  error       = {outcome['error']}")


def _cmd_reset(args) -> None:
    name = args.name.strip().lower()
    if not get_watcher(name):
        print(f"No watcher named '{name}'.")
        return
    save_watermark(name, {})
    print(f"Cleared watermark for '{name}'. Next poll will treat it as a first run.")


def _cmd_tick(args) -> None:
    """Fire every due watcher once and print outcomes (CLI-only ad-hoc poll)."""
    outcomes = tick(verbose=True)
    if not outcomes:
        print("No watchers due.")
        return
    for o in outcomes:
        flag = "!" if o["status"] != "ok" else " "
        err = f"  [{o['error']}]" if o.get("error") else ""
        print(f" {flag} {o['name']:<20} {o['status']:<8} events={o['new_events']}{err}")
