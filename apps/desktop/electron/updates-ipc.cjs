'use strict'

// Auto-update IPC: check / apply / branch get+set. The update engine
// (checkUpdates/applyUpdates) and the on-disk update config live in the main
// process and are injected, so this module owns only the request wiring.
function registerUpdatesIpc({
  applyUpdates,
  checkUpdates,
  DEFAULT_UPDATE_BRANCH,
  ipcMain,
  readDesktopUpdateConfig,
  writeDesktopUpdateConfig
}) {
  ipcMain.handle('hermes:updates:check', async () =>
    checkUpdates().catch(error => ({
      supported: true,
      branch: readDesktopUpdateConfig().branch,
      error: 'check-failed',
      message: error?.message || String(error),
      fetchedAt: Date.now()
    }))
  )

  ipcMain.handle('hermes:updates:apply', async (_event, payload) =>
    applyUpdates(payload || {}).catch(error => ({
      ok: false,
      error: 'apply-failed',
      message: error?.message || String(error)
    }))
  )

  ipcMain.handle('hermes:updates:branch:get', async () => readDesktopUpdateConfig())

  ipcMain.handle('hermes:updates:branch:set', async (_event, name) => {
    const branch = typeof name === 'string' && name.trim() ? name.trim() : DEFAULT_UPDATE_BRANCH
    writeDesktopUpdateConfig({ branch })
    return { branch }
  })
}

module.exports = { registerUpdatesIpc }
