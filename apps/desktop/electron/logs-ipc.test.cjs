'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerLogsIpc } = require('./logs-ipc.cjs')

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

test('registerLogsIpc wires only hermes:logs:* channels, each to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerLogsIpc({ ipcMain, DESKTOP_LOG_PATH: '/tmp/desktop.log', fileExists: () => true, hermesLog: [] })

  assert.deepEqual([...ipcMain.handlers.keys()].sort(), ['hermes:logs:recent', 'hermes:logs:reveal'])

  for (const handler of ipcMain.handlers.values()) {
    assert.equal(typeof handler, 'function')
  }
})

test('logs:recent returns the injected path and the last 200 buffered lines', async () => {
  const ipcMain = fakeIpcMain()
  const hermesLog = Array.from({ length: 250 }, (_, i) => `line ${i}`)

  registerLogsIpc({ ipcMain, DESKTOP_LOG_PATH: '/tmp/desktop.log', fileExists: () => true, hermesLog })

  const res = await ipcMain.handlers.get('hermes:logs:recent')({})

  assert.equal(res.path, '/tmp/desktop.log')
  assert.equal(res.lines.length, 200)
  assert.equal(res.lines[0], 'line 50')
  assert.equal(res.lines.at(-1), 'line 249')
})
