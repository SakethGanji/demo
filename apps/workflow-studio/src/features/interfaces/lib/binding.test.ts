import { describe, expect, it } from 'vitest'
import { display, interpolate, parseBinding, resolveBinding, safeHref, screenHas, screenIds, splitPath, taskNodeOfScreen, type ScreenData } from './binding'

const data: ScreenData = {
  input: { amount: 742, order_id: 'A-12345', email: 'a@b.co', nested: { list: ['x', 'y'] } },
  node: { Classify: { summary: 'Looks legit' }, 'Lookup history': { orders: [{ date: 'May', text: 'Order A' }] } },
  task: { status: 'open', title: 'Refund' },
  result: { status: 'ok' },
  run: { id: 'exe_1' },
}

describe('parseBinding', () => {
  it('accepts only the five roots', () => {
    expect(parseBinding('input.amount')).toEqual({ root: 'input', path: ['amount'] })
    expect(parseBinding('window.location')).toBeNull()
    expect(parseBinding('globalThis.process')).toBeNull()
    expect(parseBinding('')).toBeNull()
    expect(parseBinding(42)).toBeNull()
  })
  it('splits node names with spaces', () => {
    expect(parseBinding('node.Lookup history.orders')).toEqual({ root: 'node', node: 'Lookup history', path: ['orders'] })
    expect(parseBinding('node')).toBeNull()
  })
  it('handles bracket indexes', () => {
    expect(splitPath('a.b[0].c')).toEqual(['a', 'b', '0', 'c'])
  })
})

describe('resolveBinding', () => {
  it('resolves under each root', () => {
    expect(resolveBinding('input.amount', data)).toBe(742)
    expect(resolveBinding('node.Classify.summary', data)).toBe('Looks legit')
    expect(resolveBinding('node.Lookup history.orders[0].text', data)).toBe('Order A')
    expect(resolveBinding('task.status', data)).toBe('open')
    expect(resolveBinding('result.status', data)).toBe('ok')
    expect(resolveBinding('run.id', data)).toBe('exe_1')
    expect(resolveBinding('input.nested.list.1', data)).toBe('y')
  })
  it('never reaches the prototype or unknown nodes', () => {
    expect(resolveBinding('input.__proto__', data)).toBeUndefined()
    expect(resolveBinding('input.constructor', data)).toBeUndefined()
    expect(resolveBinding('input.toString', data)).toBeUndefined()
    expect(resolveBinding('input.nested.list.length', data)).toBeUndefined()
    expect(resolveBinding('node.Nope.x', data)).toBeUndefined()
    expect(resolveBinding('node.constructor.name', data)).toBeUndefined()
  })
  it('missing data is undefined, not a crash', () => {
    expect(resolveBinding('result.status', {})).toBeUndefined()
  })
})

describe('interpolate', () => {
  it('fills value, rooted paths and item scope', () => {
    expect(interpolate('Order #{{value}} · {{input.email}}', data, { value: 'A-12345' })).toBe('Order #A-12345 · a@b.co')
    expect(interpolate('{{date}} · {{text}}', data, { scope: { date: 'May', text: 'Order A' } })).toBe('May · Order A')
  })
  it('unknown paths become empty and never evaluate', () => {
    expect(interpolate('x{{alert(1)}}y{{window.name}}z', data)).toBe('xyz')
    expect(interpolate('<b>{{input.order_id}}</b>', data)).toBe('<b>A-12345</b>')
  })
})

describe('safeHref', () => {
  it('only https', () => {
    expect(safeHref('https://carrier.example/t/1')).toBe('https://carrier.example/t/1')
    expect(safeHref('http://carrier.example')).toBeNull()
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('data:text/html,hi')).toBeNull()
    expect(safeHref('//evil.example')).toBeNull()
    expect(safeHref(5)).toBeNull()
  })
})

describe('display and screen helpers', () => {
  it('stringifies objects and truncates', () => {
    expect(display({ a: 1 })).toBe('{"a":1}')
    expect(display(undefined)).toBe('')
    expect(display('x'.repeat(10), 4)).toBe('xxxx…')
  })
  it('screenIds orders start, tasks, result', () => {
    const spec = { v: 1 as const, components: [] }
    expect(screenIds({ result: spec, 'task:B': spec, start: spec, 'task:A': spec })).toEqual(['start', 'task:A', 'task:B', 'result'])
    expect(screenIds(undefined)).toEqual(['start'])
    expect(taskNodeOfScreen('task:Manager review')).toBe('Manager review')
    expect(taskNodeOfScreen('start')).toBeNull()
  })
  it('screenHas tolerates junk', () => {
    expect(screenHas({ v: 1, components: [{ type: 'actions' }] }, 'actions')).toBe(true)
    expect(screenHas(null, 'actions')).toBe(false)
    expect(screenHas({ v: 1, components: null as never }, 'form')).toBe(false)
  })
})
