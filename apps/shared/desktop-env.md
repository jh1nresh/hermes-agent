# GUI Runtime Contract

The GUI shell starts Hermes with a small, explicit environment.

## Environment

```text
HERMES_GUI=1
HERMES_WEB_DIST=<bundled web dist>
HERMES_TUI_DIR=<bundled ui-tui dir>
```

The native shell uses `127.0.0.1:9120` as its initial GUI port during dev.
Bundled builds should keep the port private to the local machine and expose it
through `/api/health` and `/api/runtime`.

The shell should also pass the selected profile through the normal Hermes CLI
profile mechanism once the profile picker is wired.

## Ports

Use `127.0.0.1` only. Start with the GUI default port, then fall back to a
free port if occupied. Show the chosen port in the tray menu.

## User Data

The installer owns app files. Hermes owns user state under `HERMES_HOME`.
Uninstallers must not delete user state unless the user explicitly asks.

## Update Model

MVP does not use Tauri's native updater. GUI runs `hermes update`, tails the
action log, notifies completion, then offers to restart the runtime.
