'use strict'

const crypto = require('crypto')

// Terminal (PTY) IPC: start / write / resize / dispose. The PTY runtime, the
// shared session registry, and the shell-spec/env/cwd helpers all live in the
// main process (deep Windows-PATH + app-path coupling) and are injected, so this
// module only owns the request wiring.
function registerTerminalIpc({
  disposeTerminalSession,
  ensureSpawnHelperExecutable,
  ipcMain,
  nodePty,
  safeTerminalCwd,
  terminalChannel,
  terminalSessions,
  terminalShellCommand,
  terminalShellEnv
}) {
  ipcMain.handle('hermes:terminal:start', async (event, payload = {}) => {
    if (!nodePty) {
      throw new Error('PTY support is unavailable. Reinstall desktop dependencies and restart Hermes.')
    }

    ensureSpawnHelperExecutable()

    const id = crypto.randomUUID()
    const { args, command, name } = terminalShellCommand()
    const cwd = safeTerminalCwd(payload?.cwd)
    const cols = Math.max(2, Number.parseInt(String(payload?.cols || 80), 10) || 80)
    const rows = Math.max(2, Number.parseInt(String(payload?.rows || 24), 10) || 24)
    const ptyProcess = nodePty.spawn(command, args, {
      cols,
      cwd,
      env: terminalShellEnv(),
      name: 'xterm-256color',
      rows
    })

    terminalSessions.set(id, { pty: ptyProcess, webContentsId: event.sender.id })

    const send = (suffix, payload) => {
      if (event.sender.isDestroyed()) {
        return
      }

      event.sender.send(terminalChannel(id, suffix), payload)
    }

    ptyProcess.onData(data => send('data', data))
    ptyProcess.onExit(({ exitCode, signal }) => {
      terminalSessions.delete(id)
      send('exit', { code: exitCode, signal: signal || null })
    })
    event.sender.once('destroyed', () => disposeTerminalSession(id))

    return { cwd, id, shell: name }
  })

  ipcMain.handle('hermes:terminal:write', (_event, id, data) => {
    const sessionInfo = terminalSessions.get(String(id || ''))

    if (!sessionInfo) {
      return false
    }

    sessionInfo.pty.write(String(data || ''))

    return true
  })

  ipcMain.handle('hermes:terminal:resize', (_event, id, size = {}) => {
    const sessionInfo = terminalSessions.get(String(id || ''))

    if (!sessionInfo) {
      return false
    }

    const cols = Math.max(2, Number.parseInt(String(size?.cols || 80), 10) || 80)
    const rows = Math.max(2, Number.parseInt(String(size?.rows || 24), 10) || 24)

    sessionInfo.pty.resize(cols, rows)

    return true
  })
  ipcMain.handle('hermes:terminal:dispose', (_event, id) => disposeTerminalSession(String(id || '')))
}

module.exports = { registerTerminalIpc }
