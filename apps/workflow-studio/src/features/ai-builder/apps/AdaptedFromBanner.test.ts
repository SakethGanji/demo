import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import type { AdaptedFrom } from './appTypes'
import { bannerModel } from './lib/appState'
import { AdaptedFromBanner } from './AdaptedFromBanner'

const teamB: AdaptedFrom = {
  pattern: 'pattern:3f', label: 'Case management app', fit: 0.86, validated_count: 1, source_project: 'Payments Ops', tests: 21,
  diff: [
    { slot: 'sla.due_in', from: '48h', to: '4h' },
    { slot: 'report.schedule', from: '0 8 * * 1', to: '0 8 * * *' },
    { slot: 'escalation.target_role', from: 'role:ops_head', to: 'role:fraud_lead' },
  ],
}

describe('bannerModel', () => {
  it('names the pattern, who validated it and how many tests', () => {
    const m = bannerModel(teamB)
    expect(m.headline).toBe("Adapted from 'Case management app'")
    expect(m.validated).toBe('validated by Payments Ops (21 tests)')
  })

  it('shows every changed slot as old -> new, in plain words', () => {
    expect(bannerModel(teamB).lines.map((l) => `${l.from} -> ${l.to}`)).toEqual(['48h -> 4h', 'Mondays 08:00 -> daily 08:00', 'ops_head -> fraud_lead'])
  })

  it('drops a slot that did not change, and never shows a score', () => {
    const m = bannerModel({ ...teamB, diff: [{ slot: 'sla.due_in', from: '48h', to: '48h' }] })
    expect(m.lines).toEqual([])
    expect(JSON.stringify(m)).not.toMatch(/0\.86|fit|%/)
  })

  it('falls back to the validation count when the engine sends no project or tests', () => {
    expect(bannerModel({ pattern: 'p', label: 'X', validated_count: 2 }).validated).toBe('validated ×2')
    expect(bannerModel({ pattern: 'p', label: 'X', validated_count: 0 }).validated).toBeNull()
    expect(bannerModel({ pattern: 'p', label: 'X', source_project: 'Ops' }).validated).toBe('validated by Ops')
  })
})

describe('AdaptedFromBanner', () => {
  const html = renderToStaticMarkup(createElement(AdaptedFromBanner, { adapted: teamB }))

  it('renders under the frozen test id with the old values struck and the new ones plain', () => {
    expect(html).toContain('data-testid="ai-app-adapted-from"')
    expect(html).toContain("Adapted from &#x27;Case management app&#x27;")
    expect(html).toContain('validated by Payments Ops (21 tests)')
    expect(html).toMatch(/<s[^>]*>48h<\/s>/)
    expect(html).toContain('4h')
    expect(html).toMatch(/<s[^>]*>Mondays 08:00<\/s>/)
    expect(html).toContain('fraud_lead')
  })

  it('renders nothing for a plan that was not adapted', () => {
    expect(renderToStaticMarkup(createElement(AdaptedFromBanner, { adapted: null }))).toBe('')
  })
})
