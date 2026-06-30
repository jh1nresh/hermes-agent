'use strict'

const { shell } = require('electron')
const fs = require('fs')
const path = require('path')

// Desktop-log IPC: reveal the log file in the OS file manager + return the
// recent in-memory tail. The log path, the in-memory ring buffer, and the
// file-exists probe live in the main process and are injected.
function registerLogsIpc({ DESKTOP_LOG_PATH, fileExists, hermesLog, ipcMain }) {
  ipcMain.handle('hermes:logs:reveal', async () => {
    try {
      await fs.promises.mkdir(path.dirname(DESKTOP_LOG_PATH), { recursive: true })
      if (!fileExists(DESKTOP_LOG_PATH)) {
        await fs.promises.appendFile(DESKTOP_LOG_PATH, '')
      }
      shell.showItemInFolder(DESKTOP_LOG_PATH)
      return { ok: true, path: DESKTOP_LOG_PATH }
    } catch (error) {
      return { ok: false, path: DESKTOP_LOG_PATH, error: error.message }
    }
  })

  ipcMain.handle('hermes:logs:recent', async () => ({ path: DESKTOP_LOG_PATH, lines: hermesLog.slice(-200) }))
}

module.exports = { registerLogsIpc }
