"""
Computer Use Tool Module

Enables Claude to control the desktop via screenshots, mouse, and keyboard
using Anthropic's Computer Use API (beta). macOS only.

Screenshots are taken via the native `screencapture` command (no dependencies).
Mouse and keyboard actions use `pyautogui` (optional dependency).

The tool definition uses Anthropic's native format (`computer_20251124`),
not the standard OpenAI function-calling schema. A stub schema is registered
in the normal tool registry for dispatch; the native definition is injected
separately into Anthropic API calls via `get_native_tool_definition()`.

Environment:
    Requires macOS and `pyautogui` (install: `uv pip install -e '.[computer-use]'`).
    Mouse/keyboard actions require macOS Accessibility permission
    (System Settings > Privacy & Security > Accessibility).

Usage:
    hermes -t computer_use   # enable the computer use toolset
"""

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Debug log path — use hermes logs directory instead of world-readable /tmp
_DEBUG_LOG_DIR = get_hermes_home() / "logs"
_DEBUG_LOG_PATH = _DEBUG_LOG_DIR / "computer_debug.log"
_DEBUG_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2MB — rotate when exceeded
_debug_log_ready = False  # mkdir guard — avoid repeated I/O


def _debug_log(line: str) -> None:
    """Append a line to the computer use debug log.

    Creates log directory on first call. Rotates the log file when it
    exceeds _DEBUG_LOG_MAX_BYTES by keeping the latest half.
    """
    global _debug_log_ready
    try:
        if not _debug_log_ready:
            _DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
            _debug_log_ready = True
        # Rotate if file is too large
        if _DEBUG_LOG_PATH.exists() and _DEBUG_LOG_PATH.stat().st_size > _DEBUG_LOG_MAX_BYTES:
            content = _DEBUG_LOG_PATH.read_text(errors="replace")
            half = len(content) // 2
            # Cut at a newline boundary to avoid broken lines
            cut = content.index("\n", half) + 1 if "\n" in content[half:] else half
            _DEBUG_LOG_PATH.write_text(f"[log rotated — older entries trimmed]\n{content[cut:]}")
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass

# Approval callback — registered by CLI at startup for prompt_toolkit integration.
# Same pattern as terminal_tool._approval_callback.
_approval_callback = None


def set_approval_callback(cb):
    """Register a callback for computer_use approval prompts (used by CLI)."""
    global _approval_callback
    _approval_callback = cb


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Anthropic recommends max 1568px on longest edge for screenshots
_MAX_SCREENSHOT_EDGE = 1568
# Anthropic auto-downscales images over ~1,150,000 pixels (~1533 tokens).
# If we exceed this, Claude sees a smaller image than we declare in
# display_width_px/display_height_px, causing coordinate mismatch.
# Use 1,100,000 as safe limit (leaves headroom).
_MAX_SCREENSHOT_PIXELS = 1_100_000

# Actions that modify system state — require user approval
_DESTRUCTIVE_ACTIONS = frozenset({
    "left_click", "right_click", "double_click", "triple_click",
    "middle_click", "left_click_drag", "left_mouse_down", "left_mouse_up",
    "type", "key", "scroll", "hold_key",
})

# Actions that are read-only
_SAFE_ACTIONS = frozenset({"screenshot", "mouse_move", "wait", "zoom"})

ALL_ACTIONS = sorted(_DESTRUCTIVE_ACTIONS | _SAFE_ACTIONS)

# Blocked key combinations — irreversible actions that cause data loss or
# loss of control. The agent prompt discourages these but prompt-level
# guardrails can be bypassed; this is a hard code-level block.
_BLOCKED_KEY_COMBOS = {
    frozenset({"command", "shift", "backspace"}),       # Empty Trash — permanent deletion
    frozenset({"command", "option", "backspace"}),      # Force delete — bypasses Trash
    frozenset({"command", "ctrl", "q"}),               # Lock screen — loses all control
    frozenset({"command", "shift", "q"}),               # Log out — terminates session
    frozenset({"command", "option", "shift", "q"}),     # Force log out — no save prompt
}

# Dangerous text patterns for the `type` action. These are blocked at code
# level because prompt-level guardrails can be bypassed via injection.
# Only patterns with high confidence of malicious intent are blocked;
# ambiguous patterns (passwords, credit cards) are left to the model.
import re as _re
_BLOCKED_TYPE_PATTERNS = [
    # Remote code execution
    _re.compile(r"curl\s+.+\|\s*(ba)?sh", _re.IGNORECASE),      # curl ... | bash
    _re.compile(r"wget\s+.+\|\s*(ba)?sh", _re.IGNORECASE),      # wget ... | bash
    _re.compile(r"curl\s+.+\|\s*python", _re.IGNORECASE),       # curl ... | python
    # Destructive commands
    _re.compile(r"sudo\s+rm\s+.*-[rR]", _re.IGNORECASE),        # sudo rm -rf
    _re.compile(r"mkfs\.", _re.IGNORECASE),                       # mkfs.ext4 (format disk)
    _re.compile(r"dd\s+if=.+of=/dev/", _re.IGNORECASE),          # dd write to device
    _re.compile(r"chmod\s+777\s+/", _re.IGNORECASE),             # chmod 777 / (open everything)
    _re.compile(r">\s*/etc/", _re.IGNORECASE),                    # overwrite system files
    # Privilege escalation — agent should never gain root via GUI typing
    _re.compile(r"sudo\s+(-\w+\s+)*su(\s|$)", _re.IGNORECASE),  # sudo su
    _re.compile(r"sudo\s+(-\w+\s+)*-s(\s|$)", _re.IGNORECASE),  # sudo -s (root shell)
    _re.compile(r"sudo\s+(-\w+\s+)*(ba)?sh(\s|$)", _re.IGNORECASE),  # sudo bash/sh
    _re.compile(r"sudo\s+(-\w+\s+)*passwd", _re.IGNORECASE),    # sudo passwd (change passwords)
    _re.compile(r"sudo\s+(-\w+\s+)*visudo", _re.IGNORECASE),    # sudo visudo (edit sudoers)
    _re.compile(r"sudo\s+(-\w+\s+)*chown\s+.*root", _re.IGNORECASE),  # sudo chown root
]

# pyautogui key name normalization.
# Claude sends "cmd" but pyautogui requires "command" (keycode 55).
# Without this mapping, pyautogui.hotkey("cmd", "n") silently drops
# the "cmd" key (no keycode found) and only presses "n".
# Same for "super" — not a valid pyautogui key on macOS.
_KEY_NAME_MAP = {
    # Modifier aliases
    "cmd": "command",
    "super": "command",
    "meta": "command",
    "win": "command",
    "opt": "option",
    "control": "ctrl",
    # Keys Claude sends with underscores/different names
    "page_down": "pagedown",
    "page_up": "pageup",
    "arrow_up": "up",
    "arrow_down": "down",
    "arrow_left": "left",
    "arrow_right": "right",
    "delete": "backspace",  # macOS Delete key = backspace
}

# Maximum number of screenshot/zoom temp files to keep in /tmp
_MAX_TEMP_FILES = 5


def _cleanup_temp_files() -> None:
    """Remove old hermes screenshot/zoom temp files, keeping the latest ones."""
    import glob
    patterns = ["/tmp/hermes_screenshot_*.jpg", "/tmp/hermes_screenshot_*.png",
                "/tmp/hermes_zoom_*.jpg", "/tmp/hermes_zoom_full_*.jpg"]
    all_files = []
    for pat in patterns:
        all_files.extend(glob.glob(pat))
    if len(all_files) <= _MAX_TEMP_FILES:
        return
    # Sort by modification time, oldest first. Use 0 for files deleted between
    # glob and getmtime (race with concurrent sessions).
    def _safe_mtime(f: str) -> float:
        try:
            return os.path.getmtime(f)
        except OSError:
            return 0
    all_files.sort(key=_safe_mtime)
    for f in all_files[:-_MAX_TEMP_FILES]:
        try:
            os.unlink(f)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Screen resolution helpers
# ---------------------------------------------------------------------------

_cached_screen_size: Optional[Tuple[int, int]] = None
_cached_screenshot_size: Optional[Tuple[int, int]] = None  # Actual image dimensions sent to Claude


def _get_screen_size() -> Tuple[int, int]:
    """Return logical screen resolution (width, height) on macOS."""
    global _cached_screen_size
    if _cached_screen_size:
        return _cached_screen_size
    try:
        import pyautogui
        _cached_screen_size = pyautogui.size()
        return _cached_screen_size
    except Exception:
        # Fallback: assume standard resolution
        return (1920, 1080)


def _compute_scale(actual_w: int, actual_h: int) -> Tuple[int, int, float]:
    """Compute the downsampled image size and scale factor.

    Returns (image_width, image_height, scale_factor) where scale_factor
    is applied to the actual dimensions to produce the image dimensions
    that Claude will see.

    Checks both edge limit (1568px) and pixel limit (~1.15MP) to match
    the logic in _take_screenshot(). Without the pixel check, the tool
    definition declares display_width_px=1470 while the actual screenshot
    is 1300px — a 13% coordinate mismatch on the first turn.
    """
    import math as _math
    long_edge = max(actual_w, actual_h)
    total_pixels = actual_w * actual_h
    edge_scale = min(1.0, _MAX_SCREENSHOT_EDGE / long_edge)
    pixel_scale = min(1.0, _math.sqrt(_MAX_SCREENSHOT_PIXELS / total_pixels))
    scale = min(edge_scale, pixel_scale)
    if scale >= 1.0:
        return actual_w, actual_h, 1.0
    new_w = int(actual_w * scale)
    # Derive height from width to match sips --resampleWidth behavior
    new_h = round(actual_h * new_w / actual_w)
    return new_w, new_h, scale


def scale_coordinates_to_screen(
    claude_x: int, claude_y: int,
    actual_w: int, actual_h: int,
    image_w: int, image_h: int,
) -> Tuple[int, int]:
    """Scale coordinates from Claude's downsampled image space to actual screen."""
    scale_x = actual_w / image_w if image_w else 1.0
    scale_y = actual_h / image_h if image_h else 1.0
    return round(claude_x * scale_x), round(claude_y * scale_y)


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def _take_screenshot() -> Tuple[str, int, int, str]:
    """Capture screenshot, resize to API limits, return (base64_data, image_w, image_h, media_type).

    Uses macOS native `screencapture` for capture and `sips` for resizing.
    Returns PNG format for text sharpness. No Python imaging dependencies required.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name

    try:
        # Capture screenshot silently (-x = no sound)
        subprocess.run(
            ["screencapture", "-x", "-C", "-t", "png", tmp_path],
            capture_output=True, timeout=10,
        )
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("screencapture produced no output")

        # Get actual image dimensions via sips
        result = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", tmp_path],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        img_w = img_h = 0
        for line in lines:
            if "pixelWidth" in line:
                img_w = int(line.split(":")[-1].strip())
            elif "pixelHeight" in line:
                img_h = int(line.split(":")[-1].strip())

        # Resize to logical resolution (pyautogui coordinate space).
        # screencapture captures at physical/Retina pixels (e.g. 2940x1912)
        # but pyautogui works in logical points (e.g. 1470x956).
        # Resizing to logical ensures Claude's coordinates map 1:1 to
        # pyautogui without any scaling math.
        logical_w, logical_h = _get_screen_size()
        if img_w != logical_w or img_h != logical_h:
            subprocess.run(
                ["sips", "--resampleWidth", str(logical_w), tmp_path],
                capture_output=True, timeout=10,
            )
            img_w, img_h = logical_w, logical_h

        # Further downscale if logical resolution exceeds Anthropic's limits.
        # Two constraints: max edge (1568px) and max total pixels (~1.15MP).
        # Exceeding either causes Anthropic to auto-downscale the image,
        # making Claude see different dimensions than display_width_px/display_height_px,
        # which causes coordinate mismatch (Claude targets wrong pixels).
        import math as _math
        total_pixels = img_w * img_h
        long_edge = max(img_w, img_h)

        edge_scale = min(1.0, _MAX_SCREENSHOT_EDGE / long_edge)
        pixel_scale = min(1.0, _math.sqrt(_MAX_SCREENSHOT_PIXELS / total_pixels))
        scale = min(edge_scale, pixel_scale)

        if scale < 1.0:
            new_w = int(img_w * scale)
            subprocess.run(
                ["sips", "--resampleWidth", str(new_w), tmp_path],
                capture_output=True, timeout=10,
            )
            # Read actual dimensions after resize — sips may round height
            # differently than Python (off by 1px), and _cached_screenshot_size
            # must match the real image pixels Claude sees.
            _resized = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", tmp_path],
                capture_output=True, text=True, timeout=5,
            )
            img_w, img_h = new_w, round(img_h * new_w / img_w)  # fallback
            for _line in _resized.stdout.strip().splitlines():
                if "pixelWidth" in _line:
                    img_w = int(_line.split(":")[-1].strip())
                elif "pixelHeight" in _line:
                    img_h = int(_line.split(":")[-1].strip())

        # Keep PNG format — token cost is pixel-based (width*height/750),
        # not byte-based, so PNG vs JPEG costs the same tokens. But PNG
        # preserves text sharpness in menus, buttons, and small UI elements
        # that Claude needs to read for accurate coordinate targeting.
        # Read and base64 encode
        with open(tmp_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        media_type = "image/png"

        # Cache actual screenshot dimensions for native tool definition
        global _cached_screenshot_size
        _cached_screenshot_size = (img_w, img_h)

        return data, img_w, img_h, media_type

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Quartz-level drag  (pyautogui can't do this correctly)
# ---------------------------------------------------------------------------

def _quartz_drag(sx: int, sy: int, ex: int, ey: int,
                 duration: float = 0.8, steps: int = 40) -> None:
    """Drag from (sx,sy) to (ex,ey) using native Quartz CGEvents.

    pyautogui.moveTo() always sends kCGEventMouseMoved, even when the mouse
    button is held down. macOS expects kCGEventLeftMouseDragged while a button
    is pressed — without the correct event type the OS never initiates a drag
    operation. This helper sends the correct event sequence directly via Quartz.
    """
    import Quartz
    btn = Quartz.kCGMouseButtonLeft

    # 1. Move cursor to start (no button pressed)
    move_ev = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, (sx, sy), btn)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move_ev)
    time.sleep(0.15)

    # 2. Mouse-down at start
    down_ev = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, (sx, sy), btn)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_ev)
    # Brief pause — just enough for macOS to register the press but short
    # enough to beat Finder's inline-rename timer (~0.5s). Moving quickly
    # after mouseDown tells the OS "this is a drag, not a click-to-rename".
    time.sleep(0.15)

    # 3. Small initial drag to cross macOS drag-initiation threshold (~3px).
    #    This nudge makes macOS commit to a drag operation before any rename
    #    or selection-rect logic can activate.
    nudge_x = sx + (4 if ex >= sx else -4)
    nudge_y = sy + (4 if ey >= sy else -4)
    nudge_ev = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDragged, (nudge_x, nudge_y), btn)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, nudge_ev)
    time.sleep(0.1)

    # 4. Drag to destination in small steps using kCGEventLeftMouseDragged
    step_delay = duration / steps
    for i in range(1, steps + 1):
        t = i / steps
        cx = int(nudge_x + (ex - nudge_x) * t)
        cy = int(nudge_y + (ey - nudge_y) * t)
        drag_ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (cx, cy), btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, drag_ev)
        time.sleep(step_delay)

    # 4. Small settle pause, then release
    time.sleep(0.2)
    up_ev = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, (ex, ey), btn)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_ev)


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _execute_action(action: str, args: Dict[str, Any],
                     image_w: int = 0, image_h: int = 0,
                     actual_w: int = 0, actual_h: int = 0) -> str:
    """Execute a computer use action. Returns status message.

    Positions in return messages are reported in screenshot/image coordinate
    space (image_w x image_h) so Claude can correlate them with what it sees.
    """
    import pyautogui
    pyautogui.FAILSAFE = True  # Move mouse to corner to abort

    def _pos_in_image_space() -> Tuple[int, int]:
        """Get current cursor position converted to screenshot coordinate space."""
        pos = pyautogui.position()
        if image_w and actual_w and image_w != actual_w:
            return round(pos.x * image_w / actual_w), round(pos.y * image_h / actual_h)
        return pos.x, pos.y

    coordinate = args.get("coordinate")
    text = args.get("text", "")

    if action == "screenshot":
        return "screenshot_taken"

    if action == "mouse_move":
        if not coordinate:
            return "error: coordinate required for mouse_move"
        # Check if mouse button is currently held (e.g. during decomposed drag).
        # If held, we must send kCGEventLeftMouseDragged instead of MouseMoved.
        import Quartz
        btn_state = Quartz.CGEventSourceButtonState(
            Quartz.kCGEventSourceStateCombinedSessionState,
            Quartz.kCGMouseButtonLeft,
        )
        if btn_state:
            # Button is held — send drag events so macOS registers the drag
            cx, cy = pyautogui.position()
            tx, ty = int(coordinate[0]), int(coordinate[1])
            steps = 20
            for i in range(1, steps + 1):
                t = i / steps
                mx = int(cx + (tx - cx) * t)
                my = int(cy + (ty - cy) * t)
                drag_ev = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventLeftMouseDragged, (mx, my),
                    Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, drag_ev)
                time.sleep(0.015)
        else:
            pyautogui.moveTo(coordinate[0], coordinate[1], duration=0.3)
        ix, iy = _pos_in_image_space()
        return f"moved to ({ix}, {iy}). Take a screenshot to verify cursor is on the correct element before clicking."

    if action == "left_click":
        if coordinate:
            pyautogui.click(coordinate[0], coordinate[1])
            ix, iy = _pos_in_image_space()
            return f"clicked at ({ix}, {iy}). Take screenshot to verify the click result."
        pyautogui.click()
        ix, iy = _pos_in_image_space()
        return f"clicked at current position ({ix}, {iy}). Take screenshot to verify."

    if action == "right_click":
        if coordinate:
            pyautogui.rightClick(coordinate[0], coordinate[1])
            ix, iy = _pos_in_image_space()
            return f"right-clicked at ({ix}, {iy}). Take screenshot to see the context menu."
        pyautogui.rightClick()
        ix, iy = _pos_in_image_space()
        return f"right-clicked at current position ({ix}, {iy}). Take screenshot to see the menu."

    if action == "double_click":
        if coordinate:
            pyautogui.doubleClick(coordinate[0], coordinate[1])
            ix, iy = _pos_in_image_space()
            return f"double-clicked at ({ix}, {iy})"
        pyautogui.doubleClick()
        return "double-clicked at current position"

    if action == "triple_click":
        if coordinate:
            pyautogui.tripleClick(coordinate[0], coordinate[1])
            ix, iy = _pos_in_image_space()
            return f"triple-clicked at ({ix}, {iy})"
        pyautogui.tripleClick()
        return "triple-clicked at current position"

    if action == "middle_click":
        if coordinate:
            pyautogui.middleClick(coordinate[0], coordinate[1])
            ix, iy = _pos_in_image_space()
            return f"middle-clicked at ({ix}, {iy})"
        pyautogui.middleClick()
        return "middle-clicked at current position"

    if action == "left_click_drag":
        # Anthropic spec: start_coordinate = drag start, coordinate = drag end.
        # Also accept end_coordinate as an alias for the endpoint.
        # Fallback: if only coordinate is given, use current cursor position as start.
        start = args.get("start_coordinate")
        end = args.get("end_coordinate") or args.get("coordinate") or coordinate
        if not start and end:
            # No explicit start — use current cursor position as drag origin
            start = list(pyautogui.position())
        if not start or not end:
            return "error: start_coordinate and end_coordinate required for drag"
        if start == end:
            return "error: start_coordinate and end_coordinate are identical — nothing to drag"
        sx, sy = int(start[0]), int(start[1])
        ex, ey = int(end[0]), int(end[1])
        _quartz_drag(sx, sy, ex, ey)
        # Report both positions in image space (what Claude sees) for consistency.
        # sx,sy are in screen space (already scaled up by handle_computer_use),
        # so convert back to image space for the status message.
        if image_w and actual_w and image_w != actual_w:
            isx = round(sx * image_w / actual_w)
            isy = round(sy * image_h / actual_h)
        else:
            isx, isy = sx, sy
        ix, iy = _pos_in_image_space()
        return f"dragged from ({isx}, {isy}) to ({ix}, {iy})"

    if action == "type":
        if not text:
            return "error: text required for type action"
        # Block dangerous shell commands that could be injected via prompt injection
        for pattern in _BLOCKED_TYPE_PATTERNS:
            if pattern.search(text):
                logger.warning("Blocked dangerous type content: %s", text[:100])
                _debug_log(f"  BLOCKED type pattern: {text[:100]}")
                return f"error: blocked — text contains dangerous command pattern"
        # Always use clipboard paste — pyautogui.write() depends on the active
        # keyboard layout (e.g. Turkish layout maps '.' differently) and only
        # supports ASCII. Clipboard paste works with any layout and any charset.
        import subprocess as _sp
        _sp.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        # interval=0.04 gives macOS time to register modifier before letter key.
        # Without this, hotkey() sends all keys with 0ms between them (it calls
        # platformModule._keyDown directly, bypassing pyautogui.PAUSE), and macOS
        # may not register cmd as held before 'v' arrives.
        pyautogui.hotkey("command", "v", interval=0.04)
        return f"typed {len(text)} characters"

    if action == "key":
        key_combo = args.get("key", text)
        if not key_combo:
            return "error: key required for key action"
        raw_keys = [k.strip() for k in key_combo.replace("+", " ").split()]
        # Normalize key names: "cmd" -> "command", "super" -> "command", etc.
        # pyautogui is case-sensitive and only accepts lowercase key names
        # (e.g. "f3" not "F3", "return" not "Return", "escape" not "Escape").
        # Claude often sends PascalCase or uppercase keys which pyautogui
        # silently drops (no keycode found), so we lowercase everything.
        keys = [_KEY_NAME_MAP.get(k.lower(), k.lower()) for k in raw_keys]
        # Block irreversible key combos (empty trash, lock screen, log out)
        if frozenset(keys) in _BLOCKED_KEY_COMBOS:
            _debug_log(f"  BLOCKED key combo: {'+'.join(keys)}")
            return f"error: key combo '{'+'.join(keys)}' is blocked — irreversible action"
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            # interval=0.04 (40ms) between key downs/ups. pyautogui.hotkey()
            # calls platformModule._keyDown() directly (bypassing the per-call
            # PAUSE decorator), so without an explicit interval all keys fire
            # with 0ms gap. macOS needs ~20-50ms to register a modifier as
            # "held" before the letter key arrives; 0ms means cmd+shift+n can
            # arrive as three simultaneous keypresses and the OS may not treat
            # cmd/shift as modifiers. 40ms is safe and imperceptible to users.
            pyautogui.hotkey(*keys, interval=0.04)
        return f"pressed {key_combo}"

    if action == "scroll":
        direction = args.get("scroll_direction", "down")
        amount = int(args.get("scroll_amount", 3))
        if coordinate:
            pyautogui.moveTo(coordinate[0], coordinate[1])
        if direction in ("up", "down"):
            # pyautogui.scroll: positive = up, negative = down
            clicks = amount if direction == "up" else -amount
            pyautogui.scroll(clicks)
        else:
            # pyautogui.hscroll: positive = right, negative = left
            clicks = -amount if direction == "left" else amount
            pyautogui.hscroll(clicks)
        return f"scrolled {direction} by {amount}"

    if action == "wait":
        duration = float(args.get("duration", 1))
        time.sleep(min(duration, 10))  # Cap at 10 seconds
        return f"waited {duration}s"

    if action == "left_mouse_down":
        import Quartz
        if coordinate:
            x, y = int(coordinate[0]), int(coordinate[1])
        else:
            pos = pyautogui.position()
            x, y = pos.x, pos.y
        # Use Quartz directly so the mouseDown event carries the correct
        # position — pyautogui.moveTo + mouseDown can desync on macOS.
        move_ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move_ev)
        time.sleep(0.1)
        down_ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_ev)
        return "mouse button pressed down"

    if action == "left_mouse_up":
        import Quartz
        pos = pyautogui.position()
        x, y = pos.x, pos.y
        up_ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_ev)
        return "mouse button released"

    if action == "hold_key":
        key = args.get("key", text)
        duration = min(float(args.get("duration", 1)), 5)
        if key:
            key = _KEY_NAME_MAP.get(key.lower(), key.lower())
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)
            return f"held {key} for {duration}s"
        return "error: key required for hold_key"

    return f"error: unknown action '{action}'"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_computer_use(args: Dict[str, Any], **kwargs) -> Any:
    """Handle a computer use tool call from Claude.

    Returns either a JSON string (for text-only results) or a dict with
    `_multimodal: True` for results containing screenshots.
    """
    action = args.get("action", "")

    # Coordinate scaling: Screenshots are resized to logical resolution
    # (pyautogui coordinate space). If further downscaled beyond that,
    # we need to scale back up. Otherwise coordinates are 1:1.
    actual_w, actual_h = _get_screen_size()
    if _cached_screenshot_size:
        image_w, image_h = _cached_screenshot_size
    else:
        # Before the first screenshot, estimate image dimensions using the
        # same logic as get_native_tool_definition(). Without this, the tool
        # definition tells Claude a smaller display (e.g. 1304x846) while
        # the scaler assumes actual screen size (e.g. 1470x956), causing
        # coordinates to land ~80px off on the first click.
        image_w, image_h, _ = _compute_scale(actual_w, actual_h)

    needs_scaling = (image_w != actual_w or image_h != actual_h)

    def _screen_to_image(sx: int, sy: int) -> Tuple[int, int]:
        """Convert screen-space coordinates to image-space for consistent debug logging."""
        if not needs_scaling:
            return sx, sy
        return round(sx * image_w / actual_w), round(sy * image_h / actual_h)

    # Debug log every tool call — all coordinates in image space (what Claude sees)
    try:
        import pyautogui as _dbg_pag
        _dbg_pos = _dbg_pag.position()
        _img_pos = _screen_to_image(_dbg_pos.x, _dbg_pos.y)
        _args_str = json.dumps({k: v for k, v in args.items() if k != "action"}, default=str)[:200]
        _debug_log(f"action={action} args={_args_str} cursor=({_img_pos[0]},{_img_pos[1]})")
    except Exception:
        pass

    if action not in ALL_ACTIONS:
        return json.dumps({"error": f"Unknown action: {action}. Valid: {ALL_ACTIONS}"})

    if needs_scaling:
        _debug_log(f"  scaling: image={image_w}x{image_h} -> screen={actual_w}x{actual_h}")

    def _scale_coord(x: int, y: int) -> Tuple[int, int]:
        if not needs_scaling:
            return int(x), int(y)
        return scale_coordinates_to_screen(x, y, actual_w, actual_h, image_w, image_h)

    for coord_key in ("coordinate", "start_coordinate", "end_coordinate"):
        coord = args.get(coord_key)
        if not coord:
            continue
        # Claude may send coordinates as a JSON string "[89, 863]" instead of a list.
        if isinstance(coord, str):
            try:
                coord = json.loads(coord)
                args[coord_key] = coord
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(coord, (list, tuple)) and len(coord) == 2:
            # Cast to int — pyautogui treats string args as image filenames.
            args[coord_key] = list(_scale_coord(int(coord[0]), int(coord[1])))

    # Execute the action
    if action == "screenshot":
        try:
            _cleanup_temp_files()
            b64_data, img_w, img_h, img_media = _take_screenshot()
            # Get current mouse position for Claude's awareness.
            # CRITICAL: Report position in screenshot coordinate space (img_w x img_h),
            # NOT pyautogui's logical screen space (actual_w x actual_h).
            # Claude sees the image at img_w x img_h and uses these coordinates
            # to understand where the cursor is visually on screen.
            try:
                import pyautogui as _pag
                _mx, _my = _pag.position()
                # Convert from screen space to screenshot/image space
                _img_mx = round(_mx * img_w / actual_w) if actual_w else _mx
                _img_my = round(_my * img_h / actual_h) if actual_h else _my
                _cursor_info = f" Cursor at ({_img_mx}, {_img_my})."
            except Exception:
                _cursor_info = ""
            # Save to file for gateway MEDIA: tag (sends image to Telegram/Discord)
            # Use session-unique path to avoid race between concurrent gateway sessions
            import uuid as _uuid
            ext = "jpg" if "jpeg" in img_media else "png"
            screenshot_path = f"/tmp/hermes_screenshot_{_uuid.uuid4().hex[:8]}.{ext}"
            with open(screenshot_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            _text_summary = f"Screenshot taken ({img_w}x{img_h}).{_cursor_info} MEDIA:{screenshot_path}"
            # Debug: log screenshot result
            try:
                _debug_log(f"  -> screenshot: {img_w}x{img_h} {img_media} {screenshot_path}")
            except Exception:
                pass
            return {
                "_multimodal": True,
                "content_blocks": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img_media,
                            "data": b64_data,
                        },
                    },
                ],
                "text_summary": _text_summary,
            }
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return json.dumps({"error": f"Screenshot failed: {e}"})

    if action == "zoom":
        # Zoom captures a specific region at full resolution for detailed inspection.
        # Takes a region [x1, y1, x2, y2] and returns a cropped, full-res screenshot.
        region = args.get("region")
        # Claude may send region as JSON string "[380, 430, 530, 490]"
        if isinstance(region, str):
            try:
                region = json.loads(region)
            except (json.JSONDecodeError, ValueError):
                return json.dumps({"error": f"zoom: invalid region format: {region}"})
        if not region or len(region) != 4:
            return json.dumps({"error": "zoom requires region: [x1, y1, x2, y2]"})
        # Validate minimum region size — tiny regions produce unusable crops
        _zw, _zh = abs(int(region[2]) - int(region[0])), abs(int(region[3]) - int(region[1]))
        if _zw < 30 or _zh < 30:
            return json.dumps({
                "error": f"zoom region too small: {_zw}x{_zh}px. Minimum 30x30px. "
                "Use a larger region for useful results."
            })
        try:
            _cleanup_temp_files()
            import uuid as _uuid
            x1, y1, x2, y2 = int(region[0]), int(region[1]), int(region[2]), int(region[3])
            crop_w, crop_h = x2 - x1, y2 - y1

            # Capture at full Retina resolution — zoom needs maximum detail.
            # Regular screenshots are downscaled to ~1300px for API limits,
            # but zoom's purpose is inspecting small UI elements (buttons,
            # text, icons) where every pixel matters.
            tmp_full = f"/tmp/hermes_zoom_full_{_uuid.uuid4().hex[:8]}.png"
            tmp_crop = f"/tmp/hermes_zoom_crop_{_uuid.uuid4().hex[:8]}.png"
            subprocess.run(
                ["screencapture", "-x", "-C", "-t", "png", tmp_full],
                capture_output=True, timeout=10,
            )
            # Get Retina dimensions to compute scale factor
            _sips_info = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", tmp_full],
                capture_output=True, text=True, timeout=5,
            )
            retina_w = retina_h = 0
            for _line in _sips_info.stdout.strip().splitlines():
                if "pixelWidth" in _line:
                    retina_w = int(_line.split(":")[-1].strip())
                elif "pixelHeight" in _line:
                    retina_h = int(_line.split(":")[-1].strip())

            # Scale region coordinates from image space to Retina space.
            # Claude sends coordinates in image space (~1300x845), but the
            # Retina screenshot is ~2940x1912 — scale up for accurate crop.
            _cached = _cached_screenshot_size
            if isinstance(_cached, tuple) and len(_cached) == 2 and _cached[0] > 0:
                img_space_w, img_space_h = _cached
            else:
                img_space_w, img_space_h = _get_screen_size()
            sx = retina_w / img_space_w if img_space_w else 1.0
            sy = retina_h / img_space_h if img_space_h else 1.0
            rx1, ry1 = round(x1 * sx), round(y1 * sy)
            rx2, ry2 = round(x2 * sx), round(y2 * sy)
            rcrop_w, rcrop_h = rx2 - rx1, ry2 - ry1

            subprocess.run(
                ["sips", "--cropOffset", str(ry1), str(rx1),
                 "--cropToHeightWidth", str(rcrop_h), str(rcrop_w),
                 tmp_full, "--out", tmp_crop],
                capture_output=True, timeout=10,
            )
            with open(tmp_crop, "rb") as f:
                crop_bytes = f.read()
            crop_b64 = base64.b64encode(crop_bytes).decode("ascii")
            # Cleanup temp files
            for p in (tmp_full, tmp_crop):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            screenshot_path = f"/tmp/hermes_zoom_{_uuid.uuid4().hex[:8]}.png"
            with open(screenshot_path, "wb") as f:
                f.write(crop_bytes)
            _zoom_summary = f"Zoomed region ({x1},{y1})-({x2},{y2}) = {rcrop_w}x{rcrop_h}px (Retina) MEDIA:{screenshot_path}"
            _debug_log(f"  -> zoom: ({x1},{y1})-({x2},{y2}) retina={rcrop_w}x{rcrop_h} {screenshot_path}")
            return {
                "_multimodal": True,
                "content_blocks": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": crop_b64,
                        },
                    },
                ],
                "text_summary": _zoom_summary,
            }
        except Exception as e:
            logger.error("Zoom failed: %s", e)
            return json.dumps({"error": f"Zoom failed: {e}"})

    # Execute the action with auto-screenshot for destructive actions.
    # Previously, screenshots were left to Claude ("send a separate screenshot
    # action"). In practice Claude skips screenshots 66% of the time, causing
    # blind action chains that waste MORE tokens than the screenshot costs.
    # Auto-screenshot eliminates the extra round-trip: Claude sees the screen
    # state in the same response and can make its next decision immediately.
    #
    # Modifier keys (shift, ctrl, alt, super) are held during click/scroll
    # actions per Anthropic spec — the "text" param on these actions holds
    # the modifier name, not text to type.
    import pyautogui as _pag
    _MODIFIER_MAP = {"shift": "shift", "ctrl": "ctrl", "alt": "alt", "super": "command", "cmd": "command", "meta": "command", "opt": "option"}
    _mod_text = args.get("text", "").strip().lower() if action not in ("type", "key") else ""
    _modifier = _MODIFIER_MAP.get(_mod_text)
    try:
        if _modifier:
            _pag.keyDown(_modifier)
        status = _execute_action(action, args,
                                  image_w=image_w, image_h=image_h,
                                  actual_w=actual_w, actual_h=actual_h)
    except Exception as e:
        logger.error("Action %s failed: %s", action, e)
        _debug_log(f"  ERROR: action={action} {e}")
        return json.dumps({"error": f"Action '{action}' failed: {e}"})
    finally:
        if _modifier:
            _pag.keyUp(_modifier)

    # Debug: log cursor position after action (image space for consistency)
    try:
        _dbg_pos_after = _pag.position()
        _img_after = _screen_to_image(_dbg_pos_after.x, _dbg_pos_after.y)
        _debug_log(f"  -> {status[:100]} cursor=({_img_after[0]},{_img_after[1]})")
    except Exception:
        pass

    # Auto-screenshot after destructive actions so Claude sees the result
    # without needing a separate screenshot call (saves 1 API round-trip).
    # Only for actions that change screen state — skip for wait, mouse_move.
    _AUTO_SCREENSHOT_ACTIONS = {
        "left_click", "right_click", "double_click", "triple_click",
        "middle_click", "left_click_drag", "type", "key", "scroll",
    }
    if action in _AUTO_SCREENSHOT_ACTIONS:
        try:
            time.sleep(1.0)  # Wait for UI to render before capturing
            b64_data, img_w, img_h, img_media = _take_screenshot()
            try:
                _mx, _my = _pag.position()
                _img_mx = round(_mx * img_w / actual_w) if actual_w else _mx
                _img_my = round(_my * img_h / actual_h) if actual_h else _my
                _cursor_info = f" Cursor at ({_img_mx}, {_img_my})."
            except Exception:
                _cursor_info = ""
            import uuid as _uuid
            ext = "jpg" if "jpeg" in img_media else "png"
            screenshot_path = f"/tmp/hermes_screenshot_{_uuid.uuid4().hex[:8]}.{ext}"
            with open(screenshot_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            _text_summary = f"{status}.{_cursor_info} MEDIA:{screenshot_path}"
            _debug_log(f"  -> auto-screenshot: {img_w}x{img_h} {screenshot_path}")
            return {
                "_multimodal": True,
                "content_blocks": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img_media,
                            "data": b64_data,
                        },
                    },
                ],
                "text_summary": _text_summary,
            }
        except Exception as e:
            logger.debug("Auto-screenshot failed (non-fatal): %s", e)
            # Fall through to text-only result

    return json.dumps({"success": True, "status": status})


# ---------------------------------------------------------------------------
# Native Anthropic tool definition
# ---------------------------------------------------------------------------

def get_native_tool_definition() -> Dict[str, Any]:
    """Return the native Anthropic computer use tool definition.

    Uses cached screenshot dimensions if available (actual image size sent
    to Claude), otherwise estimates from logical screen size. This ensures
    the declared display size matches the actual screenshot pixels Claude sees.
    """
    if _cached_screenshot_size:
        image_w, image_h = _cached_screenshot_size
    else:
        w, h = _get_screen_size()
        image_w, image_h, _ = _compute_scale(w, h)
    return {
        "type": "computer_20251124",
        "name": "computer",
        "display_width_px": image_w,
        "display_height_px": image_h,
        "enable_zoom": True,
    }


# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------

def check_computer_use_requirements() -> bool:
    """Return True if computer use is available (macOS + pyautogui + Quartz)."""
    if sys.platform != "darwin":
        return False
    try:
        import pyautogui  # noqa: F401
        import Quartz  # noqa: F401  — needed for drag and mouse_move/down/up
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Tool registration (stub schema for dispatch only)
# ---------------------------------------------------------------------------

_COMPUTER_USE_SCHEMA = {
    "name": "computer",
    "description": (
        "Control the computer desktop — take screenshots, click, type, scroll, "
        "and use keyboard shortcuts. Use 'screenshot' action first to see the "
        "current screen, then interact with elements by their coordinates. "
        "When sharing screenshots with the user, include MEDIA:<path> from the "
        "screenshot result's text_summary in your response to deliver it as an image."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ALL_ACTIONS,
                "description": "The action to perform",
            },
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "[x, y] screen coordinate for click/move actions",
            },
            "text": {
                "type": "string",
                "description": "Text to type, or key combo to press (e.g. 'ctrl+c')",
            },
            "scroll_direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "Number of scroll clicks",
            },
            "duration": {
                "type": "number",
                "description": "Duration in seconds for wait/hold_key",
            },
            "start_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "[x, y] start coordinate for left_click_drag",
            },
            "end_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "[x, y] end coordinate for left_click_drag",
            },
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "[x1, y1, x2, y2] region to zoom into for detailed view",
            },
        },
        "required": ["action"],
    },
}

try:
    from tools.registry import registry

    registry.register(
        name="computer",
        toolset="computer_use",
        schema=_COMPUTER_USE_SCHEMA,
        handler=lambda args, **kw: handle_computer_use(args, **kw),
        check_fn=check_computer_use_requirements,
        emoji="\U0001f5a5",  # desktop computer emoji
    )
except Exception as e:
    logger.debug("Computer use tool registration skipped: %s", e)
