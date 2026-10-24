import { describe, expect, it } from 'vitest'
import { applyOp, applyOps, askPrompt, describe as describeEl, selectionFor, vsrcFor, type Selection } from './editor'
import { KIT_FILES } from './registry/kitFixture'
import { diffSize } from './splice'
import { elementAt, parseSource, indexElements } from './source'

const SCREEN = `import { useState } from 'react'
import { Page, DataTable, Tabs, Button, StatTiles, type Column } from '../kit'
import { STATUS_TONE } from '../lib/tones'

type Row = { id: number; amount: number; status: string; owner: string }

const COLUMNS: Column<Row>[] = [
  { key: 'id', header: 'ID' },
  { key: 'amount', header: 'Amt', format: 'money', align: 'right' },
  { key: 'owner', header: 'Owner', render: (r) => <b>{r.owner}</b> },
  { key: 'status', header: 'Status', format: 'badge', toneMap: STATUS_TONE },
]

export default function Expenses({ rows }: { rows: Row[] }) {
  const [tab, setTab] = useState('open')
  return (
    <Page title="Expenses" subtitle={\`\${rows.length} claims\`}>
      <Tabs tabs={[{ id: 'open', label: 'Open' }, { id: 'done', label: 'Done' }]} value={tab} onChange={setTab} />
      <StatTiles
        resource="expenses"
        tiles={[
          { label: 'Pending', filter: { status: 'pending' }, tone: 'warn' },
          { label: 'Approved', filter: { status: 'approved' } },
        ]}
      />
      <DataTable rows={rows} columns={COLUMNS} rowKey={(r) => String(r.id)} emptyText="No claims yet" />
      <Button variant="primary" onClick={() => setTab('done')}>
        Approve all
      </Button>
      <div className="p-2">custom</div>
    </Page>
  )
}
`
const TONES = `export const STATUS_TONE = { pending: 'amber', approved: 'green' } as const\n`
// The props panel reads the kit's own source (the registry), so the kit files ride along as in a real app.
const FILES = { ...KIT_FILES, 'views/Expenses.tsx': SCREEN, 'lib/tones.ts': TONES }

function sel(name: string, n = 0): Selection {
  const e = indexElements(parseSource(SCREEN)).filter((x) => x.name === name)[n]
  return { file: 'views/Expenses.tsx', key: e.key }
}

describe('selection mapping', () => {
  it('maps a jsxDEV tag (babel column + 1) to exactly one element and back', () => {
    const ast = parseSource(SCREEN)
    for (const e of indexElements(ast)) {
      const tag = `views/Expenses.tsx:${e.node.loc!.start.line}:${e.node.loc!.start.column + 1}`
      const s = selectionFor(FILES, tag)
      expect(s?.key).toBe(e.key)
      expect(vsrcFor(FILES, s!)).toBe(tag)
    }
    expect(selectionFor(FILES, 'views/Expenses.tsx:1:1')).toBeNull()
    expect(selectionFor(FILES, 'views/Nope.tsx:5:5')).toBeNull()
  })
  it('gives structural keys that survive line shifts', () => {
    const before = sel('Button')
    const shifted = SCREEN.replace("type Row", "// a comment\n// another\ntype Row")
    const e = indexElements(parseSource(shifted)).find((x) => x.key === before.key)
    expect(e?.name).toBe('Button')
    expect(e?.node.loc!.start.line).toBe(indexElements(parseSource(SCREEN)).find((x) => x.key === before.key)!.node.loc!.start.line + 2)
    expect(before.key).toBe('Expenses/Page>Button#0')
  })
  it('refines to a list item from a kit part marker', () => {
    const ast = parseSource(SCREEN)
    const dt = indexElements(ast).find((x) => x.name === 'DataTable')!
    const tag = `views/Expenses.tsx:${dt.node.loc!.start.line}:${dt.node.loc!.start.column + 1}`
    expect(selectionFor(FILES, tag, 'columns.3')?.part).toEqual({ prop: 'columns', index: 3 })
    expect(selectionFor(FILES, tag, 'bogus.1')?.part).toBeNull()
    expect(elementAt(ast, dt.node.loc!.start.line, dt.node.loc!.start.column + 1)?.name).toBe('DataTable')
  })
})

describe('describe', () => {
  it('reads kit props through consts and imports', () => {
    const v = describeEl(FILES, sel('DataTable'))!
    expect(v.kit).toBe(true)
    const cols = v.props.find((p) => p.name === 'columns')!
    expect(cols.state).toBe('literal')
    expect(cols.via).toEqual({ name: 'COLUMNS', file: 'views/Expenses.tsx', scope: 'module' })
    expect(cols.items!.map((i) => i.label)).toEqual(['ID', 'Amt', 'Owner', 'Status'])
    expect(cols.items![2].fields!.render.state).toBe('custom')
    expect(cols.items![3].fields!.toneMap.state).toBe('custom')
    expect(v.props.find((p) => p.name === 'rows')!.state).toBe('bound')
    expect(v.props.find((p) => p.name === 'emptyText')!.value).toBe('No claims yet')
    expect(v.props.find((p) => p.name === 'searchPlaceholder')!.state).toBe('unset')
  })
  it('marks computed config as custom and text children as editable', () => {
    const page = describeEl(FILES, sel('Page'))!
    expect(page.props.find((p) => p.name === 'title')!.value).toBe('Expenses')
    expect(page.props.find((p) => p.name === 'subtitle')!.state).toBe('custom')
    const b = describeEl(FILES, sel('Button'))!
    expect(b.texts).toEqual(['Approve all'])
    expect(b.props.find((p) => p.name === 'onClick')!.state).toBe('event')
    const div = describeEl(FILES, sel('div'))!
    expect(div.kit).toBe(false)
    expect(div.texts).toEqual(['custom'])
  })
})

describe('edits', () => {
  const ok = (r: ReturnType<typeof applyOp>) => {
    if (!r.ok) throw new Error(r.error)
    return r
  }
  it('renames, hides, reorders, adds and removes columns in the const', () => {
    const s = sel('DataTable')
    const rename = ok(applyOp(FILES, s, { op: 'listSet', prop: 'columns', index: 1, field: 'header', value: 'Amount' }))
    expect(rename.patches[0].after).toContain("{ key: 'amount', header: 'Amount', format: 'money', align: 'right' }")
    expect(diffSize(SCREEN, rename.patches[0].after)).toEqual({ added: 1, removed: 1 })
    const hide = ok(applyOp(FILES, s, { op: 'listSet', prop: 'columns', index: 0, field: 'hidden', value: true }))
    expect(hide.patches[0].after).toContain("{ key: 'id', header: 'ID', hidden: true }")
    const move = ok(applyOp(FILES, s, { op: 'listMove', prop: 'columns', from: 3, to: 0 }))
    expect(diffSize(SCREEN, move.patches[0].after)).toEqual({ added: 1, removed: 1 })
    const add = ok(applyOp(FILES, s, { op: 'listInsert', prop: 'columns', index: 4, item: { key: 'notes', header: 'Notes' } }))
    expect(diffSize(SCREEN, add.patches[0].after)).toEqual({ added: 1, removed: 0 })
    const rm = ok(applyOp(FILES, s, { op: 'listRemove', prop: 'columns', index: 2 }))
    expect(rm.patches[0].after).not.toContain("'owner'")
  })
  it('edits inline lists, nested maps and literal props', () => {
    const tabs = ok(applyOp(FILES, sel('Tabs'), { op: 'listSet', prop: 'tabs', index: 1, field: 'label', value: 'Closed' }))
    expect(tabs.patches[0].after).toContain("{ id: 'done', label: 'Closed' }")
    const tile = ok(applyOp(FILES, sel('StatTiles'), { op: 'listSet', prop: 'tiles', index: 1, field: 'filter', value: { status: 'paid' } }))
    expect(tile.patches[0].after).toContain("{ label: 'Approved', filter: { status: 'paid' } }")
    const title = ok(applyOp(FILES, sel('Page'), { op: 'setProp', prop: 'title', value: 'Expense claims' }))
    expect(title.patches[0].after).toContain('<Page title="Expense claims"')
    const empty = ok(applyOp(FILES, sel('DataTable'), { op: 'setProp', prop: 'emptyText', value: 'Nothing here' }))
    expect(empty.patches[0].after).toContain('emptyText="Nothing here"')
  })
  it('edits a button label and variant as one transaction', () => {
    const r = ok(applyOps(FILES, sel('Button'), [
      { op: 'setText', index: 0, value: 'Approve selected' },
      { op: 'setProp', prop: 'variant', value: 'success' },
    ]))
    expect(r.patches).toHaveLength(1)
    expect(r.patches[0].after).toContain('<Button variant="success"')
    expect(r.patches[0].after).toContain('        Approve selected\n')
    expect(diffSize(SCREEN, r.patches[0].after)).toEqual({ added: 2, removed: 2 })
  })
  it('skips an op that changes nothing, and refuses a batch that changes nothing', () => {
    const r = ok(applyOps(FILES, sel('Button'), [
      { op: 'setText', index: 0, value: 'Approve selected' },
      { op: 'setText', index: 0, value: 'Approve selected' },
    ]))
    expect(diffSize(SCREEN, r.patches[0].after)).toEqual({ added: 1, removed: 1 })
    const same = applyOps(r.patches.reduce((f, p) => ({ ...f, [p.path]: p.after }), FILES), sel('Button'), [{ op: 'setText', index: 0, value: 'Approve selected' }])
    expect(same.ok).toBe(false)
  })
  it('adds a prop that is not set yet', () => {
    const r = ok(applyOp(FILES, sel('DataTable'), { op: 'setProp', prop: 'searchPlaceholder', value: 'Search claims' }))
    expect(r.patches[0].after).toContain('emptyText="No claims yet" searchPlaceholder="Search claims" />')
  })
  it('edits a const in another file when the value comes from an import', () => {
    const files = { ...FILES, 'views/B.tsx': `import { Tag } from '../kit'\nimport { LABEL } from '../lib/labels'\nexport const B = () => <Card title={LABEL} />\n`, 'lib/labels.ts': `export const LABEL = 'Old'\n` }
    const key = indexElements(parseSource(files['views/B.tsx']))[0].key
    const r = ok(applyOp(files, { file: 'views/B.tsx', key }, { op: 'setProp', prop: 'title', value: 'New' }))
    expect(r.patches[0]).toMatchObject({ path: 'lib/labels.ts', after: `export const LABEL = 'New'\n` })
  })
  it('refuses computed values and stale selections', () => {
    const r = applyOp(FILES, sel('Page'), { op: 'setProp', prop: 'subtitle', value: 'x' })
    expect(r.ok).toBe(false)
    expect(applyOp(FILES, { file: 'views/Expenses.tsx', key: 'Expenses/Nope#0' }, { op: 'unsetProp', prop: 'x' }).ok).toBe(false)
  })
  it('builds a scoped ask-AI prompt', () => {
    const p = askPrompt(describeEl(FILES, sel('DataTable'))!, 'show amounts in euros')
    expect(p).toContain('views/Expenses.tsx lines')
    expect(p).toContain('<DataTable rows={rows}')
    expect(p).toContain('columns (list)')
    expect(p).toContain('show amounts in euros')
  })
})

describe('move among siblings', () => {
  const ok = (r: ReturnType<typeof applyOp>) => {
    if (!r.ok) throw new Error(r.error)
    return r
  }
  const after = (r: ReturnType<typeof applyOp>) => ok(r).patches[0].after

  it('swaps with the previous sibling, keeping indentation, and follows the moved element', () => {
    const v = describeEl(FILES, sel('Button'))!
    expect(v.move).toMatchObject({ ok: true, index: 3, count: 5 })
    const r = ok(applyOp(FILES, sel('Button'), { op: 'move', to: 2 }))
    const out = r.patches[0].after
    expect(out.indexOf('<Button variant')).toBeLessThan(out.indexOf('<DataTable'))
    expect(out).toContain(`      <Button variant="primary" onClick={() => setTab('done')}>\n        Approve all\n      </Button>\n      <DataTable rows={rows}`)
    expect(diffSize(SCREEN, out)).toEqual({ added: 1, removed: 1 }) // the one-line DataTable hops over the Button
    // the selection follows the Button to its new place
    const moved = { ...FILES, 'views/Expenses.tsx': out }
    expect(describeEl(moved, r.select!)?.name).toBe('Button')
    expect(describeEl(moved, r.select!)?.move).toMatchObject({ ok: true, index: 2 })
  })
  it('moves later and to the ends; refuses past the boundaries', () => {
    const later = after(applyOp(FILES, sel('Tabs'), { op: 'move', to: 1 }))
    expect(later.indexOf('<StatTiles')).toBeLessThan(later.indexOf('<Tabs'))
    const last = after(applyOp(FILES, sel('Tabs'), { op: 'move', to: 4 }))
    expect(last).toMatch(/<div className="p-2">custom<\/div>\n {6}<Tabs [^\n]*\/>\n {4}<\/Page>/)
    expect(applyOp(FILES, sel('Tabs'), { op: 'move', to: -1 }).ok).toBe(false)
    expect(applyOp(FILES, sel('div'), { op: 'move', to: 5 }).ok).toBe(false)
    expect(applyOp(FILES, sel('Tabs'), { op: 'move', to: 0 }).ok).toBe(false)
  })
  it('moving there and back restores the file byte for byte', () => {
    const r1 = ok(applyOp(FILES, sel('StatTiles'), { op: 'move', to: 3 }))
    const f1 = { ...FILES, 'views/Expenses.tsx': r1.patches[0].after }
    const back = after(applyOp(f1, r1.select!, { op: 'move', to: 1 }))
    expect(back).toBe(SCREEN)
  })
  it('refuses elements rendered by .map, conditions, inline text and the component root', () => {
    const src = `import { Card, Tag } from '../kit'
export function L({ xs, on }: { xs: string[]; on: boolean }) {
  return (
    <div>
      {xs.map((x) => <Tag key={x}>{x}</Tag>)}
      {on && <Card title="a" />}
      <p>Hello <b>you</b></p>
      <section><span>only</span></section>
    </div>
  )
}
`
    const files = { 'L.tsx': src }
    const key = (name: string) => indexElements(parseSource(src)).find((e) => e.name === name)!.key
    const reason = (name: string) => {
      const m = describeEl(files, { file: 'L.tsx', key: key(name) })!.move
      return m.ok ? null : m.reason
    }
    expect(reason('Tag')).toMatch(/inside a list/i)
    expect(reason('Card')).toMatch(/generated by code/i)
    expect(reason('b')).toMatch(/text/i)
    expect(reason('span')).toMatch(/only child/i)
    expect(reason('div')).toMatch(/top element/i)
    const held = `import { Button, Card } from '../kit'\nexport function H() {\n  const b = <Button>Go</Button>\n  return <div><Card actions={<Button>x</Button>} /><p>a</p></div>\n}\n`
    const heldKeys = indexElements(parseSource(held)).filter((e) => e.name === 'Button').map((e) => e.key)
    const hr = (k: string) => describeEl({ 'H.tsx': held }, { file: 'H.tsx', key: k })!.move
    expect(hr(heldKeys[0])).toMatchObject({ ok: false, reason: expect.stringMatching(/generated by code/i) })
    expect(hr(heldKeys[1])).toMatchObject({ ok: false, reason: expect.stringMatching(/prop/i) })
    expect(applyOp(files, { file: 'L.tsx', key: key('Tag') }, { op: 'move', to: 0 }).ok).toBe(false)
    // the static <p> and <section> can still move around the expression children
    expect(describeEl(files, { file: 'L.tsx', key: key('p') })!.move).toMatchObject({ ok: true, index: 2, count: 4 })
    const out = after(applyOp(files, { file: 'L.tsx', key: key('section') }, { op: 'move', to: 0 }))
    expect(out).toContain(`    <div>\n      <section><span>only</span></section>\n      {xs.map(`)
  })
})
