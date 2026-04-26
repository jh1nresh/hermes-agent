# Hermes Apps

Platform apps live here. The first app is a cross-platform GUI shell around the
existing Hermes dashboard; it should not fork chat, config, logs, or session UI.

## Shape

```text
apps/
  gui/      # cross-platform app shell: dev Chrome shell now, Tauri native next
  shared/   # runtime bundle notes/scripts used by Windows + macOS packaging
```

## Desktop Dev

The backend-only GUI mode is:

```bash
hermes dashboard --gui
```

The fast GUI shell is:

```powershell
cd \\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui
npm run dev
```

The native Tauri shell is:

```powershell
cd \\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui
npm run dev:tauri
```

`--gui` implies the embedded TUI; do not pass `--tui` separately for GUI mode.

## MVP Boundary

Included:

- bundled Python runtime
- bundled Node/TUI runtime
- CLI install to PATH
- profile picker and first-run setup
- dashboard health/reconnect state
- tray controls
- desktop notifications
- Windows installer

Deferred:

- code signing
- native self-updater
- store distribution

For MVP updates, the desktop UI should run the existing `hermes update` flow and
surface progress/finish notifications.
