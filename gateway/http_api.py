"""
Management HTTP API for the gateway.

This module provides a FastAPI-based HTTP server that exposes the gateway's
management interface including:
- WebSocket for tui_gateway JSON-RPC (/api/ws)
- REST endpoints for status, control, sessions, config, skills, cron, etc.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from typing import Optional, List

from fastapi import FastAPI, WebSocket, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path

# Import tui_gateway WebSocket handler
from tui_gateway.ws import handle_ws as _handle_ws

# Gateway runner will be injected at startup
_gateway_runner = None
_http_token: Optional[str] = None

# Token header name (matches dashboard)
_TOKEN_HEADER = "X-Hermes-Session-Token"


def set_gateway_runner(runner) -> None:
    """Inject the gateway runner instance."""
    global _gateway_runner
    _gateway_runner = runner


def set_http_token(token: Optional[str]) -> None:
    """Set the HTTP management API token."""
    global _http_token
    _http_token = token


def get_http_token() -> Optional[str]:
    """Get the HTTP management API token."""
    return _http_token


def get_runner():
    """Get the gateway runner instance."""
    if _gateway_runner is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    return _gateway_runner


def _check_token(token_value: str) -> bool:
    """Constant-time compare token against expected."""
    expected = get_http_token()
    if not expected:
        return False
    return hmac.compare_digest(token_value, expected)


# ---- Auth dependency ----
async def verify_token(
    request: Request,
    token: Optional[str] = Query(None)
):
    """Verify the management API token.
    
    Accepts (in priority order):
    1. X-Hermes-Session-Token header
    2. Authorization: Bearer <token> header  
    3. ?token= query param
    """
    expected = get_http_token()
    if not expected:
        raise HTTPException(status_code=503, detail="Management API not configured")
    
    # Check X-Hermes-Session-Token header first
    header_token = request.headers.get(_TOKEN_HEADER, "")
    if header_token and hmac.compare_digest(header_token, expected):
        return header_token
    
    # Check Authorization: Bearer header
    auth = request.headers.get("authorization", "")
    if auth:
        bearer = f"Bearer {expected}"
        if hmac.compare_digest(auth, bearer):
            return expected
    
    # Check ?token= query param
    if token and hmac.compare_digest(token, expected):
        return token
    
    raise HTTPException(status_code=401, detail="Invalid or missing token")


# ---- WebSocket auth ----
async def verify_ws_token(ws: WebSocket, token: Optional[str] = Query(None)) -> bool:
    """Verify WebSocket token — accepts ?token= query param or X-Hermes-Session-Token header."""
    expected = get_http_token()
    if not expected:
        await ws.close(code=4401)
        return False
    # Query param
    if token and hmac.compare_digest(token, expected):
        return True
    # Header
    header_token = ws.headers.get(_TOKEN_HEADER, "")
    if header_token and hmac.compare_digest(header_token, expected):
        return True
    await ws.close(code=4401)
    return False


# ---- Response models ----
class StatusResponse(BaseModel):
    state: str
    platforms: dict
    pid: int
    uptime_seconds: float


class GatewayControlResponse(BaseModel):
    ok: bool
    message: str = ""


# ---- Request models ----
class SessionSearchRequest(BaseModel):
    q: str
    limit: int = 20


class BulkDeleteRequest(BaseModel):
    ids: list[str]


class PruneSessionsRequest(BaseModel):
    max_age_days: int = 30


class ConfigSetRequest(BaseModel):
    key: str
    value: str


class SkillToggleRequest(BaseModel):
    name: str
    enabled: bool


class ToolsetConfigRequest(BaseModel):
    enabled: bool = True


class ProfileCreateRequest(BaseModel):
    name: str


class ProfileSoulRequest(BaseModel):
    soul: str


# ---- Create FastAPI app ----
def create_app() -> FastAPI:
    """Create the management API FastAPI application."""
    from hermes_state import SessionDB
    from hermes_cli.config import load_config
    from tools.skills_tool import _find_all_skills
    from hermes_cli.skills_config import get_disabled_skills
    from hermes_constants import get_hermes_home
    from hermes_cli.logs import _read_tail, _parse_since, LOG_FILES, _LEVEL_ORDER
    from hermes_logging import COMPONENT_PREFIXES

    app = FastAPI(
        title="Hermes Gateway Management API",
        version="1.0.0",
        docs_url=None,  # Disable docs in production
        redoc_url=None,
    )

    # CORS for local desktop (file:// origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- WebSocket endpoint (tui_gateway) ----
    @app.websocket("/api/ws")
    async def ws_endpoint(ws: WebSocket, token: Optional[str] = Query(None)):
        if not await verify_ws_token(ws, token):
            return
        try:
            await _handle_ws(ws)
        except Exception as e:
            # Log but don't crash
            import logging
            logging.getLogger(__name__).error(f"WS error: {e}")

    # ---- Health/Status ----
    @app.get("/api/status")
    async def status(token: str = Depends(verify_token)):
        runner = get_runner()
        return StatusResponse(
            state=runner.state,
            platforms={
                p.value: getattr(a, "status", "unknown")
                for p, a in runner.adapters.items()
            },
            pid=os.getpid(),
            uptime_seconds=time.time() - runner.start_time,
        )

    # ---- Gateway control ----
    @app.post("/api/gateway/restart")
    async def gateway_restart(token: str = Depends(verify_token)):
        runner = get_runner()
        runner.request_restart(via_service=True)
        return GatewayControlResponse(ok=True, message="Restart initiated")

    @app.post("/api/gateway/stop")
    async def gateway_stop(token: str = Depends(verify_token)):
        runner = get_runner()
        import asyncio
        asyncio.create_task(runner.stop())
        return GatewayControlResponse(ok=True, message="Stop initiated")

    # ---- Session endpoints (Phase 2) ----
    @app.get("/api/sessions")
    async def get_sessions(
        limit: int = 20,
        offset: int = 0,
        min_messages: int = 0,
        archived: str = "exclude",
        order: str = "created",
    ):
        """List sessions."""
        if archived not in ("exclude", "only", "include"):
            raise HTTPException(
                status_code=400,
                detail="archived must be one of: exclude, only, include",
            )
        if order not in ("created", "recent"):
            raise HTTPException(
                status_code=400,
                detail="order must be one of: created, recent",
            )
        try:
            db = SessionDB()
            try:
                min_message_count = max(0, min_messages)
                archived_only = archived == "only"
                include_archived = archived == "include"
                sessions = db.list_sessions_rich(
                    limit=limit,
                    offset=offset,
                    min_message_count=min_message_count,
                    include_archived=include_archived,
                    archived_only=archived_only,
                    order_by_last_active=order == "recent",
                )
                total = db.session_count(
                    min_message_count=min_message_count,
                    include_archived=include_archived,
                    archived_only=archived_only,
                    exclude_children=True,
                )
                now = time.time()
                for s in sessions:
                    s["is_active"] = (
                        s.get("ended_at") is None
                        and (now - s.get("last_active", s.get("started_at", 0))) < 300
                    )
                    s["archived"] = bool(s.get("archived"))
                return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
            finally:
                db.close()
        except Exception:
            raise HTTPException(status_code=500, detail="Internal server error")

    @app.get("/api/sessions/{session_id}")
    async def get_session_detail(session_id: str):
        """Get a session by ID."""
        db = SessionDB()
        try:
            sid = db.resolve_session_id(session_id)
            session = db.get_session(sid) if sid else None
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            return session
        finally:
            db.close()

    @app.get("/api/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str):
        """Get messages for a session."""
        db = SessionDB()
        try:
            sid = db.resolve_session_id(session_id)
            if not sid:
                raise HTTPException(status_code=404, detail="Session not found")
            sid = db.resolve_resume_session_id(sid)
            messages = db.get_messages(sid)
            return {"session_id": sid, "messages": messages}
        finally:
            db.close()

    @app.delete("/api/sessions/{session_id}")
    async def delete_session_endpoint(session_id: str):
        """Delete a session."""
        db = SessionDB()
        try:
            if not db.delete_session(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            return {"ok": True}
        finally:
            db.close()

    # ---- Model endpoints ----
    @app.get("/api/model/info")
    async def get_model_info(token: str = Depends(verify_token)):
        """Get current model info."""
        from hermes_cli.config import load_config
        cfg = load_config()
        model = cfg.get("model", {})
        return {
            "provider": model.get("provider"),
            "model": model.get("model"),
            "base_url": model.get("base_url"),
            "context_length": model.get("context_length"),
        }

    # ---- Config endpoints ----
    @app.get("/api/config")
    async def get_config(token: str = Depends(verify_token)):
        """Get full config."""
        from hermes_cli.config import load_config
        return load_config()

    @app.post("/api/config")
    async def set_config(key: str, value: str, token: str = Depends(verify_token)):
        """Set a config value (key=dot.path, value=string).

        The value is coerced to match the existing type at that path in the
        config (int, float, bool) so callers don't have to worry about type
        encoding.  Unknown/new keys are stored as strings.
        """
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        parts = key.split(".")
        target = cfg
        for p in parts[:-1]:
            target = target.setdefault(p, {})
        leaf = parts[-1]
        # Coerce to the same type as the existing value when possible.
        existing = target.get(leaf)
        coerced: object = value
        if isinstance(existing, bool):
            coerced = value.lower() not in ("0", "false", "no", "off", "")
        elif isinstance(existing, int):
            try:
                coerced = int(value)
            except (ValueError, TypeError):
                pass
        elif isinstance(existing, float):
            try:
                coerced = float(value)
            except (ValueError, TypeError):
                pass
        target[leaf] = coerced
        save_config(cfg)
        return {"ok": True}

    @app.get("/api/config/defaults")
    async def get_config_defaults(token: str = Depends(verify_token)):
        """Get default config schema."""
        from hermes_cli.config import DEFAULT_CONFIG
        return DEFAULT_CONFIG

    @app.get("/api/config/schema")
    async def get_config_schema(token: str = Depends(verify_token)):
        """Get config schema."""
        from hermes_cli.config import DEFAULT_CONFIG
        # Return a simplified schema from DEFAULT_CONFIG
        def extract_schema(cfg, prefix=""):
            result = {}
            for k, v in cfg.items():
                key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    result[key] = "object"
                elif isinstance(v, list):
                    result[key] = "array"
                elif isinstance(v, bool):
                    result[key] = "boolean"
                elif isinstance(v, int):
                    result[key] = "integer"
                elif isinstance(v, float):
                    result[key] = "number"
                else:
                    result[key] = "string"
            return result
        return extract_schema(DEFAULT_CONFIG)

    # ---- Skills endpoints ----
    @app.get("/api/skills")
    async def get_skills(token: str = Depends(verify_token)):
        """Get all skills."""
        from tools.skills_tool import _find_all_skills
        from hermes_cli.skills_config import get_disabled_skills
        from hermes_cli.config import load_config
        config = load_config()
        disabled = get_disabled_skills(config)
        skills = _find_all_skills(skip_disabled=True)
        for s in skills:
            s["enabled"] = s["name"] not in disabled
        return skills

    @app.put("/api/skills/toggle")
    async def toggle_skill(name: str, enabled: bool, token: str = Depends(verify_token)):
        """Enable/disable a skill."""
        from hermes_cli.skills_config import get_disabled_skills, save_disabled_skills
        from hermes_cli.config import load_config
        config = load_config()
        disabled = get_disabled_skills(config)
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        save_disabled_skills(config, disabled)
        return {"ok": True, "name": name, "enabled": enabled}

    # ---- Cron endpoints ----
    @app.get("/api/cron/jobs")
    async def get_cron_jobs(token: str = Depends(verify_token)):
        """List cron jobs."""
        runner = get_runner()
        return runner.cron_scheduler.list_jobs()

    # ---- Logs endpoint ----
    @app.get("/api/logs")
    async def get_logs(
        log_name: str = "agent",
        num_lines: int = 100,
        level: Optional[str] = None,
        since: Optional[str] = None,
        component: Optional[str] = None,
        token: str = Depends(verify_token),
    ):
        """Get logs."""
        from hermes_constants import get_hermes_home
        log_dir = get_hermes_home() / "logs"
        log_path = log_dir / f"{log_name}.log"
        if not log_path.exists():
            return {"lines": []}
        try:
            from hermes_cli.logs import _read_tail, _parse_since, LOG_FILES, _LEVEL_ORDER
            from hermes_logging import COMPONENT_PREFIXES
            
            filename = LOG_FILES.get(log_name)
            if filename is None:
                return {"lines": []}
            
            actual_path = log_dir / filename
            if not actual_path.exists():
                return {"lines": []}
            
            since_dt = None
            if since:
                since_dt = _parse_since(since)
            
            min_level = level.upper() if level else None
            if min_level and min_level not in _LEVEL_ORDER:
                min_level = None
            
            component_prefixes = None
            if component:
                component_lower = component.lower()
                if component_lower in COMPONENT_PREFIXES:
                    component_prefixes = COMPONENT_PREFIXES[component_lower]
            
            lines = _read_tail(
                actual_path, num_lines, has_filters=bool(min_level or since_dt or component_prefixes),
                min_level=min_level, session_filter=None,
                since=since_dt, component_prefixes=component_prefixes
            )
            return {"lines": lines}
        except Exception:
            # Fallback simple tail
            if not log_path.exists():
                return {"lines": []}
            lines_list = log_path.read_text().splitlines()[-num_lines:]
            return {"lines": lines_list}

    # ---- Session extended endpoints ----
    @app.post("/api/sessions/search")
    async def search_sessions(request: SessionSearchRequest, token: str = Depends(verify_token)):
        """Search sessions."""
        db = SessionDB()
        try:
            results = db.search_sessions(request.q, limit=request.limit)
            return {"sessions": results}
        finally:
            db.close()

    @app.post("/api/sessions/bulk-delete")
    async def bulk_delete_sessions(request: BulkDeleteRequest, token: str = Depends(verify_token)):
        """Delete multiple sessions."""
        db = SessionDB()
        try:
            results = {}
            for sid in request.ids:
                results[sid] = db.delete_session(sid)
            return {"results": results}
        finally:
            db.close()

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str, token: str = Depends(verify_token)):
        """Export a session as JSON."""
        db = SessionDB()
        try:
            sid = db.resolve_session_id(session_id)
            if not sid:
                raise HTTPException(status_code=404, detail="Session not found")
            sid = db.resolve_resume_session_id(sid)
            session = db.get_session(sid)
            messages = db.get_messages(sid)
            return {"session": session, "messages": messages}
        finally:
            db.close()

    @app.post("/api/sessions/prune")
    async def prune_sessions(request: PruneSessionsRequest, token: str = Depends(verify_token)):
        """Prune old sessions."""
        db = SessionDB()
        try:
            pruned = db.prune_sessions(request.max_age_days)
            return {"pruned": pruned}
        finally:
            db.close()

    @app.patch("/api/sessions/{session_id}")
    async def update_session(
        session_id: str,
        title: Optional[str] = None,
        archived: Optional[bool] = None,
        token: str = Depends(verify_token),
    ):
        """Rename/archive a session."""
        db = SessionDB()
        try:
            sid = db.resolve_session_id(session_id)
            if not sid:
                raise HTTPException(status_code=404, detail="Session not found")
            if title is not None:
                db.set_session_title(sid, title)
            if archived is not None:
                db.set_session_archived(sid, archived)
            return {"ok": True}
        finally:
            db.close()

    # ---- Tools/Toolsets endpoints ----
    @app.get("/api/tools/toolsets")
    async def get_toolsets(token: str = Depends(verify_token)):
        """List available toolsets."""
        from hermes_cli.config import load_config
        cfg = load_config()
        toolsets = cfg.get("toolsets", [])
        return {"toolsets": toolsets}

    @app.get("/api/tools/toolsets/{name}")
    async def get_toolset(name: str, token: str = Depends(verify_token)):
        """Get a specific toolset."""
        from hermes_cli.config import load_config
        cfg = load_config()
        toolsets = cfg.get("toolsets", [])
        if name not in toolsets:
            raise HTTPException(status_code=404, detail="Toolset not found")
        return {"name": name}

    @app.post("/api/tools/toolsets/{name}/config")
    async def configure_toolset(name: str, enabled: bool = True, token: str = Depends(verify_token)):
        """Enable/disable a toolset."""
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        toolsets = list(cfg.get("toolsets", []))
        if enabled and name not in toolsets:
            toolsets.append(name)
        elif not enabled and name in toolsets:
            toolsets.remove(name)
        cfg["toolsets"] = toolsets
        save_config(cfg)
        return {"ok": True, "name": name, "enabled": enabled}

    # ---- Messaging Platforms endpoints ----
    @app.get("/api/messaging/platforms")
    async def get_messaging_platforms(token: str = Depends(verify_token)):
        """List configured messaging platforms."""
        runner = get_runner()
        platforms = {}
        for p, adapter in runner.adapters.items():
            platforms[p.value] = {
                "platform": p.value,
                "connected": getattr(adapter, "status", "unknown") == "connected",
                "config": str(adapter.config) if hasattr(adapter, "config") else {},
            }
        return {"platforms": platforms}

    @app.get("/api/messaging/platforms/{platform}")
    async def get_platform(platform: str, token: str = Depends(verify_token)):
        """Get a specific platform config."""
        from gateway.config import Platform
        from hermes_cli.config import load_config
        try:
            Platform(platform)  # validate — raises ValueError if unknown
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid platform")
        cfg = load_config()
        platforms = cfg.get("platforms", {})
        if platform not in platforms:
            raise HTTPException(status_code=404, detail="Platform not configured")
        return {platform: platforms[platform]}

    @app.post("/api/messaging/platforms/{platform}/test")
    async def test_platform(platform: str, token: str = Depends(verify_token)):
        """Test a platform connection."""
        from gateway.config import Platform
        from hermes_cli.config import load_config
        from gateway.platform_registry import platform_registry
        try:
            Platform(platform)  # validate — raises ValueError if unknown
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid platform")
        entry = platform_registry.get(platform)
        if not entry or not entry.validate_config:
            return {"ok": False, "error": "Platform configuration not available"}
        cfg = load_config()
        platforms = cfg.get("platforms", {})
        if platform not in platforms:
            return {"ok": False, "error": "Platform not configured"}
        try:
            ok = entry.validate_config(platforms[platform])
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- Profiles endpoints ----
    @app.get("/api/profiles")
    async def list_profiles(token: str = Depends(verify_token)):
        """List all profiles."""
        from hermes_cli.profiles import list_profiles
        profs = list_profiles()
        return {"profiles": profs}

    @app.post("/api/profiles")
    async def create_profile(name: str, token: str = Depends(verify_token)):
        """Create a new profile."""
        from hermes_cli.profiles import create_profile
        try:
            create_profile(name)
            return {"ok": True, "name": name}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/profiles/{name}")
    async def delete_profile(name: str, token: str = Depends(verify_token)):
        """Delete a profile."""
        from hermes_cli.profiles import delete_profile
        delete_profile(name)
        return {"ok": True}

    @app.get("/api/profiles/{name}/soul")
    async def get_profile_soul(name: str, token: str = Depends(verify_token)):
        """Get a profile's SOUL.md."""
        from hermes_cli.profiles import get_profile_dir
        from pathlib import Path
        try:
            prof_dir = get_profile_dir(name)
            soul_path = Path(prof_dir) / "SOUL.md"
            if not soul_path.exists():
                return {"soul": ""}
            return {"soul": soul_path.read_text()}
        except Exception:
            raise HTTPException(status_code=404, detail="Profile not found")

    @app.put("/api/profiles/{name}/soul")
    async def put_profile_soul(name: str, soul: str, token: str = Depends(verify_token)):
        """Set a profile's SOUL.md (PUT alias)."""
        from hermes_cli.profiles import get_profile_dir
        from pathlib import Path
        try:
            prof_dir = get_profile_dir(name)
            soul_path = Path(prof_dir) / "SOUL.md"
            soul_path.write_text(soul)
            return {"ok": True}
        except Exception:
            raise HTTPException(status_code=404, detail="Profile not found")

    @app.get("/api/profiles/{name}/setup-command")
    async def get_profile_setup_command(name: str, token: str = Depends(verify_token)):
        """Return the shell command used to configure a profile."""
        from hermes_cli.profiles import profile_exists
        if name != "default" and not profile_exists(name):
            raise HTTPException(status_code=404, detail=f"Profile '{name}' does not exist.")
        command = "hermes setup" if name == "default" else f"{name} setup"
        return {"command": command}

    # ---- Config write (full object) ----
    @app.put("/api/config")
    async def update_config(
        config: dict,
        token: str = Depends(verify_token),
    ):
        """Replace the full config object."""
        from hermes_cli.config import save_config
        save_config(config)
        return {"ok": True}

    # ---- Env management ----
    @app.get("/api/env")
    async def get_env_vars(token: str = Depends(verify_token)):
        """Get all env vars with is_set / redacted_value metadata."""
        from hermes_cli.config import load_env, OPTIONAL_ENV_VARS, redact_key
        env_on_disk = load_env()
        result = {}
        for var_name, info in OPTIONAL_ENV_VARS.items():
            value = env_on_disk.get(var_name)
            result[var_name] = {
                "is_set": bool(value),
                "redacted_value": redact_key(value) if value else None,
                "description": info.get("description", ""),
                "url": info.get("url"),
                "category": info.get("category", ""),
                "is_password": info.get("password", False),
                "tools": info.get("tools", []),
                "advanced": info.get("advanced", False),
                "channel_managed": False,
            }
        return result

    @app.put("/api/env")
    async def set_env_var(key: str, value: str, token: str = Depends(verify_token)):
        """Set an env var in the profile's .env."""
        from hermes_cli.config import save_env_value
        try:
            save_env_value(key, value)
            return {"ok": True, "key": key}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/env")
    async def remove_env_var(key: str, token: str = Depends(verify_token)):
        """Remove an env var from the profile's .env."""
        from hermes_cli.config import remove_env_value
        try:
            removed = remove_env_value(key)
            if not removed:
                raise HTTPException(status_code=404, detail=f"{key} not found in .env")
            return {"ok": True, "key": key}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Rate-limit state for /api/env/reveal  (5 reveals per 30s)
    _reveal_timestamps: list = []

    @app.post("/api/env/reveal")
    async def reveal_env_var(key: str, token: str = Depends(verify_token)):
        """Return the real (unredacted) value of a single env var.  Rate-limited."""
        from hermes_cli.config import load_env
        now = time.time()
        _reveal_timestamps[:] = [t for t in _reveal_timestamps if now - t < 30]
        if len(_reveal_timestamps) >= 5:
            raise HTTPException(status_code=429, detail="Too many reveal requests. Try again shortly.")
        _reveal_timestamps.append(now)
        env = load_env()
        value = env.get(key)
        if value is None:
            raise HTTPException(status_code=404, detail=f"{key} not found in .env")
        return {"key": key, "value": value}

    @app.post("/api/providers/validate")
    async def validate_provider_credential(key: str, value: str, token: str = Depends(verify_token)):
        """Live-probe a provider credential before it is saved."""
        import httpx as _httpx

        value = (value or "").strip()
        if not value:
            return {"ok": False, "reachable": True, "message": "Enter a value first."}

        _PROBES: dict = {
            "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", "bearer"),
            "OPENAI_API_KEY": ("https://api.openai.com/v1/models", "bearer"),
            "XAI_API_KEY": ("https://api.x.ai/v1/models", "bearer"),
            "GEMINI_API_KEY": ("https://generativelanguage.googleapis.com/v1beta/models", "query"),
        }

        if key == "OPENAI_BASE_URL":
            url = value.rstrip("/") + "/models"
            try:
                with _httpx.Client(timeout=8.0) as client:
                    resp = client.get(url)
                return {"ok": True, "reachable": True, "message": ""}
            except Exception:
                return {"ok": False, "reachable": False, "message": f"Could not reach {url}."}

        probe = _PROBES.get(key)
        if not probe:
            return {"ok": True, "reachable": False, "message": ""}

        url, auth = probe
        headers: dict = {"Accept": "application/json"}
        params: dict = {}
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {value}"
        else:
            params["key"] = value

        try:
            with _httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers, params=params)
        except Exception:
            return {"ok": False, "reachable": False, "message": "Could not reach the provider."}

        if resp.status_code in (401, 403):
            return {"ok": False, "reachable": True, "message": "API key was rejected."}
        if resp.status_code == 429 or resp.is_success:
            return {"ok": True, "reachable": True, "message": ""}
        return {"ok": False, "reachable": True, "message": f"Provider returned HTTP {resp.status_code}."}

    # ---- Hermes update ----
    @app.get("/api/hermes/update/check")
    async def check_hermes_update(force: bool = False, token: str = Depends(verify_token)):
        """Report whether a Hermes update is available."""
        import asyncio as _asyncio
        from hermes_cli.web_server import check_hermes_update as _dash_check
        return await _dash_check(force=force)

    @app.post("/api/hermes/update")
    async def update_hermes(token: str = Depends(verify_token)):
        """Kick off hermes update in the background."""
        from hermes_cli.web_server import update_hermes as _dash_update
        return await _dash_update()

    # ---- Action status (for post-setup progress tailing) ----
    @app.get("/api/actions/{name}/status")
    async def get_action_status(name: str, lines: int = 200, token: str = Depends(verify_token)):
        """Tail an action log (reuses dashboard action infrastructure)."""
        from hermes_cli.web_server import get_action_status as _dash_action_status
        return await _dash_action_status(name=name, lines=lines)

    # ---- Model endpoints (full) ----
    @app.get("/api/model/options")
    async def get_model_options(token: str = Depends(verify_token)):
        """Return provider/model picker payload."""
        from hermes_cli.web_server import get_model_options as _dash_get_model_options
        return _dash_get_model_options()

    @app.get("/api/model/recommended-default")
    async def get_recommended_default(provider: str = "", token: str = Depends(verify_token)):
        """Return the recommended default model for a provider."""
        from hermes_cli.web_server import get_recommended_default_model as _dash_recommended
        return _dash_recommended(provider=provider)

    @app.get("/api/model/auxiliary")
    async def get_model_auxiliary(token: str = Depends(verify_token)):
        """Return current auxiliary task model assignments."""
        from hermes_cli.web_server import get_auxiliary_models as _dash_aux
        return _dash_aux()

    @app.post("/api/model/set")
    async def set_model(
        scope: str,
        provider: str = "",
        model: str = "",
        task: str = "",
        base_url: str = "",
        confirm_expensive_model: bool = False,
        token: str = Depends(verify_token),
    ):
        """Assign a model to the main or auxiliary slot."""
        from hermes_cli.web_server import set_model_assignment as _dash_set_model
        from pydantic import BaseModel as _BM
        class _Body(_BM):
            scope: str
            provider: str = ""
            model: str = ""
            task: str = ""
            base_url: str = ""
            confirm_expensive_model: bool = False
            profile: Optional[str] = None
        body = _Body(scope=scope, provider=provider, model=model, task=task,
                     base_url=base_url, confirm_expensive_model=confirm_expensive_model)
        return await _dash_set_model(body=body)

    # ---- Cron extended CRUD ----
    @app.get("/api/cron/jobs/{job_id}")
    async def get_cron_job(job_id: str, token: str = Depends(verify_token)):
        """Get a single cron job by ID."""
        runner = get_runner()
        job = runner.cron_scheduler.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/api/cron/jobs")
    async def create_cron_job(
        prompt: str,
        schedule: str,
        name: str = "",
        deliver: str = "local",
        token: str = Depends(verify_token),
    ):
        """Create a cron job."""
        runner = get_runner()
        try:
            return runner.cron_scheduler.create_job(
                prompt=prompt, schedule=schedule, name=name, deliver=deliver
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/api/cron/jobs/{job_id}")
    async def update_cron_job(job_id: str, updates: dict, token: str = Depends(verify_token)):
        """Update a cron job."""
        runner = get_runner()
        try:
            job = runner.cron_scheduler.update_job(job_id, updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.delete("/api/cron/jobs/{job_id}")
    async def delete_cron_job(job_id: str, token: str = Depends(verify_token)):
        """Delete a cron job."""
        runner = get_runner()
        try:
            removed = runner.cron_scheduler.remove_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not removed:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True}

    @app.post("/api/cron/jobs/{job_id}/pause")
    async def pause_cron_job(job_id: str, token: str = Depends(verify_token)):
        """Pause a cron job."""
        runner = get_runner()
        job = runner.cron_scheduler.pause_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/api/cron/jobs/{job_id}/resume")
    async def resume_cron_job(job_id: str, token: str = Depends(verify_token)):
        """Resume a paused cron job."""
        runner = get_runner()
        job = runner.cron_scheduler.resume_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/api/cron/jobs/{job_id}/trigger")
    async def trigger_cron_job(job_id: str, token: str = Depends(verify_token)):
        """Trigger a cron job immediately."""
        runner = get_runner()
        job = runner.cron_scheduler.trigger_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/api/cron/jobs/{job_id}/runs")
    async def list_cron_job_runs(job_id: str, limit: int = 20, token: str = Depends(verify_token)):
        """List recent run sessions for a cron job."""
        runner = get_runner()
        # Resolve canonical job id (name → id)
        canonical = job_id
        job = runner.cron_scheduler.get_job(job_id)
        if job and job.get("id"):
            canonical = str(job["id"])
        db = SessionDB()
        try:
            limit_n = max(1, min(int(limit), 100))
            runs = db.list_cron_job_runs(canonical, limit=limit_n, offset=0)
            now = time.time()
            for s in runs:
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
                s["archived"] = bool(s.get("archived"))
            return {"runs": runs, "limit": limit_n}
        finally:
            db.close()

    # ---- Toolsets missing endpoints ----
    @app.put("/api/tools/toolsets/{name}")
    async def toggle_toolset(name: str, enabled: bool, token: str = Depends(verify_token)):
        """Enable/disable a toolset (PUT alias for toggle)."""
        from hermes_cli.config import load_config, save_config
        from hermes_cli.tools_config import (
            _get_effective_configurable_toolsets,
            _get_platform_tools,
            _save_platform_tools,
        )
        valid = {ts for ts, _, _ in _get_effective_configurable_toolsets()}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown toolset: {name}")
        cfg = load_config()
        enabled_set = set(_get_platform_tools(cfg, "cli", include_default_mcp_servers=False))
        if enabled:
            enabled_set.add(name)
        else:
            enabled_set.discard(name)
        _save_platform_tools(cfg, "cli", enabled_set)
        return {"ok": True, "name": name, "enabled": enabled}

    @app.get("/api/tools/toolsets/{name}/config")
    async def get_toolset_config(name: str, token: str = Depends(verify_token)):
        """Return provider matrix + key status for a toolset."""
        from hermes_cli.config import load_config, get_env_value
        from hermes_cli.tools_config import (
            TOOL_CATEGORIES,
            _get_effective_configurable_toolsets,
            _is_provider_active,
            _visible_providers,
        )
        valid = {ts for ts, _, _ in _get_effective_configurable_toolsets()}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown toolset: {name}")
        cfg = load_config()
        cat = TOOL_CATEGORIES.get(name)
        providers = []
        active_provider = None
        if cat:
            for prov in _visible_providers(cat, cfg, force_fresh=True):
                env_vars = [
                    {
                        "key": e["key"],
                        "prompt": e.get("prompt", e["key"]),
                        "url": e.get("url"),
                        "default": e.get("default"),
                        "is_set": bool(get_env_value(e["key"])),
                    }
                    for e in prov.get("env_vars", [])
                ]
                is_active = _is_provider_active(prov, cfg, force_fresh=True)
                if is_active and active_provider is None:
                    active_provider = prov["name"]
                providers.append({
                    "name": prov["name"],
                    "badge": prov.get("badge", ""),
                    "tag": prov.get("tag", ""),
                    "env_vars": env_vars,
                    "post_setup": prov.get("post_setup"),
                    "requires_nous_auth": bool(prov.get("requires_nous_auth")),
                    "is_active": is_active,
                })
        return {
            "name": name,
            "has_category": cat is not None,
            "providers": providers,
            "active_provider": active_provider,
        }

    @app.put("/api/tools/toolsets/{name}/provider")
    async def select_toolset_provider(name: str, provider: str, token: str = Depends(verify_token)):
        """Persist a provider selection for a toolset."""
        from hermes_cli.config import load_config, save_config
        from hermes_cli.tools_config import apply_provider_selection, _get_effective_configurable_toolsets
        valid = {ts for ts, _, _ in _get_effective_configurable_toolsets()}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown toolset: {name}")
        cfg = load_config()
        try:
            apply_provider_selection(name, provider, cfg)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc).strip('"'))
        save_config(cfg)
        return {"ok": True, "name": name, "provider": provider}

    @app.put("/api/tools/toolsets/{name}/env")
    async def save_toolset_env(name: str, env: dict, token: str = Depends(verify_token)):
        """Persist env vars for a toolset's provider."""
        from hermes_cli.config import load_config, save_env_value, get_env_value
        from hermes_cli.tools_config import (
            TOOL_CATEGORIES,
            _get_effective_configurable_toolsets,
            _visible_providers,
        )
        valid_ts = {ts for ts, _, _ in _get_effective_configurable_toolsets()}
        if name not in valid_ts:
            raise HTTPException(status_code=400, detail=f"Unknown toolset: {name}")
        cfg = load_config()
        cat = TOOL_CATEGORIES.get(name)
        allowed: set = set()
        if cat:
            for prov in _visible_providers(cat, cfg, force_fresh=True):
                for e in prov.get("env_vars", []):
                    allowed.add(e["key"])
        unknown = [k for k in env if k not in allowed]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown env vars: {', '.join(sorted(unknown))}")
        saved, skipped = [], []
        for key, value in env.items():
            if value and value.strip():
                try:
                    save_env_value(key, value.strip())
                    saved.append(key)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
            else:
                skipped.append(key)
        status = {k: bool(get_env_value(k)) for k in allowed}
        return {"ok": True, "name": name, "saved": saved, "skipped": skipped, "is_set": status}

    @app.post("/api/tools/toolsets/{name}/post-setup")
    async def run_toolset_post_setup(name: str, key: str, token: str = Depends(verify_token)):
        """Spawn a provider's post-setup install hook."""
        from hermes_cli.tools_config import _get_effective_configurable_toolsets, valid_post_setup_keys
        valid_ts = {ts for ts, _, _ in _get_effective_configurable_toolsets()}
        if name not in valid_ts:
            raise HTTPException(status_code=400, detail=f"Unknown toolset: {name}")
        if key not in valid_post_setup_keys():
            raise HTTPException(status_code=400, detail=f"Unknown post-setup key: {key}")
        from hermes_cli.web_server import run_toolset_post_setup as _dash_post_setup
        from pydantic import BaseModel as _BM
        class _Body(_BM):
            key: str
            profile: Optional[str] = None
        return await _dash_post_setup(name=name, body=_Body(key=key))

    # ---- Messaging platform configure ----
    @app.put("/api/messaging/platforms/{platform}")
    async def configure_platform(platform: str, config: dict, token: str = Depends(verify_token)):
        """Save messaging platform config."""
        from gateway.config import Platform
        from hermes_cli.config import load_config, save_config
        try:
            Platform(platform)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid platform")
        cfg = load_config()
        if "platforms" not in cfg:
            cfg["platforms"] = {}
        cfg["platforms"][platform] = config
        save_config(cfg)
        return {"ok": True, "platform": platform}

    # ---- Sessions: GET /api/sessions/search alias (desktop uses GET) ----
    @app.get("/api/sessions/search")
    async def search_sessions_get(q: str, limit: int = 20, token: str = Depends(verify_token)):
        """Search sessions (GET alias — desktop calls this as GET with ?q=)."""
        db = SessionDB()
        try:
            results = db.search_sessions(q, limit=limit)
            return {"sessions": results}
        finally:
            db.close()

    # ---- OAuth flows (proxy to dashboard — it owns all the session state) ----
    @app.get("/api/providers/oauth")
    async def get_oauth_providers(token: str = Depends(verify_token)):
        """Return the OAuth provider catalog."""
        from hermes_cli.web_server import list_oauth_providers as _dash_get_oauth
        return await _dash_get_oauth()

    @app.post("/api/providers/oauth/{provider_id}/start")
    async def start_oauth(provider_id: str, token: str = Depends(verify_token)):
        """Initiate an OAuth login flow."""
        from hermes_cli.web_server import start_oauth_login as _dash_start_oauth
        from starlette.requests import Request as _Request
        # Build a minimal Request-like object carrying our token
        # The dashboard validates via _require_token, but we've already auth'd
        # via verify_token, so just delegate directly to the underlying function.
        from hermes_cli.web_server import (
            _gc_oauth_sessions,
            _OAUTH_PROVIDER_CATALOG,
            _start_anthropic_pkce,
            _start_device_code_flow,
            _start_xai_loopback_flow,
        )
        import asyncio as _asyncio
        _gc_oauth_sessions()
        valid = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
        if provider_id not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown provider {provider_id}")
        catalog_entry = next(p for p in _OAUTH_PROVIDER_CATALOG if p["id"] == provider_id)
        if catalog_entry["flow"] == "external":
            raise HTTPException(status_code=400, detail=f"{provider_id} uses an external CLI")
        if catalog_entry["flow"] == "pkce" and provider_id == "anthropic":
            return _start_anthropic_pkce()
        if catalog_entry["flow"] == "device_code":
            return await _start_device_code_flow(provider_id)
        if catalog_entry["flow"] == "loopback" and provider_id == "xai-oauth":
            return await _asyncio.get_running_loop().run_in_executor(None, _start_xai_loopback_flow)
        raise HTTPException(status_code=400, detail="Unsupported flow")

    @app.post("/api/providers/oauth/{provider_id}/submit")
    async def submit_oauth(provider_id: str, session_id: str, code: str, token: str = Depends(verify_token)):
        """Submit auth code for PKCE flows."""
        from hermes_cli.web_server import _submit_anthropic_pkce
        import asyncio as _asyncio
        if provider_id == "anthropic":
            return await _asyncio.get_running_loop().run_in_executor(
                None, _submit_anthropic_pkce, session_id, code
            )
        raise HTTPException(status_code=400, detail=f"submit not supported for {provider_id}")

    @app.get("/api/providers/oauth/{provider_id}/poll/{session_id}")
    async def poll_oauth(provider_id: str, session_id: str, token: str = Depends(verify_token)):
        """Poll an OAuth session's status."""
        from hermes_cli.web_server import _oauth_sessions, _oauth_sessions_lock
        import threading as _threading
        with _oauth_sessions_lock:
            sess = _oauth_sessions.get(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        if sess["provider"] != provider_id:
            raise HTTPException(status_code=400, detail="Provider mismatch for session")
        return {
            "session_id": session_id,
            "status": sess["status"],
            "error_message": sess.get("error_message"),
            "expires_at": sess.get("expires_at"),
        }

    @app.delete("/api/providers/oauth/sessions/{session_id}")
    async def cancel_oauth(session_id: str, token: str = Depends(verify_token)):
        """Cancel a pending OAuth session."""
        from hermes_cli.web_server import _oauth_sessions, _oauth_sessions_lock
        with _oauth_sessions_lock:
            sess = _oauth_sessions.pop(session_id, None)
        if sess is None:
            return {"ok": False, "message": "session not found"}
        return {"ok": True}

    # ---- Audio endpoints ----
    @app.get("/api/audio/elevenlabs/voices")
    async def get_elevenlabs_voices(token: str = Depends(verify_token)):
        """Return ElevenLabs voices when an API key is configured."""
        from hermes_cli.web_server import get_elevenlabs_voices as _dash_voices
        return await _dash_voices()

    @app.post("/api/audio/transcribe")
    async def transcribe_audio(data_url: str, mime_type: str = "", token: str = Depends(verify_token)):
        """Transcribe audio via the configured TTS provider."""
        from hermes_cli.web_server import transcribe_audio_upload as _dash_transcribe
        from pydantic import BaseModel as _BM
        class _Body(_BM):
            data_url: str
            mime_type: str = ""
        return await _dash_transcribe(payload=_Body(data_url=data_url, mime_type=mime_type))

    @app.post("/api/audio/speak")
    async def speak_text(text: str, token: str = Depends(verify_token)):
        """Convert text to speech via the configured TTS provider."""
        from hermes_cli.web_server import speak_text as _dash_speak
        from pydantic import BaseModel as _BM
        class _Body(_BM):
            text: str
        return await _dash_speak(payload=_Body(text=text))

    # ---- Analytics ----
    @app.get("/api/analytics/usage")
    async def get_usage_analytics(days: int = 30, token: str = Depends(verify_token)):
        """Return token/cost analytics for the given window."""
        from hermes_cli.web_server import get_usage_analytics as _dash_analytics
        return await _dash_analytics(days=days)

    return app


# ---- Run server ----
async def run_http_server(
    runner,
    host: str = "127.0.0.1",
    port: int = 0,
    token: Optional[str] = None,
) -> tuple:
    """
    Start the HTTP management server.
    
    Returns (server, actual_port) tuple.
    """
    import uvicorn

    set_gateway_runner(runner)
    set_http_token(token)

    app = create_app()

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
    )
    server = uvicorn.Server(config)

    # Start server in background
    http_task = asyncio.create_task(server.serve())

    # Wait for server to start and get actual port
    # Give it a moment to bind
    await asyncio.sleep(0.1)
    # Try to get the port - uvicorn might expose it differently
    actual_port = port
    if server.servers:
        try:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
        except Exception:
            pass

    return server, actual_port