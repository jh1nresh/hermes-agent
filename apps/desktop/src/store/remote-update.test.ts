import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $remoteUpdate, resetRemoteUpdate, startRemoteUpdate } from './remote-update'

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  dismissNotification: vi.fn()
}))

const POLL_STEP = 2_100

describe('startRemoteUpdate', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetRemoteUpdate()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('runs starting → running → restarting → done on a clean update + restart', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true }) // update.start
      .mockResolvedValueOnce({ running: true, finished: false, exit_code: null, output: '' }) // update.status
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: 'done' }) // update.status
      .mockResolvedValueOnce({ restarting: true }) // gateway.restart
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: 'done' }) // update.status (reconnected)

    const promise = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(POLL_STEP) // → running
    await vi.advanceTimersByTimeAsync(POLL_STEP) // → finished → gateway.restart → wait
    await vi.advanceTimersByTimeAsync(POLL_STEP) // → reconnect poll → done
    await promise

    expect($remoteUpdate.get().phase).toBe('done')
    expect($remoteUpdate.get().message).toContain('restarted')
    expect(requestGateway).toHaveBeenNthCalledWith(1, 'update.start')
    expect(requestGateway).toHaveBeenCalledWith('gateway.restart')
  })

  it('surfaces an error (with output tail) on a non-zero exit', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 1, output: 'pulling…\nfatal: boom' })

    const promise = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(POLL_STEP)
    await promise

    expect($remoteUpdate.get().phase).toBe('error')
    expect($remoteUpdate.get().message).toContain('boom')
    expect(requestGateway).not.toHaveBeenCalledWith('gateway.restart')
  })

  it('shows reconnecting while the backend is down mid-update, then restarts', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true })
      .mockRejectedValueOnce(new Error('connection closed'))
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })
      .mockResolvedValueOnce({ restarting: true })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })

    const promise = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(POLL_STEP)
    expect($remoteUpdate.get().phase).toBe('reconnecting')

    await vi.advanceTimersByTimeAsync(POLL_STEP) // → finished → restart → wait
    await vi.advanceTimersByTimeAsync(POLL_STEP) // → reconnect poll → done
    await promise

    expect($remoteUpdate.get().phase).toBe('done')
  })

  it('treats a dropped transport on gateway.restart as the restart succeeding', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })
      .mockRejectedValueOnce(new Error('Hermes gateway connection closed')) // gateway.restart drops
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' }) // reconnected

    const promise = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(POLL_STEP) // → finished → gateway.restart (drops) → wait
    await vi.advanceTimersByTimeAsync(POLL_STEP) // → reconnect poll → done
    await promise

    expect($remoteUpdate.get().phase).toBe('done')
    expect($remoteUpdate.get().message).toContain('restarted')
  })

  it('falls back to a manual-restart hint when the backend lacks gateway.restart', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })
      .mockRejectedValueOnce(new Error('method not found')) // gateway.restart unsupported (RPC error)

    const promise = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(POLL_STEP)
    await promise

    expect($remoteUpdate.get().phase).toBe('done')
    expect($remoteUpdate.get().message).toContain('Restart it')
  })

  it('errors without polling when the update fails to start', async () => {
    const requestGateway = vi.fn().mockRejectedValueOnce(new Error('not a git checkout'))

    await startRemoteUpdate(requestGateway)

    expect($remoteUpdate.get().phase).toBe('error')
    expect(requestGateway).toHaveBeenCalledTimes(1)
  })

  it('ignores a second start while one is already in flight', async () => {
    const requestGateway = vi
      .fn()
      .mockResolvedValueOnce({ started: true })
      .mockResolvedValueOnce({ running: true, finished: false, exit_code: null, output: '' })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })
      .mockResolvedValueOnce({ restarting: true })
      .mockResolvedValueOnce({ running: false, finished: true, exit_code: 0, output: '' })

    const first = startRemoteUpdate(requestGateway)

    await vi.advanceTimersByTimeAsync(0)
    await startRemoteUpdate(requestGateway) // ignored — a run is already active

    await vi.advanceTimersByTimeAsync(POLL_STEP)
    await vi.advanceTimersByTimeAsync(POLL_STEP)
    await vi.advanceTimersByTimeAsync(POLL_STEP)
    await first

    const startCalls = requestGateway.mock.calls.filter(call => call[0] === 'update.start')

    expect(startCalls).toHaveLength(1)
  })
})
