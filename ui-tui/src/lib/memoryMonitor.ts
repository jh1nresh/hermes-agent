import { type HeapDumpResult, performDiagnosticsDump, performHeapDump } from './memory.js'

export type MemoryLevel = 'critical' | 'high' | 'normal'
export type MemoryTriggerSource = 'heap' | 'rss'

export interface MemorySnapshot {
  heapUsed: number
  level: MemoryLevel
  nativeUsed: number
  rss: number
  source: MemoryTriggerSource
}

export interface MemoryMonitorOptions {
  criticalBytes?: number
  highBytes?: number
  intervalMs?: number
  onCritical?: (snap: MemorySnapshot, dump: HeapDumpResult | null) => void
  onHigh?: (snap: MemorySnapshot, dump: HeapDumpResult | null) => void
  rssCriticalBytes?: number
  rssHighBytes?: number
}

const GB = 1024 ** 3

const maxLevel = (heapLevel: MemoryLevel, rssLevel: MemoryLevel): MemoryLevel => {
  if (heapLevel === 'critical' || rssLevel === 'critical') {
    return 'critical'
  }

  return heapLevel === 'high' || rssLevel === 'high' ? 'high' : 'normal'
}

export function startMemoryMonitor({
  criticalBytes = 2.5 * GB,
  highBytes = 1.5 * GB,
  intervalMs = 10_000,
  onCritical,
  onHigh,
  rssCriticalBytes = 8 * GB,
  rssHighBytes = 4 * GB
}: MemoryMonitorOptions = {}): () => void {
  const dumped = new Set<`${MemoryTriggerSource}:${Exclude<MemoryLevel, 'normal'>}`>()

  const tick = async () => {
    const { heapUsed, rss } = process.memoryUsage()
    const nativeUsed = Math.max(0, rss - heapUsed)
    const heapLevel: MemoryLevel = heapUsed >= criticalBytes ? 'critical' : heapUsed >= highBytes ? 'high' : 'normal'
    const rssLevel: MemoryLevel = rss >= rssCriticalBytes ? 'critical' : rss >= rssHighBytes ? 'high' : 'normal'
    const level = maxLevel(heapLevel, rssLevel)

    if (level === 'normal') {
      return void dumped.clear()
    }

    const source: MemoryTriggerSource =
      heapLevel === level || (heapLevel !== 'normal' && rssLevel === level) ? 'heap' : 'rss'

    const key = `${source}:${level}` as const

    if (dumped.has(key)) {
      return
    }

    dumped.add(key)

    const trigger = level === 'critical' ? 'auto-critical' : 'auto-high'

    const dump =
      source === 'heap'
        ? await performHeapDump(trigger).catch(() => null)
        : await performDiagnosticsDump(trigger).catch(() => null)

    const snap: MemorySnapshot = { heapUsed, level, nativeUsed, rss, source }

    ;(level === 'critical' ? onCritical : onHigh)?.(snap, dump)
  }

  const handle = setInterval(() => void tick(), intervalMs)

  handle.unref?.()

  return () => clearInterval(handle)
}
