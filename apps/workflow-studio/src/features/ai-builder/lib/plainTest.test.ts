import { describe, expect, it } from 'vitest'
import { categoryWords, describeAssertion, describeMock, flattenInput, parseLike, setAtPath } from './plainTest'
import { apiRefOptions } from './catalog'
import { isErrorIssue, issuesAt } from './specIssues'

describe('describeAssertion', () => {
  it('reads a value check', () => {
    expect(describeAssertion({ node: 'Mark for review', path: 'status', op: 'eq', value: 'review' })).toBe('"status" from "Mark for review" is "review"')
  })
  it('reads a port count', () => {
    expect(describeAssertion({ node: 'Over limit?', port: 'true', op: 'count', value: 1 })).toBe('The "true" path of "Over limit?" gets 1 item')
  })
  it('reads a step outcome', () => {
    expect(describeAssertion({ node: 'Mark auto approved', subject: 'node', op: 'status', value: 'not_run' })).toBe('"Mark auto approved" did not run')
    expect(describeAssertion({ node: 'Reply', op: 'status', value: 'success' })).toBe('"Reply" finished normally')
  })
  it('reads a request check', () => {
    expect(describeAssertion({ node: 'Issue refund', subject: 'request', path: 'body.amount', op: 'gt', value: 500 }))
      .toBe('The request sent by "Issue refund": "body / amount" is greater than 500')
  })
  it('reads exists without a value', () => {
    expect(describeAssertion({ node: 'A', path: 'id', op: 'exists' })).toBe('"id" from "A" is present')
  })
})

describe('describeMock', () => {
  it('describes each kind of fake answer', () => {
    expect(describeMock('Pay', { http: { status: 500 } })).toBe('Pay answers with status 500')
    expect(describeMock('Pay', { error: { message: 'boom' } })).toBe('Pay fails with "boom"')
    expect(describeMock('Pay', { timeout: true })).toBe('Pay times out')
    expect(describeMock('Manager review', { items: [{ approval: { decision: 'rejected' } }], port: 'rejected' })).toBe('Manager review is rejected by the reviewer')
    expect(describeMock('Pay', { items: [{}, {}] })).toBe('Pay returns 2 items')
  })
})

describe('input fields', () => {
  const item = { body: { amount: 900, who: { name: 'Dana' } }, headers: {}, query: {}, method: 'POST' }
  it('flattens readable leaves and hides transport noise', () => {
    expect(flattenInput(item)).toEqual([{ path: 'body.amount', value: 900 }, { path: 'body.who.name', value: 'Dana' }])
  })
  it('an edit keeps the type the field had', () => {
    expect(parseLike('1200', 900)).toBe(1200)
    expect(parseLike('abc', 900)).toBe(900)
    expect(parseLike('true', false)).toBe(true)
    expect(parseLike('hello', 'x')).toBe('hello')
  })
  it('sets a nested value without touching the original', () => {
    const next = setAtPath(item, 'body.amount', 1200)
    expect((next.body as { amount: number }).amount).toBe(1200)
    expect(item.body.amount).toBe(900)
    expect((next.body as { who: unknown }).who).toEqual({ name: 'Dana' })
  })
  it('words for categories', () => {
    expect(categoryWords({ category: 'boundary' })).toBe('Edge of a limit')
    expect(categoryWords({ category: 'ext_error' as never })).toBe('Outside system fails')
  })
})

describe('api refs', () => {
  it('uses the names the engine listed, in the engine order, with a readable label', () => {
    const out = apiRefOptions([
      { api_ref: 'refunds.listRefunds', api: 'Refunds', method: 'GET', path: '/refunds', summary: '' },
      { api_ref: 'refunds_spec_b.issueRefund', api: 'Refunds', method: 'POST', path: '/refunds', summary: 'Issue a refund' },
      { api_ref: 'x.y', api: null, method: null, path: null, summary: '' },
    ])
    expect(out.map((o) => o.ref)).toEqual(['refunds.listRefunds', 'refunds_spec_b.issueRefund', 'x.y'])
    expect(out.map((o) => o.label)).toEqual(['Refunds: GET /refunds', 'Refunds: Issue a refund', 'x.y'])
  })
  it('has no prefix rule of its own any more', async () => {
    const mod = await import('./catalog')
    expect('apiPrefix' in mod).toBe(false)
  })
})

describe('spec issues', () => {
  const issues = [
    { path: 'steps[2].api_ref', code: 'SPEC003', message: 'a' },
    { path: 'steps[20]', code: 'SPEC001', message: 'b', level: 'warning' as const },
    { path: 'tests[0]', code: 'SPEC005', message: 'c' },
  ]
  it('matches a path and what lies under it, but not a longer index', () => {
    expect(issuesAt(issues, 'steps[2]').map((i) => i.code)).toEqual(['SPEC003'])
    expect(issuesAt(issues, 'tests[0]')).toHaveLength(1)
  })
  it('knows errors from warnings', () => {
    expect(isErrorIssue(issues[0])).toBe(true)
    expect(isErrorIssue(issues[1])).toBe(false)
  })
})
