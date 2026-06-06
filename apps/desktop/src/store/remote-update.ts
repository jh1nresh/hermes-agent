/**
 * Remote backend self-update. When a desktop window drives a backend on
 * another host, the Electron native updater can't reach it — only the remote
 * gateway can update its own box. This kicks off `update.start` on the gateway,
 * polls `update.status` until the checkout is rewritten, then asks the gateway
 * to re-exec itself (`gateway.restart`) so the new code actually loads — riding
 * out the disconnect and surfacing a lightweight status pill (a persistent
 * notification) throughout.
 */

import { atom } from 'nanostores'

import { dismissNotification, notify } from '@/store/notifications'

export type RemoteUpdatePhase =
  | 'idle'
  | 'starting'
  | 'running'
  | 'restarting'
  | 'reconnecting'
  | 'done'
  | 'error'

export interface RemoteUpdateState {
  phase: RemoteUpdatePhase
  message: string
}

interface RemoteUpdateStatus {
  running: boolean
  finished: boolean
  exit_code: number | null
  output: string
}

type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

const TOAST_ID = 'remote-backend-update'
const POLL_INTERVAL_MS = 2_000
const POLL_TIMEOUT_MS = 30 * 60 * 1_000
// The backend drops while it re-execs; give it generous room to come back
// before we stop driving the pill (the gateway keeps reconnecting regardless).
const RESTART_TIMEOUT_MS = 3 * 60 * 1_000
const IDLE: RemoteUpdateState = { phase: 'idle', message: '' }

const ACTIVE_PHASES: ReadonlySet<RemoteUpdatePhase> = new Set([
  'starting',
  'running',
  'restarting',
  'reconnecting'
])

export const $remoteUpdate = atom<RemoteUpdateState>(IDLE)

const delay = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

function setPhase(phase: RemoteUpdatePhase, message: string): void {
  $remoteUpdate.set({ phase, message })

  if (phase === 'error') {
    notify({ id: TOAST_ID, kind: 'error', title: 'Backend update', message, durationMs: 0 })
  } else if (phase === 'done') {
    notify({ id: TOAST_ID, kind: 'success', title: 'Backend update', message })
  } else if (ACTIVE_PHASES.has(phase)) {
    notify({ id: TOAST_ID, kind: 'info', title: 'Backend update', message, durationMs: 0 })
  } else {
    dismissNotification(TOAST_ID)
  }
}

export function resetRemoteUpdate(): void {
  dismissNotification(TOAST_ID)
  $remoteUpdate.set(IDLE)
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// A dropped transport (the backend re-execing) vs. an RPC-level error (e.g. an
// older backend that doesn't know `gateway.restart`). Only the former means a
// restart is actually underway.
function isConnectionError(error: unknown): boolean {
  return /not connected|connection closed|could not connect|timed out|timeout/i.test(errorText(error))
}

function tail(output: string, lines = 4): string {
  const trimmed = (output || '').trim()

  return trimmed ? trimmed.split('\n').slice(-lines).join('\n') : ''
}

export async function startRemoteUpdate(requestGateway: RequestGateway): Promise<void> {
  if (ACTIVE_PHASES.has($remoteUpdate.get().phase)) {
    return
  }

  setPhase('starting', 'Starting backend update…')

  try {
    await requestGateway('update.start')
  } catch (error) {
    setPhase('error', errorText(error) || 'Could not start the backend update.')

    return
  }

  setPhase('running', 'Updating remote backend…')
  await pollRemoteUpdate(requestGateway)
}

async function pollRemoteUpdate(requestGateway: RequestGateway): Promise<void> {
  const deadline = Date.now() + POLL_TIMEOUT_MS

  while (Date.now() < deadline) {
    await delay(POLL_INTERVAL_MS)

    let status: RemoteUpdateStatus

    try {
      status = await requestGateway<RemoteUpdateStatus>('update.status')
    } catch {
      // The backend likely dropped to restart with the new code. requestGateway
      // already attempted a reconnect; reflect that and keep polling.
      setPhase('reconnecting', 'Reconnecting to backend…')

      continue
    }

    if ($remoteUpdate.get().phase === 'reconnecting') {
      setPhase('running', 'Updating remote backend…')
    }

    if (status.finished) {
      if ((status.exit_code ?? 1) === 0) {
        await restartRemoteBackend(requestGateway)
      } else {
        setPhase('error', tail(status.output) || 'Backend update failed.')
      }

      return
    }
  }

  setPhase('error', 'Backend update timed out.')
}

// The checkout is updated but the process is still running old code. Ask the
// gateway to re-exec itself, then ride out the disconnect until it answers
// again. Best-effort: a backend that can't restart (managed install, or an
// older build without the RPC) just tells the user to restart it by hand.
async function restartRemoteBackend(requestGateway: RequestGateway): Promise<void> {
  setPhase('restarting', 'Restarting backend to load the update…')

  let restarting = true

  try {
    await requestGateway('gateway.restart')
  } catch (error) {
    // A dropped transport is the success signal — the backend re-execed before
    // (or while) replying. Any other error means the restart never happened.
    restarting = isConnectionError(error)

    if (!restarting) {
      setPhase('done', 'Backend updated. Restart it to load the new version.')

      return
    }
  }

  await waitForReconnect(requestGateway)
}

async function waitForReconnect(requestGateway: RequestGateway): Promise<void> {
  const deadline = Date.now() + RESTART_TIMEOUT_MS

  while (Date.now() < deadline) {
    await delay(POLL_INTERVAL_MS)

    try {
      await requestGateway<RemoteUpdateStatus>('update.status')
      setPhase('done', 'Backend updated and restarted.')

      return
    } catch {
      setPhase('reconnecting', 'Reconnecting to backend…')
    }
  }

  setPhase('done', 'Backend updated. Reconnect once the backend is back.')
}
