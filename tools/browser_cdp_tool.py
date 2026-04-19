#!/usr/bin/env python3
"""
Chrome DevTools Protocol (CDP) tools.

Exposes two tools that share the same CDP endpoint and availability gate:

* ``browser_cdp`` — raw CDP passthrough for arbitrary commands.  Escape
  hatch for anything not covered by the wrapped browser tools.
* ``browser_dialog`` — ergonomic wrapper over ``Page.handleJavaScriptDialog``
  that accepts/dismisses a native JS dialog (alert/confirm/prompt/
  beforeunload) blocking the page.  Auto-resolves ``target_id`` when
  exactly one page tab is open.

Both tools are only registered when a CDP endpoint is actually reachable
from Python at session start — meaning ``/browser connect`` is active or
``browser.cdp_url`` is set in ``config.yaml``.  Backends that don't
currently expose CDP (Camofox, default local agent-browser, cloud
providers whose per-session ``cdp_url`` isn't surfaced) don't see these
tools at all.

CDP method reference: https://chromedevtools.github.io/devtools-protocol/
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

CDP_DOCS_URL = "https://chromedevtools.github.io/devtools-protocol/"

# ``websockets`` is a transitive dependency of hermes-agent (via fal_client
# and firecrawl-py) and is already imported by gateway/platforms/feishu.py.
# Wrap the import so a clean error surfaces if the package is ever absent.
try:
    import websockets
    from websockets.exceptions import WebSocketException

    _WS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore[assignment]
    WebSocketException = Exception  # type: ignore[assignment,misc]
    _WS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Async-from-sync bridge (matches the pattern in homeassistant_tool.py)
# ---------------------------------------------------------------------------


def _run_async(coro):
    """Run an async coroutine from a sync handler, safe inside or outside a loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------


def _resolve_cdp_endpoint() -> str:
    """Return the normalized CDP WebSocket URL, or empty string if unavailable.

    Delegates to ``tools.browser_tool._get_cdp_override`` so precedence stays
    consistent with the rest of the browser tool surface:

    1. ``BROWSER_CDP_URL`` env var (live override from ``/browser connect``)
    2. ``browser.cdp_url`` in ``config.yaml``
    """
    try:
        from tools.browser_tool import _get_cdp_override  # type: ignore[import-not-found]

        return (_get_cdp_override() or "").strip()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("browser_cdp: failed to resolve CDP endpoint: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Core CDP call
# ---------------------------------------------------------------------------


async def _cdp_call(
    ws_url: str,
    method: str,
    params: Dict[str, Any],
    target_id: Optional[str],
    timeout: float,
) -> Dict[str, Any]:
    """Make a single CDP call, optionally attaching to a target first.

    When ``target_id`` is provided, we call ``Target.attachToTarget`` with
    ``flatten=True`` to multiplex a page-level session over the same
    browser-level WebSocket, then send ``method`` with that ``sessionId``.
    When ``target_id`` is None, ``method`` is sent at browser level — which
    works for ``Target.*``, ``Browser.*``, ``Storage.*`` and a few other
    globally-scoped domains.
    """
    assert websockets is not None  # guarded by _WS_AVAILABLE at call-site

    async with websockets.connect(
        ws_url,
        max_size=None,  # CDP responses (e.g. DOM.getDocument) can be large
        open_timeout=timeout,
        close_timeout=5,
        ping_interval=None,  # CDP server doesn't expect pings
    ) as ws:
        next_id = 1
        session_id: Optional[str] = None

        # --- Step 1: attach to target if requested ---
        if target_id:
            attach_id = next_id
            next_id += 1
            await ws.send(
                json.dumps(
                    {
                        "id": attach_id,
                        "method": "Target.attachToTarget",
                        "params": {"targetId": target_id, "flatten": True},
                    }
                )
            )
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out attaching to target {target_id}"
                    )
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("id") == attach_id:
                    if "error" in msg:
                        raise RuntimeError(
                            f"Target.attachToTarget failed: {msg['error']}"
                        )
                    session_id = msg.get("result", {}).get("sessionId")
                    if not session_id:
                        raise RuntimeError(
                            "Target.attachToTarget did not return a sessionId"
                        )
                    break
                # Ignore events (messages without "id") while waiting

        # --- Step 2: dispatch the real method ---
        call_id = next_id
        next_id += 1
        req: Dict[str, Any] = {
            "id": call_id,
            "method": method,
            "params": params or {},
        }
        if session_id:
            req["sessionId"] = session_id
        await ws.send(json.dumps(req))

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for response to {method}"
                )
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("id") == call_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})
            # Ignore events / out-of-order responses


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------


def browser_cdp(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    target_id: Optional[str] = None,
    timeout: float = 30.0,
    task_id: Optional[str] = None,
) -> str:
    """Send a raw CDP command.  See ``CDP_DOCS_URL`` for method documentation.

    Args:
        method: CDP method name, e.g. ``"Target.getTargets"``.
        params: Method-specific parameters; defaults to ``{}``.
        target_id: Optional target/tab ID for page-level methods.  When set,
            we first attach to the target (``flatten=True``) and send
            ``method`` with the resulting ``sessionId``.
        timeout: Seconds to wait for the call to complete.
        task_id: Unused (tool is stateless) — accepted for uniformity with
            other browser tools.

    Returns:
        JSON string ``{"success": True, "method": ..., "result": {...}}`` on
        success, or ``{"error": "..."}`` on failure.
    """
    del task_id  # unused — stateless

    if not method or not isinstance(method, str):
        return tool_error(
            "'method' is required (e.g. 'Target.getTargets')",
            cdp_docs=CDP_DOCS_URL,
        )

    if not _WS_AVAILABLE:
        return tool_error(
            "The 'websockets' Python package is required but not installed. "
            "Install it with: pip install websockets"
        )

    endpoint = _resolve_cdp_endpoint()
    if not endpoint:
        return tool_error(
            "No CDP endpoint is available. Run '/browser connect' to attach "
            "to a running Chrome, or set 'browser.cdp_url' in config.yaml. "
            "The Camofox backend is REST-only and does not expose CDP.",
            cdp_docs=CDP_DOCS_URL,
        )

    if not endpoint.startswith(("ws://", "wss://")):
        return tool_error(
            f"CDP endpoint is not a WebSocket URL: {endpoint!r}. "
            "Expected ws://... or wss://... — the /browser connect "
            "resolver should have rewritten this. Check that Chrome is "
            "actually listening on the debug port."
        )

    call_params: Dict[str, Any] = params or {}
    if not isinstance(call_params, dict):
        return tool_error(
            f"'params' must be an object/dict, got {type(call_params).__name__}"
        )

    try:
        safe_timeout = float(timeout) if timeout else 30.0
    except (TypeError, ValueError):
        safe_timeout = 30.0
    safe_timeout = max(1.0, min(safe_timeout, 300.0))

    try:
        result = _run_async(
            _cdp_call(endpoint, method, call_params, target_id, safe_timeout)
        )
    except asyncio.TimeoutError as exc:
        return tool_error(
            f"CDP call timed out after {safe_timeout}s: {exc}",
            method=method,
        )
    except TimeoutError as exc:
        return tool_error(str(exc), method=method)
    except RuntimeError as exc:
        return tool_error(str(exc), method=method)
    except WebSocketException as exc:
        return tool_error(
            f"WebSocket error talking to CDP at {endpoint}: {exc}. The "
            "browser may have disconnected — try '/browser connect' again.",
            method=method,
        )
    except Exception as exc:  # pragma: no cover — unexpected
        logger.exception("browser_cdp unexpected error")
        return tool_error(
            f"Unexpected error: {type(exc).__name__}: {exc}",
            method=method,
        )

    payload: Dict[str, Any] = {
        "success": True,
        "method": method,
        "result": result,
    }
    if target_id:
        payload["target_id"] = target_id
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


BROWSER_CDP_SCHEMA: Dict[str, Any] = {
    "name": "browser_cdp",
    "description": (
        "Send a raw Chrome DevTools Protocol (CDP) command. Escape hatch for "
        "browser operations not covered by browser_navigate, browser_click, "
        "browser_console, etc.\n\n"
        "**Requires a reachable CDP endpoint.** Available when the user has "
        "run '/browser connect' to attach to a running Chrome, or when "
        "'browser.cdp_url' is set in config.yaml. Not currently wired up for "
        "cloud backends (Browserbase, Browser Use, Firecrawl) — those expose "
        "CDP per session but live-session routing is a follow-up. Camofox is "
        "REST-only and will never support CDP. If the tool is in your toolset "
        "at all, a CDP endpoint is already reachable.\n\n"
        f"**CDP method reference:** {CDP_DOCS_URL} — use web_extract on a "
        "method's URL (e.g. '/tot/Page/#method-handleJavaScriptDialog') "
        "to look up parameters and return shape.\n\n"
        "**Common patterns:**\n"
        "- List tabs: method='Target.getTargets', params={}\n"
        "- Handle a native JS dialog: method='Page.handleJavaScriptDialog', "
        "params={'accept': true, 'promptText': ''}, target_id=<tabId>\n"
        "- Get all cookies: method='Network.getAllCookies', params={}\n"
        "- Eval in a specific tab: method='Runtime.evaluate', "
        "params={'expression': '...', 'returnByValue': true}, "
        "target_id=<tabId>\n"
        "- Set viewport for a tab: method='Emulation.setDeviceMetricsOverride', "
        "params={'width': 1280, 'height': 720, 'deviceScaleFactor': 1, "
        "'mobile': false}, target_id=<tabId>\n\n"
        "**Usage rules:**\n"
        "- Browser-level methods (Target.*, Browser.*, Storage.*): omit "
        "target_id.\n"
        "- Page-level methods (Page.*, Runtime.*, DOM.*, Emulation.*, "
        "Network.* scoped to a tab): pass target_id from Target.getTargets.\n"
        "- Each call is independent — sessions and event subscriptions do "
        "not persist between calls. For stateful workflows, prefer the "
        "dedicated browser tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": (
                    "CDP method name, e.g. 'Target.getTargets', "
                    "'Runtime.evaluate', 'Page.handleJavaScriptDialog'."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Method-specific parameters as a JSON object. Omit or "
                    "pass {} for methods that take no parameters."
                ),
                "additionalProperties": True,
            },
            "target_id": {
                "type": "string",
                "description": (
                    "Optional. Target/tab ID from Target.getTargets result "
                    "(each entry's 'targetId'). Required for page-level "
                    "methods; must be omitted for browser-level methods."
                ),
            },
            "timeout": {
                "type": "number",
                "description": (
                    "Timeout in seconds (default 30, max 300)."
                ),
                "default": 30,
            },
        },
        "required": ["method"],
    },
}


def _browser_cdp_check() -> bool:
    """Availability check for browser_cdp.

    The tool is only offered when the Python side can actually reach a CDP
    endpoint right now — meaning a static URL is set via ``/browser connect``
    (``BROWSER_CDP_URL``) or ``browser.cdp_url`` in ``config.yaml``.

    Backends that do *not* currently expose CDP to us — Camofox (REST-only),
    the default local agent-browser mode (Playwright hides its internal CDP
    port), and cloud providers whose per-session ``cdp_url`` is not yet
    surfaced — are gated out so the model doesn't see a tool that would
    reliably fail.  Cloud-provider CDP routing is a follow-up.

    Kept in a thin wrapper so the registration statement stays at module top
    level (the tool-discovery AST scan only picks up top-level
    ``registry.register(...)`` calls).
    """
    try:
        from tools.browser_tool import (  # type: ignore[import-not-found]
            _get_cdp_override,
            check_browser_requirements,
        )
    except ImportError as exc:  # pragma: no cover — defensive
        logger.debug("browser_cdp check: browser_tool import failed: %s", exc)
        return False
    if not check_browser_requirements():
        return False
    return bool(_get_cdp_override())


registry.register(
    name="browser_cdp",
    toolset="browser",
    schema=BROWSER_CDP_SCHEMA,
    handler=lambda args, **kw: browser_cdp(
        method=args.get("method", ""),
        params=args.get("params"),
        target_id=args.get("target_id"),
        timeout=args.get("timeout", 30.0),
        task_id=kw.get("task_id"),
    ),
    check_fn=_browser_cdp_check,
    emoji="🧪",
)


# ---------------------------------------------------------------------------
# browser_dialog — ergonomic wrapper over Page.handleJavaScriptDialog
# ---------------------------------------------------------------------------


def browser_dialog(
    action: str,
    prompt_text: Optional[str] = None,
    target_id: Optional[str] = None,
    timeout: float = 30.0,
    task_id: Optional[str] = None,
) -> str:
    """Accept or dismiss a native JS dialog blocking the page.

    Thin wrapper over the CDP ``Page.handleJavaScriptDialog`` verb that
    also auto-resolves ``target_id`` when exactly one page tab is open.
    Same CDP endpoint and availability gate as :func:`browser_cdp`.

    Args:
        action: ``"accept"`` or ``"dismiss"``.
        prompt_text: Text to enter when handling a ``prompt()`` dialog;
            ignored for alert/confirm/beforeunload.
        target_id: Target/tab ID from ``Target.getTargets``.  Optional
            when exactly one page tab is open; required otherwise.
        timeout: Seconds to wait for the CDP round-trip (default 30).
        task_id: Unused — accepted for uniformity with other browser tools.

    Returns:
        JSON string ``{"success": True, "action": ..., "target_id": ...}``
        on success, or ``{"error": "..."}`` on failure.  CDP's
        ``"No dialog is showing"`` error is passed through verbatim so
        callers can use this as a probe for dialog presence.
    """
    del task_id

    # --- input validation ------------------------------------------------
    if action not in ("accept", "dismiss"):
        return tool_error(
            f"'action' must be 'accept' or 'dismiss', got {action!r}"
        )

    # --- shared gate checks (match browser_cdp) --------------------------
    if not _WS_AVAILABLE:
        return tool_error(
            "The 'websockets' Python package is required but not installed. "
            "Install it with: pip install websockets"
        )

    endpoint = _resolve_cdp_endpoint()
    if not endpoint:
        return tool_error(
            "No CDP endpoint is available. Run '/browser connect' to attach "
            "to a running Chrome, or set 'browser.cdp_url' in config.yaml.",
            cdp_docs=CDP_DOCS_URL,
        )

    if not endpoint.startswith(("ws://", "wss://")):
        return tool_error(
            f"CDP endpoint is not a WebSocket URL: {endpoint!r}. "
            "Check that Chrome is actually listening on the debug port."
        )

    try:
        safe_timeout = float(timeout) if timeout else 30.0
    except (TypeError, ValueError):
        safe_timeout = 30.0
    safe_timeout = max(1.0, min(safe_timeout, 300.0))

    # --- auto-resolve target_id when not explicitly given ---------------
    resolved_target_id = target_id
    if not resolved_target_id:
        try:
            targets_result = _run_async(
                _cdp_call(
                    endpoint, "Target.getTargets", {}, None, safe_timeout
                )
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            return tool_error(
                f"Timed out listing tabs while resolving target: {exc}"
            )
        except RuntimeError as exc:
            return tool_error(
                f"Failed to list tabs while resolving target: {exc}"
            )
        except WebSocketException as exc:
            return tool_error(
                f"WebSocket error while resolving target at {endpoint}: {exc}"
            )

        page_targets = [
            t
            for t in targets_result.get("targetInfos", [])
            if t.get("type") == "page"
        ]
        if len(page_targets) == 0:
            return tool_error(
                "No page tabs found — nothing to handle a dialog on."
            )
        if len(page_targets) > 1:
            return tool_error(
                "Multiple page tabs are open — pass target_id explicitly. "
                "Use browser_cdp(method='Target.getTargets') to list them.",
                page_count=len(page_targets),
                tabs=[
                    {
                        "targetId": t.get("targetId"),
                        "title": t.get("title", ""),
                        "url": t.get("url", ""),
                    }
                    for t in page_targets
                ],
            )
        resolved_target_id = page_targets[0].get("targetId")
        if not resolved_target_id:
            return tool_error(
                "Target.getTargets returned a page target without a targetId"
            )

    # --- dispatch the dialog handler -------------------------------------
    cdp_params = {
        "accept": action == "accept",
        "promptText": prompt_text or "",
    }
    try:
        result = _run_async(
            _cdp_call(
                endpoint,
                "Page.handleJavaScriptDialog",
                cdp_params,
                resolved_target_id,
                safe_timeout,
            )
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        return tool_error(
            f"CDP call timed out after {safe_timeout}s: {exc}",
            action=action,
            target_id=resolved_target_id,
        )
    except RuntimeError as exc:
        # CDP returns a clear "No dialog is showing" error when there's
        # nothing to handle — pass it through so callers can probe.
        return tool_error(
            str(exc), action=action, target_id=resolved_target_id
        )
    except WebSocketException as exc:
        return tool_error(
            f"WebSocket error talking to CDP at {endpoint}: {exc}. The "
            "browser may have disconnected — try '/browser connect' again.",
            action=action,
        )
    except Exception as exc:  # pragma: no cover — unexpected
        logger.exception("browser_dialog unexpected error")
        return tool_error(
            f"Unexpected error: {type(exc).__name__}: {exc}",
            action=action,
        )

    return json.dumps(
        {
            "success": True,
            "action": action,
            "target_id": resolved_target_id,
            "result": result,
        },
        ensure_ascii=False,
    )


BROWSER_DIALOG_SCHEMA: Dict[str, Any] = {
    "name": "browser_dialog",
    "description": (
        "Accept or dismiss a native JS dialog (alert/confirm/prompt/"
        "beforeunload) that's blocking a page.\n\n"
        "**When to use:** native dialogs freeze the page's JS thread, so "
        "browser_snapshot, browser_console, browser_click and similar tools "
        "will hang or error until the dialog is handled. Use this tool to "
        "unstick the page. Also safe as a probe — CDP returns a clean 'No "
        "dialog is showing' error when there isn't one, so you can call "
        "this to check whether a suspected dialog exists.\n\n"
        "**Requires the same CDP endpoint as browser_cdp.** If this tool "
        "is in your toolset, the endpoint is already reachable.\n\n"
        "**target_id auto-resolution:** when exactly one page tab is "
        "open, target_id can be omitted. With multiple page tabs, an "
        "explicit target_id is required — the error response lists the "
        "tabs so you can pick one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "dismiss"],
                "description": (
                    "'accept' confirms OK/Yes/Submit; 'dismiss' cancels. "
                    "For beforeunload dialogs, 'accept' leaves the page "
                    "and 'dismiss' stays on it."
                ),
            },
            "prompt_text": {
                "type": "string",
                "description": (
                    "Text to enter when handling a prompt() dialog. "
                    "Ignored for alert, confirm, and beforeunload dialogs."
                ),
            },
            "target_id": {
                "type": "string",
                "description": (
                    "Target/tab ID from Target.getTargets. Optional when "
                    "exactly one page tab is open; required otherwise."
                ),
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="browser_dialog",
    toolset="browser",
    schema=BROWSER_DIALOG_SCHEMA,
    handler=lambda args, **kw: browser_dialog(
        action=args.get("action", ""),
        prompt_text=args.get("prompt_text"),
        target_id=args.get("target_id"),
        timeout=args.get("timeout", 30.0),
        task_id=kw.get("task_id"),
    ),
    check_fn=_browser_cdp_check,
    emoji="💬",
)
