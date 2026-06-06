import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { $gateway } from '@/store/gateway'
import { resolveRemotePathPicker } from '@/store/remote-path-picker'
import { $connection } from '@/store/session'

import { fsGitRoot, fsReadDir, fsReadFileDataUrl, isRemoteBackend, selectPaths } from './desktop-fs'

const request = vi.fn()
const readDir = vi.fn()
const readFileDataUrl = vi.fn()
const gitRoot = vi.fn()
const desktopSelectPaths = vi.fn()

function setRemote(remote: boolean) {
  $connection.set(remote ? ({ mode: 'remote' } as never) : null)
}

beforeEach(() => {
  request.mockReset()
  readDir.mockReset()
  readFileDataUrl.mockReset()
  gitRoot.mockReset()
  desktopSelectPaths.mockReset()
  $gateway.set({ request } as unknown as HermesGateway)
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    readDir,
    readFileDataUrl,
    gitRoot,
    selectPaths: desktopSelectPaths
  }
})

afterEach(() => {
  $connection.set(null)
  $gateway.set(null)
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('desktop-fs facade', () => {
  it('routes reads to local IPC when not remote', async () => {
    setRemote(false)
    readDir.mockResolvedValue({ entries: [] })

    await fsReadDir('/p')

    expect(readDir).toHaveBeenCalledWith('/p')
    expect(request).not.toHaveBeenCalled()
    expect(isRemoteBackend()).toBe(false)
  })

  it('routes directory listing to fs.list when remote', async () => {
    setRemote(true)
    request.mockResolvedValue({ entries: [], path: '/srv' })

    const result = await fsReadDir('/srv')

    expect(request).toHaveBeenCalledWith('fs.list', { path: '/srv' })
    expect(result.path).toBe('/srv')
    expect(readDir).not.toHaveBeenCalled()
  })

  it('unwraps the data url from fs.read_data_url when remote', async () => {
    setRemote(true)
    request.mockResolvedValue({ dataUrl: 'data:image/png;base64,AAAA' })

    expect(await fsReadFileDataUrl('/srv/x.png')).toBe('data:image/png;base64,AAAA')
    expect(request).toHaveBeenCalledWith('fs.read_data_url', { path: '/srv/x.png' })
  })

  it('returns gateway git root when remote', async () => {
    setRemote(true)
    request.mockResolvedValue({ root: '/srv/repo' })

    expect(await fsGitRoot('/srv/repo/a')).toBe('/srv/repo')
  })

  it('uses the native picker locally and the remote picker when remote', async () => {
    setRemote(false)
    desktopSelectPaths.mockResolvedValue(['/local/a.png'])
    expect(await selectPaths({ title: 'pick' })).toEqual(['/local/a.png'])

    setRemote(true)
    const pending = selectPaths({ title: 'pick' })
    resolveRemotePathPicker(['/srv/a.png'])
    expect(await pending).toEqual(['/srv/a.png'])
    // Remote selection never touches the native dialog.
    expect(desktopSelectPaths).toHaveBeenCalledTimes(1)
  })
})
