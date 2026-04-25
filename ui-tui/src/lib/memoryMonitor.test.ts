import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const memory = vi.hoisted(() => ({
  performDiagnosticsDump: vi.fn(async () => ({ diagPath: '/tmp/diag.json', success: true })),
  performHeapDump: vi.fn(async () => ({ heapPath: '/tmp/heap.heapsnapshot', success: true }))
}))

vi.mock('./memory.js', () => memory)

import { type MemorySnapshot, startMemoryMonitor } from './memoryMonitor.js'

const GB = 1024 ** 3

const usage = (heapUsed: number, rss: number): NodeJS.MemoryUsage =>
  ({
    arrayBuffers: 0,
    external: 0,
    heapTotal: heapUsed,
    heapUsed,
    rss
  }) as NodeJS.MemoryUsage

describe('startMemoryMonitor', () => {
  let memoryUsageSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    memory.performDiagnosticsDump.mockClear()
    memory.performHeapDump.mockClear()
  })

  afterEach(() => {
    memoryUsageSpy?.mockRestore()
    vi.useRealTimers()
  })

  it('captures diagnostics only for native RSS pressure', async () => {
    memoryUsageSpy = vi.spyOn(process, 'memoryUsage').mockReturnValue(usage(100 * 1024 ** 2, 5 * GB))

    const snaps: MemorySnapshot[] = []

    const stop = startMemoryMonitor({
      intervalMs: 1000,
      onHigh: snap => snaps.push(snap),
      rssHighBytes: 4 * GB
    })

    await vi.advanceTimersByTimeAsync(1000)
    stop()

    expect(memory.performDiagnosticsDump).toHaveBeenCalledWith('auto-high')
    expect(memory.performHeapDump).not.toHaveBeenCalled()
    expect(snaps[0]).toMatchObject({ level: 'high', source: 'rss' })
    expect(snaps[0]?.nativeUsed).toBeGreaterThan(4 * GB)
  })

  it('keeps heap dumps for V8 heap pressure', async () => {
    memoryUsageSpy = vi.spyOn(process, 'memoryUsage').mockReturnValue(usage(3 * GB, 3.5 * GB))

    const snaps: MemorySnapshot[] = []

    const stop = startMemoryMonitor({
      intervalMs: 1000,
      onCritical: snap => snaps.push(snap)
    })

    await vi.advanceTimersByTimeAsync(1000)
    stop()

    expect(memory.performHeapDump).toHaveBeenCalledWith('auto-critical')
    expect(memory.performDiagnosticsDump).not.toHaveBeenCalled()
    expect(snaps[0]).toMatchObject({ level: 'critical', source: 'heap' })
  })
})
