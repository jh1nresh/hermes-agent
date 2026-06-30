'use strict'

const { app, dialog } = require('electron')
const fs = require('fs')

// Default-project-directory + workspace-cwd settings IPC: read / write / native
// directory picker, plus workspace-cwd sanitize. The config readers/writers and
// cwd resolvers live in the main process and are injected.
function registerProjectDirIpc({
  ipcMain,
  readDefaultProjectDir,
  resolveHermesCwd,
  sanitizeWorkspaceCwd,
  writeDefaultProjectDir
}) {
  ipcMain.handle('hermes:setting:defaultProjectDir:get', async () => ({
    dir: readDefaultProjectDir(),
    defaultLabel: app.getPath('home'),
    resolvedCwd: resolveHermesCwd()
  }))

  ipcMain.handle('hermes:workspace:sanitize', async (_event, cwd) => sanitizeWorkspaceCwd(cwd))

  ipcMain.handle('hermes:setting:defaultProjectDir:set', async (_event, dir) => {
    const next = typeof dir === 'string' && dir.trim() ? dir.trim() : null

    if (next) {
      try {
        fs.mkdirSync(next, { recursive: true })
      } catch (error) {
        throw new Error(`Could not create directory: ${error.message}`)
      }
    }

    writeDefaultProjectDir(next)

    return { dir: next }
  })

  ipcMain.handle('hermes:setting:defaultProjectDir:pick', async () => {
    const result = await dialog.showOpenDialog({
      title: 'Choose default project directory',
      properties: ['openDirectory', 'createDirectory'],
      defaultPath: readDefaultProjectDir() || app.getPath('home')
    })

    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true, dir: null }
    }

    return { canceled: false, dir: result.filePaths[0] }
  })
}

module.exports = { registerProjectDirIpc }
