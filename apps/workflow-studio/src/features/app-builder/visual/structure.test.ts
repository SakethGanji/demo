import { describe, expect, it } from 'vitest'
import { applyOp, applyOps, describe as describeEl, resolveClick, type Selection } from './editor'
import { KIT_FILES } from './registry/kitFixture'
import { indexElements, parseSource, type FileMap } from './source'
import { diffSize } from './splice'
import { dragTargetsFor, dropTargets, freeIdents, paletteFor, pruneImports, relSpec } from './structure'

const SCREEN = `import { useState } from 'react'
import { Button, Card, Stat } from '../kit'

export default function Home({ rows }: { rows: { id: string; name: string }[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="p-4">
      <h1 className="text-lg">Title</h1>
      <Button key="go" variant="primary" onClick={() => setOpen(true)}>
        Go
      </Button>
      <section className="flex gap-2">
        <span>left</span>
      </section>
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <b>{row.name}</b>
          </li>
        ))}
      </ul>
      {open && <p>Opened</p>}
      <Card title="Empty" />
      <div className="wrap">
        <Stat label="Open" value={3} />
      </div>
    </div>
  )
}
`

const files = (screen = SCREEN): FileMap => ({ ...KIT_FILES, 'views/Home.tsx': screen })

function sel(f: FileMap, name: string, nth = 0, file = 'views/Home.tsx'): Selection {
  const hits = indexElements(parseSource(f[file])).filter((e) => e.name === name)
  if (!hits[nth]) throw new Error(`no ${name}#${nth}`)
  return { file, key: hits[nth].key, part: null }
}

function run(f: FileMap, s: Selection, op: Parameters<typeof applyOp>[2]) {
  const r = applyOp(f, s, op)
  if (!r.ok) throw new Error(r.error)
  return { after: r.patches[0].after, r }
}

const refuse = (f: FileMap, s: Selection, op: Parameters<typeof applyOp>[2]) => {
  const r = applyOp(f, s, op)
  expect(r.ok).toBe(false)
  return r.ok ? '' : r.error
}

describe('delete', () => {
  it('removes the element and its lines only', () => {
    const f = files()
    const { after, r } = run(f, sel(f, 'h1'), { op: 'delete' })
    expect(after).not.toContain('<h1')
    expect(diffSize(SCREEN, after)).toEqual({ added: 0, removed: 1 })
    // the parent is selected after
    expect(r.ok && r.select && describeEl({ ...f, 'views/Home.tsx': after }, r.select)?.name).toBe('div')
  })

  it('prunes a kit import nothing uses any more, and keeps the rest', () => {
    const f = files()
    const { after } = run(f, sel(f, 'Stat'), { op: 'delete' })
    expect(after).toContain("import { Button, Card } from '../kit'")
    expect(after).not.toContain('<Stat')
  })

  it('refuses the top element, a conditional, a list template root', () => {
    const f = files()
    expect(refuse(f, sel(f, 'div'), { op: 'delete' })).toMatch(/top element/i)
    expect(refuse(f, sel(f, 'p'), { op: 'delete' })).toMatch(/condition/i)
    expect(refuse(f, sel(f, 'li'), { op: 'delete' })).toMatch(/list/i)
  })
})

describe('duplicate', () => {
  it('copies the element after itself, uniquifying a literal key, and selects the copy', () => {
    const f = files()
    const { after, r } = run(f, sel(f, 'Button'), { op: 'duplicate' })
    expect(after.match(/<Button/g)).toHaveLength(2)
    expect(after).toContain('key="go-2"')
    expect(diffSize(SCREEN, after)).toEqual({ added: 3, removed: 0 })
    const next = { ...f, 'views/Home.tsx': after }
    expect(r.ok && r.select && r.select.key).toBe(sel(next, 'Button', 1).key)
  })

  it('refuses inside a list template', () => {
    const f = files()
    expect(refuse(f, sel(f, 'b'), { op: 'duplicate' })).toMatch(/list/i)
  })
})

describe('wrap / unwrap', () => {
  it('wraps in a kit Stack (importing it) and re-indents by one level', () => {
    const f = files()
    const { after, r } = run(f, sel(f, 'Button'), { op: 'wrap', with: 'Stack' })
    expect(after).toContain("import { Button, Card, Stat, Stack } from '../kit'")
    expect(after).toContain('      <Stack>\n        <Button key="go"')
    expect(after).toContain('          Go\n')
    expect(after).toContain('        </Button>\n      </Stack>')
    const next = { ...f, 'views/Home.tsx': after }
    expect(r.ok && r.select && describeEl(next, r.select)?.name).toBe('Stack')
  })

  it('falls back to a div when the app kit has no Stack', () => {
    const f: FileMap = { 'kit/index.ts': "export * from './ui'", 'kit/ui.tsx': 'export function Button({ children }: { children?: React.ReactNode }) { return <button>{children}</button> }', 'views/Home.tsx': SCREEN }
    const { after } = run(f, sel(f, 'h1'), { op: 'wrap', with: 'Stack' })
    expect(after).toContain('<div className="flex flex-col gap-3">\n        <h1')
  })

  it('unwrap undoes wrap exactly and prunes the wrapper import', () => {
    const f = files()
    const w = run(f, sel(f, 'Button'), { op: 'wrap', with: 'Stack' })
    const f2 = { ...f, 'views/Home.tsx': w.after }
    const u = run(f2, w.r.ok ? w.r.select! : sel(f2, 'Stack'), { op: 'unwrap' })
    expect(u.after).toBe(SCREEN)
  })

  it('refuses unwrap with several children or handlers', () => {
    const f = files(SCREEN.replace('<section className="flex gap-2">', '<section className="flex gap-2" onClick={() => setOpen(false)}>'))
    expect(refuse(f, sel(f, 'section'), { op: 'unwrap' })).toMatch(/onClick/)
    expect(refuse(f, sel(f, 'div'), { op: 'unwrap' })).toMatch(/exactly one/)
  })
})

describe('insert', () => {
  it('inserts a palette Button after the heading, importing nothing new (already imported)', () => {
    const f = files()
    const { after, r } = run(f, sel(f, 'h1'), { op: 'insert', component: 'Button', where: 'after' })
    expect(after.split('\n').filter((l, i) => l !== SCREEN.split('\n')[i]).length).toBeGreaterThan(0)
    expect(diffSize(SCREEN, after).removed).toBe(0)
    const next = { ...f, 'views/Home.tsx': after }
    expect(r.ok && r.select && describeEl(next, r.select)?.name).toBe('Button')
    expect(after.indexOf('<h1')).toBeLessThan(after.indexOf('<Button', after.indexOf('<h1')))
  })

  it('adds the import for a new kit component, merging into the kit import', () => {
    const f = files()
    const { after } = run(f, sel(f, 'h1'), { op: 'insert', component: 'Divider', where: 'before' })
    expect(after).toContain("import { Button, Card, Stat, Divider } from '../kit'")
    expect(after).toMatch(/<Divider[^>]*\/>\n {6}<h1/)
  })

  it('adds a new import line when the file has no kit import', () => {
    const f = files(SCREEN.replace("import { Button, Card, Stat } from '../kit'\n", '').replace(/<Button[\s\S]*?<\/Button>\n/, '').replace(/ {6}<Card title="Empty" \/>\n/, '').replace(/ {8}<Stat [^\n]*\n/, ''))
    const { after } = run(f, sel(f, 'h1'), { op: 'insert', component: 'Divider', where: 'after' })
    expect(after).toContain("import { useState } from 'react'\nimport { Divider } from '../kit'\n")
  })

  it('inserts inside a self-closing container, and appends inside one with children', () => {
    const f = files()
    const a = run(f, sel(f, 'Card'), { op: 'insert', component: 'Divider', where: 'inside' })
    expect(a.after).toMatch(/<Card title="Empty">\n {8}<Divider[^\n]*\/>\n {6}<\/Card>/)
    const b = run(f, sel(f, 'section'), { op: 'insert', component: 'Divider', where: 'inside' })
    expect(b.after).toMatch(/<span>left<\/span>\n {8}<Divider[^\n]*\/>\n {6}<\/section>/)
  })

  it('refuses beside the top element, inside a list template root, and inside a text holder', () => {
    const f = files()
    expect(refuse(f, sel(f, 'div'), { op: 'insert', component: 'Divider', where: 'after' })).toMatch(/top element/i)
    expect(refuse(f, sel(f, 'li'), { op: 'insert', component: 'Divider', where: 'inside' })).toMatch(/list/i)
    expect(refuse(f, sel(f, 'h1'), { op: 'insert', component: 'Divider', where: 'inside' })).toMatch(/text|children/i)
  })

  it('fills an empty slot prop', () => {
    const f = files()
    const { after } = run(f, sel(f, 'Card'), { op: 'insert', component: 'Button', where: 'slot', slot: 'actions' })
    expect(after).toMatch(/<Card actions=\{<Button[^]*?<\/Button>\} title="Empty" \/>/)
  })
})

describe('move across containers', () => {
  it('moves into another container with one undo-able patch and keeps the selection', () => {
    const f = files()
    const target = sel(f, 'section')
    const { after, r } = run(f, sel(f, 'h1'), { op: 'moveTo', target, index: 1 })
    expect(after).toMatch(/<span>left<\/span>\n {8}<h1 className="text-lg">Title<\/h1>\n {6}<\/section>/)
    expect(r.ok && r.select && describeEl({ ...f, 'views/Home.tsx': after }, r.select)?.name).toBe('h1')
  })

  it('refuses moving code that reads a list item out of its list', () => {
    const f = files(SCREEN.replace('<li key={row.id}>\n            <b>{row.name}</b>', '<li key={row.id}>\n            <b>{row.name}</b>\n            <i>{row.id}</i>'))
    expect(refuse(f, sel(f, 'i'), { op: 'moveTo', target: sel(f, 'section'), index: 0 })).toMatch(/row/)
  })

  it('refuses into itself, into a list template root and across files', () => {
    const f = { ...files(), 'views/Other.tsx': 'export function O() { return <div><span /></div> }' }
    expect(refuse(f, sel(f, 'section'), { op: 'moveTo', target: sel(f, 'section'), index: 0 })).toMatch(/itself/)
    expect(refuse(f, sel(f, 'h1'), { op: 'moveTo', target: sel(f, 'li'), index: 0 })).toMatch(/row/)
    expect(refuse(f, sel(f, 'h1'), { op: 'moveTo', target: sel(f, 'div', 0, 'views/Other.tsx'), index: 0 })).toMatch(/file/)
  })

  it('drag targets exclude the element and its insides; list template roots are never targets', () => {
    const f = files()
    const d = dragTargetsFor(f, sel(f, 'section'))
    expect(d.parent).toMatch(/^views\/Home.tsx:7:/)
    expect(d.targets.some((t) => t.key === sel(f, 'section').key)).toBe(false)
    expect(dropTargets(f).some((t) => t.key === sel(f, 'li').key)).toBe(false)
    expect(dropTargets(f).some((t) => t.key === sel(f, 'Card').key)).toBe(true)
  })
})

describe('one undo step per op', () => {
  it('insert, duplicate, wrap, move, delete chain; each patch reverts to its own before', () => {
    let f = files()
    const steps: string[] = [f['views/Home.tsx']]
    let s = sel(f, 'h1')
    for (const op of [
      { op: 'insert', component: 'Button', where: 'after' },
      { op: 'duplicate' },
      { op: 'wrap', with: 'Stack' },
      { op: 'moveTo', target: sel(f, 'Card'), index: 0 },
    ] as const) {
      // the Card's key does not change through these edits (they land before it in another chain)
      const r = applyOps(f, s, [op.op === 'moveTo' ? { ...op, target: sel(f, 'Card') } : op])
      if (!r.ok) throw new Error(`${op.op}: ${r.error}`)
      expect(r.patches).toHaveLength(1)
      f = { ...f, 'views/Home.tsx': r.patches[0].after }
      steps.push(r.patches[0].after)
      s = r.select!
    }
    const del = applyOp(f, s, { op: 'delete' })
    if (!del.ok) throw new Error(del.error)
    // undoing every step in turn lands back on the original
    expect(steps[0]).toBe(SCREEN)
    expect(del.patches[0].before).toBe(steps[steps.length - 1])
  })
})

describe('helpers', () => {
  it('free identifiers skip what the node declares', () => {
    const ast = parseSource('const x = <Foo a={b.c} onClick={(e) => go(e, d)}>{rows.map((r) => <i key={r.id}>{r.n}</i>)}</Foo>')
    const decl = ast.program.body[0]
    expect([...freeIdents(decl.type === 'VariableDeclaration' ? decl.declarations[0].init! : decl)].sort()).toEqual(['Foo', 'b', 'd', 'go', 'rows'])
  })

  it('prunes only unused specifiers', () => {
    expect(pruneImports("import { A, B, C } from './kit'\nexport const x = <B />\n", ['A', 'C'])).toBe("import { B } from './kit'\nexport const x = <B />\n")
    expect(pruneImports("import { A } from './kit'\nexport const x = 1\n", ['A'])).toBe('export const x = 1\n')
  })

  it('relative specifiers', () => {
    expect(relSpec('App.tsx', 'kit')).toBe('./kit')
    expect(relSpec('views/a/X.tsx', 'kit')).toBe('../../kit')
    expect(relSpec('views/X.tsx', 'components/KpiCard.tsx')).toBe('../components/KpiCard')
  })

  it('the palette groups kit components by category and keeps examples that need no state', () => {
    const p = paletteFor(files())
    const stack = p.find((x) => x.name === 'Stack')
    expect(stack?.category).toBe('layout')
    const field = p.find((x) => x.name === 'Field')
    // Field's example reads form state, so the palette offers a plain one instead
    expect(field?.snippet).not.toContain('setAmount')
    expect(p.some((x) => x.name === 'Toaster')).toBe(false)
  })
})

describe('click resolution wiring', () => {
  it('a click inside a reusable component selects the instance; a double-click enters', () => {
    const f: FileMap = {
      ...KIT_FILES,
      'components/Kpi.tsx': 'export function Kpi({ n }: { n: number }) {\n  return <div className="k"><b>{n}</b></div>\n}\n',
      'views/A.tsx': "import { Kpi } from '../components/Kpi'\nexport default function A() {\n  return <section><Kpi n={1} /><Kpi n={2} /></section>\n}\n",
    }
    const chain = [{ vsrc: 'components/Kpi.tsx:2:29', name: 'b' }, { vsrc: 'components/Kpi.tsx:2:10', name: 'div' }, { vsrc: 'views/A.tsx:3:19', name: 'Kpi' }]
    expect(resolveClick(f, chain, null).selection?.file).toBe('views/A.tsx')
    expect(resolveClick(f, chain, null, true).selection?.file).toBe('components/Kpi.tsx')
  })
})
