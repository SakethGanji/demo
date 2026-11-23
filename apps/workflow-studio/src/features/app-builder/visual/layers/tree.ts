/**
 * The Layers tree: one app file's JSX as rows, the way Figma or Retool show a component tree. Built from the same parse the
 * editor uses (source.ts keys, structure.ts placement), so a row IS a selection and every row action is an existing `Op`.
 * Rows say what can't be changed and why (rendered from a list, by a condition, held in a variable); hideable list items
 * (a shell's screens, a table's columns) are rows of their own. Pure: no React, memoized per source version.
 */
import type * as t from '@babel/types'
import { describe, type Op, type Selection } from '../editor'
import { editorsOf } from '../manifest'
import { componentFor, isKitFile } from '../registry/registry'
import { importsOf, indexElements, isKitImport, jsxName, readProps, textChildren, tryParse, walk, type FileMap } from '../source'
import { acceptsChildren, childItems, placeFrom } from '../structure'

export type LayerKind = 'layout' | 'text' | 'button' | 'table' | 'form' | 'chart' | 'image' | 'component' | 'item' | 'group'

export type HideState = { ok: true; hidden: boolean } | { ok: false; reason: string }

export interface LayerRow {
  id: string
  type: 'group' | 'element' | 'item'
  file: string
  /** the element's structural key (an item row: its owner's) */
  key: string
  name: string
  /** a short hint: the element's text or title, an item's label */
  hint: string
  kind: LayerKind
  depth: number
  parent: string | null
  children: string[]
  line: number
  /** the test-id suffix: `file:line` (`:col` when two elements share a line; `.prop.index` for items) */
  tid: string
  vsrc: string | null
  kit: boolean
  /** an app-defined component in another file: where its definition lives */
  defines?: string
  /** why the element can't be moved, deleted or hidden from here (it is still selectable) */
  readOnly?: string
  /** rendered once per row of a list (its template) */
  repeats?: boolean
  /** its place among its JSX parent's children (absent for roots and code-generated elements) */
  /** `container`: the row id of a JSX parent element, or an id of the fragment (<>…</>) it sits in */
  slot?: { container: string; parentKey: string | null; index: number }
  /** new elements can be dropped inside it */
  accepts: boolean
  childCount: number
  hide: HideState
  /** an item row: the list prop and index */
  part?: { prop: string; index: number }
}

export interface LayerTree {
  file: string
  rows: Map<string, LayerRow>
  roots: string[]
  /** `key` → row id, for following the preview selection */
  byKey: Map<string, string>
}

// ── Kinds ───────────────────────────────────────────────────────────────────

const HTML_KIND: Record<string, LayerKind> = {}
for (const tag of ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span', 'strong', 'em', 'b', 'i', 'small', 'label', 'code', 'pre', 'blockquote', 'li', 'dt', 'dd']) HTML_KIND[tag] = 'text'
for (const tag of ['button', 'a']) HTML_KIND[tag] = 'button'
for (const tag of ['table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th']) HTML_KIND[tag] = 'table'
for (const tag of ['form', 'input', 'select', 'textarea', 'option', 'fieldset']) HTML_KIND[tag] = 'form'
for (const tag of ['img', 'svg', 'video', 'canvas', 'picture', 'path']) HTML_KIND[tag] = 'image'

const CATEGORY_KIND: Record<string, LayerKind> = { layout: 'layout', page: 'layout', overlay: 'layout', chart: 'chart', form: 'form', data: 'table', display: 'text', navigation: 'button' }

/** The icon kind of an element: the tag for DOM elements, the name and registry category for components. */
export function kindOf(name: string, category: string | undefined, appComponent: boolean): LayerKind {
  if (/^[a-z]/.test(name)) return HTML_KIND[name] ?? 'layout'
  const base = name.split('.').pop() ?? name
  if (/Chart|Plot|Sparkline|Gauge|Heatmap/.test(base)) return 'chart'
  if (/Table|DataGrid/.test(base)) return 'table'
  if (/Button|Link|Menu|Tabs?$/.test(base)) return 'button'
  if (/Form|Field|Input|Select|Switch|Checkbox|Textarea|Radio|Picker|Upload/.test(base)) return 'form'
  if (/Text|Heading|Title|Label|Badge/.test(base)) return 'text'
  if (appComponent) return 'component'
  return (category ? CATEGORY_KIND[category] : undefined) ?? 'layout'
}

// ── Building ────────────────────────────────────────────────────────────────

const HINT_PROPS = ['title', 'label', 'heading', 'placeholder', 'alt', 'name']

function clip(s: string, n = 32): string {
  const x = s.replace(/\s+/g, ' ').trim()
  return x.length > n ? `${x.slice(0, n - 1)}…` : x
}

/** A hint for the row: the element's text, else a literal title / label prop, else the first `{expression}` child. */
function hintOf(src: string, el: t.JSXElement): string {
  const text = textChildren(el)[0]
  if (text) return clip(text.value)
  for (const a of el.openingElement.attributes) {
    if (a.type !== 'JSXAttribute' || a.name.type !== 'JSXIdentifier' || !HINT_PROPS.includes(a.name.name)) continue
    if (a.value?.type === 'StringLiteral') return clip(a.value.value)
    if (a.value?.type === 'JSXExpressionContainer' && a.value.expression.type === 'StringLiteral') return clip(a.value.expression.value)
  }
  const expr = el.children.find((c): c is t.JSXExpressionContainer => c.type === 'JSXExpressionContainer' && c.expression.type !== 'JSXEmptyExpression')
  return expr ? clip(src.slice(expr.start!, expr.end!), 28) : ''
}

/** Whether `hidden` can be toggled on the element, and its current value. */
function hideOf(files: FileMap, file: string, ast: t.File, el: t.JSXElement, name: string, hasHiddenProp: boolean, readOnly: string | undefined): HideState {
  if (readOnly) return { ok: false, reason: readOnly }
  if (/^[a-z]/.test(name) || hasHiddenProp) {
    const p = readProps(files, file, ast, el).props.find((x) => x.name === 'hidden')
    if (!p) return { ok: true, hidden: false }
    if (p.value.kind === 'boolean' && !p.value.via) return { ok: true, hidden: p.value.value === true }
    return { ok: false, reason: `hidden is computed (${clip(p.value.src, 24)}) — change it with AI or code` }
  }
  return { ok: false, reason: `${name} has no hidden prop — hide one of its items, or ask AI to show it on a condition` }
}

/** The more specific reason for a code-generated root: kept in a variable reads better than "generated by code". */
function refusedReason(reason: string, parents: t.Node[]): string {
  if (!reason.startsWith('Generated by code')) return reason
  let i = parents.length - 1
  while (i >= 0 && parents[i].type !== 'JSXElement' && parents[i].type !== 'JSXFragment') i--
  if (parents.slice(i + 1).some((n) => n.type === 'VariableDeclarator')) return 'Kept in a variable — edit it where it is defined, or use AI'
  return reason
}

const memo = new WeakMap<FileMap, Map<string, LayerTree>>()

/** The layers of `file` (memoized per FileMap object, so per source version). */
export function layerTree(files: FileMap, file: string): LayerTree {
  let perFiles = memo.get(files)
  if (!perFiles) memo.set(files, (perFiles = new Map()))
  const hit = perFiles.get(file)
  if (hit) return hit
  const tree = build(files, file)
  perFiles.set(file, tree)
  return tree
}

function build(files: FileMap, file: string): LayerTree {
  const tree: LayerTree = { file, rows: new Map(), roots: [], byKey: new Map() }
  const src = files[file]
  const ast = src == null ? null : tryParse(src)
  if (!ast) return tree
  const keyOf = new Map(indexElements(ast).map((e) => [e.node, e.key]))
  const imports = importsOf(ast)
  const itemsOf = new Map<t.Node, t.Node[]>()
  const idOf = new Map<t.Node, string>()
  const lines = new Map<string, number>()
  const sealed = isKitFile(file)
  const add = (row: LayerRow) => {
    tree.rows.set(row.id, row)
    if (row.parent) tree.rows.get(row.parent)!.children.push(row.id)
    else tree.roots.push(row.id)
  }

  walk(ast.program, (n, parents) => {
    if (n.type !== 'JSXElement') return
    const key = keyOf.get(n)
    if (!key) return
    const name = jsxName(n.openingElement.name)
    // the group: the top-level declaration the element is written in
    const top = key.slice(0, key.indexOf('/'))
    const gid = `${file}@${top}`
    if (!tree.rows.has(gid)) {
      add({ id: gid, type: 'group', file, key: '', name: top, hint: '', kind: 'group', depth: 0, parent: null, children: [], line: n.loc!.start.line, tid: `${file}@${top}`, vsrc: null, kit: false, accepts: false, childCount: 0, hide: { ok: false, reason: 'A component, not an element' } })
    }
    let pi = parents.length - 1
    while (pi >= 0 && parents[pi].type !== 'JSXElement') pi--
    const parentEl = pi >= 0 ? (parents[pi] as t.JSXElement) : null
    const parentId = parentEl ? idOf.get(parentEl)! : gid
    const place = placeFrom(n, [...parents])
    const readOnly = sealed ? 'Kit source is sealed — change it through props' : place.kind === 'refused' ? refusedReason(place.reason, parents) : undefined
    // its index among the nearest JSX parent's children (the child item that holds it, for one inside `{…}`)
    let slot: LayerRow['slot']
    const jsxParent = [...parents].reverse().find((p): p is t.JSXElement | t.JSXFragment => p.type === 'JSXElement' || p.type === 'JSXFragment')
    if (jsxParent && place.kind === 'child') {
      let items = itemsOf.get(jsxParent)
      if (!items) itemsOf.set(jsxParent, (items = childItems(jsxParent)))
      const el = jsxParent.type === 'JSXElement'
      slot = { container: el ? parentId : `${parentId}~${jsxParent.start}`, parentKey: el ? (keyOf.get(jsxParent) ?? null) : null, index: items.indexOf(n) }
    }
    const comp = componentFor(files, file, name)
    const imp = imports.get(name.split('.')[0])
    const kit = !!imp && isKitImport(imp.source)
    const appComp = !!comp && !kit && !isKitFile(comp.file)
    const line = n.loc!.start.line
    const col = n.loc!.start.column + 1
    const seen = lines.get(`${line}`) ?? 0
    lines.set(`${line}`, seen + 1)
    const id = `${file}#${key}`
    idOf.set(n, id)
    const row: LayerRow = {
      id,
      type: 'element',
      file,
      key,
      name,
      hint: hintOf(src, n),
      kind: kindOf(name, comp?.category, appComp),
      depth: (parentEl ? tree.rows.get(parentId)!.depth : 0) + 1,
      parent: parentId,
      children: [],
      line,
      tid: seen ? `${file}:${line}:${col}` : `${file}:${line}`,
      vsrc: `${file}:${line}:${col}`,
      kit,
      ...(appComp && comp!.file !== file ? { defines: comp!.file } : {}),
      ...(readOnly ? { readOnly } : {}),
      ...(place.inList ? { repeats: true } : {}),
      ...(slot ? { slot } : {}),
      accepts: !readOnly && acceptsChildren(files, file, n),
      childCount: childItems(n).length,
      hide: hideOf(files, file, ast, n, name, !!comp?.props.some((p) => p.name === 'hidden'), readOnly),
    }
    add(row)
    tree.byKey.set(key, id)
    // hideable list items (a shell's screens, a table's columns) are rows of their own
    if (!comp) return
    const lists = Object.entries(editorsOf(comp, kit)).filter(([, e]) => e.kind === 'list' && e.item.kind === 'object' && e.item.hideable)
    if (!lists.length) return
    const view = describe(files, { file, key, part: null })
    for (const [prop] of lists) {
      const pv = view?.props.find((p) => p.name === prop)
      for (const it of pv?.items ?? []) {
        add({
          id: `${id}@${prop}.${it.index}`,
          type: 'item',
          file,
          key,
          name: prop,
          hint: clip(it.label),
          kind: 'item',
          depth: row.depth + 1,
          parent: id,
          children: [],
          line,
          tid: `${row.tid}.${prop}.${it.index}`,
          vsrc: row.vsrc,
          kit,
          ...(it.custom ? { readOnly: 'Computed item — edit it with AI or code' } : {}),
          accepts: false,
          childCount: 0,
          hide: it.custom ? { ok: false, reason: 'Computed item — edit it with AI or code' } : { ok: true, hidden: !!it.hidden },
          part: { prop, index: it.index },
        })
      }
    }
  })
  return tree
}

// ── Showing ─────────────────────────────────────────────────────────────────

/** The row ids from the root down to `id` (inclusive). */
export function pathTo(tree: LayerTree, id: string): string[] {
  const out: string[] = []
  for (let r = tree.rows.get(id); r; r = r.parent ? tree.rows.get(r.parent) : undefined) out.unshift(r.id)
  return out
}

/** Breadcrumbs (innermost first) for a selection made from the tree rather than a preview click. */
export function layerCrumbs(files: FileMap, sel: Selection): { name: string; sel: Selection }[] {
  const tree = layerTree(files, sel.file)
  const id = tree.byKey.get(sel.key)
  if (!id) return []
  return pathTo(tree, id)
    .map((r) => tree.rows.get(r)!)
    .filter((r) => r.type === 'element')
    .map((r) => ({ name: r.name, sel: { file: r.file, key: r.key, part: r.id === id ? (sel.part ?? null) : null } }))
    .reverse()
}

/** The row for a selection (an item row when it selects a list part that has one). */
export function rowFor(tree: LayerTree, sel: Selection | null): string | null {
  if (!sel || sel.file !== tree.file) return null
  const id = tree.byKey.get(sel.key)
  if (!id) return null
  if (sel.part) {
    const item = `${id}@${sel.part.prop}.${sel.part.index}`
    if (tree.rows.has(item)) return item
  }
  return id
}

function matches(r: LayerRow, needle: string): boolean {
  return r.name.toLowerCase().includes(needle) || r.hint.toLowerCase().includes(needle)
}

/**
 * The rows to draw, depth first: children of a collapsed row are skipped; with a filter, the matching rows and their
 * ancestors (always expanded) instead.
 */
export function visibleRows(tree: LayerTree, collapsed: ReadonlySet<string>, filter: string): LayerRow[] {
  const needle = filter.trim().toLowerCase()
  const out: LayerRow[] = []
  if (!needle) {
    const go = (id: string) => {
      const r = tree.rows.get(id)!
      out.push(r)
      if (!collapsed.has(id)) for (const c of r.children) go(c)
    }
    for (const id of tree.roots) go(id)
    return out
  }
  const keep = new Set<string>()
  for (const r of tree.rows.values()) if (r.type !== 'group' && matches(r, needle)) for (const id of pathTo(tree, r.id)) keep.add(id)
  const go = (id: string) => {
    if (!keep.has(id)) return
    const r = tree.rows.get(id)!
    out.push(r)
    for (const c of r.children) go(c)
  }
  for (const id of tree.roots) go(id)
  return out
}

/** The app files that hold JSX worth a tree: App first, then screens (views / pages / screens), then the rest. */
export function screenFiles(files: FileMap): string[] {
  const rank = (p: string) => (/(^|\/)[Aa]pp\.(t|j)sx$/.test(p) ? 0 : /(^|\/)(views|pages|screens)\//.test(p) ? 1 : 2)
  return Object.keys(files)
    .filter((p) => /\.(t|j)sx$/.test(p) && !isKitFile(p))
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

// ── Row actions as ops ──────────────────────────────────────────────────────

export type DropZone = 'before' | 'after' | 'inside'

/** Where a drop lands: as the child `index` of row `parent` (or the refusal). */
export type DropPlan = { ok: true; sel: Selection; ops: Op[]; label: string } | { ok: false; reason: string }

/** The ops that move row `drag` to `zone` of row `over`: a reorder among its siblings, or a move into another container. */
export function dropPlan(tree: LayerTree, drag: LayerRow, over: LayerRow, zone: DropZone): DropPlan {
  if (drag.type !== 'element') return { ok: false, reason: 'Only elements can be dragged' }
  if (drag.readOnly) return { ok: false, reason: drag.readOnly }
  if (!drag.slot) return { ok: false, reason: 'Top element of its component — select an inner element, or move it where the component is used' }
  if (over.type !== 'element') return { ok: false, reason: over.type === 'group' ? 'Drop onto an element' : 'List items are reordered in the Properties tab' }
  if (pathTo(tree, over.id).includes(drag.id)) return { ok: false, reason: "Can't move an element into itself" }
  let container: string
  let index: number
  if (zone === 'inside') {
    if (!over.accepts) return { ok: false, reason: over.readOnly ?? `${over.name} doesn't take children here` }
    container = over.id
    index = over.childCount
  } else {
    if (!over.slot) return { ok: false, reason: over.readOnly ?? `Can't place an element next to ${over.name} (the top element of its component)` }
    container = over.slot.container
    index = over.slot.index + (zone === 'after' ? 1 : 0)
  }
  const sel: Selection = { file: drag.file, key: drag.key, part: null }
  const where = zone === 'inside' ? `into ${over.name}` : `${zone} ${over.name}`
  if (container === drag.slot.container) {
    const from = drag.slot.index
    const to = index > from ? index - 1 : index
    if (to === from) return { ok: false, reason: 'Already there' }
    return { ok: true, sel, ops: [{ op: 'move', to }], label: where }
  }
  const parentKey = zone === 'inside' ? over.key : over.slot!.parentKey
  if (!parentKey) return { ok: false, reason: 'That spot is inside a fragment (<>…</>) — drop next to its parent instead' }
  return { ok: true, sel, ops: [{ op: 'moveTo', target: { file: drag.file, key: parentKey }, index }], label: where }
}

/** The ops that show / hide a row (the `hidden` attribute, or an item's `hidden` field), or the refusal. */
export function hideOps(row: LayerRow): { sel: Selection; ops: Op[] } | { reason: string } {
  if (!row.hide.ok) return { reason: row.hide.reason }
  const hidden = row.hide.hidden
  if (row.part) return { sel: { file: row.file, key: row.key, part: null }, ops: [{ op: 'listSet', prop: row.part.prop, index: row.part.index, field: 'hidden', value: hidden ? undefined : true }] }
  return { sel: { file: row.file, key: row.key, part: null }, ops: [hidden ? { op: 'unsetProp', prop: 'hidden' } : { op: 'setProp', prop: 'hidden', value: true }] }
}

/** The selection a row stands for (an item row selects its list part), or null for a group. */
export function selectionOfRow(row: LayerRow): Selection | null {
  if (row.type === 'group') return null
  return { file: row.file, key: row.key, part: row.part ?? null }
}
