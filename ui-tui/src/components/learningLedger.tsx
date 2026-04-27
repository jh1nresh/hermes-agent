import { Box, Text, useInput, useStdout } from '@hermes/ink'
import { useEffect, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import { rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { OverlayGrid } from './overlayGrid.js'
import { OverlayHint, windowItems, windowOffset } from './overlayControls.js'

const EDGE_GUTTER = 10
const MAX_WIDTH = 132
const MIN_WIDTH = 64
const VISIBLE_ROWS = 12

const LISTS = [
  { id: 'memories', title: 'Memories', types: ['user', 'memory'] },
  { id: 'skills', title: 'Skills', types: ['skill-use'] },
  { id: 'recalls', title: 'Recalls', types: ['recall'] },
  { id: 'connected', title: 'Connected', types: ['integration'] }
] as const

const typeIcon: Record<string, string> = {
  integration: '◇',
  memory: '◆',
  recall: '↺',
  'skill-use': '✦',
  user: '●'
}

const fmtTime = (ts?: null | number) => {
  if (!ts) {
    return ''
  }

  const days = Math.floor((Date.now() - ts * 1000) / 86_400_000)

  return days <= 0 ? 'today' : `${days}d ago`
}

export function LearningLedger({ borderColor, gw, maxHeight, onClose, t, width: fixedWidth }: LearningLedgerProps) {
  const [ledger, setLedger] = useState<LearningLedgerResponse | null>(null)
  const [activeList, setActiveList] = useState(0)
  const [indices, setIndices] = useState<Record<string, number>>({})
  const [expanded, setExpanded] = useState(false)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const { stdout } = useStdout()
  const width = fixedWidth ?? Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - EDGE_GUTTER))

  useEffect(() => {
    gw.request<LearningLedgerResponse>('learning.ledger', { limit: 120 })
      .then(r => {
        setLedger(r)
        setErr('')
      })
      .catch((e: unknown) => setErr(rpcErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [gw])

  const items = ledger?.items ?? []
  const lists = LISTS.map(list => ({
    ...list,
    items: items.filter(item => list.types.includes(item.type as never))
  }))
  const active = lists[activeList] ?? lists[0]!
  const activeIdx = Math.min(indices[active.id] ?? 0, Math.max(0, active.items.length - 1))
  const selected = active.items[activeIdx]
  const detailOpen = expanded && !!selected
  useInput((ch, key) => {
    if (key.escape || ch.toLowerCase() === 'q') {
      onClose()

      return
    }

    if (key.leftArrow && activeList > 0) {
      setActiveList(v => v - 1)

      return
    }

    if (key.rightArrow && activeList < lists.length - 1) {
      setActiveList(v => v + 1)

      return
    }

    if (key.upArrow && activeIdx > 0) {
      setIndices(v => ({ ...v, [active.id]: activeIdx - 1 }))

      return
    }

    if (key.downArrow && activeIdx < active.items.length - 1) {
      setIndices(v => ({ ...v, [active.id]: activeIdx + 1 }))

      return
    }

    if (key.return || ch === ' ') {
      setExpanded(v => !v)

      return
    }

    const n = ch === '0' ? 10 : parseInt(ch, 10)
    if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, active.items.length)) {
      const next = windowOffset(active.items.length, activeIdx, VISIBLE_ROWS) + n - 1

      if (active.items[next]) {
        setIndices(v => ({ ...v, [active.id]: next }))
      }
    }
  })

  if (loading) {
    return <Text color={t.color.muted}>indexing learning ledger…</Text>
  }

  if (err) {
    return (
      <Box flexDirection="column" width={width}>
        <Text color={t.color.label}>learning ledger error: {err}</Text>
        <OverlayHint t={t}>Esc/q close</OverlayHint>
      </Box>
    )
  }

  if (!items.length) {
    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.accent}>
          Recent Learning
        </Text>
        <Text color={t.color.muted}>no memories, recalls, used skills, or integrations found yet</Text>
        {ledger?.inventory?.skills ? (
          <Text color={t.color.muted}>available knowledge: {ledger.inventory.skills} installed skills</Text>
        ) : null}
        <OverlayHint t={t}>Esc/q close</OverlayHint>
      </Box>
    )
  }

  const listPanels = lists.map((list, listIdx) => {
    const selectedIndex = Math.min(indices[list.id] ?? 0, Math.max(0, list.items.length - 1))
    const { items: visible, offset } = windowItems(list.items, selectedIndex, Math.max(3, Math.floor(VISIBLE_ROWS / 2)))

    return {
      content: (
        <LearningList
          active={activeList === listIdx}
          items={visible}
          offset={offset}
          selectedIndex={selectedIndex}
          t={t}
          total={list.items.length}
        />
      ),
      grow: 1,
      id: `learning-${list.id}`,
      title: list.title
    }
  })

  return (
    <OverlayGrid
      borderColor={borderColor}
      footer={<OverlayHint t={t}>←/→ panel · ↑/↓ select · Enter/Space details · 1-9,0 quick · Esc/q close</OverlayHint>}
      panels={[
        ...listPanels,
        ...(detailOpen && selected
          ? [
              {
                content: <LedgerDetails item={selected} t={t} />,
                grow: 2,
                id: 'learning-details',
                title: 'Details'
              }
            ]
          : [])
      ]}
      maxHeight={maxHeight}
      t={t}
      width={width}
    />
  )
}

function LearningList({ active, items, offset, selectedIndex, t, total }: LearningListProps) {
  return (
    <Box flexDirection="column">
      <Text color={active ? t.color.accent : t.color.muted}>{total} item{total === 1 ? '' : 's'}</Text>
      {offset > 0 && <Text color={t.color.muted}>↑ {offset} more</Text>}

      <Box flexDirection="column">
        {items.map((item, i) => {
          const absolute = offset + i

          return (
            <LedgerRow
              active={active && absolute === selectedIndex}
              index={i + 1}
              item={item}
              key={`${item.type}:${item.name}:${i}`}
              t={t}
            />
          )
        })}
      </Box>

      {offset + items.length < total && (
        <Text color={t.color.muted}>↓ {total - offset - items.length} more</Text>
      )}

    </Box>
  )
}

function LedgerRow({ active, index, item, t }: LedgerRowProps) {
  const when = fmtTime(item.last_used_at ?? item.learned_at)
  const count = item.count ? ` ×${item.count}` : ''
  const icon = typeIcon[item.type] ?? '•'
  const title = compactTitle(item)

  return (
    <Box flexShrink={0} width="100%">
      <Text bold={active} color={active ? t.color.accent : t.color.muted} inverse={active} wrap="truncate-end">
        {active ? '▸ ' : '  '}
        {index}. {icon} {title}
        <Text color={active ? t.color.accent : t.color.muted}>
          {' '}
          {count}
          {when ? ` · ${when}` : ''}
        </Text>
      </Text>
    </Box>
  )
}

function compactTitle(item: LearningLedgerItem) {
  const raw = item.type === 'memory' || item.type === 'user' ? item.summary : item.name
  return raw
    .replace(/^User\s+/i, '')
    .replace(/^Durable memory updates$/i, 'memory updated')
    .replace(/^session_search$/i, 'past sessions')
}

function LedgerDetails({ item, t }: LedgerDetailsProps) {
  const memoryLike = item.type === 'memory' || item.type === 'user'

  return (
    <Box flexDirection="column">
      <Text color={t.color.primary} wrap="truncate-end">
        {memoryLike ? item.name : item.summary}
      </Text>
      {memoryLike ? <Text color={t.color.text}>{item.summary}</Text> : null}
      {item.count ? <Text color={t.color.muted}>used: {item.count}×</Text> : null}
      {item.learned_from ? <Text color={t.color.muted}>from: {item.learned_from}</Text> : null}
      {item.via ? <Text color={t.color.muted}>via: {item.via}</Text> : null}
      {item.last_used_at ? <Text color={t.color.muted}>last used: {fmtTime(item.last_used_at)}</Text> : null}
      <Text color={t.color.muted}>source: {item.source}</Text>
    </Box>
  )
}

interface LearningLedgerItem {
  count?: number
  learned_from?: null | string
  last_used_at?: null | number
  learned_at?: null | number
  name: string
  source: string
  summary: string
  type: string
  via?: null | string
}

interface LearningLedgerResponse {
  counts?: Record<string, number>
  generated_at?: number
  home?: string
  inventory?: { skills?: number }
  items?: LearningLedgerItem[]
  total?: number
}

interface LearningListProps {
  active: boolean
  items: LearningLedgerItem[]
  offset: number
  selectedIndex: number
  t: Theme
  total: number
}

interface LedgerRowProps {
  active: boolean
  index: number
  item: LearningLedgerItem
  t: Theme
}

interface LedgerDetailsProps {
  item: LearningLedgerItem
  t: Theme
}

interface LearningLedgerProps {
  borderColor: string
  gw: GatewayClient
  maxHeight?: number
  onClose: () => void
  t: Theme
  width?: number
}
