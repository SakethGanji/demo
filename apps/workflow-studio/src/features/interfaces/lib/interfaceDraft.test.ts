import { describe, expect, it } from 'vitest'
import { kindName, navbarLabel, previewScreenData, previewScreenIds, slugify, switchKind, validateSlug, withScreen } from './interfaceDraft'
import type { Interface, ScreenSpec } from './types'

const spec = (t: string): ScreenSpec => ({ v: 1, components: [{ type: 'heading', text: t }] })
const custom: Interface = {
  kind: 'custom', slug: 'refunds', title: 'Refund request', trigger_node: 'Refund request',
  result_node: 'Show result', screens: { start: spec('s'), 'task:Manager review': spec('t'), result: spec('r') },
}

describe('slug', () => {
  it('follows ^[a-z0-9][a-z0-9-]{1,62}$', () => {
    expect(validateSlug('refunds')).toBeNull()
    expect(validateSlug('a')).toBeTruthy()
    expect(validateSlug('-bad')).toBeTruthy()
    expect(validateSlug('Bad')).toBeTruthy()
    expect(validateSlug('x'.repeat(64))).toBeTruthy()
    expect(validateSlug('x'.repeat(63))).toBeNull()
    expect(validateSlug('')).toBeTruthy()
  })
  it('slugify', () => {
    expect(slugify('Refund triage (UI)')).toBe('refund-triage-ui')
    expect(slugify('!')).toBe('')
  })
})

describe('labels', () => {
  it('navbar label', () => {
    expect(navbarLabel(custom)).toBe('Interface (Custom)')
    expect(navbarLabel({ ...custom, kind: 'form' })).toBe('Interface (Form)')
    expect(navbarLabel({ ...custom, kind: 'chat' })).toBe('Interface (Chat)')
    expect(navbarLabel(null)).toBe('Interface (Off)')
    expect(kindName(undefined)).toBe('Off')
  })
})

describe('switchKind', () => {
  const genForm: Interface = { kind: 'form', slug: 'gen', title: 'Gen', trigger_node: 'Refund request' }
  const genCustom: Interface = { kind: 'custom', slug: 'gen', title: 'Gen', trigger_node: 'Refund request', screens: { start: spec('generated') } }
  it('keeps slug and title, drops screens for form', () => {
    const f = switchKind(custom, genForm)
    expect(f.kind).toBe('form')
    expect(f.slug).toBe('refunds')
    expect(f.title).toBe('Refund request')
    expect(f.screens).toBeUndefined()
    expect(f.result_node).toBe('Show result')
  })
  it('brings stashed screens back when returning to custom', () => {
    const back = switchKind(switchKind(custom, genForm), genCustom, custom.screens)
    expect(back.screens?.['task:Manager review']).toEqual(spec('t'))
    expect(back.screens?.start).toEqual(spec('s'))
  })
  it('with no current interface it is just the generated one', () => {
    expect(switchKind(null, genCustom)).toEqual(genCustom)
  })
})

describe('preview', () => {
  it('lists screens', () => {
    expect(previewScreenIds(custom)).toEqual(['start', 'task:Manager review', 'result'])
    expect(previewScreenIds({ ...custom, kind: 'form' })).toEqual(['start'])
    expect(previewScreenIds(null)).toEqual(['start'])
  })
  it('data for a task screen carries that node task and the run input', () => {
    const d = previewScreenData(
      {
        input: { amount: 742 },
        nodes: { Classify: { summary: 's' } },
        result: null,
        tasks: [
          { id: 't1', node_name: 'Other', title: 'x', status: 'open', due_at: null, assignees: [] },
          { id: 't2', node_name: 'Manager review', title: 'Refund', status: 'open', due_at: null, assignees: ['d'] },
        ] as never,
      },
      'task:Manager review',
    )
    expect(d.input).toEqual({ amount: 742 })
    expect(d.node).toEqual({ Classify: { summary: 's' } })
    expect(d.task).toMatchObject({ title: 'Refund', status: 'open' })
    expect(previewScreenData(undefined, 'start')).toEqual({})
  })
  it('withScreen replaces one screen only', () => {
    const n = withScreen(custom, 'result', spec('new'))
    expect(n.screens?.result).toEqual(spec('new'))
    expect(n.screens?.start).toEqual(spec('s'))
  })
})
