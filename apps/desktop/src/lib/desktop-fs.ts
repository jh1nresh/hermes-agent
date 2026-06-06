import type { HermesReadDirResult, HermesReadFileTextResult, HermesSelectPathsOptions } from '@/global'
import { $gateway } from '@/store/gateway'
import { openRemotePathPicker } from '@/store/remote-path-picker'
import { $connection } from '@/store/session'

// On a remote gateway (e.g. a VPS over tailscale) the agent's filesystem lives
// on the server, but the Electron IPC helpers only see the client machine. This
// facade routes reads + path selection through gateway `fs.*` RPCs when remote,
// and falls back to local Electron IPC against a locally-spawned backend.
export const isRemoteBackend = (): boolean => $connection.get()?.mode === 'remote'

function gw<T>(method: string, params: Record<string, unknown>): Promise<T> {
  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('Hermes gateway unavailable')
  }

  return gateway.request<T>(method, params)
}

const unavailable = (): never => {
  throw new Error('File reading is unavailable')
}

export function fsReadDir(path: string): Promise<HermesReadDirResult> {
  if (isRemoteBackend()) {
    return gw('fs.list', { path })
  }

  return window.hermesDesktop?.readDir?.(path) ?? Promise.resolve({ entries: [], error: 'no-bridge' })
}

export function fsReadFileText(path: string): Promise<HermesReadFileTextResult> {
  if (isRemoteBackend()) {
    return gw('fs.read_text', { path })
  }

  return window.hermesDesktop?.readFileText?.(path) ?? unavailable()
}

export async function fsReadFileDataUrl(path: string): Promise<string> {
  if (isRemoteBackend()) {
    return (await gw<{ dataUrl?: string }>('fs.read_data_url', { path })).dataUrl ?? unavailable()
  }

  return window.hermesDesktop?.readFileDataUrl?.(path) ?? unavailable()
}

export async function fsGitRoot(path: string): Promise<string | null> {
  if (isRemoteBackend()) {
    return (await gw<{ root?: string | null }>('fs.git_root', { path })).root ?? null
  }

  return window.hermesDesktop?.gitRoot?.(path) ?? null
}

export async function selectPaths(options: HermesSelectPathsOptions = {}): Promise<string[]> {
  if (isRemoteBackend()) {
    return openRemotePathPicker(options)
  }

  return (await window.hermesDesktop?.selectPaths?.(options)) ?? []
}
