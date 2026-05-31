import { describe, expect, it } from 'vitest'

import { stripAnsi } from '../lib/text.js'
import { estimatedMsgHeight, messageHeightKey, wrappedLines } from '../lib/virtualHeights.js'
import type { Msg } from '../types.js'

const ESC = String.fromCharCode(27)

// Heavy per-word truecolor SGR — the shape cli-highlight / Rich tool output
// takes when it lands in assistant/system history. Visible text is short;
// raw byte length is ~5-6x larger.
const colorize = (line: string) =>
  line
    .split(' ')
    .map((w, i) => `${ESC}[38;2;${(i * 40) % 255};${(i * 17) % 255};${(i * 90) % 255}m${w}${ESC}[39m`)
    .join(' ')

const cols = 80
const opts = { compact: false, details: false }

describe('ANSI message height estimation parity (regression for resume desync)', () => {
  it('wrappedLines must measure visible width, not raw escape bytes', () => {
    const visible = 'the quick brown fox jumps over the lazy dog and keeps going for a while'
    const ansi = colorize(visible)

    // The estimator path strips ANSI before measuring; verify the helper a
    // caller would use produces the visible row count, not the byte count.
    expect(wrappedLines(stripAnsi(ansi), cols)).toBe(wrappedLines(visible, cols))
    // Guard the test fixture itself: the raw form really is much longer.
    expect(ansi.length).toBeGreaterThan(visible.length * 3)
  })

  it('estimatedMsgHeight for an ANSI assistant message matches its visible height', () => {
    const lines = Array.from({ length: 20 }, (_, i) =>
      colorize(`line ${i} with several colored words that wrap when measured by raw byte length only`)
    )

    const ansiMsg: Msg = { role: 'assistant', text: lines.join('\n') }
    const visibleMsg: Msg = { role: 'assistant', text: stripAnsi(lines.join('\n')) }

    const estAnsi = estimatedMsgHeight(ansiMsg, cols, opts)
    const estVisible = estimatedMsgHeight(visibleMsg, cols, opts)

    // Before the fix this was ~3x (120 vs 40). Allow ±2 for paragraph-gap
    // heuristics that key off blank lines.
    expect(Math.abs(estAnsi - estVisible)).toBeLessThanOrEqual(2)
  })

  it('FAKE LONG SESSION: cold-mount offsets track visible heights so the right rows mount', () => {
    // Synthesize a long resumed session: alternating user prompts and
    // ANSI-heavy assistant replies (highlighted code / tool echoes), exactly
    // what `-c` rehydrates from the DB before any row is Yoga-measured.
    const items: { key: string; msg: Msg }[] = []

    for (let turn = 0; turn < 120; turn++) {
      const user: Msg = { role: 'user', text: `question ${turn} about the codebase` }

      const codeLines = Array.from({ length: 12 }, (_, i) =>
        colorize(`  const result_${i} = doSomething(${i}, "a fairly long argument string here")`)
      )

      const assistant: Msg = { role: 'assistant', text: `Here is turn ${turn}:\n\n${codeLines.join('\n')}` }
      items.push({ key: messageHeightKey(user), msg: user })
      items.push({ key: messageHeightKey(assistant), msg: assistant })
    }

    // Build the prefix-sum offset array the way useVirtualHistory does on
    // cold mount (every height comes from the estimator — no Yoga yet).
    const estOffsets = new Float64Array(items.length + 1)
    const visibleOffsets = new Float64Array(items.length + 1)

    for (let i = 0; i < items.length; i++) {
      estOffsets[i + 1] = estOffsets[i]! + estimatedMsgHeight(items[i]!.msg, cols, opts)
      const visMsg: Msg = { ...items[i]!.msg, text: stripAnsi(items[i]!.msg.text) }
      visibleOffsets[i + 1] = visibleOffsets[i]! + estimatedMsgHeight(visMsg, cols, opts)
    }

    const estTotal = estOffsets[items.length]!
    const visTotal = visibleOffsets[items.length]!

    // The estimated total height must track the visible total closely. Pre-fix
    // the ANSI estimate was several multiples larger (escape bytes counted as
    // width), so the binary search that maps scrollTop → mounted-row-index
    // landed on the wrong items → "jumbled / broken text" on resume.
    //
    // Tolerance note: the visible baseline runs estimatedMsgHeight on the
    // ANSI-stripped text, which (being non-ANSI) still earns the markdown
    // paragraph-gap bonus that the ANSI render path legitimately omits. That
    // asymmetry is a few rows across the whole synthetic, so allow 8% here —
    // the estimator-vs-REAL-render parity (within a couple rows) is asserted
    // in messageLineAnsiHeight.test.tsx, which is the truth source.
    const drift = Math.abs(estTotal - visTotal) / visTotal

    expect(drift).toBeLessThan(0.08)
  })

  it('upperBound on estimated offsets selects the same start index as visible offsets', () => {
    // Directly exercise the failure mechanism: with inflated offsets the
    // viewport's scrollTop maps to a different row than the one actually
    // visible there. This asserts they agree at several scroll positions.
    const items: Msg[] = []

    for (let t = 0; t < 80; t++) {
      items.push({ role: 'user', text: `q${t}` })
      const code = Array.from({ length: 8 }, (_, i) => colorize(`token_${i} = highlight(${i})`)).join('\n')
      items.push({ role: 'assistant', text: code })
    }

    const est = new Float64Array(items.length + 1)
    const vis = new Float64Array(items.length + 1)

    for (let i = 0; i < items.length; i++) {
      est[i + 1] = est[i]! + estimatedMsgHeight(items[i]!, cols, opts)
      vis[i + 1] = vis[i]! + estimatedMsgHeight({ ...items[i]!, text: stripAnsi(items[i]!.text) }, cols, opts)
    }

    const upperBound = (arr: Float64Array, target: number) => {
      let lo = 0
      let hi = arr.length

      while (lo < hi) {
        const mid = (lo + hi) >> 1

        arr[mid]! <= target ? (lo = mid + 1) : (hi = mid)
      }

      return lo
    }

    // Probe at 10%, 30%, 50%, 70%, 90% down the VISIBLE transcript. The row
    // index the estimator-derived offsets point to must match the visible
    // one, otherwise the wrong rows mount into the viewport.
    const visTotal = vis[items.length]!

    for (const frac of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      const scrollTop = visTotal * frac
      const visIdx = upperBound(vis, scrollTop) - 1
      const estIdx = upperBound(est, scrollTop) - 1

      expect(estIdx).toBe(visIdx)
    }
  })
})
