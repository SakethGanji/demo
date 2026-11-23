import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { READ_NODE, layoutRead, personOf, portLabel, readDetail, readKind, readSummary } from './readView'

const step = (id: string, type: string, parameters: Record<string, unknown> = {}, extra = {}): Node =>
  ({ id, type: 'workflowNode', position: { x: 0, y: 0 }, data: { name: id, label: id, type, parameters, ...extra } }) as Node

describe('readView', () => {
  it('names people from $vars, literal addresses, and managers', () => {
    expect(personOf('{{ $vars.CREDIT_COMMITTEE_EMAIL }}')).toBe('Credit committee')
    expect(personOf('ops@bank.com')).toBe('ops@bank.com')
    expect(personOf('{{ $json.manager_email }}')).toBe('Manager')
    expect(personOf(42)).toBe('')
  })

  it('summarises approvals, waits, emails, APIs and databases', () => {
    expect(readDetail('Approval', { notify_email: '{{ $vars.UNDERWRITER_EMAIL }}', due_in: '48h', four_eyes: true, on_timeout: 'reject' }))
      .toBe('Underwriter · within 48h · four eyes · silence = no')
    expect(readDetail('Wait', { duration: 24, unit: 'hours' })).toBe('24h')
    expect(readDetail('SendEmail', { toEmail: '{{ $vars.RISK_MANAGER_EMAIL }}' })).toBe('to Risk manager')
    expect(readDetail('HttpRequest', { api_ref: { spec_id: 'api_1', operation_id: 'releaseWire' } })).toBe('releaseWire')
    expect(readDetail('Postgres', { connection: 'core', query: 'INSERT INTO audit_log VALUES (1)' })).toBe('core · write')
    expect(readDetail('Postgres', { connection: 'core', query: 'SELECT 1' })).toBe('core · read')
    expect(readDetail('Loop', { maxIterations: 3 })).toBe('up to 3 times')
    expect(readDetail('Set')).toBe('')
  })

  it('labels decision ports in plain words, switch rules by name', () => {
    expect(portLabel('timed_out')).toBe('no answer')
    expect(portLabel('true')).toBe('yes')
    expect(portLabel(null)).toBe('')
    const sw = { name: 's', type: 'Switch', parameters: { rules: [{ output_name: 'high risk' }, {}] } } as never
    expect(portLabel('output0', sw)).toBe('high risk')
    expect(portLabel('output1', sw)).toBe('case 2')
    const byValue = {
      name: 'v', type: 'Switch',
      parameters: { rules: [{ field: 'x', value: 'no', output: 1, operation: 'equals' }, { field: 'x', value: 5, output: 0, operation: 'greater_than' }] },
    } as never
    expect(portLabel('output1', byValue)).toBe('no')
    expect(portLabel('output0', byValue)).toBe('greater than 5')
  })

  it('classifies node types', () => {
    expect(readKind('Webhook')).toBe('trigger')
    expect(readKind('Approval')).toBe('person')
    expect(readKind('If')).toBe('decision')
    expect(readKind('Code')).toBe('step')
  })

  it('lays steps out top to bottom with labelled, routed arrows and leaves non-steps out', () => {
    const nodes = [
      step('Start', 'Webhook'),
      step('Underwriter', 'Approval', { notify_email: '{{ $vars.UNDERWRITER_EMAIL }}' }),
      step('Big?', 'If'),
      step('Done', 'Respond', { statusCode: 200 }, { disabled: true }),
      { id: 'note', type: 'stickyNote', position: { x: 0, y: 0 }, data: {} } as Node,
    ]
    const edges = [
      { id: 'e1', source: 'Start', target: 'Underwriter' },
      { id: 'e2', source: 'Underwriter', target: 'Big?', sourceHandle: 'approved' },
      { id: 'e3', source: 'Big?', target: 'Done', sourceHandle: 'false' },
      { id: 'e4', source: 'note', target: 'Done' },
      { id: 'e5', source: 'Big?', target: 'Underwriter', sourceHandle: 'true' },
    ] as Edge[]
    const { nodes: out, edges: arrows, counts } = layoutRead(nodes, edges)
    expect(out.map((n) => n.id)).toEqual(['Start', 'Underwriter', 'Big?', 'Done'])
    expect(counts).toEqual({ trigger: 1, person: 1, decision: 1, reply: 1 })
    const y = Object.fromEntries(out.map((n) => [n.id, n.position.y]))
    expect(y.Start).toBeLessThan(y.Underwriter)
    expect(y.Underwriter).toBeLessThan(y['Big?'])
    expect(y['Big?']).toBeLessThan(y.Done)
    const card = out.find((n) => n.id === 'Underwriter')!
    expect(card.type).toBe('readNode')
    expect(card.data.detail).toBe('Underwriter')
    expect(out.find((n) => n.id === 'Done')!.data.disabled).toBe(true)
    expect(arrows.map((e) => [e.id, e.data!.label])).toEqual([['e1', ''], ['e2', 'approved'], ['e3', 'no'], ['e5', 'yes']])
    for (const e of arrows) expect(e.data!.points.length).toBeGreaterThanOrEqual(2)
    expect(arrows.find((e) => e.id === 'e2')!.data!.labelAt).toBeDefined()
    expect(arrows.find((e) => e.id === 'e1')!.data!.labelAt).toBeUndefined()
    // cards never overlap
    for (const a of out) for (const b of out) if (a !== b) {
      const apart = Math.abs(a.position.x - b.position.x) >= READ_NODE.width || Math.abs(a.position.y - b.position.y) >= READ_NODE.height
      expect(apart).toBe(true)
    }
  })

  it('summarises what a workflow contains in plain words', () => {
    expect(readSummary({ trigger: 1, person: 5, email: 1, decision: 4 })).toEqual(['5 approvals', '1 email', '4 checks'])
    expect(readSummary({})).toEqual([])
  })
})
