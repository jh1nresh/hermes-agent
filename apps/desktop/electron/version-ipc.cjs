'use strict'

// App-version IPC: report the canonical Hermes version (resolved from the source
// tree, falling back to the Electron app version) alongside the Electron/Node
// runtime versions + the resolved Hermes root. The version + root resolvers live
// in the main process and are injected.
function registerVersionIpc({ ipcMain, resolveHermesVersion, resolveUpdateRoot }) {
  ipcMain.handle('hermes:version', async () => ({
    appVersion: resolveHermesVersion(),
    electronVersion: process.versions.electron,
    nodeVersion: process.versions.node,
    platform: process.platform,
    hermesRoot: resolveUpdateRoot()
  }))
}

module.exports = { registerVersionIpc }
