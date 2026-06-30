'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerUpdatesIpc } = require('./updates-ipc.cjs')

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
    applyUpdates: async () => ({ ok: true }),
    checkUpdates: async () => ({ supported: true }),
    DEFAULT_UPDATE_BRANCH: 'main',
    readDesktopUpdateConfig: () => ({ branch: 'main' }),
    writeDesktopUpdateConfig: () => {},
    ...overrides
  }
}

test('registerUpdatesIpc wires only hermes:updates:* channels, each to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerUpdatesIpc({ ipcMain, ...deps() })

  assert.ok(ipcMain.handlers.size >= 4, `expected the full updates surface, got ${ipcMain.handlers.size}`)

  for (const [channel, handler] of ipcMain.handlers) {
    assert.match(channel, /^hermes:updates:/, `${channel} is not an updates channel`)
    assert.equal(typeof handler, 'function', `${channel} should register a handler`)
  }

  for (const channel of ['hermes:updates:check', 'hermes:updates:apply', 'hermes:updates:branch:set']) {
    assert.ok(ipcMain.handlers.has(channel), `missing ${channel}`)
  }
})

test('branch:set falls back to the default branch for blank input and persists it', async () => {
  const ipcMain = fakeIpcMain()
  const writes = []

  registerUpdatesIpc({ ipcMain, ...deps({ writeDesktopUpdateConfig: c => writes.push(c) }) })

  assert.deepEqual(await ipcMain.handlers.get('hermes:updates:branch:set')({}, '   '), { branch: 'main' })
  assert.deepEqual(await ipcMain.handlers.get('hermes:updates:branch:set')({}, 'dev'), { branch: 'dev' })
  assert.deepEqual(writes, [{ branch: 'main' }, { branch: 'dev' }])
})

test('check swallows engine failures into a structured error payload', async () => {
  const ipcMain = fakeIpcMain()

  registerUpdatesIpc({
    ipcMain,
    ...deps({
      checkUpdates: async () => {
        throw new Error('network down')
      }
    })
  })

  const res = await ipcMain.handlers.get('hermes:updates:check')({})

  assert.equal(res.error, 'check-failed')
  assert.equal(res.message, 'network down')
  assert.equal(res.branch, 'main')
})
