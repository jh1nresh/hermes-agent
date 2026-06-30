'use strict'

const { scanGitRepos } = require('./git-repo-scan.cjs')
const {
  fileDiffVsHead,
  repoStatus,
  reviewCommit,
  reviewCommitContext,
  reviewCreatePr,
  reviewDiff,
  reviewList,
  reviewPush,
  reviewRevParse,
  reviewRevert,
  reviewShipInfo,
  reviewStage,
  reviewUnstage
} = require('./git-review-ops.cjs')
const { addWorktree, listBranches, listWorktrees, removeWorktree, switchBranch } = require('./git-worktree-ops.cjs')

// Register the git/worktree/review IPC handlers. Thin delegators to the
// git-*-ops sibling modules; the git/gh binary resolution lives in the main
// process (Windows PATH discovery) and is injected so this module stays pure.
function registerGitIpc({ ipcMain, resolveGitBinary, resolveGhBinary }) {
  // Git-driven worktree management ("Start work" flow). Errors surface to the
  // renderer as rejected promises so it can toast a friendly message.
  ipcMain.handle('hermes:git:worktreeList', async (_event, repoPath) => listWorktrees(repoPath, resolveGitBinary()))

  ipcMain.handle('hermes:git:worktreeAdd', async (_event, repoPath, options) =>
    addWorktree(repoPath, options || {}, resolveGitBinary())
  )

  ipcMain.handle('hermes:git:worktreeRemove', async (_event, repoPath, worktreePath, options) =>
    removeWorktree(repoPath, worktreePath, options || {}, resolveGitBinary())
  )

  ipcMain.handle('hermes:git:branchSwitch', async (_event, repoPath, branch) =>
    switchBranch(repoPath, branch, resolveGitBinary())
  )

  ipcMain.handle('hermes:git:branchList', async (_event, repoPath) => listBranches(repoPath, resolveGitBinary()))

  // Compact repo status (branch, ahead/behind, change counts + files) for the
  // composer coding rail. Returns null on a non-repo / remote backend so the rail
  // hides cleanly rather than erroring.
  ipcMain.handle('hermes:git:repoStatus', async (_event, repoPath) => repoStatus(repoPath, resolveGitBinary()))

  // Codex-style review pane: list changed files for a scope, fetch one file's
  // unified diff, and stage / unstage / revert. Reads return empty on failure;
  // mutations reject so the renderer can toast.
  ipcMain.handle('hermes:git:review:list', async (_event, repoPath, scope, baseRef) =>
    reviewList(repoPath, scope, baseRef, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:diff', async (_event, repoPath, filePath, scope, baseRef, staged) =>
    reviewDiff(repoPath, filePath, scope, baseRef, staged, resolveGitBinary())
  )
  // Working-tree-vs-HEAD diff for one file (the preview's "show the diff" view).
  ipcMain.handle('hermes:git:fileDiff', async (_event, repoPath, filePath) =>
    fileDiffVsHead(repoPath, filePath, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:stage', async (_event, repoPath, filePath) =>
    reviewStage(repoPath, filePath ?? null, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:unstage', async (_event, repoPath, filePath) =>
    reviewUnstage(repoPath, filePath ?? null, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:revert', async (_event, repoPath, filePath) =>
    reviewRevert(repoPath, filePath ?? null, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:revParse', async (_event, repoPath, ref) =>
    reviewRevParse(repoPath, ref, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:commit', async (_event, repoPath, message, push) =>
    reviewCommit(repoPath, message, Boolean(push), resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:commitContext', async (_event, repoPath) =>
    reviewCommitContext(repoPath, resolveGitBinary())
  )
  ipcMain.handle('hermes:git:review:push', async (_event, repoPath) => reviewPush(repoPath, resolveGitBinary()))
  ipcMain.handle('hermes:git:review:shipInfo', async (_event, repoPath) => reviewShipInfo(repoPath, resolveGhBinary()))
  ipcMain.handle('hermes:git:review:createPr', async (_event, repoPath) =>
    reviewCreatePr(repoPath, resolveGitBinary(), resolveGhBinary())
  )

  // Repo-first project discovery: scan bounded roots for git repos (pure fs walk,
  // no native addon). Never throws to the renderer — failures yield an empty list.
  ipcMain.handle('hermes:git:scanRepos', async (_event, roots, options) => {
    try {
      return await scanGitRepos(roots || [], options || {})
    } catch {
      return []
    }
  })
}

module.exports = { registerGitIpc }
