---
sidebar_position: 17
title: "LSP — Semantic Diagnostics"
description: "Real language servers (pyright, gopls, rust-analyzer, …) surfacing type errors on write_file and patch."
---

# LSP Plugin — Semantic Diagnostics

The LSP plugin runs real language servers (pyright, gopls, rust-analyzer, typescript-language-server, and ~20 more) in the background and surfaces their diagnostics when the agent writes files. The agent sees type errors, undefined names, and missing imports **introduced by its edit** — not just syntax errors.

## Enable

Add `lsp` to your enabled plugins:

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - lsp
```

Or use the CLI:

```bash
hermes plugins enable lsp
```

That's it. On the next session, the plugin activates for any file edit inside a git repository.

## Install Language Servers

The plugin **detects** servers already on your PATH — it doesn't auto-install anything. Use `hermes lsp status` to see what's available:

```bash
hermes lsp status
```

```
LSP Service
===========
  enabled:         True

Registered Servers
==================
  ✓ pyright                  [installed  ] .py, .pyi
  ✓ typescript               [installed  ] .ts, .tsx, .js, .jsx
  · gopls                    [missing    ] .go
  ? rust-analyzer            [manual-only] .rs
```

To install a server into the Hermes-managed staging directory (`$HERMES_HOME/lsp/bin/`):

```bash
hermes lsp install pyright           # npm-based
hermes lsp install gopls             # go install
hermes lsp install bash-language-server
hermes lsp install-all               # try all recipes
```

Servers that are too heavy to auto-install (rust-analyzer, clangd, lua-language-server) are marked `manual-only` — install them through your normal toolchain (`rustup component add rust-analyzer`, etc.).

### Other ways to make servers available

- **System PATH**: If `pyright-langserver` is already on your PATH (e.g., from `npm install -g pyright`), the plugin finds it automatically.
- **Custom path**: Pin a specific binary in config:
  ```yaml
  lsp:
    servers:
      gopls:
        command: ["/usr/local/go/bin/gopls", "serve"]
  ```

## How It Works

On every `write_file` or `patch` call inside a git workspace:

1. **Before the write**: plugin snapshots current diagnostics for the file (baseline)
2. **After the write**: plugin queries the language server for fresh diagnostics
3. **Delta**: only errors *introduced by this edit* are surfaced (pre-existing errors filtered out)
4. **Injection**: diagnostics appear as an `lsp_diagnostics` field in the tool result JSON

The agent sees output like:

```json
{
  "bytes_written": 42,
  "dirs_created": false,
  "lsp_diagnostics": "<diagnostics file=\"/path/to/foo.py\">\nERROR [2:12] Type \"str\" is not assignable to return type \"int\" [reportReturnType] (Pyright)\n</diagnostics>"
}
```

### When LSP stays dormant

- **No git workspace**: files outside a git repo don't trigger LSP
- **No matching server**: if you edit a `.rs` file and rust-analyzer isn't installed, LSP silently skips
- **Remote backends**: Docker, SSH, Modal — the host-side LSP can't see container files, so it skips
- **Plugin disabled**: if `lsp` isn't in `plugins.enabled`, nothing happens
- **Cold start**: first write after server spawn may timeout (3s) — diagnostics appear on subsequent writes

## Configuration

```yaml
# ~/.hermes/config.yaml
lsp:
  enabled: true              # master toggle (default: true when plugin is enabled)
  wait_mode: document        # "document" or "full" (workspace-wide)
  wait_timeout: 5.0          # max seconds to wait for diagnostics
  install_strategy: manual   # "manual" = detect only; "auto" = install on first use

  servers:                   # per-server overrides
    pyright:
      disabled: false        # set true to skip even when installed
      command: ["pyright-langserver", "--stdio"]  # pin binary
      env:                   # extra env vars for the process
        PYTHONPATH: "/my/stubs"
      initialization_options:  # LSP initializationOptions
        python:
          analysis:
            typeCheckingMode: "strict"
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `hermes lsp status` | Service state + per-server install status |
| `hermes lsp list` | All registered servers (26 languages) |
| `hermes lsp install <id>` | Install a server binary |
| `hermes lsp install-all` | Try every auto-install recipe |
| `hermes lsp restart` | Tear down running servers (next edit re-spawns) |
| `hermes lsp which <id>` | Print resolved binary path |

## Supported Languages

| Language | Server | Install |
|----------|--------|---------|
| Python | pyright | `hermes lsp install pyright` |
| TypeScript/JavaScript | typescript-language-server | `hermes lsp install typescript-language-server` |
| Go | gopls | `hermes lsp install gopls` |
| Rust | rust-analyzer | manual (rustup) |
| C/C++ | clangd | manual (LLVM) |
| Vue | @vue/language-server | `hermes lsp install @vue/language-server` |
| Svelte | svelte-language-server | `hermes lsp install svelte-language-server` |
| Bash/Zsh | bash-language-server | `hermes lsp install bash-language-server` |
| YAML | yaml-language-server | `hermes lsp install yaml-language-server` |
| PHP | intelephense | `hermes lsp install intelephense` |
| Lua | lua-language-server | manual |
| Dockerfile | dockerfile-language-server | `hermes lsp install dockerfile-language-server-nodejs` |
| Terraform | terraform-ls | manual |
| Dart | dart language-server | manual |
| Haskell | haskell-language-server | manual |
| Julia | LanguageServer.jl | manual |
| Clojure | clojure-lsp | manual |
| Nix | nixd | manual |
| Zig | zls | manual |
| Gleam | gleam lsp | manual |
| Elixir | elixir-ls | manual |
| OCaml | ocaml-lsp | manual |
| Kotlin | kotlin-language-server | manual |
| Java | jdtls | manual |
| Prisma | prisma language-server | manual |
| Astro | @astrojs/language-server | `hermes lsp install @astrojs/language-server` |

## Troubleshooting

**"No diagnostics appearing"**
1. Check `hermes lsp status` — is the server installed?
2. Is the file inside a git repository? (`git rev-parse --git-dir` should succeed)
3. Check logs: `hermes logs --level WARNING | grep lsp`

**"Server unavailable" warning in logs**
The binary isn't on PATH or in `$HERMES_HOME/lsp/bin/`. Run `hermes lsp install <id>`.

**"First write has no diagnostics, second does"**
Normal. The language server needs time to index the project on cold start. The 3-second timeout keeps writes fast — diagnostics appear once the server is warm.

**Performance**
- Warm server: diagnostics in 200–500ms (pyright), 1–2s (typescript-language-server)
- Cold start: 5–30s indexing (project-size dependent) — writes succeed immediately, diagnostics arrive on subsequent edits
- Servers stay alive for the session duration (one process per language per project root)
