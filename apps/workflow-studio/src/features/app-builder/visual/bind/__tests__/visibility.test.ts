import { describe, expect, it } from 'vitest'
import { appRoles, buildVisible, dataConds, readVisible } from '../visibility'
import { keyAt } from '../scope'
import { after, ctxFor, FILES } from './fixture'

const RUNS = 'views/Runs.tsx'

describe('readVisible', () => {
  it('reads an existing state wrapper, and plain children as always', () => {
    expect(readVisible(ctxFor(FILES, RUNS, 'p', 'Panel'))).toMatchObject({ ok: true, current: { kind: 'state', name: 'open', negate: false } })
    expect(readVisible(ctxFor(FILES, RUNS, 'p', 'Hello'))).toMatchObject({ ok: true, wrapper: null, current: { kind: 'always' } })
  })

  it('refuses the root of a component', () => {
    const r = readVisible(ctxFor(FILES, RUNS, 'div'))
    expect(r.ok === false && r.reason).toMatch(/top element of its component/)
  })

  it('calls conditions it did not write custom', () => {
    const files = { ...FILES, [RUNS]: FILES[RUNS].replace('{open && <p>Panel</p>}', '{open && count > 2 && <p>Panel</p>}') }
    expect(readVisible(ctxFor(files, RUNS, 'p', 'Panel'))).toMatchObject({ ok: true, current: { kind: 'custom', src: 'open && count > 2' } })
  })

  it('offers has-data conditions and the app’s roles', () => {
    expect(dataConds(ctxFor(FILES, RUNS, 'p', 'Hello'))).toEqual([{ label: 'runs has loaded', expr: '!!runs.data' }])
    expect(dataConds(ctxFor(FILES, 'views/Expenses.tsx', 'button'))).toEqual([{ label: 'rows has rows', expr: 'rows.length > 0' }])
    expect(appRoles(FILES)).toEqual(['approver', 'staff', 'manager', 'admin'])
  })
})

describe('buildVisible', () => {
  it('wraps an element in a role check, adding the viewer helper once', () => {
    const ctx = ctxFor(FILES, RUNS, 'p', 'Hello')
    const b = buildVisible(ctx, readVisible(ctx), { kind: 'role', role: 'manager' })
    const out = after(b)
    expect(out).toContain(`      {viewerHas('manager') && <p>Hello</p>}\n`)
    expect(out).toContain(`import { api } from '../api/client'\n\n/** Whether the viewer has a role`)
    expect(out.match(/const viewerHas = /g)).toHaveLength(1)
    if (b.ok) expect(keyAt(out, b.focus!)).toBeTruthy()
    // a second role check reuses the helper
    const files = { ...FILES, [RUNS]: out }
    const ctx2 = ctxFor(files, RUNS, 'p', 'Panel')
    const out2 = after(buildVisible(ctx2, readVisible(ctx2), { kind: 'role', role: 'admin' }))
    expect(out2).toContain(`{viewerHas('admin') && <p>Panel</p>}`)
    expect(out2.match(/const viewerHas = /g)).toHaveLength(1)
  })

  it('wraps in a state or has-data condition', () => {
    const ctx = ctxFor(FILES, RUNS, 'p', 'Hello')
    expect(after(buildVisible(ctx, readVisible(ctx), { kind: 'state', name: 'open', negate: true }))).toContain('{!open && <p>Hello</p>}')
    expect(after(buildVisible(ctx, readVisible(ctx), { kind: 'data', expr: '!!runs.data' }))).toContain('{!!runs.data && <p>Hello</p>}')
  })

  it('edits our wrapper’s condition in place, and "always" removes it', () => {
    const ctx = ctxFor(FILES, RUNS, 'p', 'Panel')
    expect(after(buildVisible(ctx, readVisible(ctx), { kind: 'state', name: 'open', negate: true }))).toContain('      {!open && <p>Panel</p>}\n')
    const out = after(buildVisible(ctx, readVisible(ctx), { kind: 'always' }))
    expect(out).toContain('      <p>Panel</p>\n')
    expect(out).toBe(FILES[RUNS].replace('{open && <p>Panel</p>}', '<p>Panel</p>'))
  })

  it('refuses to change a custom condition', () => {
    const files = { ...FILES, [RUNS]: FILES[RUNS].replace('{open && <p>Panel</p>}', '{open && count > 2 && <p>Panel</p>}') }
    const ctx = ctxFor(files, RUNS, 'p', 'Panel')
    expect(buildVisible(ctx, readVisible(ctx), { kind: 'always' })).toMatchObject({ ok: false })
  })
})
