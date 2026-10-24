import { describe, expect, it } from 'vitest'
import { bindNew, describeSource, nameForOp, propValueFor, readData, rebindOp, rebindResource, tableFields, trace } from '../data'
import { findAttr } from '../../splice'
import { apiOps, keyAt, resources, shapeFromDoc, type ApiOp } from '../scope'
import { after, ctxFor, FILES } from './fixture'

const ops = apiOps(FILES)
const op = (name: string) => ops.find((o) => o.name === name)!

describe('apiOps', () => {
  it('reads every op with method, path, params and argument order', () => {
    expect(ops.map((o) => o.name)).toEqual(['listRuns', 'listExpenses', 'cancelRun'])
    expect(op('listRuns')).toMatchObject({ method: 'GET', path: '/runs', params: [], args: ['query'] })
    expect(op('cancelRun')).toMatchObject({ method: 'POST', path: '/runs/{id}/cancel', params: ['id'], body: true, args: ['params', 'body', 'query'] })
  })

  it('takes fields from the response type, else from the doc', () => {
    expect(op('listRuns')).toMatchObject({ listKey: 'rows', fields: ['id', 'status', 'started_at'], top: ['rows', 'total'] })
    expect(op('listExpenses')).toMatchObject({ listKey: 'items', fields: ['id', 'title', 'amount'], top: ['items', 'total'] })
  })

  it('parses doc shapes: arrays, wrapped lists and plain objects', () => {
    expect(shapeFromDoc('Returns [{id, name}]')).toEqual({ listKey: '', fields: ['id', 'name'], top: [] })
    expect(shapeFromDoc('Lists. Returns {results: [{a, b: {c}}], next}')).toMatchObject({ listKey: 'results', fields: ['a', 'b'] })
    expect(shapeFromDoc('Returns {count, avg}')).toEqual({ listKey: null, fields: ['count', 'avg'], top: ['count', 'avg'] })
    expect(shapeFromDoc('No shape here')).toBeNull()
  })
})

describe('trace', () => {
  it('follows a prop through ?? and members to a kit hook', () => {
    const r = readData(ctxFor(FILES, 'views/Runs.tsx', 'DataTable'))
    expect(r.kind).toBe('api')
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(r.trace.source).toMatchObject({ kind: 'hook', variable: 'runs', hook: 'usePoll', op: 'listRuns', refetch: 'runs.refresh()' })
    expect(r.trace.source.path.map((p) => p.name)).toEqual(['data', 'rows'])
    expect(r.trace.source.uses).toBeGreaterThan(1)
  })

  it('follows state filled by api.op().then(setX) inside an effect', () => {
    const r = readData(ctxFor(FILES, 'views/Expenses.tsx', 'DataTable'))
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(r.trace.source).toMatchObject({ kind: 'state', variable: 'rows', op: 'listExpenses', refetch: 'reload()' })
  })

  it('refuses props, conditions and fixed values with a reason', () => {
    const inner = ctxFor(FILES, 'views/Expenses.tsx', 'DataTable', 'items')
    const attr = findAttr(inner.el, 'rows')
    if (attr?.value?.type !== 'JSXExpressionContainer') throw new Error('no rows')
    const t1 = trace(inner, attr.value.expression)
    expect(t1).toMatchObject({ ok: false })
    expect(!t1.ok && t1.reason).toMatch(/passed in/)
    const cond = readData(ctxFor(FILES, 'views/Expenses.tsx', 'DataTable', 'rows.length'))
    expect(cond.kind === 'api' && cond.trace && !cond.trace.ok && cond.trace.reason).toMatch(/condition/)
  })

  it('ignores non-kit and non-data elements', () => {
    expect(readData(ctxFor(FILES, 'views/Runs.tsx', 'p', 'Hello')).kind).toBe('none')
    expect(readData(ctxFor(FILES, 'views/Runs.tsx', 'Button', 'New')).kind).toBe('none')
  })
})

describe('rebindOp', () => {
  it('renames the op in the hook and swaps the list key', () => {
    const ctx = ctxFor(FILES, 'views/Runs.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    const b = rebindOp(ctx, r.trace.source, ops, 'listExpenses')
    const out = after(b)
    expect(out).toContain('const runs = usePoll(() => api.listExpenses(), 5000)')
    expect(out).toContain('<DataTable rows={runs.data?.items ?? []}')
    // nothing else moved
    expect(out.replace('listExpenses', 'listRuns').replace('data?.items', 'data?.rows')).toBe(FILES['views/Runs.tsx'])
    if (b.ok) expect(keyAt(out, b.focus!)).toBe(keyAt(FILES['views/Runs.tsx'], ctx.el.start!))
  })

  it('swaps the key read off the response in a state setter', () => {
    const ctx = ctxFor(FILES, 'views/Expenses.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    const out = after(rebindOp(ctx, r.trace.source, ops, 'listRuns'))
    expect(out).toContain('api.listRuns().then((res) => setRows(res.rows))')
  })

  it('refuses ops that need path parameters', () => {
    const ctx = ctxFor(FILES, 'views/Runs.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(rebindOp(ctx, r.trace.source, ops, 'cancelRun')).toMatchObject({ ok: false })
  })
})

describe('old-style state + effect + append (expense app)', () => {
  const PAGED = `import { useEffect, useState } from 'react'
import { Card, DataTable } from '../kit'
import { api } from '../api/client'

interface Expense { id: string; title: string }

export default function Mine() {
  const [rows, setRows] = useState<Expense[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  useEffect(() => {
    api.listExpenses().then((res) => {
      setRows((res.items as Expense[]) || [])
    })
  }, [])
  async function loadMore() {
    const res = await api.listExpenses()
    setRows((r) => [...r, ...((res.items as Expense[]) || [])])
  }
  async function remove(id: string) {
    await api.cancelRun({ id })
    setRows((r) => r.filter((x) => x.id !== id))
  }
  return (
    <Card>
      {rows.length === 0 ? <p>None</p> : <DataTable<Expense> rows={rows} columns={[]} />}
      <button onClick={loadMore}>More {cursor}</button>
      <button onClick={() => remove('1')}>{rows.length}</button>
    </Card>
  )
}
`
  const files = { ...FILES, 'views/Mine.tsx': PAGED }

  it('traces a generic DataTable in a ternary to api.op() via state', () => {
    const r = readData(ctxFor(files, 'views/Mine.tsx', 'DataTable'))
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(r.trace.source).toMatchObject({ kind: 'state', variable: 'rows', op: 'listExpenses' })
    expect(r.trace.source.fixed).toBeUndefined()
    expect(describeSource(r.trace.source)).toMatch(/^api\.listExpenses\(\)\.items via state → rows$/)
  })

  it('re-points the effect load and the append together, but not a local filter', () => {
    const ctx = ctxFor(files, 'views/Mine.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    const out = after(rebindOp(ctx, r.trace.source, ops, 'listRuns'))
    expect(out).not.toContain('listExpenses')
    expect(out).toContain('setRows((res.rows as Expense[]) || [])')
    expect(out).toContain('...((res.rows as Expense[]) || [])')
    expect(out).toContain('api.cancelRun({ id })')
  })

  it('shows but refuses to re-point state filled from two different ops', () => {
    const mixed = { ...FILES, 'views/Mine.tsx': PAGED.replace('const res = await api.listExpenses()', 'const res = await api.listRuns()') }
    const ctx = ctxFor(mixed, 'views/Mine.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(r.trace.source.fixed).toMatch(/loadMore/)
    expect(rebindOp(ctx, r.trace.source, ops, 'listRuns')).toMatchObject({ ok: false })
  })
})

describe('bindNew', () => {
  it('declares a query in the file’s own pattern after the hooks and binds the prop', () => {
    const ctx = ctxFor(FILES, 'views/Runs.tsx', 'Stat')
    const out = after(bindNew(ctx, 'value', ops, 'listExpenses'))
    expect(out).toContain("const [open, setOpen] = useState(false)\n  const expenses = usePoll(() => api.listExpenses(), 5000)\n")
    expect(out).toContain(`<Stat label="Runs" value={expenses.data?.total ?? '—'} />`)
  })

  it('names the query after the op and picks list keys', () => {
    expect(nameForOp('listExpenses')).toBe('expenses')
    expect(nameForOp('getStats')).toBe('stats')
    expect(nameForOp('summary')).toBe('summary')
    const o = { ...ops[0], listKey: 'rows' } as ApiOp
    expect(propValueFor('DataTable', 'runs', o)).toBe('{runs.data?.rows ?? []}')
    expect(propValueFor('DataTable', 'runs', { ...o, listKey: '' })).toBe('{runs.data ?? []}')
  })
})

describe('resources and columns', () => {
  it('lists resources with their fields', () => {
    expect(resources(FILES).map((r) => [r.name, r.fields])).toEqual([
      ['expenses', ['title', 'amount', 'status']],
      ['vendors', ['name', 'country']],
    ])
  })

  it('re-points a string resource prop', () => {
    const ctx = ctxFor(FILES, 'views/Expenses.tsx', 'ListDetailLayout')
    const r = readData(ctx)
    if (r.kind !== 'resource') throw new Error('not a resource block')
    expect(r).toMatchObject({ current: 'expenses', quoted: true, columnsProp: 'columns' })
    const out = after(rebindResource(ctx, r, resources(FILES)[1]))
    expect(out).toContain(`<ListDetailLayout resource="vendors"`)
  })

  it('knows a table’s fields from the op and the row type', () => {
    const ctx = ctxFor(FILES, 'views/Expenses.tsx', 'DataTable')
    const r = readData(ctx)
    if (r.kind !== 'api' || !r.trace?.ok) throw new Error('not traced')
    expect(tableFields(ctx, r.trace.source, ops)).toEqual(['id', 'title', 'amount', 'owner'])
  })
})
