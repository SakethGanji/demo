import { describe, expect, it } from 'vitest'
import { applyOp, describe as describeEl, resolveClick, type Selection } from '../editor'
import { editorsOf, type PropEditor } from '../manifest'
import { indexElements, parseSource } from '../source'
import { extractComponents, parseDoc } from './extract'
import { KIT_FILES } from './kitFixture'
import { KIT_MANIFEST } from './manifest.fixture'
import { registryFor, usagesOf } from './registry'

/**
 * Where the extractor knows MORE than the old hand-written manifest did. Each entry is the exact extra editor; the rest of
 * every fixture prop must match verbatim.
 */
const UPGRADES: Record<string, (e: PropEditor) => PropEditor> = {
  // TabItem.count exists on the kit type; the old manifest just omitted it
  'Tabs.tabs': (e) => withField(e, 'count', { kind: 'text' }),
  // Column.toneMap (badge colours per value) is a plain record of tone names
  'DataTable.columns': (e) => withField(e, 'toneMap', { kind: 'map' }),
  // RowAction is a typed object list: label/tone/disabled/hidden are editable; onClick keeps adding items to code
  'RowActions.actions': () => ({
    kind: 'list',
    item: {
      kind: 'object', noun: 'action', label: 'label', hideable: true, template: {},
      fields: { label: { kind: 'text' }, tone: { kind: 'enum', options: ['default', 'primary', 'danger'] }, disabled: { kind: 'bool' }, title: { kind: 'text' }, hidden: { kind: 'bool' } },
    },
  }),
}

function withField(e: PropEditor, field: string, ed: PropEditor): PropEditor {
  if (e.kind !== 'list' || e.item.kind !== 'object') throw new Error('not an object list')
  return { ...e, item: { ...e.item, fields: { ...e.item.fields, [field]: ed } } }
}

describe('registry vs the old hand-written manifest', () => {
  const reg = registryFor(KIT_FILES)
  for (const [name, spec] of Object.entries(KIT_MANIFEST)) {
    it(`${name}: same editors`, () => {
      const comp = reg.components.find((c) => c.name === name && c.file === `kit/${spec.source}.tsx`)
      expect(comp, `${name} in kit/${spec.source}.tsx`).toBeTruthy()
      const got = editorsOf(comp!, true)
      for (const [prop, editor] of Object.entries(spec.props)) {
        const want = UPGRADES[`${name}.${prop}`]?.(editor) ?? editor
        expect({ [prop]: got[prop] }).toEqual({ [prop]: want })
      }
    })
  }
  it('still edits list items of an older kit copy without the JSDoc tags (apps carry their own kit/)', () => {
    const old = Object.fromEntries(Object.entries(KIT_FILES).map(([p, s]) => [p, s.replace(/\/\*\*[^*]*@control text[^*]*\*\/\s*/g, '')]))
    expect(old['kit/DataTable.tsx']).not.toContain('@control text')
    const columns = editorsOf(registryFor(old).components.find((c) => c.name === 'DataTable')!, true).columns
    if (columns.kind !== 'list' || columns.item.kind !== 'object') throw new Error('columns')
    expect(columns.item.label).toBe('header')
    expect(columns.item.fields.header).toEqual({ kind: 'text' })
    expect(columns.item.template).toEqual({ key: '$', header: '$' })
  })
  it('documents every exported kit component with a category and an example', () => {
    const undocumented = reg.components.filter((c) => c.file.startsWith('kit/') && (!c.category || !c.example)).map((c) => `${c.file}#${c.name}`)
    expect(undocumented).toEqual([])
  })
})

describe('parseDoc', () => {
  it('reads several tags on one line, free text and a multi-line example', () => {
    expect(parseDoc('* @control select @options a|b|c @group Style ').tags).toEqual({ control: 'select', options: 'a|b|c', group: 'Style' })
    const d = parseDoc('*\n * A card.\n * Second line.\n * @category display\n * @example <Card title="x">\n *   body\n * </Card>\n * @default 3\n ')
    expect(d.description).toBe('A card. Second line.')
    expect(d.tags).toEqual({ category: 'display', example: '<Card title="x">\n  body\n</Card>', default: '3' })
  })
})

const KPI = `import React from 'react'
import { Card } from '../kit'

/** A KPI tile.
 * @category display */
export function KpiCard({ label, value, tone = 'blue', trend, onOpen }: {
  label: string
  value: number
  tone?: 'blue' | 'green'
  /** @control color @group Style */
  trend?: string
  onOpen?: () => void
}) {
  return (
    <Card title={label}>
      <b>{value}</b>
    </Card>
  )
}

export function Note({ heading }: { heading: React.ReactNode }) {
  return <p>{heading}</p>
}
`
const HOME = `import { KpiCard, Note } from '../components/KpiCard'

export default function Home() {
  return (
    <div>
      <KpiCard label="Open" value={3} />
      <KpiCard label="Closed" value={9} tone="green" />
      <Note heading="Hello" />
    </div>
  )
}
`
const APP = { ...KIT_FILES, 'components/KpiCard.tsx': KPI, 'views/Home.tsx': HOME }

function el(file: string, name: string, n = 0) {
  const e = indexElements(parseSource(APP[file as keyof typeof APP])).filter((x) => x.name === name)[n]
  return { sel: { file, key: e.key, part: null } as Selection, vsrc: `${file}:${e.node.loc!.start.line}:${e.node.loc!.start.column + 1}` }
}

describe('app-defined components', () => {
  it('infers controls from the props type and JSDoc', () => {
    const [kpi] = extractComponents(APP, parseSource, ['components/KpiCard.tsx'])
    expect(kpi.name).toBe('KpiCard')
    expect(kpi.category).toBe('display')
    const by = Object.fromEntries(kpi.props.map((p) => [p.name, p]))
    expect(by.label.control).toBe('text')
    expect(by.value.control).toBe('number')
    expect(by.tone).toMatchObject({ control: 'select', options: ['blue', 'green'], default: 'blue' })
    expect(by.trend).toMatchObject({ control: 'color', group: 'Style' })
    expect(by.onOpen.control).toBe('event')
  })

  it('counts usages across the app', () => {
    const kpi = registryFor(APP).components.find((c) => c.name === 'KpiCard')!
    expect(usagesOf(APP, kpi).map((u) => u.file)).toEqual(['views/Home.tsx', 'views/Home.tsx'])
  })

  it('selects the instance and edits its literal props at the usage site only', () => {
    const v = describeEl(APP, el('views/Home.tsx', 'KpiCard', 1).sel)!
    expect(v.instance).toMatchObject({ name: 'KpiCard', usedBy: 2, files: 1 })
    expect(v.props.find((p) => p.name === 'tone')).toMatchObject({ state: 'literal', value: 'green', editor: { kind: 'enum', options: ['blue', 'green'] } })
    const r = applyOp(APP, el('views/Home.tsx', 'KpiCard', 1).sel, { op: 'setProp', prop: 'label', value: 'Done' })
    if (!r.ok) throw new Error(r.error)
    expect(r.patches.map((p) => p.path)).toEqual(['views/Home.tsx'])
    expect(r.patches[0].after).toContain('<KpiCard label="Open" value={3} />')
    expect(r.patches[0].after).toContain('<KpiCard label="Done" value={9} tone="green" />')
  })

  it('a click lands on the instance; a double-click enters the definition', () => {
    const chain = [el('components/KpiCard.tsx', 'b'), el('components/KpiCard.tsx', 'Card'), el('views/Home.tsx', 'KpiCard', 1), el('views/Home.tsx', 'div')]
      .map((x, i) => ({ vsrc: x.vsrc, name: ['b', 'Card', 'KpiCard', 'div'][i] }))
    expect(resolveClick(APP, chain, null).selection).toEqual(el('views/Home.tsx', 'KpiCard', 1).sel)
    expect(resolveClick(APP, chain, null, true).selection).toEqual(el('components/KpiCard.tsx', 'b').sel)
    const inside = describeEl(APP, el('components/KpiCard.tsx', 'Card').sel)!
    expect(inside.inside).toMatchObject({ name: 'KpiCard', usedBy: 2, enter: el('components/KpiCard.tsx', 'Card').sel })
  })

  it('edits a ReactNode prop holding a plain string as text', () => {
    const v = describeEl(APP, el('views/Home.tsx', 'Note').sel)!
    expect(v.props.find((p) => p.name === 'heading')).toMatchObject({ state: 'literal', value: 'Hello', editor: { kind: 'text' } })
  })
})
