'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { registerGitIpc } = require('./git-ipc.cjs')

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

test('registerGitIpc wires only hermes:git:* channels, each to a handler fn', () => {
  const ipcMain = fakeIpcMain()

  registerGitIpc({ ipcMain, resolveGitBinary: () => 'git', resolveGhBinary: () => 'gh' })

  assert.ok(ipcMain.handlers.size >= 19, `expected the full git surface, got ${ipcMain.handlers.size}`)

  for (const [channel, handler] of ipcMain.handlers) {
    assert.match(channel, /^hermes:git:/, `${channel} is not a git channel`)
    assert.equal(typeof handler, 'function', `${channel} should register a handler`)
  }

  // Spot-check the load-bearing channels across the worktree / review / scan groups.
  for (const channel of ['hermes:git:worktreeList', 'hermes:git:review:commit', 'hermes:git:scanRepos']) {
    assert.ok(ipcMain.handlers.has(channel), `missing ${channel}`)
  }
})

test('handlers thread the injected resolver into the ops layer', async () => {
  const ipcMain = fakeIpcMain()
  const calls = []

  registerGitIpc({
    ipcMain,
    resolveGitBinary: () => {
      calls.push('git')

      return 'git'
    },
    resolveGhBinary: () => 'gh'
  })

  // The resolver is consulted synchronously to build the ops call; whatever the
  // ops layer does with a non-repo path is irrelevant to the wiring.
  try {
    await ipcMain.handlers.get('hermes:git:worktreeList')({}, '/definitely/not/a/repo')
  } catch {
    // ops layer may reject on a bad path — not what this test asserts.
  }

  assert.deepEqual(calls, ['git'])
})
