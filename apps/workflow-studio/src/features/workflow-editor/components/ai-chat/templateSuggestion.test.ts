import { describe, expect, it } from 'vitest'
import { suggestionOf } from './templateSuggestion'

const good = { kind: 'template', slug: 'refund-triage', name: 'Refund triage', version: '1.0.0', summary: 's', reason: 'r', score: 0.5 }

describe('suggestionOf', () => {
  it('reads a complete suggestion', () => {
    expect(suggestionOf({ template_suggestion: good })).toEqual({ slug: 'refund-triage', name: 'Refund triage', version: '1.0.0', summary: 's', reason: 'r', score: 0.5 })
  })
  it.each([null, undefined, {}, { template_suggestion: null }, { template_suggestion: { ...good, kind: 'other' } },
    { template_suggestion: { ...good, slug: '' } }, { template_suggestion: 'x' }])('is none for %j', (spec) => {
    expect(suggestionOf(spec)).toBeNull()
  })
  it('tolerates missing optional text', () => {
    expect(suggestionOf({ template_suggestion: { kind: 'template', slug: 'a', name: 'A' } })).toMatchObject({ reason: '', score: 0 })
  })
})
