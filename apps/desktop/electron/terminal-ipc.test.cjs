'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerTerminalIpc } = require('./terminal-ipc.cjs')

function fakeIpcMain() {
  const handlers = new Map()

  return {
    handlers,
    handle(channel, handler) {
      assert.ok(!handlers.has(channel), `duplicate registration for ${channel}`)
      handlers.set(channel, handler)
    }
  }
}

function deps(overrides = {}) {
  return {
    disposeTerminalSession: () => true,
    ensureSpawnHelperExecutable: () => {},
    nodePty: { spawn: () => ({ onData() {}, onExit() {} }) },
    safeTerminalCwd: c => c || '/',
    terminalChannel: (id, suffix) => `hermes:terminal:${id}:${suffix}`,
    terminalSessions: new Map(),
    terminalShellCommand: () => ({ args: [], command: 'sh', name: 'sh' }),
    terminalShellEnv: () => ({}),
    ...overrides
  }
}

test('registerTerminalIpc wires only hermes:terminal:* channels, each to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerTerminalIpc({ ipcMain, ...deps() })

  assert.ok(ipcMain.handlers.size >= 4, `expected the full terminal surface, got ${ipcMain.handlers.size}`)

  for (const [channel, handler] of ipcMain.handlers) {
    assert.match(channel, /^hermes:terminal:/, `${channel} is not a terminal channel`)
    assert.equal(typeof handler, 'function', `${channel} should register a handler`)
  }

  for (const channel of ['hermes:terminal:start', 'hermes:terminal:write', 'hermes:terminal:resize']) {
    assert.ok(ipcMain.handlers.has(channel), `missing ${channel}`)
  }
})

test('write / resize on an unknown session id return false instead of throwing', async () => {
  const ipcMain = fakeIpcMain()

  registerTerminalIpc({ ipcMain, ...deps() })

  assert.equal(await ipcMain.handlers.get('hermes:terminal:write')({}, 'nope', 'x'), false)
  assert.equal(await ipcMain.handlers.get('hermes:terminal:resize')({}, 'nope', {}), false)
})

test('start surfaces a clear error when the PTY runtime is unavailable', async () => {
  const ipcMain = fakeIpcMain()

  registerTerminalIpc({ ipcMain, ...deps({ nodePty: null }) })

  await assert.rejects(
    () => ipcMain.handlers.get('hermes:terminal:start')({ sender: {} }, {}),
    /PTY support is unavailable/
  )
})
