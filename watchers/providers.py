"""Built-in watcher providers.

Each provider is a callable ``fetch_new(config, watermark) -> (items, new_watermark)``:

- ``config``: the provider-specific config dict from the subscription.
- ``watermark``: the opaque dict the provider returned last time (empty on first run).
- Returns a tuple of ``(new_items, new_watermark)``.

``new_items`` is a list of dicts (shape is provider-defined but should at
minimum include a human-readable ``title`` and ``url`` field where
applicable).  ``new_watermark`` is persisted verbatim and handed back to the
provider on the next tick.

Providers MUST be idempotent — if nothing new is available, return
``([], watermark)``.  Raising ``ProviderError`` marks the watcher as errored
for the tick but preserves the watermark (no data loss on transient
failures).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when a provider fetch fails transiently.

    Persistent config errors (missing required fields, bad URLs) should
    surface as ProviderError too; the watcher engine records ``last_error``
    on the subscription so ``hermes watch list`` shows the failure.
    """


# (config, watermark) -> (items, new_watermark)
ProviderFn = Callable[[Dict[str, Any], Dict[str, Any]], Tuple[List[Dict[str, Any]], Dict[str, Any]]]


PROVIDERS: Dict[str, ProviderFn] = {}


def register(name: str, fn: ProviderFn) -> None:
    """Register a custom provider under ``name``.

    Plugins can call this at import time to add watcher providers.  Names are
    case-insensitive and must be unique.
    """
    key = name.lower()
    PROVIDERS[key] = fn


def resolve_provider(name: str) -> ProviderFn:
    """Look up a provider by name. Raises KeyError if unknown."""
    key = (name or "").lower()
    if key not in PROVIDERS:
        raise KeyError(f"Unknown watcher provider: {name!r}. Known: {sorted(PROVIDERS)}")
    return PROVIDERS[key]


# ---------------------------------------------------------------------------
# Shared HTTP helper — honors optional header/timeout config across providers.
# ---------------------------------------------------------------------------


def _http_get(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Hermes-Watcher/1.0")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"HTTP error: {e.reason} ({url})") from e
    except (TimeoutError, OSError) as e:
        raise ProviderError(f"Network error: {e} ({url})") from e


# ---------------------------------------------------------------------------
# Provider: http_json
#
# Polls a JSON endpoint and treats the response as either:
#   - a top-level list of items, or
#   - a dict with an ``items_path`` pointing at the list via dotted keys.
#
# Dedup is by a configurable ``id_field`` (default ``"id"``).  Watermark stores
# a set of seen IDs, capped at ``max_seen`` (default 500) to bound memory.
# ---------------------------------------------------------------------------


def _dig(obj: Any, path: str) -> Any:
    """Dotted-path lookup: _dig({"a":{"b":[1,2]}}, "a.b") -> [1,2]."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _provider_http_json(
    config: Dict[str, Any], watermark: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    url = config.get("url")
    if not url:
        raise ProviderError("http_json: 'url' is required")

    id_field = config.get("id_field", "id")
    items_path = config.get("items_path", "")
    headers = config.get("headers") or {}
    max_seen = int(config.get("max_seen", 500))
    timeout = float(config.get("timeout", 20.0))

    raw = _http_get(url, headers=headers, timeout=timeout)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProviderError(f"http_json: response is not valid JSON: {e}") from e

    items = _dig(data, items_path) if items_path else data
    if not isinstance(items, list):
        raise ProviderError(
            f"http_json: items_path={items_path!r} did not resolve to a list"
            f" (got {type(items).__name__})"
        )

    seen_ids = set(watermark.get("seen_ids") or [])
    is_first_run = not watermark  # empty watermark = never polled

    new_items: List[Dict[str, Any]] = []
    new_seen_ids: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = item.get(id_field)
        if ident is None:
            continue
        ident_str = str(ident)
        new_seen_ids.append(ident_str)
        if ident_str in seen_ids:
            continue
        # On first run, record all IDs but don't emit any as "new" events.
        # Otherwise the first tick of a watcher would replay the entire feed.
        if is_first_run:
            continue
        new_items.append(item)

    # Cap the stored ID set.  Keep the most recent entries — the response
    # order is provider-defined, so we just preserve insertion order and
    # trim the head.
    combined = list(seen_ids) + [i for i in new_seen_ids if i not in seen_ids]
    if len(combined) > max_seen:
        combined = combined[-max_seen:]

    return new_items, {"seen_ids": combined, "last_polled_at": time.time()}


register("http_json", _provider_http_json)


# ---------------------------------------------------------------------------
# Provider: rss (Atom + RSS 2.0)
#
# Watermark = {"seen_guids": [...]}.  On first run, record all existing GUIDs
# as seen so the watcher only emits posts published AFTER it was created.
# ---------------------------------------------------------------------------


def _parse_rss_entries(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """Return a list of {id, title, url, published, summary} dicts.

    Handles both RSS 2.0 ``<item>`` and Atom ``<entry>``.  Namespaces are
    stripped for cleaner lookups.
    """

    def _strip_ns(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ProviderError(f"rss: invalid XML: {e}") from e

    entries: List[Dict[str, Any]] = []

    # RSS 2.0: <rss><channel><item>
    for item in root.iter():
        tag = _strip_ns(item.tag)
        if tag not in ("item", "entry"):
            continue
        children = {_strip_ns(c.tag): c for c in item}
        # ElementTree Elements with no children are *falsy* — use explicit
        # `is not None` checks when picking between possible tags.
        guid_el = children.get("guid")
        if guid_el is None:
            guid_el = children.get("id")
        link_el = children.get("link")
        if link_el is not None:
            # Atom: link is empty element with href attr; RSS: link has text.
            href = link_el.attrib.get("href") or (link_el.text or "").strip()
        else:
            href = ""
        guid = (guid_el.text.strip() if guid_el is not None and guid_el.text else "") or href
        if not guid:
            continue
        title_el = children.get("title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        pub_el = children.get("pubDate")
        if pub_el is None:
            pub_el = children.get("published")
        if pub_el is None:
            pub_el = children.get("updated")
        published = (pub_el.text or "").strip() if pub_el is not None else ""
        summ_el = children.get("description")
        if summ_el is None:
            summ_el = children.get("summary")
        summary = (summ_el.text or "").strip() if summ_el is not None else ""
        entries.append(
            {
                "id": guid,
                "title": title,
                "url": href,
                "published": published,
                "summary": summary,
            }
        )

    return entries


def _provider_rss(
    config: Dict[str, Any], watermark: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    url = config.get("url")
    if not url:
        raise ProviderError("rss: 'url' is required")
    headers = config.get("headers") or {}
    max_seen = int(config.get("max_seen", 500))
    timeout = float(config.get("timeout", 20.0))

    raw = _http_get(url, headers=headers, timeout=timeout)
    entries = _parse_rss_entries(raw)

    seen = set(watermark.get("seen_guids") or [])
    is_first_run = not watermark

    new_items: List[Dict[str, Any]] = []
    new_guids: List[str] = []
    for entry in entries:
        guid = entry["id"]
        new_guids.append(guid)
        if guid in seen:
            continue
        if is_first_run:
            continue
        new_items.append(entry)

    combined = list(seen) + [g for g in new_guids if g not in seen]
    if len(combined) > max_seen:
        combined = combined[-max_seen:]
    return new_items, {"seen_guids": combined, "last_polled_at": time.time()}


register("rss", _provider_rss)


# ---------------------------------------------------------------------------
# Provider: github
#
# Polls either:
#   - ``repo`` mode: https://api.github.com/repos/<owner>/<repo>/issues or /releases
#   - ``search`` mode: https://api.github.com/search/issues?q=<query>
#
# Authenticates with ``GITHUB_TOKEN`` if present (avoids 60 req/hr anon limit).
# ---------------------------------------------------------------------------


_GITHUB_SCOPES = {"issues", "pulls", "releases", "commits"}


def _provider_github(
    config: Dict[str, Any], watermark: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    import os

    repo = config.get("repo")
    scope = (config.get("scope") or "issues").lower()
    search_query = config.get("search")
    per_page = int(config.get("per_page", 30))

    if not repo and not search_query:
        raise ProviderError("github: one of 'repo' or 'search' is required")
    if scope not in _GITHUB_SCOPES and not search_query:
        raise ProviderError(
            f"github: scope must be one of {sorted(_GITHUB_SCOPES)} (got {scope!r})"
        )

    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo or "") and repo:
        raise ProviderError(f"github: repo must be 'owner/name' (got {repo!r})")

    if search_query:
        url = f"https://api.github.com/search/issues?q={urllib.parse.quote(search_query)}&per_page={per_page}"
        items_path = "items"
    elif scope == "commits":
        url = f"https://api.github.com/repos/{repo}/commits?per_page={per_page}"
        items_path = ""
    else:
        url = f"https://api.github.com/repos/{repo}/{scope}?per_page={per_page}&state=all"
        items_path = ""

    headers = {"Accept": "application/vnd.github+json"}
    token = config.get("token") or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    raw = _http_get(url, headers=headers, timeout=30.0)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProviderError(f"github: response is not valid JSON: {e}") from e

    items = _dig(data, items_path) if items_path else data
    if not isinstance(items, list):
        raise ProviderError(
            f"github: expected a list; got {type(items).__name__}"
        )

    id_field = "sha" if scope == "commits" else "id"

    seen = set(str(x) for x in (watermark.get("seen_ids") or []))
    is_first_run = not watermark

    new_items: List[Dict[str, Any]] = []
    all_ids: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get(id_field, "")) or ""
        if not ident:
            continue
        all_ids.append(ident)
        if ident in seen or is_first_run:
            continue
        # Flatten the interesting fields so the prompt template is readable.
        new_items.append(
            {
                "id": ident,
                "title": item.get("title") or item.get("name") or item.get("commit", {}).get("message", "").splitlines()[0:1] or "",
                "url": item.get("html_url") or item.get("url"),
                "number": item.get("number"),
                "state": item.get("state"),
                "author": (item.get("user") or {}).get("login") or (item.get("author") or {}).get("login"),
                "created_at": item.get("created_at") or (item.get("commit") or {}).get("author", {}).get("date"),
                "body": (item.get("body") or "")[:2000],  # cap so the prompt stays bounded
            }
        )

    combined = list(seen) + [i for i in all_ids if i not in seen]
    if len(combined) > int(config.get("max_seen", 500)):
        combined = combined[-int(config.get("max_seen", 500)):]

    return new_items, {"seen_ids": combined, "last_polled_at": time.time()}


register("github", _provider_github)


# Re-export urllib.parse lazily so providers can use it without each
# importing it separately.  (Doing the import at module-top would force it
# into every codepath that touches watchers.store.)
import urllib.parse  # noqa: E402
