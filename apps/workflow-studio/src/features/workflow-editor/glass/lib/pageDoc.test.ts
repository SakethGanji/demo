import { describe, expect, it } from 'vitest'
import { caseHead, docKind, docSentence, failRule, humanMs, isSimulated, refExpr, tokenRef, upstreamOf } from './pageDoc'
import { sentenceText } from './pageSentences'
import type { PageNode } from './pageModel'

const n = (type: string, parameters: Record<string, unknown> = {}, extra: Partial<PageNode> = {}): PageNode => ({ name: type, type, parameters, ...extra })

describe('docKind', () => {
  it('names each step by what it does, in the words of the mockups', () => {
    expect(docKind(n('Webhook'))).toBe('When')
    expect(docKind(n('Cron'))).toBe('When')
    expect(docKind(n('If'))).toBe('Choose one path')
    expect(docKind(n('Switch'))).toBe('Choose one path')
    expect(docKind(n('Loop'))).toBe('For each')
    expect(docKind(n('Loop', { maxIterations: 3 }))).toBe('Repeat')
    expect(docKind(n('Loop'), { inLoop: true })).toBe('For each, inside a for each')
    expect(docKind(n('Poll'))).toBe('Repeat until')
    expect(docKind(n('HttpRequest', { method: 'GET' }))).toBe('Look up')
    expect(docKind(n('HttpRequest', { method: 'POST' }))).toBe('Call an API')
    expect(docKind(n('DataTable', { operation: 'insert' }))).toBe('Add a row')
    expect(docKind(n('DataTable', { operation: 'update' }))).toBe('Update')
    expect(docKind(n('DataTable', { operation: 'list' }))).toBe('Find rows')
    expect(docKind(n('Postgres', { query: 'select 1' }))).toBe('Find rows')
    expect(docKind(n('Postgres', { query: 'update t set a=1' }))).toBe('Update')
    expect(docKind(n('Approval'))).toBe('Ask a person')
    expect(docKind(n('SendEmail'))).toBe('Send an email')
    expect(docKind(n('StopAndError'))).toBe('Stop')
    expect(docKind(n('Code'))).toBe('Run code')
    expect(docKind(n('SomethingNew'))).toBe('Something new')
  })
})

describe('isSimulated', () => {
  it('marks the steps that write to the outside world (a test run mocks them)', () => {
    expect(isSimulated(n('Approval'))).toBe(true)
    expect(isSimulated(n('SendEmail'))).toBe(true)
    expect(isSimulated(n('DataTable', { operation: 'insert' }))).toBe(true)
    expect(isSimulated(n('DataTable', { operation: 'get' }))).toBe(false)
    expect(isSimulated(n('Postgres', { query: 'INSERT INTO x VALUES (1)' }))).toBe(true)
    expect(isSimulated(n('Postgres', { operation: 'transaction' }))).toBe(true)
    expect(isSimulated(n('Postgres', { query: 'select * from x' }))).toBe(false)
    expect(isSimulated(n('HttpRequest', { method: 'POST' }))).toBe(true)
    expect(isSimulated(n('HttpRequest', { method: 'GET' }))).toBe(false)
    expect(isSimulated(n('Set'))).toBe(false)
  })
})

describe('failRule', () => {
  it('is absent for the engine default (stop, no retries)', () => {
    expect(failRule(n('Set'))).toBeNull()
    expect(failRule(n('Set', {}, { on_error: 'stop', retry_on_fail: 0 }))).toBeNull()
  })
  it('reads retries, the wait between them and what happens after', () => {
    expect(failRule(n('Postgres', {}, { retry_on_fail: 3, retry_delay: 300000 }))).toEqual({ retries: 3, delayMs: 300000, then: 'stop' })
    expect(failRule(n('Set', {}, { on_error: 'continue' }))).toEqual({ retries: 0, delayMs: 1000, then: 'continue' })
    expect(failRule(n('Set', {}, { continue_on_fail: true }))).toEqual({ retries: 0, delayMs: 1000, then: 'continue' })
    expect(failRule(n('Set', {}, { on_error: 'error_output' }))).toEqual({ retries: 0, delayMs: 1000, then: 'error' })
  })
  it('humanMs says waits the way people do', () => {
    expect(humanMs(300000)).toBe('5 minutes')
    expect(humanMs(1000)).toBe('1 second')
    expect(humanMs(90000)).toBe('90 seconds')
    expect(humanMs(3600000)).toBe('1 hour')
    expect(humanMs(250)).toBe('250 ms')
  })
})

describe('caseHead', () => {
  it('If cases read If <condition> / Otherwise', () => {
    const d = n('If', { condition: '{{ $json.amount > 500 }}' })
    // intentional: conditions read as plain words (was 'amount > 500')
    expect(caseHead(d, 'true', 'yes')).toEqual({ cs: 'If', text: 'amount is over 500' })
    expect(caseHead(d, 'false', 'no')).toEqual({ cs: 'Otherwise', text: "it isn't" })
  })
  it('Switch cases read Is <value>, a non-equality rule If <test>, the fallback Otherwise', () => {
    const sw = n('Switch', { rules: [{ output: 0, field: 'rail', operation: 'equals', value: 'fed' }, { output: 1, field: 'amount', operation: 'larger', value: 5 }] })
    expect(caseHead(sw, 'output0', 'fed')).toEqual({ cs: 'Is', text: 'fed' })
    expect(caseHead(sw, 'output1', 'larger 5')).toEqual({ cs: 'If', text: 'amount larger 5' })
    expect(caseHead(sw, 'fallback', 'otherwise')).toEqual({ cs: 'Otherwise', text: 'any other value' })
  })
  it('the error port is a red If it fails; approvals read their outcome', () => {
    expect(caseHead(n('Postgres'), 'error', 'on error')).toEqual({ cs: 'If', text: 'it fails', tone: 'bad' })
    expect(caseHead(n('Postgres'), 'main', 'then')).toEqual({ cs: 'If', text: 'it works' })
    expect(caseHead(n('Approval'), 'approved', 'approved')).toEqual({ cs: 'If', text: 'approved' })
    expect(caseHead(n('Approval'), 'timed_out', 'no answer')).toEqual({ cs: 'If', text: 'no answer in time' })
    expect(caseHead(n('Poll'), 'timeout', 'timeout')).toEqual({ cs: 'If', text: 'it times out', tone: 'bad' })
  })
})

describe('docSentence', () => {
  it('a choice reads Go one way depending on…', () => {
    expect(sentenceText(docSentence(n('If', { condition: '{{ $json.amount > 500 }}' }))!)).toBe('Go one way depending on whether amount is over 500.') // intentional: conditions read as plain words
    const sw = n('Switch', { rules: [{ field: '{{ $json.rail }}', value: 'fed' }, { field: '{{ $json.rail }}', value: 'ach' }] })
    expect(sentenceText(docSentence(sw)!)).toBe('Go one way depending on the rail.')
    expect(docSentence(n('Set'))).toBeNull()
  })
})

describe('tokenRef / refExpr', () => {
  it('reads a reference to an earlier step and writes one back', () => {
    expect(tokenRef('{{ $node["Look up"].json.account_no }}')).toEqual({ field: 'account_no', source: 'Look up' })
    expect(tokenRef("{{ $node['A'].json.x }}")).toEqual({ field: 'x', source: 'A' })
    expect(tokenRef('{{ $json.amount }}')).toEqual({ field: 'amount' })
    expect(tokenRef('{{ $json.amount > 5 }}')).toBeNull()
    expect(tokenRef('plain')).toBeNull()
    expect(tokenRef('{{ $json.body.order_id }}')).toEqual({ field: 'body.order_id' })
    expect(refExpr('Look up', 'account_no')).toBe('{{ $node["Look up"].json.account_no }}')
  })
})

describe('upstreamOf', () => {
  it('lists the steps before a node, nearest first', () => {
    const conns = [
      { source_node: 'T', source_output: 'main', target_node: 'A' },
      { source_node: 'A', source_output: 'main', target_node: 'B' },
      { source_node: 'T', source_output: 'main', target_node: 'C' },
      { source_node: 'C', source_output: 'main', target_node: 'B' },
      { source_node: 'B', source_output: 'main', target_node: 'D' },
    ]
    expect(upstreamOf(conns, 'D')).toEqual(['B', 'A', 'C', 'T'])
    expect(upstreamOf(conns, 'T')).toEqual([])
  })
})
