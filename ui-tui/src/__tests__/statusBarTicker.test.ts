import { describe, expect, it } from 'vitest'

import { padVerb, VERB_PAD_LEN } from '../components/appChrome.js'
import { VERBS } from '../content/verbs.js'

describe('FaceTicker verb padding (#13610)', () => {
  it('pads every verb in the catalogue to the same column width (+1 separator)', () => {
    for (const verb of VERBS) {
      expect(padVerb(verb)).toHaveLength(VERB_PAD_LEN + 1)
    }
  })

  it('keeps the trailing ellipsis attached to the verb', () => {
    for (const verb of VERBS) {
      expect(padVerb(verb).startsWith(`${verb}…`)).toBe(true)
    }
  })

  it('handles empty verbs without truncating the pad target', () => {
    expect(padVerb('')).toHaveLength(VERB_PAD_LEN + 1)
  })

  it('keeps a trailing space even when the verb is already at the limit', () => {
    const longest = VERBS.reduce((a, b) => (b.length > a.length ? b : a), '')
    expect(padVerb(longest)).toBe(`${longest}… `)
    expect(padVerb(longest).endsWith(' ')).toBe(true)
  })
})
