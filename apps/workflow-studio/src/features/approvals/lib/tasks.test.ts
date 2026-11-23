import { describe, expect, it } from 'vitest'
import {
  decisionBody, describeOutcome, describeTaskState, flattenPages, nextCursor, nodeDataFromBindings, stripFormDataPrefix,
  validateApproverForm, validateDecision, visibleContext, type DecisionInput,
} from './tasks'
import type { ApprovalFieldSpec, DecideResult, TaskDetail, TaskPage, TaskSummary } from '@/features/interfaces/lib/types'

const sum = (id: string): TaskSummary => ({
  id, workflow_id: 'w', workflow_name: 'W', execution_id: 'e', node_name: 'Manager review', title: id, status: 'open',
  assignees: [], due_at: null, created_at: '2026-10-18T10:00:00Z', decided_by: null, decided_at: null,
})
const page = (ids: string[], next: string | null): TaskPage => ({ items: ids.map(sum), next_cursor: next })

describe('keyset inbox merge', () => {
  it('concatenates pages without dupes or gaps', () => {
    const rows = flattenPages([page(['a', 'b', 'c'], 'c1'), page(['c', 'd'], 'c2'), page(['e'], null)])
    expect(rows.map((r) => r.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
  })
  it('stops when there is no cursor', () => {
    expect(nextCursor(page(['a'], 'abc'))).toBe('abc')
    expect(nextCursor(page(['a'], null))).toBeUndefined()
    expect(nextCursor(undefined)).toBeUndefined()
    expect(flattenPages(undefined)).toEqual([])
  })
})

const detail = (p: Partial<TaskDetail>): TaskDetail => ({
  ...sum('t1'), context: {}, form: [], decision: null, screen: null, input: {}, allow_comment: true,
  require_comment_on_reject: false, on_timeout: 'timed_out', run_status: 'waiting', ...p,
})

describe('describeOutcome', () => {
  it('decided: names the approver', () => {
    const r: DecideResult = { outcome: 'decided', task: detail({ status: 'approved', decided_by: 'Dana', decided_at: '2026-10-18T14:02:00' }) }
    const v = describeOutcome(r)
    expect(v.text).toMatch(/^Approved by Dana at \d\d:\d\d$/)
    expect(v.tone).toBe('ok')
    expect(v.outcome).toBe('decided')
  })
  it('already_decided: says who won', () => {
    const r: DecideResult = { outcome: 'already_decided', task: detail({ status: 'approved', decided_by: 'Dana', decided_at: '2026-10-18T14:02:00' }) }
    expect(describeOutcome(r).text).toMatch(/^Already approved by Dana at \d\d:\d\d$/)
    const rej: DecideResult = { outcome: 'already_decided', task: detail({ status: 'rejected', decided_by: 'Lee', decided_at: '2026-10-18T09:05:00' }) }
    expect(describeOutcome(rej).text).toMatch(/^Already rejected by Lee at /)
  })
  it('already_closed: timed out or cancelled', () => {
    const r: DecideResult = { outcome: 'already_closed', task: detail({ status: 'timed_out' }) }
    expect(describeOutcome(r).text).toBe('This task is already closed (timed out)')
    expect(describeOutcome({ outcome: 'already_closed', task: detail({ status: 'cancelled' }) }).outcome).toBe('already_closed')
  })
  it('a task opened after the fact reads from its own fields', () => {
    expect(describeTaskState(detail({}))).toBeNull()
    expect(describeTaskState(detail({ status: 'rejected', decided_by: 'Lee', decided_at: '2026-10-18T09:05:00' }))?.text).toMatch(/^Rejected by Lee at /)
    expect(describeTaskState(detail({ status: 'timed_out', decided_by: 'system:timeout' }))?.text).toBe('Timed out')
  })
})

const form: ApprovalFieldSpec[] = [
  { name: 'note', label: 'Note', type: 'textarea', required: true },
  { name: 'level', type: 'select', options: ['low', { value: 'high', label: 'High' }] },
  { name: 'qty', type: 'number' },
  { name: 'ack', type: 'checkbox', required: true },
]

describe('approver form validation', () => {
  it('flags required, option, number and checkbox problems', () => {
    expect(validateApproverForm(form, { ack: false, level: 'mid', qty: 'x' })).toEqual({
      note: 'This field is required', level: 'Choose one of the options', qty: 'Enter a number', ack: 'This must be checked',
    })
    expect(validateApproverForm(form, { note: 'ok', ack: true, level: 'high', qty: '3' })).toEqual({})
  })
})

const base: DecisionInput = { decision: 'approved', decidedBy: '  Dana ', comment: '', formData: { note: 'ok', ack: true, qty: '3' }, form, allowComment: true, requireCommentOnReject: false }

describe('decision rules', () => {
  it('needs a typed name (1-200 chars)', () => {
    expect(validateDecision({ ...base, decidedBy: '  ' }).decided_by).toBeTruthy()
    expect(validateDecision({ ...base, decidedBy: 'x'.repeat(201) }).decided_by).toBeTruthy()
    expect(validateDecision(base)).toEqual({})
  })
  it('reject may skip the form but must comment when required', () => {
    const rej: DecisionInput = { ...base, decision: 'rejected', formData: {}, requireCommentOnReject: true }
    expect(validateDecision(rej)).toEqual({ comment: 'A comment is required to reject' })
    expect(validateDecision({ ...rej, comment: 'no' })).toEqual({})
  })
  it('builds a typed, trimmed body', () => {
    expect(decisionBody({ ...base, comment: ' fine ' })).toEqual({
      decision: 'approved', comment: 'fine', form_data: { note: 'ok', ack: true, qty: 3 }, decided_by: 'Dana',
    })
    expect(decisionBody({ ...base, allowComment: false, comment: 'x', formData: { ack: true } }).comment).toBeUndefined()
  })
  it('strips form_data. prefixes from server errors', () => {
    expect(stripFormDataPrefix({ 'form_data.note': 'bad', comment: 'x' })).toEqual({ note: 'bad', comment: 'x' })
  })
})

describe('task screens', () => {
  it('rebuilds the node root from _bindings (nested or flat)', () => {
    expect(nodeDataFromBindings({ Classify: { summary: 'S' } })).toEqual({ Classify: { summary: 'S' } })
    expect(nodeDataFromBindings({ 'node.Classify.summary': 'S', 'Lookup history.orders': [1] })).toEqual({
      Classify: { summary: 'S' }, 'Lookup history': { orders: [1] },
    })
    expect(nodeDataFromBindings({ '__proto__.x': 1 })).toEqual({})
    expect(nodeDataFromBindings(null)).toEqual({})
  })
  it('hides underscore context keys', () => {
    expect(visibleContext({ Summary: 'a', _bindings: {}, _truncated: true })).toEqual([['Summary', 'a']])
  })
})
