"""
Shared helper for discovering and communicating with a profile's running
gateway HTTP management API.

Usage
-----
    from hermes_cli.gateway_http import get_profile_gateway, call_profile_gateway

    info = get_profile_gateway("worker")
    if info:
        # Gateway is running — talk to it
        result = await call_profile_gateway("worker", "GET", "/api/config")
    else:
        # Not running — fall back to direct file access

The gateway writes ``{HERMES_HOME}/gateway_http.json`` when it starts.
This module reads that file for any profile to obtain the host/port/token.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Token header name — matches the dashboard and the gateway's auth middleware.
_TOKEN_HEADER = "X-Hermes-Session-Token"


def _get_profile_home(profile: Optional[str]) -> Optional[Path]:
    """Resolve a profile name to its HERMES_HOME directory.

    Returns None for the default/current profile (callers use get_hermes_home()
    directly).
    """
    if not profile or profile.lower() in ("default", "current", ""):
        return None
    try:
        from hermes_cli.profiles import get_profile_dir, profile_exists
        if not profile_exists(profile):
            return None
        return get_profile_dir(profile)
    except Exception:
        return None


def get_profile_gateway(profile: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return the running gateway's HTTP info for a profile, or None.

    Returns a dict with ``base_url``, ``ws_url``, and ``token`` when a gateway
    is running for the given profile — or ``None`` when no gateway is up (file
    absent, stale PID, or gateway not configured with HTTP).

    Callers must fall back to direct file access when this returns ``None``.

    :param profile: Profile name, or None/'' for the current default profile.
    """
    from gateway.status import read_gateway_http_info

    home = _get_profile_home(profile)
    return read_gateway_http_info(home)


async def call_profile_gateway(
    profile: Optional[str],
    method: str,
    path: str,
    **httpx_kwargs: Any,
) -> Optional[Any]:
    """Call a profile's gateway HTTP API.

    Returns the parsed JSON response, or ``None`` when the gateway isn't
    running (so callers can fall back to ``_profile_scope``).

    Raises ``httpx.HTTPStatusError`` on HTTP 4xx/5xx.

    :param profile: Profile name, or None for the default profile.
    :param method: HTTP method (GET, POST, PUT, DELETE, PATCH).
    :param path: Path including leading slash, e.g. ``"/api/config"``.
    :param httpx_kwargs: Extra kwargs forwarded to ``httpx.AsyncClient.request``
                         (e.g. ``json=...``, ``params=...``).
    """
    info = get_profile_gateway(profile)
    if not info:
        return None

    try:
        import httpx
    except ImportError:
        logger.debug("httpx not available; cannot proxy to profile gateway")
        return None

    url = f"{info['base_url']}{path}"
    headers = {_TOKEN_HEADER: info["token"]}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, **httpx_kwargs)
            resp.raise_for_status()
            return resp.json() if resp.content else None
    except httpx.ConnectError:
        # Gateway reported as running but TCP refused — stale PID surviving a
        # crash where atexit didn't fire.  Don't raise; caller falls back.
        logger.debug("Gateway HTTP connect failed for profile %r at %s", profile, url)
        return None
    except Exception:
        raise


def call_profile_gateway_sync(
    profile: Optional[str],
    method: str,
    path: str,
    **httpx_kwargs: Any,
) -> Optional[Any]:
    """Synchronous wrapper around ``call_profile_gateway`` for non-async contexts.

    Spins up a throwaway event loop.  Prefer the async version when already
    inside an asyncio context.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
        # A loop is already running — can't call asyncio.run() from here.
        # Caller is async and should use call_profile_gateway directly.
        logger.debug(
            "call_profile_gateway_sync called from a running loop; use async version"
        )
        return None
    except RuntimeError:
        pass  # no running loop — safe to call asyncio.run()

    return asyncio.run(call_profile_gateway(profile, method, path, **httpx_kwargs))
