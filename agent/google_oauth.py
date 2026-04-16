"""Google OAuth PKCE flow for the Gemini (google-gemini-cli) inference provider.

This module implements the browser-based Authorization Code + PKCE (S256) flow
against Google's OAuth 2.0 endpoints so users can authenticate Hermes with their
Google account and hit the Gemini OpenAI-compatible endpoint
(``https://generativelanguage.googleapis.com/v1beta/openai``) with a Bearer
access token instead of copy-pasting an API key.

Synthesized from competing PRs #10176 (@sliverp) and #10779 (@newarthur):

- PKCE generator, save/load, exchange, refresh, login flow, email fetch — shaped
  after #10176's clean module layout.
- Cross-process file lock (fcntl on POSIX, msvcrt on Windows) with a thread-local
  re-entrancy counter — from #10779. This prevents two Hermes processes racing
  a token refresh and clobbering the rotated refresh_token.

Flow summary
------------
1. Generate a PKCE verifier/challenge pair (S256).
2. Spin up a localhost HTTP server on 127.0.0.1 to receive the OAuth callback.
   If port 8085 is taken, we fall back to an ephemeral port and retry.
3. Open ``accounts.google.com/o/oauth2/v2/auth?...`` in the user's browser.
4. Capture the ``code`` from the callback (or accept a manual paste in headless
   environments) and exchange it for access + refresh tokens.
5. Save tokens atomically to ``~/.hermes/auth/google_oauth.json`` (0o600).
6. On every runtime request, ``get_valid_access_token()`` loads the file, refreshes
   the access token if it expires within ``REFRESH_SKEW_SECONDS``, and returns
   a fresh bearer token.

Client ID / secret
------------------
No OAuth client is shipped by default. A maintainer must register a "Desktop"
OAuth client in Google Cloud Console (Generative Language API enabled) and set
``HERMES_GEMINI_CLIENT_ID`` (and optionally ``HERMES_GEMINI_CLIENT_SECRET``) in
``~/.hermes/.env``. See ``website/docs/integrations/providers.md`` for the full
registration walkthrough.

Storage format (``~/.hermes/auth/google_oauth.json``)::

    {
      "client_id": "...",
      "client_secret": "...",
      "access_token": "...",
      "refresh_token": "...",
      "expires_at": 1744848000.0,   // unix seconds, float
      "email": "user@example.com"    // optional, display-only
    }

Public API
----------
- ``start_oauth_flow(force_relogin=False)`` — interactive browser login
- ``get_valid_access_token()`` — runtime entry, refreshes as needed
- ``load_credentials()`` / ``save_credentials()`` / ``clear_credentials()``
- ``refresh_access_token()`` — standalone refresh helper
- ``run_gemini_oauth_login_pure()`` — credential-pool-compatible variant
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import logging
import os
import secrets
import socket
import stat
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo"

# Scopes: generative-language lets us hit v1beta/openai; userinfo.email is for
# the display label in `hermes auth list`. Joined with a space per RFC 6749.
OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/generative-language "
    "https://www.googleapis.com/auth/userinfo.email"
)

# Preferred loopback port. If busy we ask the OS for an ephemeral port.
# 8085 matches Google's own gemini-cli so users who already authorized a redirect
# URI on that port keep working.
DEFAULT_REDIRECT_PORT = 8085
REDIRECT_HOST = "127.0.0.1"
CALLBACK_PATH = "/oauth2callback"

# Refresh the access token when fewer than this many seconds remain.
REFRESH_SKEW_SECONDS = 300  # 5 minutes

# Default timeouts
TOKEN_REQUEST_TIMEOUT_SECONDS = 20.0
CALLBACK_WAIT_SECONDS = 300  # 5 min for user to complete browser flow
LOCK_TIMEOUT_SECONDS = 30.0

# Environment overrides (documented in reference/environment-variables.md)
ENV_CLIENT_ID = "HERMES_GEMINI_CLIENT_ID"
ENV_CLIENT_SECRET = "HERMES_GEMINI_CLIENT_SECRET"

# Module-level default client credentials. Empty until a maintainer registers
# a "Hermes Agent" desktop OAuth client in Google Cloud Console and pastes the
# values here (or sets them as env vars at runtime). See module docstring.
_DEFAULT_CLIENT_ID = ""
_DEFAULT_CLIENT_SECRET = ""


# =============================================================================
# Error types
# =============================================================================

class GoogleOAuthError(RuntimeError):
    """Raised for any failure in the Google OAuth flow."""

    def __init__(self, message: str, *, code: str = "google_oauth_error") -> None:
        super().__init__(message)
        self.code = code


# =============================================================================
# File paths & cross-process locking
# =============================================================================

def _credentials_path() -> Path:
    """Location of the Gemini OAuth creds file."""
    return get_hermes_home() / "auth" / "google_oauth.json"


def _lock_path() -> Path:
    return _credentials_path().with_suffix(".json.lock")


# Re-entrancy depth counter so nested calls to _credentials_lock() from the same
# thread don't deadlock. Per-thread via threading.local.
_lock_state = threading.local()


@contextlib.contextmanager
def _credentials_lock(timeout_seconds: float = LOCK_TIMEOUT_SECONDS):
    """Cross-process lock around the credentials file (fcntl POSIX / msvcrt Windows).

    Thread-safe via a per-thread re-entrancy counter — nested acquisitions by the
    same thread no-op after the first. On unsupported platforms this degrades to
    a threading.Lock (still safe within a single process).
    """
    depth = getattr(_lock_state, "depth", 0)
    if depth > 0:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return

    lock_file_path = _lock_path()
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            import fcntl  # POSIX
        except ImportError:
            fcntl = None  # Windows

        if fcntl is not None:
            deadline = time.monotonic() + max(0.0, float(timeout_seconds))
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring Google OAuth credentials lock at {lock_file_path}."
                        )
                    time.sleep(0.05)
        else:
            try:
                import msvcrt  # type: ignore[import-not-found]

                deadline = time.monotonic() + max(0.0, float(timeout_seconds))
                while True:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"Timed out acquiring Google OAuth credentials lock at {lock_file_path}."
                            )
                        time.sleep(0.05)
            except ImportError:
                # Last resort: threading-only
                logger.debug("Neither fcntl nor msvcrt available; falling back to thread-only lock")
                acquired = True

        _lock_state.depth = 1
        yield
    finally:
        try:
            if acquired:
                try:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                except ImportError:
                    try:
                        import msvcrt  # type: ignore[import-not-found]

                        try:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    except ImportError:
                        pass
        finally:
            os.close(fd)
            _lock_state.depth = 0


# =============================================================================
# Client ID resolution
# =============================================================================

def _get_client_id() -> str:
    return (os.getenv(ENV_CLIENT_ID) or _DEFAULT_CLIENT_ID).strip()


def _get_client_secret() -> str:
    return (os.getenv(ENV_CLIENT_SECRET) or _DEFAULT_CLIENT_SECRET).strip()


def _require_client_id() -> str:
    client_id = _get_client_id()
    if not client_id:
        raise GoogleOAuthError(
            "Google OAuth client ID is not configured. Set "
            f"{ENV_CLIENT_ID} in ~/.hermes/.env, or ask the Hermes maintainers to "
            "ship a default. See "
            "https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md "
            "for the GCP Desktop OAuth client registration walkthrough.",
            code="google_oauth_client_id_missing",
        )
    return client_id


# =============================================================================
# PKCE
# =============================================================================

def _generate_pkce_pair() -> Tuple[str, str]:
    """Generate a PKCE (verifier, challenge) pair using S256.

    Verifier is 43–128 chars of url-safe base64 (we use 64 bytes → 86 chars).
    Challenge is SHA256(verifier), url-safe base64 without padding.
    """
    verifier = secrets.token_urlsafe(64)  # 86 chars, well within PKCE limits
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# =============================================================================
# Credential I/O
# =============================================================================

@dataclass
class GoogleCredentials:
    access_token: str
    refresh_token: str
    expires_at: float
    client_id: str
    client_secret: str = ""
    email: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": float(self.expires_at),
            "email": self.email,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoogleCredentials":
        return cls(
            access_token=str(data.get("access_token", "") or ""),
            refresh_token=str(data.get("refresh_token", "") or ""),
            expires_at=float(data.get("expires_at", 0) or 0),
            client_id=str(data.get("client_id", "") or ""),
            client_secret=str(data.get("client_secret", "") or ""),
            email=str(data.get("email", "") or ""),
        )


def load_credentials() -> Optional[GoogleCredentials]:
    """Load credentials from disk. Returns None if missing or corrupt."""
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        with _credentials_lock():
            raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, IOError) as exc:
        logger.warning("Failed to read Google OAuth credentials at %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Google OAuth credentials file %s is not a JSON object", path)
        return None
    creds = GoogleCredentials.from_dict(data)
    if not creds.access_token:
        return None
    return creds


def save_credentials(creds: GoogleCredentials) -> Path:
    """Atomically write creds to disk with 0600 permissions."""
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize outside the lock to minimize hold time
    payload = json.dumps(creds.to_dict(), indent=2, sort_keys=True) + "\n"

    with _credentials_lock():
        tmp_path = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_path, path)
        finally:
            # Cleanup temp file if replace failed partway
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
    return path


def clear_credentials() -> None:
    """Remove the creds file and its lock file. Idempotent."""
    path = _credentials_path()
    with _credentials_lock():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove Google OAuth credentials at %s: %s", path, exc)


# =============================================================================
# Token endpoint
# =============================================================================

def _post_form(url: str, data: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """POST x-www-form-urlencoded and return parsed JSON response."""
    body = urllib.parse.urlencode(data).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise GoogleOAuthError(
            f"Google OAuth token endpoint returned HTTP {exc.code}: {detail or exc.reason}",
            code="google_oauth_token_http_error",
        ) from exc
    except urllib.error.URLError as exc:
        raise GoogleOAuthError(
            f"Google OAuth token request failed: {exc}",
            code="google_oauth_token_network_error",
        ) from exc


def exchange_code(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Exchange an authorization code for access + refresh tokens."""
    cid = client_id if client_id is not None else _require_client_id()
    csecret = client_secret if client_secret is not None else _get_client_secret()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": cid,
        "redirect_uri": redirect_uri,
    }
    if csecret:
        data["client_secret"] = csecret
    return _post_form(TOKEN_ENDPOINT, data, timeout)


def refresh_access_token(
    refresh_token: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Refresh the access token using a refresh token."""
    if not refresh_token:
        raise GoogleOAuthError(
            "Cannot refresh: refresh_token is empty. Re-run `hermes login --provider google-gemini-cli`.",
            code="google_oauth_refresh_token_missing",
        )
    cid = client_id if client_id is not None else _require_client_id()
    csecret = client_secret if client_secret is not None else _get_client_secret()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cid,
    }
    if csecret:
        data["client_secret"] = csecret
    return _post_form(TOKEN_ENDPOINT, data, timeout)


def _fetch_user_email(access_token: str, timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS) -> str:
    """Best-effort userinfo lookup for display. Failures return empty string."""
    try:
        request = urllib.request.Request(
            USERINFO_ENDPOINT + "?alt=json",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return str(data.get("email", "") or "")
    except Exception as exc:
        logger.debug("Userinfo fetch failed (non-fatal): %s", exc)
        return ""


# =============================================================================
# Local callback server
# =============================================================================

class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the OAuth callback code/error and returns a styled HTML page."""

    # Injected at class level before each flow; protected by the server being
    # single-shot (we spin up a new HTTPServer per flow).
    expected_state: str = ""
    captured_code: Optional[str] = None
    captured_error: Optional[str] = None
    ready: Optional[threading.Event] = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, N802
        # Silence default access-log chatter
        logger.debug("OAuth callback: " + format, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        error = (params.get("error") or [""])[0]
        code = (params.get("code") or [""])[0]

        if state != type(self).expected_state:
            type(self).captured_error = "state_mismatch"
            self._respond_html(400, _ERROR_PAGE.format(message="State mismatch — aborting for safety."))
        elif error:
            type(self).captured_error = error
            self._respond_html(400, _ERROR_PAGE.format(message=f"Authorization denied: {error}"))
        elif code:
            type(self).captured_code = code
            self._respond_html(200, _SUCCESS_PAGE)
        else:
            type(self).captured_error = "no_code"
            self._respond_html(400, _ERROR_PAGE.format(message="Callback received no authorization code."))

        if type(self).ready is not None:
            type(self).ready.set()

    def _respond_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


_SUCCESS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Hermes — signed in</title>
<style>
body { font: 16px/1.5 system-ui, sans-serif; margin: 10vh auto; max-width: 32rem; text-align: center; color: #222; }
h1 { color: #1a7f37; } p { color: #555; }
</style></head>
<body><h1>Signed in to Google.</h1>
<p>You can close this tab and return to your terminal.</p></body></html>
"""

_ERROR_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Hermes — sign-in failed</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 10vh auto; max-width: 32rem; text-align: center; color: #222; }}
h1 {{ color: #b42318; }} p {{ color: #555; }}
</style></head>
<body><h1>Sign-in failed</h1><p>{message}</p>
<p>Return to your terminal — Hermes can walk you through a manual paste fallback.</p></body></html>
"""


def _bind_callback_server(preferred_port: int = DEFAULT_REDIRECT_PORT) -> Tuple[http.server.HTTPServer, int]:
    """Try to bind on the preferred port; fall back to an ephemeral port if busy.

    Returns (server, actual_port) or raises OSError if even the ephemeral bind fails.
    """
    try:
        server = http.server.HTTPServer((REDIRECT_HOST, preferred_port), _OAuthCallbackHandler)
        return server, preferred_port
    except OSError as exc:
        logger.info(
            "Preferred OAuth callback port %d unavailable (%s); requesting ephemeral port",
            preferred_port, exc,
        )
    # Ephemeral port fallback
    server = http.server.HTTPServer((REDIRECT_HOST, 0), _OAuthCallbackHandler)
    return server, server.server_address[1]


def _port_is_available(port: int) -> bool:
    """Return True if ``port`` can be bound on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((REDIRECT_HOST, port))
            return True
        except OSError:
            return False


# =============================================================================
# Main login flow
# =============================================================================

def start_oauth_flow(
    *,
    force_relogin: bool = False,
    open_browser: bool = True,
    callback_wait_seconds: float = CALLBACK_WAIT_SECONDS,
) -> GoogleCredentials:
    """Run the interactive browser OAuth flow and persist credentials.

    Args:
        force_relogin: If False and valid creds already exist, return them unchanged.
        open_browser: If False, skip webbrowser.open and print the URL only.
        callback_wait_seconds: Max seconds to wait for the browser callback.

    Returns:
        GoogleCredentials that were just saved to disk.
    """
    if not force_relogin:
        existing = load_credentials()
        if existing and existing.access_token:
            logger.info("Google OAuth credentials already present; skipping login.")
            return existing

    client_id = _require_client_id()
    client_secret = _get_client_secret()

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    server, port = _bind_callback_server(DEFAULT_REDIRECT_PORT)
    redirect_uri = f"http://{REDIRECT_HOST}:{port}{CALLBACK_PATH}"

    # Reset class-level capture state on the handler
    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.captured_code = None
    _OAuthCallbackHandler.captured_error = None
    ready = threading.Event()
    _OAuthCallbackHandler.ready = ready

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        # prompt=consent on initial login ensures Google issues a refresh_token.
        # We do NOT pass prompt=consent on refresh, so users aren't re-nagged.
        "prompt": "consent",
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print()
    print("Opening your browser to sign in to Google…")
    print(f"If it does not open automatically, visit:\n  {auth_url}")
    print()

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception as exc:
            logger.debug("webbrowser.open failed: %s", exc)

    code: Optional[str] = None
    try:
        if ready.wait(timeout=callback_wait_seconds):
            code = _OAuthCallbackHandler.captured_code
            error = _OAuthCallbackHandler.captured_error
            if error:
                raise GoogleOAuthError(
                    f"Authorization failed: {error}",
                    code="google_oauth_authorization_failed",
                )
        else:
            logger.info("Callback server timed out — offering manual paste fallback.")
            code = _prompt_paste_fallback()
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        server_thread.join(timeout=2.0)

    if not code:
        raise GoogleOAuthError(
            "No authorization code received. Aborting.",
            code="google_oauth_no_code",
        )

    token_resp = exchange_code(
        code, verifier, redirect_uri,
        client_id=client_id, client_secret=client_secret,
    )
    return _persist_token_response(token_resp, client_id=client_id, client_secret=client_secret)


def _prompt_paste_fallback() -> Optional[str]:
    """Ask the user to paste the callback URL or raw code (headless fallback)."""
    print()
    print("The browser callback did not complete automatically (port blocked or headless env).")
    print("Paste the full redirect URL Google showed you, OR just the 'code=' parameter value.")
    raw = input("Callback URL or code: ").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urllib.parse.urlparse(raw)
        params = urllib.parse.parse_qs(parsed.query)
        return (params.get("code") or [""])[0] or None
    return raw


def _persist_token_response(
    token_resp: Dict[str, Any],
    *,
    client_id: str,
    client_secret: str,
) -> GoogleCredentials:
    access_token = str(token_resp.get("access_token", "") or "").strip()
    refresh_token = str(token_resp.get("refresh_token", "") or "").strip()
    expires_in = int(token_resp.get("expires_in", 0) or 0)
    if not access_token or not refresh_token:
        raise GoogleOAuthError(
            "Google token response missing access_token or refresh_token.",
            code="google_oauth_incomplete_token_response",
        )
    creds = GoogleCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + max(60, expires_in),
        client_id=client_id,
        client_secret=client_secret,
        email=_fetch_user_email(access_token),
    )
    save_credentials(creds)
    logger.info("Google OAuth credentials saved to %s", _credentials_path())
    return creds


# =============================================================================
# Runtime token accessor (called on every inference request)
# =============================================================================

def get_valid_access_token(*, force_refresh: bool = False) -> str:
    """Load creds, refreshing if near expiry, and return a valid bearer token.

    Raises GoogleOAuthError if no creds are stored or if refresh fails.
    """
    creds = load_credentials()
    if creds is None:
        raise GoogleOAuthError(
            "No Google OAuth credentials found. Run `hermes login --provider google-gemini-cli` first.",
            code="google_oauth_not_logged_in",
        )

    should_refresh = force_refresh or (time.time() + REFRESH_SKEW_SECONDS >= creds.expires_at)
    if not should_refresh:
        return creds.access_token

    try:
        resp = refresh_access_token(
            creds.refresh_token,
            client_id=creds.client_id or None,
            client_secret=creds.client_secret or None,
        )
    except GoogleOAuthError:
        raise

    new_access = str(resp.get("access_token", "") or "").strip()
    if not new_access:
        raise GoogleOAuthError(
            "Refresh response did not include an access_token.",
            code="google_oauth_refresh_empty",
        )
    # Google sometimes rotates refresh_token; preserve existing if omitted.
    new_refresh = str(resp.get("refresh_token", "") or "").strip() or creds.refresh_token
    expires_in = int(resp.get("expires_in", 0) or 0)

    creds.access_token = new_access
    creds.refresh_token = new_refresh
    creds.expires_at = time.time() + max(60, expires_in)
    save_credentials(creds)
    return creds.access_token


# =============================================================================
# Credential-pool-compatible variant
# =============================================================================

def run_gemini_oauth_login_pure() -> Dict[str, Any]:
    """Run the login flow and return a dict matching the credential pool shape.

    This is used by `hermes auth add --provider google-gemini-cli` to register
    an entry in the multi-account credential pool alongside the flat file.
    """
    creds = start_oauth_flow(force_relogin=True)
    return {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at_ms": int(creds.expires_at * 1000),
        "email": creds.email,
        "client_id": creds.client_id,
    }
