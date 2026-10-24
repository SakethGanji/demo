import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { editorsOf, newItem, partOf, type PropEditor } from './manifest'
import { KIT_FILES } from './registry/kitFixture'
import { KIT_MANIFEST } from './registry/manifest.fixture'
import { registryFor } from './registry/registry'

/** The props-panel editors the registry derives for a kit component. */
const kitEditors = (name: string) => editorsOf(registryFor(KIT_FILES).components.find((c) => c.name === name && c.file.startsWith('kit/'))!, true)

// The kit ships from the engine; the manifest must name only props (and enum options) the kit really has.
const KIT_DIR = path.resolve(__dirname, '../../../../../workflow-engine/src/services/app_kit')
const kit = (name: string) => fs.readFileSync(path.join(KIT_DIR, `${name}.tsx`), 'utf8')

/** The text of a component's signature: from `export function Name` to the start of its body. */
function signature(src: string, name: string): string {
  const start = src.search(new RegExp(`export function ${name}\\b`))
  if (start < 0) return ''
  const body = src.indexOf(') {', start)
  return src.slice(start, body < 0 ? start + 1500 : body)
}

/** The body of `interface Name { … }` (or `type Name = { … }`). */
function iface(src: string, name: string): string {
  const m = new RegExp(`(?:interface ${name}(?:<[^>]*>)?(?:\\s+extends [^{]+)?|type ${name}(?:<[^>]*>)? =)\\s*\\{`).exec(src)
  if (!m) return ''
  let depth = 0
  for (let i = m.index + m[0].length - 1; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}' && --depth === 0) return src.slice(m.index, i + 1)
  }
  return ''
}

function checkEnum(where: string, text: string, prop: string, e: PropEditor) {
  if (e.kind !== 'enum') return
  for (const o of e.options) expect(text, `${where}.${prop} option '${o}'`).toContain(`'${o}'`)
}

describe('kit manifest', () => {
  const sources = { ui: kit('ui'), DataTable: kit('DataTable'), resource: kit('resource'), review: kit('review') }
  const all = Object.values(sources).join('\n')

  for (const [name, comp] of Object.entries(KIT_MANIFEST)) {
    it(`${name}: every prop exists on the kit component`, () => {
      const src = sources[comp.source]
      const sig = signature(src, name)
      expect(sig, `${name} is exported from kit/${comp.source}`).not.toBe('')
      // props declared via a named props interface (DataTable) or inline in the signature
      const propsType = /\((?:props|\{[^)]*\}): ([A-Z]\w+)(?:<\w+>)?\)?$/.exec(sig)?.[1]
      // Button takes every native button attribute
      const html = /ButtonHTMLAttributes/.test(sig) ? ' onClick: disabled: title:' : ''
      const text = sig + (propsType ? iface(all, propsType) : '') + html
      for (const [prop, editor] of Object.entries(comp.props)) {
        expect(text, `${name}.${prop}`).toMatch(new RegExp(`\\b${prop}\\??:|[{,]\\s*${prop}\\b`))
        checkEnum(name, text + (prop === 'tone' ? iface(all, 'Tone') + all.match(/export type Tone = [^\n]+/)?.[0] : ''), prop, editor)
      }
    })
  }

  it('list item fields exist on their item types', () => {
    const items: Record<string, string> = { columns: iface(all, 'Column') + (all.match(/export type ColumnFormat = [^\n]+/)?.[0] ?? ''), tiles: iface(all, 'TileDef'), tabs: iface(all, 'TabItem'), screens: iface(all, 'ShellScreen') }
    const specs = { columns: KIT_MANIFEST.DataTable.props.columns, tiles: KIT_MANIFEST.StatTiles.props.tiles, tabs: KIT_MANIFEST.Tabs.props.tabs, screens: KIT_MANIFEST.AppShell.props.screens }
    for (const [k, spec] of Object.entries(specs)) {
      if (spec.kind !== 'list' || spec.item.kind !== 'object') throw new Error(k)
      for (const [field, editor] of Object.entries(spec.item.fields)) {
        expect(items[k], `${k}[].${field}`).toMatch(new RegExp(`\\b${field}\\??:`))
        checkEnum(k, items[k], field, editor)
      }
    }
  })

  it('maps part markers to list props', () => {
    expect(partOf(kitEditors('DataTable'), 'DataTable', 'columns.2')).toEqual({ prop: 'columns', index: 2 })
    expect(partOf(kitEditors('AppShell'), 'AppShell', 'screens.0')).toEqual({ prop: 'screens', index: 0 })
    expect(partOf(kitEditors('DashboardLayout'), 'DashboardLayout', 'columns.1')).toEqual({ prop: 'recentColumns', index: 1 })
    expect(partOf(kitEditors('Page'), 'Page', 'columns.1')).toBeNull()
    expect(partOf(kitEditors('DataTable'), 'DataTable', null)).toBeNull()
  })
})

describe('newItem', () => {
  it('fills the template with a free slug', () => {
    const spec = kitEditors('DataTable').columns
    if (spec.kind !== 'list') throw new Error('columns')
    expect(newItem(spec.item, [{ key: 'a', header: 'A' }])).toEqual({ key: 'new_column', header: 'new_column' })
    expect(newItem(spec.item, [{ key: 'new_column', header: 'x' }])).toEqual({ key: 'new_column_2', header: 'new_column_2' })
  })
  it('string items and screens', () => {
    expect(newItem({ kind: 'string', noun: 'field' }, ['new_field'])).toBe('new_field_2')
    const screens = kitEditors('AppShell').screens
    if (screens.kind !== 'list') throw new Error('screens')
    expect(newItem(screens.item, [])).toBeNull()
  })
})
