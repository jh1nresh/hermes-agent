'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerProjectDirIpc } = require('./project-dir-ipc.cjs')

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
    readDefaultProjectDir: () => '/projects',
    resolveHermesCwd: () => '/cwd',
    sanitizeWorkspaceCwd: cwd => `safe:${cwd}`,
    writeDefaultProjectDir: () => {},
    ...overrides
  }
}

test('registerProjectDirIpc wires the project-dir + workspace settings channels', () => {
  const ipcMain = fakeIpcMain()

  registerProjectDirIpc({ ipcMain, ...deps() })

  assert.deepEqual([...ipcMain.handlers.keys()].sort(), [
    'hermes:setting:defaultProjectDir:get',
    'hermes:setting:defaultProjectDir:pick',
    'hermes:setting:defaultProjectDir:set',
    'hermes:workspace:sanitize'
  ])
})

// `get` / `pick` touch Electron's `app` / `dialog`, which are unavailable under
// `node --test` (require('electron') is a path stub), so they're exercised in-app
// only. The wiring of all four channels is covered by the surface test above.

test('set normalizes a blank dir to null and persists that (clears the override)', async () => {
  const ipcMain = fakeIpcMain()
  const writes = []

  registerProjectDirIpc({ ipcMain, ...deps({ writeDefaultProjectDir: d => writes.push(d) }) })

  assert.deepEqual(await ipcMain.handlers.get('hermes:setting:defaultProjectDir:set')({}, '   '), { dir: null })
  assert.deepEqual(writes, [null])
})

test('workspace:sanitize delegates to the injected sanitizer', async () => {
  const ipcMain = fakeIpcMain()

  registerProjectDirIpc({ ipcMain, ...deps() })

  assert.equal(await ipcMain.handlers.get('hermes:workspace:sanitize')({}, '/x'), 'safe:/x')
})
