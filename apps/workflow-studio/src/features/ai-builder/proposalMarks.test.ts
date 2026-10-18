import { describe, expect, it } from 'vitest'
import { diffItems, diffSummaryText, fieldLabel, ghostNodesFor, marksFromDefinitions, marksFromDiff } from './proposalMarks'
import type { ProposalDiff, ProposedDefinition } from './types'

const DIFF: ProposalDiff = {
  nodes: { added: ['Over limit?', 'Notify'], removed: ['Old step'], changed: [{ name: 'Issue refund', fields: ['parameters.body', 'on_error'] }] },
  connections: { added: [['A', 'main', 'B', 'main']], removed: [] },
  tests: { added: ['t_1'], changed: [], removed: [] },
  interface: { changed: false },
  new_destinations: ['https://refunds.bank.internal'],
  new_code: [],
  counts: { added: 2, removed: 1, changed: 1 },
}

describe('diffItems / marksFromDiff', () => {
  it('lists added, then changed, then removed', () => {
    expect(diffItems(DIFF).map((i) => `${i.kind}:${i.name}`)).toEqual([
      'added:Over limit?', 'added:Notify', 'changed:Issue refund', 'removed:Old step',
    ])
  })
  it('keeps the changed fields', () => {
    expect(diffItems(DIFF).find((i) => i.kind === 'changed')?.fields).toEqual(['parameters.body', 'on_error'])
  })
  it('keys the marks by node name (the canvas id)', () => {
    expect(marksFromDiff(DIFF)).toEqual({ 'Over limit?': 'added', Notify: 'added', 'Issue refund': 'changed', 'Old step': 'removed' })
  })
  it('no diff, no marks', () => {
    expect(marksFromDiff(null)).toEqual({})
    expect(diffItems(undefined)).toEqual([])
  })
})

describe('text', () => {
  it('summarises the counts', () => {
    expect(diffSummaryText(DIFF)).toBe('2 steps added, 1 changed, 1 removed')
    expect(diffSummaryText({ ...DIFF, counts: { added: 1, changed: 0, removed: 0 } })).toBe('1 step added')
    expect(diffSummaryText({ ...DIFF, counts: { added: 0, changed: 0, removed: 0 } })).toBe('No steps changed.')
    expect(diffSummaryText(null)).toBe('No changes to show.')
  })
  it('names changed parts in plain words', () => {
    expect(fieldLabel('parameters.url')).toBe('its url setting')
    expect(fieldLabel('on_error')).toBe('what happens when it fails')
    expect(fieldLabel('parameters.request_body')).toBe('its request body setting')
  })
})

const node = (name: string, parameters: Record<string, unknown> = {}) => ({ name, type: 'Set', parameters, position: { x: 0, y: 0 } })
const def = (...nodes: ReturnType<typeof node>[]) => ({ nodes, connections: [] }) as unknown as ProposedDefinition

describe('marksFromDefinitions (the editor diff, as the fallback)', () => {
  it('a new workflow marks every node added', () => {
    expect(marksFromDefinitions(null, def(node('A'), node('B')))).toEqual({ A: 'added', B: 'added' })
  })
  it('marks added, changed and removed against the base', () => {
    const base = def(node('A', { x: 1 }), node('B'), node('Gone'))
    const next = def(node('A', { x: 2 }), node('B'), node('New'))
    expect(marksFromDefinitions(base as never, next)).toEqual({ A: 'changed', New: 'added', Gone: 'removed' })
  })
  it('no proposal, no marks', () => {
    expect(marksFromDefinitions(null, null)).toEqual({})
  })
})

describe('ghostNodesFor', () => {
  it('draws only the removed steps, frozen and sized', () => {
    const base = def(node('Keep'), node('Gone'))
    const ghosts = ghostNodesFor(['Gone'], base as never)
    expect(ghosts.map((g) => g.id)).toEqual(['Gone'])
    expect(ghosts[0]).toMatchObject({ draggable: false, selectable: false, connectable: false, measured: { width: 64, height: 87 } })
  })
  it('nothing to draw without a base or without removals', () => {
    expect(ghostNodesFor([], def(node('A')) as never)).toEqual([])
    expect(ghostNodesFor(['A'], null)).toEqual([])
  })
})
