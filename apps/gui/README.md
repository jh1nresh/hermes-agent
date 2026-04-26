# Hermes GUI

Cross-platform GUI shell for the Hermes dashboard.

## Fast Dev Shell

This gets a GUI window on Windows/WSL today by launching Chrome in app mode:

```bash
cd apps/gui
npm run dev
```

It starts `hermes dashboard --gui --no-open --port 9120`, waits for
`/api/health`, then opens a standalone app window at `http://127.0.0.1:9120`.

## Native Shell

The native Tauri shell is still scaffolded:

```bash
cd apps/gui
npm run dev:tauri
```

From Windows PowerShell on a `\\wsl$` path, use PowerShell `npm`, not
`npm.cmd`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
cd \\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui
npm run dev:tauri
```

`npm.cmd` goes through `cmd.exe`, and `cmd.exe` cannot use UNC paths as the
current directory.

If `npm run` still falls through `cmd.exe`, bypass npm entirely:

```powershell
\\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui\dev-tauri.ps1
```

The launcher builds into `%LOCALAPPDATA%\Hermes\cargo-target\gui` instead of
`\\wsl$` because Windows Cargo incremental locks do not work reliably on UNC
WSL filesystems.

In dev, either start Hermes yourself:

```bash
hermes dashboard --gui --no-open --port 9120
```

or let the native shell start it. The tray menu owns:

- Open Hermes
- Open in Browser
- Restart Hermes Runtime
- Quit Hermes

The native shell reuses a healthy GUI runtime when one is already running.
Otherwise it picks the first free port from `9120..9139`, passes that port into
the WSL/backend process, and navigates the Tauri window there. Set
`HERMES_GUI_PORT` to force a starting port.

## Fresh Install Emulation

Use an isolated Hermes home without touching your real `~/.hermes`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File \\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui\dev-tauri.ps1 -Fresh
```

Reset that disposable home and run again:

```powershell
powershell.exe -ExecutionPolicy Bypass -File \\wsl$\Ubuntu\home\bb\hermes-agent\apps\gui\dev-tauri.ps1 -Fresh -ResetFresh
```

Fresh mode stores state in `%LOCALAPPDATA%\Hermes\fresh-install-home` and starts
from port `9140` so it does not collide with your normal GUI dev session.

Set `HERMES_GUI_MIN_SPLASH_MS` only when debugging the startup screen; default
startup is instant once the backend is healthy.

## Boundary

GUI owns:

- app shell/window
- startup state
- sidecar process lifecycle
- future tray/notifications/installers

Hermes owns:

- dashboard UI
- auth/session token
- profiles/config/env
- TUI/PTT chat bridge
- tools/skills/gateway
- update flow
