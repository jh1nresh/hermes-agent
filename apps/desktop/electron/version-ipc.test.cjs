'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerVersionIpc } = require('./version-ipc.cjs')

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

test('registerVersionIpc wires hermes:version to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerVersionIpc({ ipcMain, resolveHermesVersion: () => '1.2.3', resolveUpdateRoot: () => '/root' })

  assert.deepEqual([...ipcMain.handlers.keys()], ['hermes:version'])
  assert.equal(typeof ipcMain.handlers.get('hermes:version'), 'function')
})

test('version reports the resolved Hermes version + root alongside runtime versions', async () => {
  const ipcMain = fakeIpcMain()

  registerVersionIpc({ ipcMain, resolveHermesVersion: () => '1.2.3', resolveUpdateRoot: () => '/root' })

  const res = await ipcMain.handlers.get('hermes:version')({})

  assert.equal(res.appVersion, '1.2.3')
  assert.equal(res.hermesRoot, '/root')
  assert.equal(res.electronVersion, process.versions.electron)
  assert.equal(res.nodeVersion, process.versions.node)
  assert.equal(res.platform, process.platform)
})
