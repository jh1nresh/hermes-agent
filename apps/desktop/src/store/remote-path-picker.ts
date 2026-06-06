import { atom } from 'nanostores'

import type { HermesSelectPathsOptions } from '@/global'

export interface RemotePathPickerRequest {
  id: number
  options: HermesSelectPathsOptions
  resolve: (paths: string[]) => void
}

// Holds the currently open remote path-picker request, if any. The picker
// modal subscribes and resolves the promise when the user confirms or cancels.
// Used only when the desktop is connected to a remote gateway, where the native
// OS dialog (which browses the client machine) is the wrong filesystem.
export const $remotePathPicker = atom<RemotePathPickerRequest | null>(null)

let nextRequestId = 0

export function openRemotePathPicker(options: HermesSelectPathsOptions = {}): Promise<string[]> {
  // Only one picker at a time; cancel any prior request.
  const previous = $remotePathPicker.get()

  if (previous) {
    previous.resolve([])
  }

  return new Promise<string[]>(resolve => {
    $remotePathPicker.set({ id: (nextRequestId += 1), options, resolve })
  })
}

export function resolveRemotePathPicker(paths: string[]): void {
  const request = $remotePathPicker.get()

  if (!request) {
    return
  }

  $remotePathPicker.set(null)
  request.resolve(paths)
}
