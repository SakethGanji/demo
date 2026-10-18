import { describe, expect, it } from 'vitest'
import { rebuildToast } from './rebuild'
import { sectionsOf } from './sections'

const ref = (key: string) => ({ id: key, key, label: key, kind: 'capability' })

describe('sectionsOf', () => {
  it('splits each relation by direction, in a stable order, and drops empty directions', () => {
    const s = sectionsOf({
      validated_by: [{ direction: 'out', node: ref('wf:a'), props: {}, source: 'learned' }],
      implements: [
        { direction: 'in', node: ref('node:Approval'), props: {}, source: 'seed' },
        { direction: 'in', node: ref('wf:b'), props: {}, source: 'seed' },
        { direction: 'out', node: ref('concept:x'), props: {}, source: 'seed' },
      ],
      requires_permission: [{ direction: 'out', node: ref('role:ops_manager'), props: { slot: 'approver_role' }, source: 'seed' }],
    })
    expect(s.map((x) => x.key)).toEqual(['implements|in', 'implements|out', 'requires_permission|out', 'validated_by|out'])
    expect(s[0].items).toHaveLength(2)
  })
})

describe('rebuildToast', () => {
  it('lists the counts and mentions proposals only when there are some', () => {
    expect(rebuildToast({ added: 3, updated: 1, staled: 0, tagged: 2, proposed: 0, warnings: [] })).toBe('Knowledge rebuilt: 3 added · 1 updated · 0 stale · 2 tagged')
    expect(rebuildToast({ added: 0, updated: 0, staled: 1, tagged: 0, proposed: 2, warnings: [] })).toContain('2 proposed')
  })
})
