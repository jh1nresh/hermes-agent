import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Loader } from '@/components/ui/loader'
import type { HermesReadDirEntry } from '@/global'
import { fsReadDir } from '@/lib/desktop-fs'
import { cn } from '@/lib/utils'
import { $remotePathPicker, resolveRemotePathPicker } from '@/store/remote-path-picker'

function parentDir(path: string): string | null {
  const trimmed = path.replace(/[\\/]+$/, '')
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))

  if (idx <= 0) {
    return idx === 0 ? '/' : null
  }

  return trimmed.slice(0, idx)
}

function baseName(path: string): string {
  return (
    path
      .replace(/[\\/]+$/, '')
      .split(/[\\/]+/)
      .filter(Boolean)
      .pop() ?? path
  )
}

// Browses the GATEWAY filesystem (via fs.list) so users on a remote backend can
// pick files/folders that exist on the agent host rather than their own machine.
// Mirrors the native selectPaths contract: resolves with absolute gateway paths
// (or [] when cancelled).
export function RemotePathPicker() {
  const request = useStore($remotePathPicker)

  if (!request) {
    return null
  }

  return <RemotePathPickerDialog key={request.id} />
}

function RemotePathPickerDialog() {
  const request = useStore($remotePathPicker)
  const options = request?.options ?? {}
  const directoriesMode = Boolean(options.directories)
  const allowMultiple = options.multiple !== false && !directoriesMode

  const [dir, setDir] = useState<string>(options.defaultPath ?? '')
  const [entries, setEntries] = useState<HermesReadDirEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const allowedExtensions = useMemo(() => {
    const exts = (options.filters ?? []).flatMap(filter => filter.extensions)

    return exts.length > 0 ? new Set(exts.map(ext => ext.toLowerCase().replace(/^\./, ''))) : null
  }, [options.filters])

  const load = useCallback(async (target: string) => {
    setLoading(true)
    setError(null)

    const result = await fsReadDir(target)

    setDir(result.path ?? target)
    setEntries(result.entries ?? [])
    setError(result.error ?? null)
    setSelected(new Set())
    setLoading(false)
  }, [])

  // Loads the initial directory. `load` is stable and defaultPath is fixed for
  // this keyed instance, so this runs once; navigation calls `load` directly.
  useEffect(() => {
    void load(options.defaultPath ?? '')
  }, [load, options.defaultPath])

  const visibleEntries = useMemo(() => {
    return entries.filter(entry => {
      if (entry.isDirectory) {
        return true
      }

      if (directoriesMode) {
        return false
      }

      if (!allowedExtensions) {
        return true
      }

      const ext = baseName(entry.name).split('.').pop()?.toLowerCase() ?? ''

      return allowedExtensions.has(ext)
    })
  }, [allowedExtensions, directoriesMode, entries])

  const cancel = useCallback(() => resolveRemotePathPicker([]), [])

  const confirm = useCallback(() => {
    if (directoriesMode) {
      resolveRemotePathPicker([dir])

      return
    }

    if (selected.size > 0) {
      resolveRemotePathPicker([...selected])
    }
  }, [dir, directoriesMode, selected])

  const onEntryClick = useCallback(
    (entry: HermesReadDirEntry) => {
      if (entry.isDirectory) {
        void load(entry.path)

        return
      }

      if (directoriesMode) {
        return
      }

      if (!allowMultiple) {
        resolveRemotePathPicker([entry.path])

        return
      }

      setSelected(prev => {
        const next = new Set(prev)

        if (next.has(entry.path)) {
          next.delete(entry.path)
        } else {
          next.add(entry.path)
        }

        return next
      })
    },
    [allowMultiple, directoriesMode, load]
  )

  const parent = parentDir(dir)
  const title = options.title || (directoriesMode ? 'Select a folder' : 'Select files')
  const confirmLabel = directoriesMode ? 'Use this folder' : `Attach${selected.size > 1 ? ` (${selected.size})` : ''}`
  const confirmDisabled = directoriesMode ? !dir : selected.size === 0

  return (
    <Dialog onOpenChange={value => !value && cancel()} open>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-1.5 text-xs text-(--ui-text-tertiary)">
          <Button
            aria-label="Up one folder"
            disabled={!parent || loading}
            onClick={() => parent && void load(parent)}
            size="icon-xs"
            variant="ghost"
          >
            <Codicon name="arrow-up" size="0.9rem" />
          </Button>
          <span className="truncate font-mono" title={dir}>
            {dir || '…'}
          </span>
        </div>

        <div className="h-72 overflow-y-auto rounded-md border border-(--ui-stroke-secondary) bg-background/40">
          {loading ? (
            <div className="flex h-full items-center justify-center">
              <Loader />
            </div>
          ) : error ? (
            <div className="flex h-full items-center justify-center px-4 text-center text-xs text-destructive">
              Could not read this folder ({error}).
            </div>
          ) : visibleEntries.length === 0 ? (
            <div className="flex h-full items-center justify-center px-4 text-center text-xs text-(--ui-text-tertiary)">
              {directoriesMode ? 'No subfolders here.' : 'No matching files here.'}
            </div>
          ) : (
            <ul className="py-1">
              {visibleEntries.map(entry => {
                const isSelected = selected.has(entry.path)

                return (
                  <li key={entry.path}>
                    <button
                      className={cn(
                        'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-(--chrome-action-hover)',
                        isSelected && 'bg-(--chrome-action-hover)'
                      )}
                      onClick={() => onEntryClick(entry)}
                      type="button"
                    >
                      <Codicon
                        className={entry.isDirectory ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)'}
                        name={entry.isDirectory ? 'folder' : 'file'}
                        size="0.95rem"
                      />
                      <span className="flex-1 truncate">{entry.name}</span>
                      {!entry.isDirectory && isSelected && <Codicon name="check" size="0.9rem" />}
                      {entry.isDirectory && <Codicon name="chevron-right" size="0.85rem" />}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button onClick={cancel} type="button" variant="ghost">
            Cancel
          </Button>
          <Button disabled={confirmDisabled} onClick={confirm} type="button">
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
