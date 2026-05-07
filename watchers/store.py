"""Watcher subscription + watermark storage.

Subscriptions persist to ``<hermes_home>/watchers.json``.  Watermarks live
under ``<hermes_home>/watchers/<name>.watermark.json`` — separate files so
``hermes watch reset <name>`` can nuke a single watchermark without touching
the subscription, and so provider state can be inspected in isolation.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from utils import atomic_replace


_SUBSCRIPTIONS_FILENAME = "watchers.json"


@dataclass
class WatcherSubscription:
    """A watcher subscription as persisted to ``watchers.json``.

    Mirrors the webhook subscription shape where possible so users who know
    one subsystem can reason about the other.
    """

    name: str
    provider: str
    config: Dict[str, Any] = field(default_factory=dict)
    interval_seconds: int = 300
    prompt: str = ""
    skills: List[str] = field(default_factory=list)
    deliver: str = "origin"
    deliver_only: bool = False
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_run_at: Optional[float] = None
    last_error: Optional[str] = None
    last_event_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WatcherSubscription":
        """Tolerant of missing keys — old subscription files gain defaults."""
        return cls(
            name=d["name"],
            provider=d["provider"],
            config=dict(d.get("config") or {}),
            interval_seconds=int(d.get("interval_seconds", 300)),
            prompt=str(d.get("prompt", "")),
            skills=list(d.get("skills") or []),
            deliver=str(d.get("deliver", "origin")),
            deliver_only=bool(d.get("deliver_only", False)),
            enabled=bool(d.get("enabled", True)),
            created_at=float(d.get("created_at") or time.time()),
            last_run_at=(float(d["last_run_at"]) if d.get("last_run_at") else None),
            last_error=d.get("last_error"),
            last_event_count=int(d.get("last_event_count") or 0),
        )


# ---------------------------------------------------------------------------
# Path helpers — resolved at call time so HERMES_HOME overrides work in tests.
# ---------------------------------------------------------------------------


def _subscriptions_path() -> Path:
    return get_hermes_home() / _SUBSCRIPTIONS_FILENAME


def _watermark_dir() -> Path:
    return get_hermes_home() / "watchers"


def _watermark_path(name: str) -> Path:
    return _watermark_dir() / f"{name}.watermark.json"


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


def _load_all() -> Dict[str, Dict[str, Any]]:
    path = _subscriptions_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_all(subs: Dict[str, Dict[str, Any]]) -> None:
    path = _subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs, indent=2, ensure_ascii=False), encoding="utf-8")
    atomic_replace(tmp, path)


def list_watchers() -> List[WatcherSubscription]:
    return [WatcherSubscription.from_dict(v) for v in _load_all().values()]


def get_watcher(name: str) -> Optional[WatcherSubscription]:
    raw = _load_all().get(name)
    return WatcherSubscription.from_dict(raw) if raw else None


def save_watcher(sub: WatcherSubscription) -> None:
    all_subs = _load_all()
    all_subs[sub.name] = sub.to_dict()
    _save_all(all_subs)


def delete_watcher(name: str) -> bool:
    all_subs = _load_all()
    if name not in all_subs:
        return False
    del all_subs[name]
    _save_all(all_subs)
    # Also remove watermark file so re-adding doesn't inherit stale state.
    wm = _watermark_path(name)
    if wm.exists():
        try:
            wm.unlink()
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# Watermark store — opaque per-provider state.
# ---------------------------------------------------------------------------


def load_watermark(name: str) -> Dict[str, Any]:
    """Return the persisted watermark dict for ``name`` (empty if unset)."""
    path = _watermark_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_watermark(name: str, watermark: Dict[str, Any]) -> None:
    """Persist the watermark dict for ``name``.

    Providers should pass back whatever they were given via their ``fetch_new``
    return value.  The scheduler persists it verbatim — the shape is entirely
    up to the provider.
    """
    path = _watermark_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(watermark, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    atomic_replace(tmp, path)
