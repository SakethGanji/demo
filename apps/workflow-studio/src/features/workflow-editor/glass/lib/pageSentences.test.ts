import { describe, expect, it } from 'vitest'
import { conditionWords, describeCron, kindFor, opWords, prettyExpr, sentenceFor, sentenceParts, sentenceText } from './pageSentences'

const txt = (type: string, parameters: Record<string, unknown> = {}, notes?: string) => sentenceText(sentenceFor({ name: 'x', type, parameters, notes }))

describe('sentenceFor', () => {
  it('writes the refund seed in words', () => {
    expect(txt('Webhook', { method: 'POST', path: 'refund-triage' })).toBe('When a POST request arrives at /refund-triage.')
    // intentional: conditions read as plain words (was 'If amount > 500:')
    expect(txt('If', { condition: '{{ $json.amount > 500 }}' })).toBe('If amount is over 500:')
    expect(txt('HttpRequest', { method: 'POST', url: 'http://risk.local/score' })).toBe('Call POST http://risk.local/score.')
    expect(txt('DataTable', { table: 'refunds', operation: 'insert' })).toBe('Add a row to the table refunds.')
  })

  it('gives each value a token with its parameter path', () => {
    const s = sentenceFor({ name: 'x', type: 'Postgres', parameters: { connection: 'bank-prod', query: 'select * from wires' } })
    expect(s.tokens).toEqual([
      { path: 'parameters.connection', value: 'bank-prod', label: 'connection' },
      { path: 'parameters.query', value: 'select * from wires', label: 'query' },
    ])
    expect(sentenceText(s)).toBe('Pull from bank-prod: select * from wires')
    expect(txt('Postgres', { connection: 'c', query: 'INSERT INTO t VALUES (1)' })).toMatch(/^Write to c:/)
  })

  it('reads schedules', () => {
    expect(txt('Cron', { mode: 'cron', cronExpression: '0 7 * * 1-5' })).toBe('On a schedule, weekdays at 07:00 (0 7 * * 1-5).')
    expect(txt('Cron', { mode: 'interval', interval: 3600 })).toBe('Every hour.')
    expect(txt('Cron', { mode: 'cron', cronExpression: '5 4 3 2 1' })).toBe('On the schedule 5 4 3 2 1.')
  })

  it('reads people steps, waits, loops and sub-workflows', () => {
    expect(txt('Approval', { assignees: ['mlro@bank.com'], title: 'the wire', due_in: '4h', four_eyes: true })).toBe(
      'Ask mlro@bank.com to approve the wire, and wait up to 4h for an answer. Two different people must approve.',
    )
    expect(txt('Wait', { duration: 2, unit: 'hours' })).toBe('Wait 2 hours.')
    expect(txt('Loop', { maxIterations: 3 })).toBe('Repeat the steps below 3 times.')
    expect(txt('Loop', {})).toBe('For each item, do the steps below.')
    expect(txt('ExecuteWorkflow', { workflowId: 'wf_1', inputs: { a: 1, b: 2 } })).toBe('↳ Run the workflow wf_1 with a, b.')
  })

  it('lists Set fields with their values', () => {
    expect(txt('Set', { fields: [{ name: 'amount', value: '{{ $json.amount }}' }, { name: 'currency', value: 'GBP' }] })).toBe('Set amount to amount and currency to GBP.')
  })

  it('uses the notes as the sentence for custom code', () => {
    expect(txt('Code', { code: 'a\nb' }, 'normalise the beneficiary name')).toBe('Run custom code: normalise the beneficiary name.')
    expect(txt('Code', { code: 'a\nb\n' })).toBe('Run custom code (2 lines).')
  })

  it('an empty value is a token with its label in brackets', () => {
    const s = sentenceFor({ name: 'x', type: 'SendEmail', parameters: {} })
    expect(sentenceText(s)).toBe('Email [to] with the subject [subject].')
    expect(s.tokens.every((t) => t.value === '')).toBe(true)
  })

  it('falls back to the type name for an unknown type', () => {
    expect(txt('MongoQueryTool')).toBe('Run the MongoQueryTool step.')
    expect(txt('Banana')).toBe('Run the Banana step.')
  })
})

describe('sentenceParts', () => {
  it('splits text and tokens in order', () => {
    const parts = sentenceParts(sentenceFor({ name: 'x', type: 'Wait', parameters: { duration: 5, unit: 'minutes' } }))
    expect(parts.map((p) => (typeof p === 'string' ? p : `<${p.token.value}>`)).join('')).toBe('Wait <5> <minutes>.')
  })
})

describe('helpers', () => {
  it('prettyExpr strips template syntax', () => {
    expect(prettyExpr('{{ $json.status === "failed" }}')).toBe('status === "failed"')
    expect(prettyExpr("{{ $node['Score'].json.total }} > 80")).toBe('Score.total > 80')
  })
  it('describeCron knows the common shapes', () => {
    expect(describeCron('*/15 * * * *')).toBe('every 15 minutes')
    expect(describeCron('0 * * * *')).toBe('every hour')
    expect(describeCron('30 15 * * *')).toBe('every day at 15:30')
    expect(describeCron('0 9 * * 1')).toBe('every Monday at 09:00')
    expect(describeCron('nope')).toBeNull()
  })
  it('kindFor gives the mono label', () => {
    expect(kindFor({ name: 'q', type: 'Postgres', parameters: { connection: 'bank-prod' } })).toEqual({ kind: 'query', parts: ['postgres', 'bank-prod'] })
    expect(kindFor({ name: 'h', type: 'HttpRequest', parameters: { method: 'GET' } })).toEqual({ kind: 'http', parts: ['GET'] })
    expect(kindFor({ name: 'u', type: 'SomeThingNew', parameters: {} }).kind).toBe('some thing new')
  })
})

describe('conditions in plain words', () => {
  const w = (c: string) => conditionWords(`{{ ${c} }}`)
  it('reads each comparison', () => {
    expect(w('$node["Normalize"].json.amount < 5000')).toEqual({ text: 'amount is under 5,000', source: 'Normalize' })
    expect(w('$json.amount <= 5000').text).toBe('amount is at most 5,000')
    expect(w('$json.amount > 10000').text).toBe('amount is over 10,000')
    expect(w('$json.amount >= 3').text).toBe('amount is at least 3')
    expect(w("$json.rail == 'fed'").text).toBe('rail is fed')
    expect(w('$json.rail === "fed"').text).toBe('rail is fed')
    expect(w("$json.rail != 'ach'").text).toBe('rail is not ach')
    expect(w("$json.rail !== 'ach'").text).toBe('rail is not ach')
    expect(w('$json.risk_level == true').text).toBe('risk level is true')
  })
  it('reads contains, starts with, ends with and empty', () => {
    expect(w("$json.name.includes('Ltd')").text).toBe('name contains Ltd')
    expect(w("'Ltd' in $json.name").text).toBe('name contains Ltd')
    expect(w("'Ltd' not in $json.name").text).toBe('name does not contain Ltd')
    expect(w("$json.iban.startswith('GB')").text).toBe('iban starts with GB')
    expect(w("$json.iban.endsWith('00')").text).toBe('iban ends with 00')
    expect(w('len($json.notes) == 0').text).toBe('notes is empty')
    expect(w('len($json.notes) > 0').text).toBe('notes is not empty')
    expect(w('$json.done').text).toBe('done is true')
    expect(w('not $json.done').text).toBe('done is false')
  })
  it('joins and / or, and falls back to the expression for anything else', () => {
    expect(w('$json.amount > 100 and $json.rail == "fed"').text).toBe('amount is over 100 and rail is fed')
    expect(w('$json.a > 1 || $json.b < 2').text).toBe('a is over 1 or b is under 2')
    expect(w('true').text).toBe('true')
    expect(w('$json.a * 2 > $json.b').text).toBe('a * 2 > b')
    expect(conditionWords('plain text').text).toBe('plain text')
  })
  it('names each rule operation', () => {
    expect(['equals', 'notEquals', 'contains', 'notContains', 'gt', 'gte', 'lt', 'lte', 'isEmpty', 'isNotEmpty', 'isTrue', 'isFalse', 'regex', 'startsWith', 'endsWith'].map(opWords)).toEqual([
      'is', 'is not', 'contains', 'does not contain', 'is over', 'is at least', 'is under', 'is at most', 'is empty', 'is not empty', 'is true', 'is false', 'matches', 'starts with', 'ends with',
    ])
    expect(txt('If', { field: 'amount', operation: 'lt', value: 5000 })).toBe('If amount is under 5000:')
    expect(txt('If', { field: 'notes', operation: 'isEmpty' })).toBe('If notes is empty:')
  })
  it('keeps the step a condition reads from on its token', () => {
    const s = sentenceFor({ name: 'x', type: 'If', parameters: { condition: '{{ $node["Normalize"].json.amount < 5000 }}' } })
    expect(s.tokens[0]).toEqual({ path: 'parameters.condition', value: 'amount is under 5,000', label: 'condition', source: 'Normalize' })
  })
})

describe('plainer step sentences', () => {
  it('split out and carry on', () => {
    expect(txt('ItemLists', { operation: 'splitOut', arrayField: 'body.wires' })).toBe('Split the list wires into one item per entry.')
    expect(txt('ItemLists', { operation: 'splitOut' })).toBe('Split the list into one item per entry.')
    expect(txt('NoOp')).toBe('Carry on.')
    expect(txt('Poll', { interval: 1, timeout: 30, condition: '{{ $json.attempt >= 3 }}' })).toBe('Check every 1 until attempt is at least 3, for up to 30.')
  })
})
