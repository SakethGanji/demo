import { describe, expect, it } from 'vitest'
import { buildPageModel, parKey, toPageWorkflow, type Block, type PageWorkflow } from './pageModel'

const n = (name: string, type = 'Set', parameters: Record<string, unknown> = {}) => ({ name, type, parameters })
const c = (s: string, t: string, port = 'main') => ({ source_node: s, source_output: port, target_node: t, target_input: 'main' })

/** The e2e seed (apps/workflow-engine/tests/fixtures/workflows/refund_triage.json). */
const refund: PageWorkflow = toPageWorkflow({
  name: 'Refund triage',
  nodes: [
    n('Refund request', 'Webhook', { method: 'POST', path: 'refund-triage' }),
    n('Normalize request', 'Set'),
    n('Amount > 500?', 'If', { condition: '{{ $json.amount > 500 }}' }),
    n('Ask risk API', 'HttpRequest', { method: 'POST', url: 'http://x/risk' }),
    n('Log escalated refund', 'DataTable', { table: 'refunds', operation: 'insert' }),
    n('Log auto-approved refund', 'DataTable', { table: 'refunds', operation: 'insert' }),
  ],
  connections: [
    c('Refund request', 'Normalize request'),
    c('Normalize request', 'Amount > 500?'),
    c('Amount > 500?', 'Ask risk API', 'true'),
    c('Ask risk API', 'Log escalated refund'),
    c('Amount > 500?', 'Log auto-approved refund', 'false'),
  ],
})

const kinds = (bs: Block[]) => bs.map((b) => b.t)

describe('buildPageModel', () => {
  it('reads the refund seed as trigger, Intake and one decision section', () => {
    const m = buildPageModel(refund)
    expect(m.trigger).toBe('Refund request')
    expect(m.sections.map((s) => [s.title, s.kind])).toEqual([
      ['Intake', 'intake'],
      ['Amount > 500?', 'structure'],
    ])
    const br = m.sections[1].blocks[0]
    expect(br.t).toBe('branch')
    if (br.t !== 'branch') return
    expect(br.cases.map((x) => [x.port, x.label, x.blocks.length])).toEqual([
      ['true', 'yes', 2],
      ['false', 'no', 1],
    ])
    expect(br.join).toBeUndefined()
    // document numbering: top level 1, 2, 3; inside each case N.1, N.2 again; every node once
    expect(m.numbers).toEqual({ 'Refund request': '1', 'Normalize request': '2', 'Amount > 500?': '3', 'Ask risk API': '3.1', 'Log escalated refund': '3.2', 'Log auto-approved refund': '3.1' })
    expect(m.top).toMatchObject({ 'Refund request': '1', 'Ask risk API': '3', 'Log auto-approved refund': '3' })
    // the outline lists the top-level steps only
    expect(m.outline.map((o) => [o.num, o.node])).toEqual([
      ['1', 'Refund request'],
      ['2', 'Normalize request'],
      ['3', 'Amount > 500?'],
    ])
    expect(new Set(m.order).size).toBe(6)
    expect(m.ancestors['Log escalated refund']).toEqual(['sec-amount-500', 'Amount > 500?:true'])
  })

  it('joins a branch at its merge and carries on in the same section, then Finish', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('D', 'If'), n('A'), n('B'), n('M', 'Merge'), n('E'), n('F', 'Switch'), n('G'), n('H'), n('Z')],
        connections: [c('T', 'D'), c('D', 'A', 'true'), c('D', 'B', 'false'), c('A', 'M'), c('B', 'M'), c('M', 'E'), c('E', 'F'), c('F', 'G', 'output0'), c('F', 'H', 'fallback'), c('G', 'Z'), c('H', 'Z')],
      }),
    )
    expect(m.sections.map((s) => s.title)).toEqual(['D', 'F', 'Finish'])
    expect(kinds(m.sections[0].blocks)).toEqual(['branch', 'step', 'step'])
    const d = m.sections[0].blocks[0]
    expect(d.t === 'branch' && d.join).toBe('M')
    expect(m.sections[2].nodes).toEqual(['Z'])
  })

  it('a case that goes straight to the join has no steps and says where it continues', () => {
    const m = buildPageModel(toPageWorkflow({ nodes: [n('T', 'Webhook'), n('D', 'If'), n('A'), n('M')], connections: [c('T', 'D'), c('D', 'A', 'true'), c('D', 'M', 'false'), c('A', 'M')] }))
    const d = m.sections[0].blocks[0]
    if (d.t !== 'branch') throw new Error('expected a branch')
    expect(d.join).toBe('M')
    expect(d.cases[1]).toMatchObject({ port: 'false', blocks: [], continues: 'M' })
  })

  it('reads a Loop: body from the loop port, the page carries on from done', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Cron'), n('L', 'Loop', { batchSize: 1 }), n('X'), n('Y'), n('After')],
        connections: [c('T', 'L'), c('L', 'X', 'loop'), c('X', 'Y'), c('Y', 'L'), c('L', 'After', 'done')],
      }),
    )
    expect(m.sections.map((s) => s.title)).toEqual(['L', 'Finish'])
    const l = m.sections[0].blocks[0]
    if (l.t !== 'loop') throw new Error('expected a loop')
    expect(l.forEach).toBe(true)
    expect(l.kind).toBe('each')
    expect(l.exits).toEqual([])
    expect(kinds(l.body)).toEqual(['step', 'step'])
    expect(m.numbers).toMatchObject({ T: '1', L: '2', X: '2.1', Y: '2.2', After: '3' })
  })

  it('a loop with a fixed count is a repeat, not a for-each', () => {
    const m = buildPageModel(toPageWorkflow({ nodes: [n('T', 'Webhook'), n('L', 'Loop', { maxIterations: 3 }), n('X')], connections: [c('T', 'L'), c('L', 'X', 'loop'), c('X', 'L')] }))
    const l = m.sections[0].blocks[0]
    expect(l.t === 'loop' && l.forEach).toBe(false)
    expect(l.t === 'loop' && l.kind).toBe('times')
  })

  it('fans out on one port as parallel lanes until they join', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('S'), n('A'), n('B'), n('J', 'Merge'), n('Z')],
        connections: [c('T', 'S'), c('S', 'A'), c('S', 'B'), c('A', 'J'), c('B', 'J', 'main'), c('J', 'Z')],
      }),
    )
    expect(m.sections.map((s) => s.title)).toEqual(['Intake', 'At the same time', 'Finish'])
    const p = m.sections[1].blocks[0]
    if (p.t !== 'parallel') throw new Error('expected parallel')
    expect(p.from).toBe('S')
    expect(p.lanes.map(kinds)).toEqual([['step'], ['step']])
    expect(p.join).toBe('J')
    expect(m.sections[2].nodes).toEqual(['J', 'Z'])
    // the parallel block takes a number of its own; its lanes are 3a, 3b
    expect(m.numbers).toMatchObject({ T: '1', S: '2', [parKey(p)]: '3', A: '3a', B: '3b', J: '4', Z: '5' })
    expect(m.outline.map((o) => [o.num, o.title, o.node, o.dom])).toEqual([
      ['1', 'T', 'T', 'gp-step-t'],
      ['2', 'S', 'S', 'gp-step-s'],
      ['3', 'At the same time', 'A', 'gp-par-s-main'],
      ['4', 'J', 'J', 'gp-step-j'],
      ['5', 'Z', 'Z', 'gp-step-z'],
    ])
    expect(m.top).toMatchObject({ A: '3', B: '3' })
  })

  it('an edge back to a placed step is a go-back line, not a second copy', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('Call'), n('Ok?', 'If'), n('Done')],
        connections: [c('T', 'Call'), c('Call', 'Ok?'), c('Ok?', 'Done', 'true'), c('Ok?', 'Call', 'false')],
      }),
    )
    const br = m.sections.flatMap((s) => s.blocks).find((b) => b.t === 'branch')
    if (!br || br.t !== 'branch') throw new Error('expected a branch')
    expect(br.cases[1].blocks).toEqual([{ t: 'goto', target: 'Call', from: 'Ok?' }])
    expect(m.order.filter((x) => x === 'Call')).toHaveLength(1)
  })

  it('puts the ErrorTrigger chain in On failure, other triggers and loose steps in their own sections', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('Hook', 'Webhook'), n('A'), n('Nightly', 'Cron'), n('Oops', 'ErrorTrigger'), n('Page someone', 'SendEmail'), n('Orphan', 'Code')],
        connections: [c('Hook', 'A'), c('Nightly', 'A'), c('Oops', 'Page someone')],
      }),
    )
    expect(m.trigger).toBe('Hook')
    expect(m.sections.map((s) => [s.title, s.kind])).toEqual([
      ['Steps', 'steps'],
      ['Also starts: Nightly', 'trigger'],
      ['On failure', 'failure'],
      ['Not connected yet', 'unconnected'],
    ])
    expect(m.sections[1].blocks).toEqual([{ t: 'step', node: 'Nightly' }, { t: 'goto', target: 'A', from: 'Nightly' }])
    expect(m.numbers['Page someone']).toBe('!')
    expect(m.numbers).toMatchObject({ Hook: '1', A: '2', Nightly: '3', Orphan: '4' })
    expect(m.outline.filter((o) => o.node).map((o) => o.node)).toEqual(['Hook', 'A', 'Nightly', 'Oops', 'Page someone', 'Orphan'])
    // extra sections get a heading in the outline; the main chain does not
    expect(m.outline.filter((o) => !o.node).map((o) => o.title)).toEqual(['Also starts: Nightly', 'On failure', 'Not connected yet'])
  })

  it('an error output makes a branch with an "on error" case', () => {
    const m = buildPageModel(toPageWorkflow({ nodes: [n('T', 'Webhook'), n('Q', 'Postgres'), n('Ok'), n('Bad')], connections: [c('T', 'Q'), c('Q', 'Ok'), c('Q', 'Bad', 'error')] }))
    const br = m.sections[0].blocks[0]
    if (br.t !== 'branch') throw new Error('expected a branch')
    expect(br.cases.map((x) => x.label)).toEqual(['then', 'on error'])
  })

  it('an empty workflow has no sections and no trigger', () => {
    const m = buildPageModel(toPageWorkflow({ nodes: [], connections: [] }))
    expect(m.trigger).toBeUndefined()
    expect(m.sections).toEqual([])
  })

  it('ignores connections to nodes that do not exist', () => {
    const m = buildPageModel(toPageWorkflow({ nodes: [n('T', 'Webhook'), n('A')], connections: [c('T', 'A'), c('A', 'Ghost')] }))
    expect(m.order).toEqual(['T', 'A'])
  })

  it('numbers a choice inside a case a, b and a third level i, ii', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('D', 'If'), n('A'), n('E', 'If'), n('B'), n('F', 'If'), n('G'), n('H'), n('C'), n('Z')],
        connections: [
          c('T', 'D'),
          c('D', 'A', 'true'),
          c('A', 'E'),
          c('E', 'B', 'true'),
          c('B', 'F'),
          c('F', 'G', 'true'),
          c('F', 'H', 'false'),
          c('E', 'C', 'false'),
          c('D', 'Z', 'false'),
        ],
      }),
    )
    expect(m.numbers).toMatchObject({ T: '1', D: '2', A: '2.1', E: '2.2', B: 'a', F: 'b', G: 'i', H: 'i', C: 'a', Z: '2.1' })
    expect(m.top).toMatchObject({ G: '2', C: '2' })
    expect(m.ancestors.G).toEqual(['sec-d', 'D:true', 'E:true', 'F:true'])
  })

  it('reads a Poll as a repeat-until: the re-check body, carry on from done, timeout as an exit case', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('Wait for it', 'Poll', { condition: '{{ $json.ready }}', interval: 2, timeout: 30 }), n('Check'), n('Gave up', 'StopAndError'), n('After')],
        connections: [c('T', 'Wait for it'), c('Wait for it', 'Check', 'loop'), c('Check', 'Wait for it'), c('Wait for it', 'After', 'done'), c('Wait for it', 'Gave up', 'timeout')],
      }),
    )
    const l = m.sections[0].blocks[0]
    if (l.t !== 'loop') throw new Error('expected a loop')
    expect(l.kind).toBe('until')
    expect(l.forEach).toBe(false)
    expect(kinds(l.body)).toEqual(['step'])
    expect(l.exits.map((x) => [x.key, x.label, kinds(x.blocks), x.continues])).toEqual([['Wait for it:timeout', 'timeout', ['step'], undefined]])
    expect(m.numbers).toMatchObject({ 'Wait for it': '2', Check: '2.1', 'Gave up': '2.2', After: '3' })
    expect(m.ancestors['Gave up']).toEqual(['sec-wait-for-it', 'Wait for it:timeout'])
  })

  it('a Poll whose timeout path rejoins the done path says where it continues', () => {
    const m = buildPageModel(
      toPageWorkflow({
        nodes: [n('T', 'Webhook'), n('P', 'Poll'), n('Check'), n('Note'), n('After')],
        connections: [c('T', 'P'), c('P', 'Check', 'loop'), c('Check', 'P'), c('P', 'After', 'done'), c('P', 'Note', 'timeout'), c('Note', 'After')],
      }),
    )
    const l = m.sections[0].blocks[0]
    if (l.t !== 'loop') throw new Error('expected a loop')
    expect(l.exits[0].blocks.map((b) => b.t === 'step' && b.node)).toEqual(['Note'])
    expect(m.order.filter((x) => x === 'After')).toHaveLength(1)
    expect(m.numbers.After).toBe('3')
  })

  it('keeps the engine retry policy on each node', () => {
    const wf = toPageWorkflow({ nodes: [{ name: 'Q', type: 'Postgres', parameters: {}, retry_on_fail: 3, retry_delay: 300000, on_error: 'continue', continue_on_fail: true }], connections: [] })
    expect(wf.nodes[0]).toMatchObject({ retry_on_fail: 3, retry_delay: 300000, on_error: 'continue', continue_on_fail: true })
  })
})
