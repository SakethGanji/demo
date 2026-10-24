/**
 * The visual editor's model: what a selected element looks like to the property panel (`describe`), and the edits the
 * panel can make (`applyOp`), each a minimal splice of the source that still parses. A selection is `{file, key}` with the
 * structural key from source.ts, so it survives the line shifts of its own edits and most chat edits.
 */
import type * as t from '@babel/types'
import { editorsOf, partOf, type ListItem, type PropEditor } from './manifest'
import { componentFor, definitionRoot, enclosingComponent, isReusable, usagesOf, type ComponentSchema } from './registry/registry'
import {
  constUses, elementAt, elementByKey, followConst, importsOf, indexElements, isKitImport, isLiteralKind, literalValue, nodeAt, parseVsrc,
  propKey, readProps, tryParse, unwrap, vsrcOf, walk, type ConstRef, type FileMap, type PropRead, type Value,
} from './source'
import {
  applyChecked, arrayInsert, arrayMove, arrayRemove, moveNodes, printAttrValue, printLiteral, quoteStyle, removeAttr, removeObjectKey,
  replaceNode, setAttr, setObjectKey, setText, type Literal, type TextEdit,
} from './splice'
import { applyStructOp, type StructOp } from './structure'

export interface Selection {
  file: string
  key: string
  /** a list item inside the element (a column, tab, tile…) from the kit's `data-vpart` marker */
  part?: { prop: string; index: number } | null
}

export interface FieldView {
  state: 'literal' | 'custom'
  value?: Literal
  src?: string
}

export interface ItemView {
  index: number
  label: string
  /** a plain literal item (string array) */
  value?: Literal
  fields?: Record<string, FieldView>
  hidden?: boolean
  /** the item is not a literal (spread, call, variable): only AI / code can change it */
  custom?: string
}

export type PropState = 'literal' | 'unset' | 'custom' | 'bound' | 'event' | 'slot'

export interface PropView {
  name: string
  editor: PropEditor | null
  state: PropState
  value?: Literal
  src?: string
  /** the value lives in a named const (possibly in another file): editing it changes every use */
  via?: { name: string; file: string; scope: 'module' | 'local' }
  /** with `via`: how many places read that const */
  uses?: number
  /** why a variable-backed value is read-only (declared with let, a prop, state, a function result…) */
  reason?: string
  items?: ItemView[]
}

/** An edit on this element that lands in a const declared elsewhere in the file, or a variable that can't be followed. */
export interface ConstNote {
  /** the const (or variable) name */
  name: string
  /** what of the element it feeds: `text`, a prop name, or `element` (the element itself is inside the const) */
  what: string
  file: string
  line: number
  /** places that read it (editing changes all of them); 0 when refused */
  uses: number
  /** set when the value can't be edited visually, and why */
  reason?: string
}

/** An app-defined component (not the kit): how widely it is used, and where its definition starts. */
export interface ComponentRef {
  name: string
  file: string
  /** instances across the app's files */
  usedBy: number
  files: number
  /** the outermost element of its body (double-click / "Edit inside" lands here) */
  enter: Selection | null
}

export interface ElementView {
  file: string
  key: string
  name: string
  line: number
  endLine: number
  kit: boolean
  /** the registry schema of the component this element renders (kit or app-defined), when known */
  component?: { name: string; file: string; description?: string; category?: string; example?: string }
  /** the element is an instance of an app-defined component */
  instance?: ComponentRef
  /** the element sits inside the definition of an app-defined component: editing it changes every instance */
  inside?: ComponentRef
  props: PropView[]
  texts: string[]
  spreads: number
  /** values this element shows that live in consts (or variables it can't follow), for the "used in N places" note */
  consts?: ConstNote[]
  /** the element's source (clipped) */
  source: string
  /** whether it can be reordered among its siblings, and if not why */
  move: MoveView
}

export type MoveView =
  | { ok: true; index: number; count: number; siblings: { vsrc: string; index: number }[] }
  | { ok: false; reason: string }

// ── Reading ─────────────────────────────────────────────────────────────────

/** A literal as a JS value, or undefined when any part of it is computed. */
export function toLiteral(n: t.Node): Literal | undefined {
  const x = unwrap(n)
  const v = literalValue(x)
  if (v !== undefined) return v
  if (x.type === 'Identifier' && x.name === 'undefined') return null
  if (x.type === 'ArrayExpression') {
    const out: Literal[] = []
    for (const e of x.elements) {
      const l = e && e.type !== 'SpreadElement' ? toLiteral(e) : undefined
      if (l === undefined) return undefined
      out.push(l)
    }
    return out
  }
  if (x.type === 'ObjectExpression') {
    const out: Record<string, Literal> = {}
    for (const p of x.properties) {
      const k = p.type === 'ObjectProperty' ? propKey(p) : null
      const l = k !== null && p.type === 'ObjectProperty' ? toLiteral(p.value) : undefined
      if (k === null || l === undefined) return undefined
      out[k] = l
    }
    return out
  }
  return undefined
}

function itemsOf(src: string, arr: t.ArrayExpression, spec: ListItem | null): ItemView[] {
  return arr.elements.flatMap((e, index): ItemView[] => {
    if (!e) return []
    const text = src.slice(e.start!, e.end!)
    const x = unwrap(e)
    if (x.type === 'ObjectExpression') {
      const fields: Record<string, FieldView> = {}
      let custom: string | undefined
      for (const p of x.properties) {
        const k = p.type === 'ObjectProperty' ? propKey(p) : null
        if (k === null || p.type !== 'ObjectProperty') {
          custom = src.slice(p.start!, p.end!)
          continue
        }
        const l = toLiteral(p.value)
        fields[k] = l === undefined ? { state: 'custom', src: src.slice(p.value.start!, p.value.end!) } : { state: 'literal', value: l }
      }
      const labelKey = spec?.kind === 'object' ? spec.label : 'label'
      const lf = fields[labelKey] ?? fields.label ?? fields.header ?? fields.key ?? fields.id
      const label = lf?.state === 'literal' ? String(lf.value) : lf?.src ?? text.slice(0, 40)
      return [{ index, label, fields, hidden: fields.hidden?.value === true, custom }]
    }
    const l = toLiteral(x)
    if (l !== undefined && typeof l !== 'object') return [{ index, label: String(l), value: l }]
    return [{ index, label: text.slice(0, 40), custom: text }]
  })
}

function stateOf(v: Value, editor: PropEditor | null): PropState {
  if (v.kind === 'event' || editor?.kind === 'event') return 'event'
  if (v.kind === 'jsx') return 'slot'
  if (isLiteralKind(v.kind)) return 'literal'
  if (v.kind === 'bound' || editor?.kind === 'bound') return 'bound'
  return 'custom'
}

function viewProp(files: FileMap, p: PropRead | null, name: string, editor0: PropEditor | null): PropView {
  if (!p) return { name, editor: editor0, state: 'unset' }
  const v = p.value
  // a slot (ReactNode) holding a plain string is edited as text; JSX in it stays a slot
  const editor: PropEditor | null = editor0?.kind === 'slot' && v.kind === 'string' ? { kind: 'text' } : editor0
  const view: PropView = { name, editor, state: stateOf(v, editor), src: v.src }
  if (v.via) {
    view.via = { name: v.via.name, file: v.via.file, scope: v.via.scope }
    view.uses = constUses(files, v.via)
  }
  if (v.reason) view.reason = v.reason
  if (view.state === 'literal') {
    if (v.kind === 'array' && v.node.type === 'ArrayExpression') {
      view.items = itemsOf(files[v.file], v.node, editor?.kind === 'list' ? editor.item : null)
      const l = toLiteral(v.node)
      if (l !== undefined) view.value = l
    } else {
      const l = v.node.type === 'JSXAttribute' ? true : toLiteral(v.node)
      if (l === undefined) view.state = 'custom'
      else view.value = l
    }
  }
  return view
}

interface Located {
  ast: t.File
  el: t.JSXElement
  name: string
  kit: boolean
  comp: ComponentSchema | null
}

function refOf(files: FileMap, comp: ComponentSchema): ComponentRef {
  const uses = usagesOf(files, comp)
  const root = definitionRoot(files, comp)
  return { name: comp.name, file: comp.file, usedBy: uses.length, files: new Set(uses.map((u) => u.file)).size, enter: root ? { ...root, part: null } : null }
}

/** The panel editors of an element's component (kit or app-defined), or null for DOM elements and unknowns. */
function editorsFor(l: { comp: ComponentSchema | null; kit: boolean }): Record<string, PropEditor> | null {
  return l.comp ? editorsOf(l.comp, l.kit) : null
}

function locate(files: FileMap, sel: Selection): Located | null {
  const src = files[sel.file]
  const ast = src == null ? null : tryParse(src)
  const hit = ast && elementByKey(ast, sel.key)
  if (!ast || !hit) return null
  const root = hit.name.split('.')[0]
  const imp = importsOf(ast).get(root)
  return { ast, el: hit.node, name: hit.name, kit: !!imp && isKitImport(imp.source), comp: componentFor(files, sel.file, hit.name) }
}

/** The selection for a preview tag (`file:line:col`), or null when the file or element is gone. */
export function selectionFor(files: FileMap, vsrc: string, vpart?: string | null): Selection | null {
  const loc = parseVsrc(vsrc)
  const src = loc && files[loc.file]
  const ast = src == null ? null : tryParse(src)
  const hit = ast && loc && elementAt(ast, loc.line, loc.col)
  if (!hit || !loc) return null
  const comp = componentFor(files, loc.file, hit.name)
  const imp = importsOf(ast).get(hit.name.split('.')[0])
  return { file: loc.file, key: hit.key, part: partOf(editorsFor({ comp, kit: !!imp && isKitImport(imp.source) }), hit.name, vpart) }
}

/**
 * What a click in the preview selects. `chain` is the clicked element's tagged ancestry, innermost first. By default the
 * INSTANCE of an app-defined component is selected rather than an element inside its definition (as in Figma); with
 * `enter` (double-click) the innermost element is. Kit internals are never in the chain (the kit isn't tagged).
 */
export function resolveClick(
  files: FileMap,
  chain: { vsrc: string; name: string }[],
  part: { owner: string; vpart: string } | null,
  enter = false,
): { crumbs: { name: string; sel: Selection }[]; selection: Selection | null } {
  const crumbs = chain
    .map((c) => ({ name: c.name, sel: selectionFor(files, c.vsrc, part && part.owner === c.vsrc ? part.vpart : null) }))
    .filter((c): c is { name: string; sel: Selection } => c.sel !== null)
  if (enter || crumbs.length < 2) return { crumbs, selection: crumbs[0]?.sel ?? null }
  let i = 0
  while (i < crumbs.length - 1) {
    const inside = enclosingComponent(files, crumbs[i].sel.file, crumbs[i].sel.key)
    // skip out of a reusable component's definition only when its instance is further up the chain
    const j = inside && isReusable(files, inside) ? crumbs.findIndex((c, k) => k > i && c.sel.file !== inside.file && componentFor(files, c.sel.file, keyName(c.sel.key)) === inside) : -1
    if (j < 0) break
    i = j
  }
  return { crumbs, selection: crumbs[i].sel }
}

/** The element name at the end of a structural key (`Top/A>B#0` → `B`). */
function keyName(key: string): string {
  const path = key.slice(key.indexOf('/') + 1, key.lastIndexOf('#'))
  return path.slice(path.lastIndexOf('>') + 1)
}

// ── Texts (literal children, and `{name}` children that name a const in the same file) ──

/** A text the element shows: a JSX text child, or `{name}` where `name` is a same-file const holding a string or number. */
type TextSlot = { kind: 'text'; node: t.JSXText; value: string } | { kind: 'const'; ref: ConstRef; value: string }

/** A `{name}` child that names a variable the editor can't follow to an editable const. */
interface TextRefused {
  name: string
  line: number
  reason: string
}

const tidy = (s: string) => s.trim().replace(/\s+/g, ' ')

/** The element's editable texts in source order (`setText`'s index), and the variable texts it refuses with a reason. */
function textSlots(files: FileMap, file: string, ast: t.File, el: t.JSXElement): { slots: TextSlot[]; refused: TextRefused[] } {
  const slots: TextSlot[] = []
  const refused: TextRefused[] = []
  for (const c of el.children) {
    if (c.type === 'JSXText') {
      if (c.value.trim()) slots.push({ kind: 'text', node: c, value: tidy(c.value) })
      continue
    }
    if (c.type !== 'JSXExpressionContainer' || c.expression.type === 'JSXEmptyExpression') continue
    const x = unwrap(c.expression)
    if (x.type !== 'Identifier') continue
    const f = followConst(files, file, ast, x, x.name)
    if (!f) continue
    if (!f.ok) {
      refused.push({ name: x.name, line: x.loc?.start.line ?? 0, reason: f.reason })
      continue
    }
    const v = literalValue(f.ref.node)
    if (typeof v === 'string' || typeof v === 'number') slots.push({ kind: 'const', ref: f.ref, value: tidy(String(v)) })
  }
  return { slots, refused }
}

/**
 * Why the element's text can't be edited in place, or null when its only child is one plain text or one `{name}` naming a
 * same-file const string (so the element's rendered text is exactly what the `setText` op replaces).
 */
export function textRefusal(files: FileMap, sel: Selection): string | null {
  const l = locate(files, sel)
  if (!l) return 'The element is no longer in the source'
  const kids = l.el.children.filter((c) => !(c.type === 'JSXText' && !c.value.trim()))
  if (kids.length === 1 && kids[0].type === 'JSXText') return null
  if (kids.length === 1 && kids[0].type === 'JSXExpressionContainer') {
    const { slots, refused } = textSlots(files, sel.file, l.ast, l.el)
    if (slots.length === 1) return null
    if (refused.length) return refused[0].reason
  }
  if (kids.some((c) => c.type === 'JSXExpressionContainer')) return 'This text comes from a variable or prop; edit it where it is defined, or use AI'
  return 'This element mixes text with other elements; edit the parts separately'
}

/** The const whose initializer holds the element (`const header = <h1>…</h1>`, a JSX item of a const list), if any. */
function holderConst(ast: t.File, el: t.JSXElement): { name: string; decl: t.VariableDeclarator; block: t.Node; kind: string } | null {
  const ps = parentsOf(ast, el)
  for (let i = ps.length - 1; i >= 0; i--) {
    const p = ps[i]
    if (p.type === 'ArrowFunctionExpression' || p.type === 'FunctionExpression' || p.type === 'FunctionDeclaration' || p.type === 'ObjectMethod' || p.type === 'ClassMethod') return null
    if (p.type === 'VariableDeclarator') {
      const decl = ps[i - 1]
      const block = ps.slice(0, i).reverse().find((b) => b.type === 'BlockStatement' || b.type === 'Program')
      if (p.id.type !== 'Identifier' || decl?.type !== 'VariableDeclaration' || !block) return null
      return { name: p.id.name, decl: p, block, kind: decl.kind }
    }
  }
  return null
}

function constNotes(files: FileMap, file: string, l: Located, props: PropView[], texts: { slots: TextSlot[]; refused: TextRefused[] }): ConstNote[] {
  const out: ConstNote[] = []
  const holder = holderConst(l.ast, l.el)
  if (holder) {
    const ref: ConstRef = { file, name: holder.name, node: holder.decl.init ?? holder.decl, scope: holder.block.type === 'Program' ? 'module' : 'local', block: holder.block }
    const line = holder.decl.loc?.start.line ?? 0
    if (holder.kind === 'const') out.push({ name: holder.name, what: 'element', file, line, uses: constUses(files, ref) })
    else out.push({ name: holder.name, what: 'element', file, line, uses: 0, reason: `${holder.name} is declared with ${holder.kind}; edits here change its starting value only` })
  }
  for (const s of texts.slots) {
    if (s.kind === 'const') out.push({ name: s.ref.name, what: 'text', file: s.ref.file, line: s.ref.node.loc?.start.line ?? 0, uses: constUses(files, s.ref) })
  }
  for (const r of texts.refused) out.push({ name: r.name, what: 'text', file, line: r.line, uses: 0, reason: r.reason })
  for (const p of props) {
    if (p.via) out.push({ name: p.via.name, what: p.name, file: p.via.file, line: 0, uses: p.uses ?? 0 })
    // only props the panel would otherwise edit (not handlers, data bindings or unknown props)
    else if (p.reason && p.editor && !['event', 'bound', 'slot'].includes(p.editor.kind) && !/^on[A-Z]/.test(p.name)) out.push({ name: p.src ?? p.name, what: p.name, file, line: 0, uses: 0, reason: p.reason })
  }
  return out
}

/** The preview tag for a selection (to outline it after a re-render). */
export function vsrcFor(files: FileMap, sel: Selection): string | null {
  const l = locate(files, sel)
  return l ? `${sel.file}:${l.el.loc!.start.line}:${l.el.loc!.start.column + 1}` : null
}

export function describe(files: FileMap, sel: Selection): ElementView | null {
  const l = locate(files, sel)
  if (!l) return null
  const src = files[sel.file]
  const { props, spreads } = readProps(files, sel.file, l.ast, l.el)
  const editors = editorsFor(l)
  const views: PropView[] = []
  const seen = new Set<string>()
  // the component's props first (set or not), in declaration order; then whatever else the element sets
  for (const [name, editor] of Object.entries(editors ?? {})) {
    views.push(viewProp(files, props.find((p) => p.name === name) ?? null, name, editor))
    seen.add(name)
  }
  for (const p of props) if (!seen.has(p.name)) views.push(viewProp(files, p, p.name, null))
  const texts = textSlots(files, sel.file, l.ast, l.el)
  const consts = constNotes(files, sel.file, l, views, texts)
  const start = l.el.loc!.start.line
  const end = l.el.loc!.end.line
  const lines = src.slice(l.el.start!, l.el.end!).split('\n')
  const view: ElementView = {
    file: sel.file,
    key: sel.key,
    name: l.name,
    line: start,
    endLine: end,
    kit: l.kit,
    props: views,
    texts: texts.slots.map((s) => s.value),
    spreads,
    source: lines.length > 80 ? [...lines.slice(0, 80), '  …'].join('\n') : lines.join('\n'),
    move: moveView(sel.file, l),
  }
  if (consts.length) view.consts = consts
  if (l.comp) {
    const c = l.comp
    view.component = { name: c.name, file: c.file, ...(c.description ? { description: c.description } : {}), ...(c.category ? { category: c.category } : {}), ...(c.example ? { example: c.example } : {}) }
    if (!l.kit && isReusable(files, c)) view.instance = refOf(files, c)
  }
  const inside = enclosingComponent(files, sel.file, sel.key)
  if (inside && isReusable(files, inside)) view.inside = refOf(files, inside)
  return view
}

// ── Moving among siblings ───────────────────────────────────────────────────

function parentsOf(ast: t.File, node: t.Node): t.Node[] {
  let out: t.Node[] = []
  walk(ast.program, (n, parents) => {
    if (n === node) out = [...parents]
  })
  return out
}

const LIST_CALLS = new Set(['map', 'flatMap', 'filter', 'reduce', 'forEach'])

type MoveCtx = { ok: true; items: t.Node[]; index: number } | { ok: false; reason: string }

/**
 * The element's movable siblings: the non-blank children of its parent JSX element. Refused when the element is not a
 * fixed child of JSX (rendered from a list, a condition or other code) or shares its parent with inline text.
 */
function moveCtx(l: Located): MoveCtx {
  const ps = parentsOf(l.ast, l.el)
  const p = ps[ps.length - 1]
  if (!p || (p.type !== 'JSXElement' && p.type !== 'JSXFragment')) {
    let i = ps.length - 1
    while (i >= 0 && ps[i].type !== 'JSXElement' && ps[i].type !== 'JSXFragment') i--
    const between = ps.slice(i + 1)
    const inList = between.some((n) => n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && n.callee.property.type === 'Identifier' && LIST_CALLS.has(n.callee.property.name))
    if (inList) return { ok: false, reason: 'Inside a list — edit the template' }
    if (between.some((n) => n.type === 'JSXAttribute')) return { ok: false, reason: 'Passed in a prop — use AI or code' }
    const holder = [...between].reverse().find((n) => n.type !== 'ParenthesizedExpression')
    if (i < 0 && (holder?.type === 'ReturnStatement' || holder?.type === 'ArrowFunctionExpression')) {
      return { ok: false, reason: 'Top element of its component — select an inner element' }
    }
    return { ok: false, reason: 'Generated by code — use AI or code' }
  }
  const items = p.children.filter(
    (c) => !(c.type === 'JSXText' && !c.value.trim()) && !(c.type === 'JSXExpressionContainer' && c.expression.type === 'JSXEmptyExpression'),
  )
  if (items.some((c) => c.type === 'JSXText')) return { ok: false, reason: 'Sits inside a line of text — edit the text instead' }
  if (items.length < 2) return { ok: false, reason: 'Only child — nothing to move past' }
  return { ok: true, items, index: items.indexOf(l.el) }
}

function moveView(file: string, l: Located): MoveView {
  const c = moveCtx(l)
  if (!c.ok) return c
  const siblings = c.items.flatMap((n, index) => (n.type === 'JSXElement' ? [{ vsrc: vsrcOf(file, n), index }] : []))
  return { ok: true, index: c.index, count: c.items.length, siblings }
}

// ── Editing ─────────────────────────────────────────────────────────────────

export type Op =
  | { op: 'setProp'; prop: string; value: Literal }
  | { op: 'unsetProp'; prop: string }
  | { op: 'setText'; index: number; value: string }
  /** set (or with value undefined, remove) a field of list item `index`; without `field`, replace the item */
  | { op: 'listSet'; prop: string; index: number; field?: string; value: Literal | undefined }
  | { op: 'listMove'; prop: string; from: number; to: number }
  | { op: 'listRemove'; prop: string; index: number }
  | { op: 'listInsert'; prop: string; index: number; item: Literal }
  /** move the element itself to position `to` among its parent's children */
  | { op: 'move'; to: number }
  /** structure: insert / delete / duplicate / wrap / unwrap / move across containers (structure.ts) */
  | StructOp

export interface Patch {
  path: string
  before: string
  after: string
}

/** `select`: the selection to follow after the edit (a move changes the element's key); null clears it */
export type OpResult = { ok: true; patches: Patch[]; label: string; select?: Selection | null } | { ok: false; error: string }

const STRUCT_OPS = new Set(['delete', 'duplicate', 'wrap', 'unwrap', 'insert', 'moveTo'])

function isStructOp(op: Op): op is StructOp {
  return STRUCT_OPS.has(op.op)
}

function opLabel(name: string, op: Op): string {
  if (isStructOp(op)) return `${name}: ${op.op}`
  switch (op.op) {
    case 'setProp':
      return `${name}: set ${op.prop}`
    case 'unsetProp':
      return `${name}: clear ${op.prop}`
    case 'setText':
      return `${name}: edit text`
    case 'listSet':
      return `${name}: ${op.prop}[${op.index}]${op.field ? `.${op.field}` : ''}`
    case 'listMove':
      return `${name}: move ${op.prop}[${op.from}]`
    case 'listRemove':
      return `${name}: remove ${op.prop}[${op.index}]`
    case 'listInsert':
      return `${name}: add to ${op.prop}`
    case 'move':
      return `${name}: move`
  }
}

/** Apply one edit to the selected element. Never throws; refuses edits on computed values and edits that break parsing. */
export function applyOp(files: FileMap, sel: Selection, op: Op): OpResult {
  try {
    return applyOpInner(files, sel, op)
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

const NOTHING = 'Nothing to change'

function applyOpInner(files: FileMap, sel: Selection, op: Op): OpResult {
  if (isStructOp(op)) return applyStructOp(files, sel, op)
  const l = locate(files, sel)
  if (!l) return { ok: false, error: 'The element is no longer in the source' }
  const src = files[sel.file]
  const q = quoteStyle(src)
  const done = (path: string, edits: TextEdit[] | TextEdit | null): OpResult => {
    const list = edits === null ? [] : Array.isArray(edits) ? edits : [edits]
    if (!list.length) return { ok: false, error: NOTHING }
    const before = files[path]
    const after = applyChecked(before, list)
    if (after === null) return { ok: false, error: 'That edit would break the file; use AI or code' }
    if (after === before) return { ok: false, error: NOTHING }
    return { ok: true, patches: [{ path, before, after }], label: opLabel(l.name, op) }
  }
  if (op.op === 'move') {
    const c = moveCtx(l)
    if (!c.ok) return { ok: false, error: c.reason }
    if (op.to < 0 || op.to >= c.items.length) return { ok: false, error: 'Out of range' }
    if (op.to === c.index) return { ok: false, error: NOTHING }
    const { edits, newStart } = moveNodes(src, c.items, c.index, op.to)
    const r = done(sel.file, edits)
    if (!r.ok) return r
    const ast = tryParse(r.patches[0].after)
    const moved = ast && nodeAt(ast, newStart, 'JSXElement')
    const key = moved && indexElements(ast).find((e) => e.node === moved)?.key
    return key ? { ...r, select: { file: sel.file, key, part: null } } : r
  }
  if (op.op === 'setText') {
    const slot = textSlots(files, sel.file, l.ast, l.el).slots[op.index]
    if (!slot) return { ok: false, error: 'No such text' }
    if (slot.kind === 'text') return done(sel.file, setText(src, slot.node, op.value, q))
    // a const's literal: a number stays a number when the new text is one
    const num = typeof literalValue(slot.ref.node) === 'number' && /^-?\d+(\.\d+)?$/.test(op.value.trim())
    return done(slot.ref.file, replaceNode(slot.ref.node, num ? op.value.trim() : printLiteral(op.value, q)))
  }
  const { props } = readProps(files, sel.file, l.ast, l.el)
  const prop = props.find((p) => p.name === op.prop) ?? null
  if (op.op === 'unsetProp') return done(sel.file, removeAttr(src, l.el, op.prop))
  if (op.op === 'setProp') {
    if (!prop) return done(sel.file, setAttr(src, l.el, op.prop, op.value === true ? null : printAttrValue(op.value, q)))
    const v = prop.value
    if (!isLiteralKind(v.kind)) return { ok: false, error: `${op.prop} is computed (${v.src}); use AI or code` }
    if (v.via) return done(v.file, replaceNode(v.node, printLiteral(op.value, quoteStyle(files[v.file]))))
    if (v.node.type === 'JSXAttribute' || v.node === prop.attr.value) {
      return done(sel.file, setAttr(src, l.el, op.prop, op.value === true && !prop.attr.value ? null : printAttrValue(op.value, q)))
    }
    return done(sel.file, replaceNode(v.node, printLiteral(op.value, q)))
  }
  // list operations
  if (!prop) return { ok: false, error: `${op.prop} is not set on this ${l.name}` }
  const v = prop.value
  const arr = v.node
  if (v.kind !== 'array' || arr.type !== 'ArrayExpression') return { ok: false, error: `${op.prop} is computed (${v.src}); use AI or code` }
  const fsrc = files[v.file]
  const fq = quoteStyle(fsrc)
  const items = arr.elements
  switch (op.op) {
    case 'listMove': {
      if (op.from < 0 || op.to < 0 || op.from >= items.length || op.to >= items.length) return { ok: false, error: 'Out of range' }
      return done(v.file, arrayMove(fsrc, arr, op.from, op.to))
    }
    case 'listRemove':
      if (!items[op.index]) return { ok: false, error: 'Out of range' }
      return done(v.file, arrayRemove(fsrc, arr, op.index))
    case 'listInsert':
      return done(v.file, arrayInsert(fsrc, arr, Math.min(op.index, items.length), printLiteral(op.item, fq)))
    case 'listSet': {
      const it = items[op.index]
      if (!it) return { ok: false, error: 'Out of range' }
      const x = unwrap(it)
      if (op.field === undefined) {
        if (op.value === undefined) return done(v.file, arrayRemove(fsrc, arr, op.index))
        return done(v.file, replaceNode(x, printLiteral(op.value, fq)))
      }
      if (x.type !== 'ObjectExpression') return { ok: false, error: 'That item is computed; use AI or code' }
      if (op.value === undefined) return done(v.file, removeObjectKey(fsrc, x, op.field))
      return done(v.file, setObjectKey(fsrc, x, op.field, printLiteral(op.value, fq), fq))
    }
  }
}

/** Apply a sequence of ops (each against the result of the previous one); an op that changes nothing is skipped. */
export function applyOps(files: FileMap, sel: Selection, ops: Op[]): OpResult {
  let cur = { ...files }
  const first = new Map<string, string>()
  const labels: string[] = []
  let select: Selection | null | undefined
  for (const op of ops) {
    if (select === null) return { ok: false, error: 'The element is gone' }
    const r = applyOp(cur, select ?? sel, op)
    if (!r.ok && r.error === NOTHING) continue
    if (!r.ok) return r
    for (const p of r.patches) {
      if (!first.has(p.path)) first.set(p.path, p.before)
      cur = { ...cur, [p.path]: p.after }
    }
    labels.push(r.label)
    if (r.select !== undefined) select = r.select
  }
  if (!first.size) return { ok: false, error: NOTHING }
  return { ok: true, patches: [...first].map(([path, before]) => ({ path, before, after: cur[path] })), label: labels.join('; '), select }
}

/** A prompt for "Ask AI about this element": the element's source, its place and what the kit lets it take. */
export function askPrompt(view: ElementView, request: string): string {
  const typed = view.props.filter((p) => p.editor)
  const props = view.component ? typed.map((p) => `${p.name} (${p.editor!.kind === 'enum' ? p.editor!.options.join('|') : p.editor!.kind})`).join(', ') : ''
  const what = view.kit ? 'kit component' : view.instance ? `app component defined in ${view.instance.file}, used ${view.instance.usedBy} times` : 'custom element'
  return [
    `About the <${view.name}> in ${view.file} lines ${view.line}-${view.endLine} (${what}):`,
    '```tsx',
    view.source,
    '```',
    props ? `Its ${view.kit ? 'kit ' : ''}props: ${props}.` : '',
    view.inside ? `It is inside the ${view.inside.name} component (${view.inside.file}), used ${view.inside.usedBy} times: a change here changes every instance.` : '',
    `Change this element only, surgically (keep the rest of the file as it is): ${request.trim()}`,
  ].filter(Boolean).join('\n')
}
