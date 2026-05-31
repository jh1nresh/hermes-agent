import { PassThrough } from 'stream'

import { Box, renderSync } from '@hermes/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { MessageLine } from '../components/messageLine.js'
import { stripAnsi } from '../lib/text.js'
import { estimatedMsgHeight } from '../lib/virtualHeights.js'
import { DEFAULT_THEME } from '../theme.js'
import type { Msg } from '../types.js'

const BEL = String.fromCharCode(7)
const ESC = String.fromCharCode(27)
const CSI_RE = new RegExp(`${ESC}\\[[0-?]*[ -/]*[@-~]`, 'g')
const OSC_RE = new RegExp(`${ESC}\\][\\s\\S]*?(?:${BEL}|${ESC}\\\\)`, 'g')

const cols = 80

// Render a node at a fixed terminal width and return its visible line count
// (blank trailing lines trimmed) — i.e. the real laid-out height Ink + Yoga
// produce, which is what post-mount measurement writes into the height cache.
const renderHeight = (node: React.ReactNode): number => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: cols, isTTY: false, rows: 24 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(node, {
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  const lines = output
    .replace(OSC_RE, '')
    .split('\n')
    .map(line => stripAnsi(line).replace(CSI_RE, '').trimEnd())

  // Drop trailing blank lines so we count only rows with content.
  while (lines.length && lines[lines.length - 1] === '') {
    lines.pop()
  }

  return lines.length
}

const colorize = (line: string) =>
  line
    .split(' ')
    .map((w, i) => `${ESC}[38;2;${(i * 40) % 255};${(i * 17) % 255};${(i * 90) % 255}m${w}${ESC}[39m`)
    .join(' ')

describe('MessageLine render height ↔ estimator parity (ANSI history)', () => {
  it('estimator predicts the real rendered height of an ANSI assistant message', () => {
    // A short visible message wrapped in heavy SGR — the cli-highlight shape.
    const visible = Array.from(
      { length: 6 },
      (_, i) => `line ${i}: some highlighted code tokens that should wrap predictably here`
    ).join('\n')

    const msg: Msg = { role: 'assistant', text: colorize(visible) }

    const rendered = renderHeight(
      React.createElement(Box, { width: cols }, React.createElement(MessageLine, { cols, msg, t: DEFAULT_THEME }))
    )

    const estimated = estimatedMsgHeight(msg, cols, { compact: false, details: false })

    // The estimate must be in the same ballpark as the real render. Before
    // the ANSI-strip fix the estimate counted escape bytes as width and ran
    // 3x high, which is what desynced the virtual list on resume. Allow a
    // small margin for gutter/separator chrome the estimator folds in.
    expect(Math.abs(estimated - rendered)).toBeLessThanOrEqual(4)
  })

  it('the same message stripped of ANSI renders to the same height it estimates', () => {
    const visible = Array.from(
      { length: 6 },
      (_, i) => `line ${i}: some highlighted code tokens that should wrap predictably here`
    ).join('\n')

    const ansiMsg: Msg = { role: 'assistant', text: colorize(visible) }
    const plainMsg: Msg = { role: 'assistant', text: visible }

    const ansiRendered = renderHeight(
      React.createElement(Box, { width: cols }, React.createElement(MessageLine, { cols, msg: ansiMsg, t: DEFAULT_THEME }))
    )

    const plainRendered = renderHeight(
      React.createElement(Box, { width: cols }, React.createElement(MessageLine, { cols, msg: plainMsg, t: DEFAULT_THEME }))
    )

    // <Ansi> lays out only visible graphemes, so colored and plain forms of
    // the same text occupy the same number of rows.
    expect(Math.abs(ansiRendered - plainRendered)).toBeLessThanOrEqual(1)
  })
})
