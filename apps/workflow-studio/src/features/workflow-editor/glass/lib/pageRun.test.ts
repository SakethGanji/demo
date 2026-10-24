import { describe, expect, it } from 'vitest'
import type { ApiExecutionDetail } from '@/shared/lib/api'
import { buildPageModel, toPageWorkflow, type Block, type PageWorkflow } from './pageModel'
import { runPath } from './runPath'
import { caseCount, endChip, foldText, itemOutcome, itemPairs, loopItems, loopRun, passFor, pollSession, reachNote, sampleText, stepForItem, stoppedAt, tryTime } from './pageRun'

const n = (name: string, type = 'Set', parameters: Record<string, unknown> = {}) => ({ name, type, parameters })
const c = (s: string, t: string, port = 'main') => ({ source_node: s, source_output: port, target_node: t, target_input: 'main' })

function exec(status: string, metrics: Record<string, Record<string, unknown>>, data: Record<string, unknown[]> = {}, errors: Array<{ node_name: string; error: string }> = []) {
  const node_data: Record<string, Array<{ json: unknown }>> = {}
  for (const k of Object.keys(metrics)) node_data[k] = (data[k] ?? [{}]).map((j) => ({ json: j }))
  return { id: 'run1', status, start_time: '2026-10-24T10:00:00Z', end_time: '2026-10-24T10:00:02Z', node_metrics: metrics, node_data, errors } as unknown as ApiExecutionDetail
}
const pathFor = (wf: PageWorkflow, e: ApiExecutionDetail) => runPath(wf.connections, wf.nodes.map((x) => x.name), e)

/** A switch with three cases and a Stop on one, then two steps after. */
const wf = toPageWorkflow({
  nodes: [
    n('T', 'Webhook'),
    n('Rail', 'Switch', { rules: [{ output: 0, field: 'rail', value: 'fed' }, { output: 1, field: 'rail', value: 'ach' }] }),
    n('Fed'),
    n('Ach'),
    n('Halt', 'StopAndError'),
    n('Hold'),
    n('Log'),
  ],
  connections: [c('T', 'Rail'), c('Rail', 'Fed', 'output0'), c('Rail', 'Ach', 'output1'), c('Rail', 'Hold', 'fallback'), c('Hold', 'Halt'), c('Fed', 'Log'), c('Ach', 'Log')],
})
const model = buildPageModel(wf)
const branch = model.sections.flatMap((s) => s.blocks).find((b) => b.t === 'branch') as Extract<Block, { t: 'branch' }>

describe('pageRun', () => {
  it('counts items per case from the case first step, and 0 for an untaken case', () => {
    const p = pathFor(wf, exec('success', { T: { executionOrder: 1 }, Rail: { activeOutputs: ['output0', 'output1'], executionOrder: 2 }, Fed: { inputItemCount: 4, executionOrder: 3 }, Ach: { inputItemCount: 6, executionOrder: 4 }, Log: { executionOrder: 5 } }))
    expect(branch.cases.map((x) => caseCount(p, model, wf.connections, 'Rail', x))).toEqual([4, 6, 0])
    expect(caseCount(null, model, wf.connections, 'Rail', branch.cases[0])).toBeUndefined()
  })

  it('says a case is not taken, or that a step was not reached because the run stopped', () => {
    const p = pathFor(wf, exec('failed', { T: { executionOrder: 1 }, Rail: { activeOutputs: ['fallback'], executionOrder: 2 }, Hold: { executionOrder: 3 } }, {}, [{ node_name: 'Halt', error: 'held' }]))
    expect(stoppedAt(p)).toBe('Halt')
    expect(reachNote(p, model, 'Fed')).toEqual({ kind: 'not-taken' })
    expect(reachNote(p, model, 'Log')).toEqual({ kind: 'not-reached', at: 'Halt', num: '2' })
    expect(reachNote(p, model, 'Hold')).toBeNull()
    expect(endChip(p, model)).toEqual({ tone: 'bad', text: 'Stops at step 2' })
  })

  it('the end chip names the last top-level step of a run that finished', () => {
    const p = pathFor(wf, exec('success', { T: { executionOrder: 1 }, Rail: { activeOutputs: ['output0'], executionOrder: 2 }, Fed: { executionOrder: 3 }, Log: { executionOrder: 4 } }))
    expect(endChip(p, model)).toEqual({ tone: 'ok', text: 'Ends at step 3' })
    expect(endChip(null, model)).toBeNull()
  })

  it('folds read how many steps, a choice inside, or ending with Stop', () => {
    expect(branch.cases.map((x) => foldText(x.blocks, model))).toEqual(['1 step', '1 step', '2 steps, ends with Stop'])
  })

  it('a loop lists its items from the done output; counts inside a loop are not given', () => {
    const lw = toPageWorkflow({
      nodes: [n('T', 'Webhook'), n('L', 'Loop'), n('Pick', 'If'), n('A'), n('B')],
      connections: [c('T', 'L'), c('L', 'Pick', 'loop'), c('Pick', 'A', 'true'), c('Pick', 'B', 'false'), c('A', 'L'), c('B', 'L')],
    })
    const lm = buildPageModel(lw)
    const p = pathFor(lw, exec('success', { T: {}, L: {}, Pick: { activeOutputs: ['true'] }, A: { inputItemCount: 1 } }, { L: [{ _loop_completed: true, total_items: 2, items: [{ id: 1 }, { id: 2 }] }] }))
    expect(loopItems(p, 'L')).toEqual([{ id: 1 }, { id: 2 }])
    const br = lm.sections.flatMap((s) => s.blocks).flatMap((b) => (b.t === 'loop' ? b.body : [])).find((b) => b.t === 'branch') as Extract<Block, { t: 'branch' }>
    expect(caseCount(p, lm, lw.connections, 'Pick', br.cases[0])).toBeUndefined()
  })

  it('with the per-item loop history: outcomes per item, the step for each item, and exact counts per case', () => {
    const lw = toPageWorkflow({
      nodes: [n('T', 'Webhook'), n('L', 'Loop'), n('Pick', 'If'), n('A'), n('B')],
      connections: [c('T', 'L'), c('L', 'Pick', 'loop'), c('Pick', 'A', 'true'), c('Pick', 'B', 'false'), c('A', 'L'), c('B', 'L')],
    })
    const lm = buildPageModel(lw)
    const e = exec('success', { T: {}, L: {}, Pick: { activeOutputs: ['true'] }, A: { inputItemCount: 1 } }, { L: [{ _loop_completed: true, total_items: 3, items: [{ id: 1 }, { id: 2 }, { id: 3 }] }] })
    e.loop_runs = {
      L: {
        totalItems: 3,
        batchSize: 1,
        passes: [
          { index: 0, status: 'ok', nodes: { Pick: { status: 'ok', ports: ['true'], portCounts: { true: 1 }, items: [{ id: 1 }] }, A: { status: 'ok', items: [{ id: 1, a: 1 }] } } },
          { index: 1, status: 'skipped', failedAt: 'Pick', error: 'boom', nodes: { Pick: { status: 'skipped', error: 'boom' } } },
          { index: 2, status: 'ok', nodes: { Pick: { status: 'ok', ports: ['false'], portCounts: { false: 1 } }, B: { status: 'ok', items: [{ id: 3 }] } } },
        ],
      },
    }
    const p = pathFor(lw, e)
    const run = loopRun(p, 'L')
    expect([0, 1, 2, 3].map((k) => itemOutcome(run, k))).toEqual(['ok', 'skipped', 'ok', 'none'])
    expect(passFor(run, 2)?.index).toBe(2)
    expect(stepForItem(p, lm, 'A', 0)?.step?.items).toEqual([{ id: 1, a: 1 }])
    expect(stepForItem(p, lm, 'A', 1)?.step).toBeUndefined()
    const br = lm.sections.flatMap((s) => s.blocks).flatMap((b) => (b.t === 'loop' ? b.body : [])).find((b) => b.t === 'branch') as Extract<Block, { t: 'branch' }>
    expect(br.cases.map((x) => caseCount(p, lm, lw.connections, 'Pick', x))).toEqual([1, 1])
    // a truncated history cannot give exact counts
    e.loop_runs.L.truncated = true
    expect(caseCount(pathFor(lw, e), lm, lw.connections, 'Pick', br.cases[0])).toBeUndefined()
  })

  it('poll tries: the session of the item being followed, and the clock time of a try', () => {
    const e = exec('success', { U: {} })
    e.poll_tries = { U: { sessions: [{ tries: [{ n: 1, at: '2026-10-24T10:00:01.5', result: 'pending' }, { n: 2, at: '2026-10-24T10:00:02', result: 'met' }], outcome: 'met', attempts: 2 }] } }
    const p = runPath([], ['U'], e)
    expect(pollSession(p, 'U')?.tries.map((t) => t.result)).toEqual(['pending', 'met'])
    expect(pollSession(p, 'X')).toBeUndefined()
    expect(tryTime('2026-10-24T10:00:01.5')).toBe('10:00:01')
  })

  it('sample text shows the item count and the first item fields', () => {
    expect(sampleText([{ order_id: 'ord_31', amount: 900, _x: 1, nested: { a: 1 }, tags: [1, 2] }])).toBe('order_id ord_31 · amount 900 · tags 2 items')
    expect(sampleText([{ a: 1 }, { a: 2 }])).toBe('2 items · a 1')
    expect(sampleText([])).toBe('No items')
    expect(itemPairs('plain')).toEqual([['', 'plain']])
  })
})
