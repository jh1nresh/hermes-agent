"""Telegram Managed Bot — automatic bot creation via Bot API 9.6.

Uses Telegram's Managed Bots feature to create bots for users without
manual BotFather interaction.  The flow:

1. CLI generates a pairing nonce and a t.me/newbot deep link.
2. User opens the link in Telegram and confirms bot creation.
3. A Nous-hosted manager bot receives the ``managed_bot`` update,
   calls ``getManagedBotToken``, and stores the token keyed by nonce.
4. CLI polls the pairing API and retrieves the token.
5. Token is saved to ``.env`` — zero manual copy-paste.

Requires:
  - A Nous-hosted manager bot with Bot Management Mode enabled.
  - A pairing API (Cloudflare Worker + KV or equivalent) at
    ``MANAGED_BOT_API_URL``.
"""

from __future__ import annotations

import secrets
import string
import sys
import time
import urllib.parse
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default pairing API base URL (Nous-hosted Cloudflare Worker).
# Override via config.yaml ``telegram.managed_bot_api_url`` for self-hosted.
DEFAULT_API_URL = "https://setup.hermes-agent.nousresearch.com"

# The Nous-hosted manager bot username (without @).
DEFAULT_MANAGER_BOT = "HermesSetupBot"

# How long to poll before giving up (seconds).
DEFAULT_POLL_TIMEOUT = 180

# Poll interval (seconds).
POLL_INTERVAL = 2

# ---------------------------------------------------------------------------
# QR code rendering
# ---------------------------------------------------------------------------


def render_qr_terminal(url: str) -> str:
    """Render a URL as a QR code string suitable for terminal output.

    Uses the ``qrcode`` library if available, otherwise returns an empty
    string (caller should fall back to printing the URL directly).
    """
    try:
        import io

        import qrcode  # type: ignore[import-untyped]

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)

        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except ImportError:
        return ""


def print_qr_code(url: str) -> None:
    """Print a QR code to stdout, with URL fallback if qrcode is missing."""
    qr_text = render_qr_terminal(url)
    if qr_text:
        print(qr_text)
    else:
        print(f"  (Install 'qrcode' for a scannable QR code: pip install qrcode)")
    print(f"  Link: {url}")


# ---------------------------------------------------------------------------
# Deep link generation
# ---------------------------------------------------------------------------


def _random_suffix(length: int = 4) -> str:
    """Generate a short random alphanumeric suffix for bot usernames."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_bot_username(profile_name: Optional[str] = None) -> str:
    """Generate a suggested bot username like ``hermes_work_a7f3_bot``.

    Telegram requires bot usernames to end with ``bot`` and be 5-32 chars.
    """
    base = "hermes"
    suffix = _random_suffix()
    if profile_name and profile_name != "default":
        # Sanitize profile name for Telegram username rules (a-z, 0-9, _)
        clean = "".join(c if c.isalnum() else "_" for c in profile_name.lower())
        clean = clean[:12]  # Keep it short
        return f"{base}_{clean}_{suffix}_bot"
    return f"{base}_{suffix}_bot"


def generate_deep_link(
    manager_bot: str = DEFAULT_MANAGER_BOT,
    suggested_username: Optional[str] = None,
    suggested_name: Optional[str] = None,
) -> str:
    """Build the ``t.me/newbot`` deep link for managed bot creation.

    Format: ``https://t.me/newbot/{manager_bot}/{suggested_username}[?name={name}]``
    """
    username = suggested_username or generate_bot_username()
    base_url = f"https://t.me/newbot/{manager_bot}/{username}"

    if suggested_name:
        params = urllib.parse.urlencode({"name": suggested_name})
        return f"{base_url}?{params}"
    return base_url


# ---------------------------------------------------------------------------
# Pairing protocol (client side)
# ---------------------------------------------------------------------------


def generate_pairing_nonce() -> str:
    """Generate a cryptographically random pairing nonce (32 hex chars)."""
    return secrets.token_hex(16)


def register_pairing(api_url: str, nonce: str, timeout: float = 10.0) -> bool:
    """Register a pairing nonce with the pairing API.

    ``POST /pair``  body: ``{"nonce": "..."}``

    Returns True on success, False on failure.
    """
    try:
        resp = httpx.post(
            f"{api_url}/pair",
            json={"nonce": nonce},
            timeout=timeout,
        )
        return resp.status_code in (200, 201)
    except httpx.HTTPError:
        return False


def poll_for_token(
    api_url: str,
    nonce: str,
    timeout: float = DEFAULT_POLL_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> Optional[str]:
    """Poll the pairing API until the bot token is available or timeout.

    ``GET /pair/{nonce}`` → 200 with ``{"token": "..."}`` when ready,
    404 while waiting.

    Returns the bot token string on success, None on timeout/failure.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(
                f"{api_url}/pair/{nonce}",
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    return token
            # 404 = not yet ready, keep polling
        except httpx.HTTPError:
            pass  # Network hiccup, retry
        time.sleep(interval)
    return None


# ---------------------------------------------------------------------------
# Orchestrator — called from setup wizard
# ---------------------------------------------------------------------------


def auto_setup_telegram_bot(
    api_url: str = DEFAULT_API_URL,
    manager_bot: str = DEFAULT_MANAGER_BOT,
    profile_name: Optional[str] = None,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
) -> Optional[str]:
    """Run the full automatic Telegram bot creation flow.

    1. Generate nonce + suggested username.
    2. Register the nonce with the pairing API.
    3. Print the QR code / deep link for the user.
    4. Poll until the token arrives (or timeout).

    Returns the bot token on success, None on failure/timeout.
    """
    nonce = generate_pairing_nonce()
    username = generate_bot_username(profile_name)
    deep_link = generate_deep_link(
        manager_bot=manager_bot,
        suggested_username=username,
        suggested_name="Hermes Agent",
    )

    # Embed the nonce in the pairing API so the manager bot can match it.
    # The manager bot receives the suggested_username from Telegram's
    # managed_bot update and uses it to look up the nonce.
    if not register_pairing(api_url, nonce):
        print("  ✗ Could not reach the Hermes setup service.")
        print("    Try the manual setup instead, or check your network.")
        return None

    print()
    print("  Scan this QR code with your phone, or open the link below:")
    print()
    print_qr_code(deep_link)
    print()
    print("  When Telegram opens, tap 'Create Bot' to confirm.")
    print("  (You can edit the bot name and username before confirming)")
    print()

    # Animated waiting
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start = time.monotonic()
    deadline = start + poll_timeout
    idx = 0

    while time.monotonic() < deadline:
        char = spinner_chars[idx % len(spinner_chars)]
        elapsed = int(time.monotonic() - start)
        remaining = int(poll_timeout - elapsed)
        sys.stdout.write(f"\r  {char} Waiting for bot creation... ({remaining}s remaining) ")
        sys.stdout.flush()
        idx += 1

        try:
            resp = httpx.get(f"{api_url}/pair/{nonce}", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    sys.stdout.write("\r  ✓ Bot created successfully!                              \n")
                    sys.stdout.flush()
                    return token
        except httpx.HTTPError:
            pass
        time.sleep(POLL_INTERVAL)

    sys.stdout.write("\r  ✗ Timed out waiting for bot creation.                    \n")
    sys.stdout.flush()
    print("    The bot may still be created — check Telegram.")
    print("    You can paste the token manually below, or re-run setup.")
    return None
