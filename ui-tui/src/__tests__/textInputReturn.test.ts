import { describe, expect, it } from 'vitest'

import { resolveReturn } from '../components/textInput.js'

// Behavioural contract for Enter inside the composer. Three outcomes:
//   - `submit`       no modifier, cursor not after a backslash
//   - `newline`      modifier held (Shift / Ctrl / Alt / Cmd / Ctrl+J ...)
//   - `continuation` plain Enter but value[cursor-1] === '\\' — backslash
//                    is consumed, '\n' inserted in its place. One char out,
//                    one char in: cursor index is unchanged but now points
//                    *after* the inserted newline.

describe('resolveReturn', () => {
  it('submits when no modifier and no trailing backslash', () => {
    expect(resolveReturn('hello', 5, false)).toEqual({ kind: 'submit' })
  })

  it('inserts a newline when a modifier is held', () => {
    expect(resolveReturn('hello', 5, true)).toEqual({
      kind: 'newline',
      value: 'hello\n',
      cursor: 6
    })
  })

  it('inserts the newline at the cursor, not at end of value', () => {
    expect(resolveReturn('helloworld', 5, true)).toEqual({
      kind: 'newline',
      value: 'hello\nworld',
      cursor: 6
    })
  })

  it("consumes a trailing backslash and inserts a newline (\\\\+Enter)", () => {
    expect(resolveReturn('foo\\', 4, false)).toEqual({
      kind: 'continuation',
      value: 'foo\n',
      cursor: 4
    })
  })

  it('continuation also fires when the cursor is mid-string after a backslash', () => {
    expect(resolveReturn('foo\\bar', 4, false)).toEqual({
      kind: 'continuation',
      value: 'foo\nbar',
      cursor: 4
    })
  })

  it('continuation takes precedence over modifier (no double newline)', () => {
    expect(resolveReturn('foo\\', 4, true)).toEqual({
      kind: 'continuation',
      value: 'foo\n',
      cursor: 4
    })
  })

  it('does not fire continuation when cursor is at start (no preceding char)', () => {
    expect(resolveReturn('\\foo', 0, false)).toEqual({ kind: 'submit' })
  })

  it('only the last backslash is consumed on a double-backslash sequence', () => {
    // Matches shell line-continuation semantics: the right-most '\' before
    // the cursor is the continuation marker; any preceding '\' chars are
    // literal.
    expect(resolveReturn('foo\\\\', 5, false)).toEqual({
      kind: 'continuation',
      value: 'foo\\\n',
      cursor: 5
    })
  })
})
