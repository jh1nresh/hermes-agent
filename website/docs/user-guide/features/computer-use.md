---
title: Computer Use
description: Control the macOS desktop via screenshots, mouse clicks, keyboard input, and scrolling using Anthropic's Computer Use API.
sidebar_label: Computer Use
sidebar_position: 6
---

# Computer Use

Hermes Agent can control your macOS desktop through Anthropic's Computer Use API — taking screenshots, clicking UI elements, typing text, scrolling, and using keyboard shortcuts. This enables the agent to interact with **any** application on your computer, not just the terminal or browser.

:::caution Beta Feature
Computer Use is in beta. It requires macOS, the Anthropic provider (`anthropic_messages` API mode), and `pyautogui` for mouse/keyboard control.
:::

## Setup

### 1. Install dependencies

```bash
uv pip install -e '.[computer-use]'
# or
pip install -e '.[computer-use]'
```

This installs `pyautogui` and its macOS dependencies (`pyobjc-framework-Quartz`).

### 2. Grant macOS permissions

The tool needs two macOS permissions:

- **Screen Recording**: System Settings → Privacy & Security → Screen Recording → add your Terminal app
- **Accessibility**: System Settings → Privacy & Security → Accessibility → add your Terminal app

After granting permissions, **fully restart Terminal** (not just new tab).

### 3. Enable the toolset

**Option A — Interactive setup (recommended):**

```bash
hermes setup tools
# or
hermes tools
```

Select `computer_use` from the checklist and choose which platforms to enable it for (CLI, Telegram, Discord, Slack, WhatsApp, Signal, Email, DingTalk).

**Option B — CLI command:**

```bash
# Enable for CLI
hermes tools enable computer_use --platform cli

# Enable for Telegram
hermes tools enable computer_use --platform telegram

# Enable for Discord
hermes tools enable computer_use --platform discord
```

**Option C — Edit `~/.hermes/config.yaml` manually:**

```yaml
platform_toolsets:
  cli:
    - computer_use
    - terminal
    - file
    # ... other toolsets
  telegram:
    - computer_use
    # ... other toolsets
```

**Option D — Enable temporarily for one session:**

```bash
hermes -t computer_use
```

## How It Works

1. **Screenshot**: Agent captures the screen and sees it via Claude's vision
2. **Decide**: Claude identifies UI elements and coordinates from the screenshot
3. **Act**: Agent performs mouse/keyboard actions at the identified coordinates
4. **Verify**: Agent takes another screenshot to confirm the action worked

The coordinate system matches your logical screen resolution (e.g., 1470×956 on a Retina MacBook). Screenshots are automatically resized to this resolution so coordinates map 1:1 to `pyautogui` — no manual scaling needed.

## Available Actions

| Action | Description | Parameters |
|--------|-------------|------------|
| `screenshot` | Capture current screen | — |
| `left_click` | Click at position | `coordinate: [x, y]` |
| `right_click` | Right-click at position | `coordinate: [x, y]` |
| `double_click` | Double-click at position | `coordinate: [x, y]` |
| `triple_click` | Triple-click (select line) | `coordinate: [x, y]` |
| `middle_click` | Middle-click at position | `coordinate: [x, y]` |
| `mouse_move` | Move cursor (drag-aware when button held) | `coordinate: [x, y]` |
| `left_click_drag` | Atomic drag from A to B | `start_coordinate`, `coordinate` |
| `left_mouse_down` | Press and hold left button | `coordinate: [x, y]` |
| `left_mouse_up` | Release left button | — |
| `type` | Type text (via clipboard paste) | `text: "hello"` |
| `key` | Press key or shortcut | `key: "command+l"` |
| `hold_key` | Press and hold a key for duration | `key: "shift"`, `duration: 2` |
| `scroll` | Scroll at position | `coordinate`, `scroll_direction`, `scroll_amount` |
| `zoom` | Inspect a screen region at full resolution | `region: [x1, y1, x2, y2]` |
| `wait` | Pause for N seconds (max 10) | `duration: 2` |

## Usage Examples

### Take a screenshot and describe it

```
You: What's on my screen?
Agent: [takes screenshot] I see Chrome open with GitHub, Terminal in the background...
```

### Open a website

```
You: Open x.com in Chrome
Agent: [activates Chrome via osascript, Cmd+L, types URL, presses Enter]
```

### Fill a form

```
You: Fill in the search box on this page
Agent: [clicks on search field, types text, presses Enter]
```

## CLI vs Gateway Mode

### CLI Mode

The terminal running Hermes has focus. After using `osascript` or `open` via the terminal tool, Terminal regains focus. The agent must re-activate the target app before typing.

### Gateway Mode (Recommended)

When running via Telegram/Discord gateway, the agent runs in the background with no terminal window. Focus issues don't occur, making this the most reliable mode for desktop automation.

Screenshots are sent as images to the chat. Each screenshot generates a unique file path (e.g., `MEDIA:/tmp/hermes_screenshot_a1b2c3d4.png`). The agent extracts this path from the tool result's `text_summary` and includes it in the response, and the gateway delivers it as a native image.

## Skills

When the `computer_use` toolset is enabled, the **macOS Computer Use** skill is automatically available. This skill teaches the agent:

- Reliable app switching patterns (osascript > Cmd+Tab > click)
- macOS keyboard shortcuts for system, browser, and text editing
- Typing via clipboard paste (keyboard layout independent)
- Scrolling alternatives when the scroll action fails
- Click accuracy strategies
- Error recovery patterns
- Safety rules (what NOT to do)

The agent loads this skill automatically when handling computer use tasks.

## Configuration

Computer Use is configured via the `computer_use` toolset. No additional environment variables are needed.

```yaml
platform_toolsets:
  cli:
    - computer_use  # Enable for CLI
  telegram:
    - computer_use  # Enable for Telegram gateway
  discord:
    - computer_use  # Enable for Discord gateway
```

The tool is gated behind a requirements check — it only loads on macOS when `pyautogui` is installed.

## Limitations

- **macOS only** — not available on Linux or Windows
- **Anthropic provider only** — requires `anthropic_messages` API mode (uses beta API)
- **Primary display only** — multi-monitor setups: secondary displays are not visible
- **Coordinate accuracy**: ~1-2px after scaling — precise for most UI targets
- **Type overwrites clipboard** — the `type` action uses `pbcopy` + `Cmd+V`
- **Scroll unreliable** — use keyboard shortcuts (`space`, `Page_Down`) as fallback
- **Wait capped at 10s** — chain multiple waits for longer pauses
- **No Touch Bar** — Touch Bar interactions not supported
- **No Spaces/Mission Control** — full-screen spaces not navigable

## Troubleshooting

### "No such file or directory: '['"
Coordinate formatting issue — fixed in latest version. Update your Hermes installation.

### Screenshots return empty
Missing Screen Recording permission. Grant it in System Settings → Privacy & Security → Screen Recording and restart Terminal.

### Clicks/typing don't work
Missing Accessibility permission. Grant it in System Settings → Privacy & Security → Accessibility and restart Terminal.

### Tool not loading
Ensure `pyautogui` is installed (`pip install pyautogui`) and you're on macOS. Check `hermes doctor` for tool availability.
