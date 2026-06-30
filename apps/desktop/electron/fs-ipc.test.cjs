'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerFsIpc } = require('./fs-ipc.cjs')

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

test('registerFsIpc wires only hermes:fs:* channels, each to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerFsIpc({ ipcMain, directoryExists: () => true, expandUserPath: p => p })

  assert.ok(ipcMain.handlers.size >= 6, `expected the full fs surface, got ${ipcMain.handlers.size}`)

  for (const [channel, handler] of ipcMain.handlers) {
    assert.match(channel, /^hermes:fs:/, `${channel} is not an fs channel`)
    assert.equal(typeof handler, 'function', `${channel} should register a handler`)
  }

  for (const channel of ['hermes:fs:readDir', 'hermes:fs:rename', 'hermes:fs:trash']) {
    assert.ok(ipcMain.handlers.has(channel), `missing ${channel}`)
  }
})

test('rename rejects names that traverse out of the parent dir', async () => {
  const ipcMain = fakeIpcMain()

  registerFsIpc({ ipcMain, directoryExists: () => true, expandUserPath: p => p })

  for (const bad of ['..', '.', 'a/b', 'a\\b']) {
    await assert.rejects(
      () => ipcMain.handlers.get('hermes:fs:rename')({}, '/tmp/x', bad),
      /Invalid rename/,
      `"${bad}" should be rejected`
    )
  }
})
