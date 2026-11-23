/**
 * The component extractor: from TSX source to a schema per component (name, category, description, props with the editor
 * control each one gets, `@example` snippet). Types come from the TypeScript annotations; editor intent from JSDoc tags.
 *
 * This file is shared verbatim by the studio (props panel) and the engine (`services/editability/registry.mjs`, run by
 * Node with type stripping), so it must stay self-contained: no runtime imports (only `import type`), no enums, no
 * parameter properties. The Babel parser is passed in.
 *
 * JSDoc tag spec (the kit and AI-written components use exactly this):
 *   prop-level:      @control text|textarea|number|boolean|color|spacing|select|icon|slot|hidden
 *                    @options a|b|c   @min n   @max n   @step n   @group Content|Layout|Style|Data|Behavior   @default v
 *   component-level: @category layout|display|form|data|chart|overlay|navigation|page   @example <JSX snippet>
 * Inference without tags: string-literal union → select; boolean → boolean; number → number; string → text;
 * ReactNode / children → slot; functions → event (read-only); T[] → list; Record<string, …> → map; anything else → code.
 */
import type * as t from '@babel/types'

export type Control =
  | 'text' | 'textarea' | 'number' | 'boolean' | 'color' | 'spacing' | 'select' | 'icon' | 'slot' | 'hidden'
  /** read-only: a handler (edit with AI / code) */
  | 'event'
  /** read-only: a type the editor can't offer a control for (shown as source) */
  | 'code'
  /** an array literal edited item by item */
  | 'list'
  /** a `Record<string, scalar>` literal */
  | 'map'

export const CONTROLS: readonly Control[] = ['text', 'textarea', 'number', 'boolean', 'color', 'spacing', 'select', 'icon', 'slot', 'hidden', 'event', 'code', 'list', 'map']
export const CATEGORIES = ['layout', 'display', 'form', 'data', 'chart', 'overlay', 'navigation', 'page'] as const
export const GROUPS = ['Content', 'Layout', 'Style', 'Data', 'Behavior'] as const
export type Category = (typeof CATEGORIES)[number]
export type Group = (typeof GROUPS)[number]
export type Scalar = string | number | boolean | null

export interface PropSchema {
  name: string
  /** the type as written */
  type: string
  control: Control
  /** why a prop got `code` (for the coverage report) */
  reason?: string
  options?: string[]
  default?: Scalar
  group?: Group
  min?: number
  max?: number
  step?: number
  isSlot: boolean
  required: boolean
  description?: string
  /** list items: a scalar per item, or an object with fields */
  item?: { kind: 'string' | 'number' } | { kind: 'object'; type: string; fields: PropSchema[] }
}

export interface ComponentSchema {
  name: string
  file: string
  exported: boolean
  category?: Category
  description?: string
  props: PropSchema[]
  example?: string
  /** takes `children` */
  children: boolean
  /** named inner parts it marks with a literal `data-vpart="header"` (list items use `<prop>.<i>` and are not listed) */
  parts?: string[]
  /** source offsets of the definition (for "enter definition") */
  start: number
  end: number
}

export type ParseFn = (src: string) => t.File

// ── JSDoc ────────────────────────────────────────────────────────────────────

export interface Doc {
  description: string
  tags: Record<string, string>
}

/** Parse a `/** … *\/` block: free text first, then `@tag value` (an `@example` runs to the next tag at a line start). */
export function parseDoc(raw: string): Doc {
  const lines = raw.replace(/^\*/, '').split('\n').map((l) => l.replace(/^\s*\*? ?/, ''))
  const tags: Record<string, string> = {}
  const desc: string[] = []
  let cur: string | null = null
  let buf: string[] = []
  const flush = () => {
    if (cur) tags[cur] = cur === 'example' ? buf.join('\n').replace(/^\n+|\s+$/g, '') : buf.join(' ').trim()
  }
  for (const line of lines) {
    // one-line blocks can hold several tags: `/** @control select @options a|b */`
    const m = /^@(\w+)\s?(.*)$/.exec(line.trim())
    if (m && (cur !== 'example' || /^\s*@\w/.test(line))) {
      flush()
      const parts = m[1] === 'example' ? [[m[1], m[2]]] : splitTags(`@${m[1]} ${m[2]}`)
      for (const [k, v] of parts.slice(0, -1)) tags[k] = v.trim()
      ;[cur, buf] = [parts[parts.length - 1][0], [parts[parts.length - 1][1]]]
    } else if (cur) buf.push(line)
    else desc.push(line)
  }
  flush()
  return { description: desc.join(' ').replace(/\s+/g, ' ').trim(), tags }
}

function splitTags(s: string): [string, string][] {
  const out: [string, string][] = []
  for (const m of s.matchAll(/@(\w+)\s*([^@]*)/g)) out.push([m[1], m[2]])
  return out
}

function docOf(n: t.Node | null | undefined): Doc | null {
  const cs = n?.leadingComments
  const c = cs && [...cs].reverse().find((x) => x.type === 'CommentBlock' && x.value.startsWith('*'))
  return c ? parseDoc(c.value) : null
}

// ── Files, imports and type lookup ───────────────────────────────────────────

interface Ctx {
  files: Record<string, string>
  parse: ParseFn
  asts: Map<string, t.File | null>
}

function astOf(ctx: Ctx, file: string): t.File | null {
  if (!ctx.asts.has(file)) {
    let ast: t.File | null = null
    try {
      ast = file in ctx.files ? ctx.parse(ctx.files[file]) : null
    } catch {
      ast = null
    }
    ctx.asts.set(file, ast)
  }
  return ctx.asts.get(file) ?? null
}

function dirname(p: string): string {
  const i = p.lastIndexOf('/')
  return i < 0 ? '' : p.slice(0, i)
}

function joinPath(base: string, rel: string): string {
  const out: string[] = []
  for (const seg of `${base}/${rel}`.split('/')) {
    if (seg === '..') out.pop()
    else if (seg && seg !== '.') out.push(seg)
  }
  return out.join('/')
}

export function resolveModule(files: Record<string, string>, from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null
  const p = joinPath(dirname(from), spec)
  for (const c of [p, `${p}.ts`, `${p}.tsx`, `${p}/index.ts`, `${p}/index.tsx`]) if (c in files) return c
  return null
}

type TypeDecl = { node: t.TSInterfaceDeclaration | t.TSTypeAliasDeclaration; file: string }

function stmtDecl(s: t.Statement): t.Node | null {
  if (s.type === 'ExportNamedDeclaration' || s.type === 'ExportDefaultDeclaration') return s.declaration ?? null
  return s
}

/** A named type visible in `file`: declared there, imported from a sibling, or re-exported (`export *`). */
function findType(ctx: Ctx, file: string, name: string, seen = new Set<string>()): TypeDecl | null {
  const key = `${file}#${name}`
  if (seen.has(key)) return null
  seen.add(key)
  const ast = astOf(ctx, file)
  if (!ast) return null
  for (const s of ast.program.body) {
    const d = stmtDecl(s)
    if ((d?.type === 'TSInterfaceDeclaration' || d?.type === 'TSTypeAliasDeclaration') && d.id.name === name) return { node: d, file }
  }
  for (const s of ast.program.body) {
    if (s.type === 'ImportDeclaration') {
      for (const sp of s.specifiers) {
        if (sp.type !== 'ImportSpecifier' || sp.local.name !== name) continue
        const target = resolveModule(ctx.files, file, s.source.value)
        const imported = sp.imported.type === 'Identifier' ? sp.imported.name : sp.imported.value
        return target ? findType(ctx, target, imported, seen) : null
      }
    }
    if (s.type === 'ExportAllDeclaration' || (s.type === 'ExportNamedDeclaration' && s.source)) {
      const target = resolveModule(ctx.files, file, s.source!.value)
      const hit = target && findType(ctx, target, name, seen)
      if (hit) return hit
    }
  }
  return null
}

// ── Types → controls ─────────────────────────────────────────────────────────

const SLOT_TYPES = new Set(['ReactNode', 'ReactElement', 'Element', 'ReactChild', 'ReactFragment'])

function refName(n: t.TSEntityName): string {
  return n.type === 'Identifier' ? n.name : n.type === 'TSQualifiedName' ? `${refName(n.left)}.${n.right.name}` : 'this'
}

interface Kind {
  control: Control
  options?: string[]
  reason?: string
  item?: PropSchema['item']
}

function strip(n: t.TSType): t.TSType {
  let x = n
  while (x.type === 'TSParenthesizedType' || x.type === 'TSOptionalType') x = x.typeAnnotation
  return x
}

/** Map a type to an editor control, following named types through the files. */
function kindOf(ctx: Ctx, file: string, n0: t.TSType, depth = 0): Kind {
  const n = strip(n0)
  if (depth > 6) return { control: 'code', reason: 'type too deep' }
  switch (n.type) {
    case 'TSStringKeyword':
      return { control: 'text' }
    case 'TSNumberKeyword':
      return { control: 'number' }
    case 'TSBooleanKeyword':
      return { control: 'boolean' }
    case 'TSFunctionType':
      return { control: 'event' }
    case 'TSLiteralType':
      if (n.literal.type === 'StringLiteral') return { control: 'select', options: [n.literal.value] }
      if (n.literal.type === 'BooleanLiteral') return { control: 'boolean' }
      return { control: 'code', reason: 'literal type' }
    case 'TSArrayType':
      return listOf(ctx, file, n.elementType, depth)
    case 'TSUnionType': {
      const parts = n.types.map(strip).filter((x) => x.type !== 'TSUndefinedKeyword' && x.type !== 'TSNullKeyword')
      if (parts.length === 1) return kindOf(ctx, file, parts[0], depth + 1)
      const kinds = parts.map((x) => kindOf(ctx, file, x, depth + 1))
      if (kinds.every((k) => k.control === 'select')) return { control: 'select', options: kinds.flatMap((k) => k.options ?? []) }
      if (kinds.some((k) => k.control === 'slot')) return { control: 'slot' }
      if (kinds.every((k) => k.control === 'boolean')) return { control: 'boolean' }
      // `string | number` and friends: typed as text
      if (kinds.every((k) => k.control === 'text' || k.control === 'number' || k.control === 'select' || k.control === 'boolean')) return { control: 'text' }
      if (kinds.some((k) => k.control === 'event')) return { control: 'event' }
      return { control: 'code', reason: 'mixed union' }
    }
    case 'TSTypeReference': {
      const name = refName(n.typeName)
      const short = name.replace(/^(React|JSX)\./, '')
      if (SLOT_TYPES.has(short)) return { control: 'slot' }
      const args = n.typeParameters?.params ?? []
      if (name === 'Array' || name === 'ReadonlyArray') return args[0] ? listOf(ctx, file, args[0], depth) : { control: 'code', reason: 'untyped array' }
      if (name === 'Record') {
        const v = args[1] && kindOf(ctx, file, args[1], depth + 1)
        return v && ['text', 'number', 'boolean', 'select'].includes(v.control) ? { control: 'map' } : { control: 'code', reason: 'record of non-scalars' }
      }
      if (name.includes('.') || /^(Partial|Pick|Omit|Required|Readonly|ReturnType|Parameters)$/.test(name)) return { control: 'code', reason: `external type ${name}` }
      const decl = findType(ctx, file, name)
      if (!decl) return { control: 'code', reason: `unresolved type ${name}` }
      if (decl.node.type === 'TSTypeAliasDeclaration') return kindOf(ctx, decl.file, decl.node.typeAnnotation, depth + 1)
      return { control: 'code', reason: 'object type' }
    }
    case 'TSTypeOperator':
      return n.operator === 'readonly' ? kindOf(ctx, file, n.typeAnnotation, depth + 1) : { control: 'code', reason: `${n.operator} operator` }
    case 'TSIndexedAccessType': {
      const hit = indexedOptions(ctx, file, n)
      if (hit) return hit
      // `TrendPoint['tone']`: the member's type
      const key = keysOf(n.indexType)
      const m = key.length === 1 ? membersOf(ctx, file, n.objectType, depth + 1) : null
      const member = m?.list.find((s) => memberName(s) === key[0])
      const ann = member?.typeAnnotation?.typeAnnotation
      return ann ? kindOf(ctx, fileOfMember.get(member) ?? m!.file, ann, depth + 1) : { control: 'code', reason: 'indexed access type' }
    }
    case 'TSTypeLiteral':
      return { control: 'code', reason: 'object type' }
    case 'TSAnyKeyword':
    case 'TSUnknownKeyword':
      return { control: 'code', reason: 'any/unknown' }
    default:
      return { control: 'code', reason: n.type }
  }
}

/** `(typeof X)[number]` over a `const X = ['a', 'b'] as const` → its options. */
function indexedOptions(ctx: Ctx, file: string, n: t.TSIndexedAccessType): Kind | null {
  const obj = strip(n.objectType)
  if (obj.type !== 'TSTypeQuery' || obj.exprName.type !== 'Identifier' || strip(n.indexType).type !== 'TSNumberKeyword') return null
  const ast = astOf(ctx, file)
  const name = obj.exprName.name
  for (const s of ast?.program.body ?? []) {
    const d = stmtDecl(s)
    if (d?.type !== 'VariableDeclaration') continue
    for (const v of d.declarations) {
      if (v.id.type !== 'Identifier' || v.id.name !== name || !v.init) continue
      let init: t.Node = v.init
      while (init.type === 'TSAsExpression' || init.type === 'TSSatisfiesExpression') init = init.expression
      if (init.type === 'ArrayExpression' && init.elements.every((e) => e?.type === 'StringLiteral')) {
        return { control: 'select', options: init.elements.map((e) => (e as t.StringLiteral).value) }
      }
    }
  }
  return null
}

function listOf(ctx: Ctx, file: string, el0: t.TSType, depth: number): Kind {
  const el = strip(el0)
  // `(string | { value; label })[]`: items written as plain strings are the editable form
  if (el.type === 'TSUnionType' && el.types.some((x) => strip(x).type === 'TSStringKeyword')) return { control: 'list', item: { kind: 'string' } }
  const k = kindOf(ctx, file, el, depth + 1)
  if (k.control === 'text' || k.control === 'select') return { control: 'list', item: { kind: 'string' } }
  if (k.control === 'number') return { control: 'list', item: { kind: 'number' } }
  const members = membersOf(ctx, file, el, depth + 1)
  if (members) {
    const fields = members.list.map((m) => propOf(ctx, members.file, m, null, depth + 1))
    return { control: 'list', item: { kind: 'object', type: typeText(ctx, file, el), fields } }
  }
  return { control: 'code', reason: 'list of non-objects' }
}

function typeText(ctx: Ctx, file: string, n: t.Node): string {
  const src = ctx.files[file]
  return src && n.start != null && n.end != null ? src.slice(n.start, n.end).replace(/\s+/g, ' ') : ''
}

interface Members {
  file: string
  list: t.TSPropertySignature[]
  /** the props type includes something we can't see into (an HTML attributes type, a generic) */
  open: string[]
}

/** The property signatures of an object-ish type: literal, interface (with local `extends`), intersection, alias. */
function membersOf(ctx: Ctx, file: string, n0: t.TSType, depth = 0): Members | null {
  const n = strip(n0)
  if (depth > 6) return null
  const fromBody = (body: t.Node[], f: string): Members => ({
    file: f,
    list: body.filter((m): m is t.TSPropertySignature => m.type === 'TSPropertySignature'),
    open: [],
  })
  if (n.type === 'TSTypeLiteral') return fromBody(n.members, file)
  if (n.type === 'TSIntersectionType' || n.type === 'TSUnionType') {
    // an intersection merges; for a union of object types take the members every variant can pass (rare in props)
    const out: Members = { file, list: [], open: [] }
    for (const part of n.types) {
      const m = membersOf(ctx, file, part, depth + 1)
      if (!m) {
        out.open.push(typeText(ctx, file, part))
        continue
      }
      // members from another file keep their own file for type lookups: tag them
      for (const s of m.list) fileOfMember.set(s, m.file)
      out.list.push(...m.list)
      out.open.push(...m.open)
    }
    return out
  }
  if (n.type === 'TSTypeReference') {
    const name = refName(n.typeName)
    if (name === 'Omit' || name === 'Pick' || name === 'Partial' || name === 'Readonly' || name === 'Required') {
      const inner = n.typeParameters?.params[0]
      const m = inner ? membersOf(ctx, file, inner, depth + 1) : null
      if (!m || name === 'Partial' || name === 'Readonly' || name === 'Required') return m
      const keys = new Set(keysOf(n.typeParameters?.params[1]))
      return { ...m, list: m.list.filter((s) => (name === 'Omit' ? !keys.has(memberName(s) ?? '') : keys.has(memberName(s) ?? ''))) }
    }
    if (name.includes('.')) return null
    const decl = findType(ctx, file, name)
    if (!decl) return null
    if (decl.node.type === 'TSTypeAliasDeclaration') return membersOf(ctx, decl.file, decl.node.typeAnnotation, depth + 1)
    const own = fromBody(decl.node.body.body, decl.file)
    for (const s of own.list) fileOfMember.set(s, decl.file)
    for (const h of decl.node.extends ?? []) {
      const base = h.expression.type === 'Identifier' ? h.expression.name : null
      const bt = base ? findType(ctx, decl.file, base) : null
      const m = bt ? membersOf(ctx, bt.file, bt.node.type === 'TSTypeAliasDeclaration' ? bt.node.typeAnnotation : ({ type: 'TSTypeReference', typeName: bt.node.id } as t.TSTypeReference), depth + 1) : null
      if (m) {
        for (const s of m.list) fileOfMember.set(s, m.file)
        own.list.unshift(...m.list.filter((s) => !own.list.some((o) => memberName(o) === memberName(s))))
      } else own.open.push(typeText(ctx, decl.file, h))
    }
    return own
  }
  return null
}

const fileOfMember = new WeakMap<t.Node, string>()

function keysOf(n: t.TSType | undefined): string[] {
  if (!n) return []
  const x = strip(n)
  if (x.type === 'TSLiteralType' && x.literal.type === 'StringLiteral') return [x.literal.value]
  if (x.type === 'TSUnionType') return x.types.flatMap(keysOf)
  return []
}

function memberName(s: t.TSPropertySignature): string | null {
  if (s.computed) return null
  return s.key.type === 'Identifier' ? s.key.name : s.key.type === 'StringLiteral' ? s.key.value : null
}

function num(s: string | undefined): number | undefined {
  if (s === undefined || s.trim() === '') return undefined
  const v = Number(s)
  return Number.isFinite(v) ? v : undefined
}

function scalarOf(s: string): Scalar {
  const x = s.trim()
  if (x === 'true' || x === 'false') return x === 'true'
  if (x === 'null') return null
  if (x !== '' && Number.isFinite(Number(x))) return Number(x)
  const q = /^(['"`])(.*)\1$/.exec(x)
  return q ? q[2] : x
}

function literalScalar(n: t.Node | null | undefined): Scalar | undefined {
  if (!n) return undefined
  if (n.type === 'StringLiteral' || n.type === 'NumericLiteral' || n.type === 'BooleanLiteral') return n.value
  if (n.type === 'NullLiteral') return null
  if (n.type === 'UnaryExpression' && n.operator === '-' && n.argument.type === 'NumericLiteral') return -n.argument.value
  if (n.type === 'TemplateLiteral' && !n.expressions.length) return n.quasis.map((q) => q.value.cooked ?? '').join('')
  return undefined
}

/** Prop names that are wiring, not design: hidden from the panel unless a tag says otherwise. */
const HIDDEN_NAMES = /^(key|ref|style|className|[a-z]\w*ClassName)$/

/** One prop from its signature, its JSDoc and (when known) its destructuring default. */
function propOf(ctx: Ctx, file0: string, s: t.TSPropertySignature, defaults: Map<string, Scalar> | null, depth = 0): PropSchema {
  const file = fileOfMember.get(s) ?? file0
  const name = memberName(s) ?? '?'
  const ann = s.typeAnnotation?.typeAnnotation
  const k: Kind = ann ? kindOf(ctx, file, ann, depth) : { control: 'code', reason: 'untyped' }
  const doc = docOf(s)
  const out: PropSchema = { name, type: ann ? typeText(ctx, file, ann) : 'unknown', control: k.control, isSlot: k.control === 'slot', required: !s.optional }
  if (k.reason) out.reason = k.reason
  if (k.options) out.options = [...new Set(k.options)]
  if (k.item) out.item = k.item
  if (name === 'children' && k.control === 'slot') out.isSlot = true
  // wiring, not design (`key` only at the top: a list item's `key` is data)
  if (HIDDEN_NAMES.test(name) && (defaults !== null || name !== 'key')) out.control = 'hidden'
  if (/^on[A-Z]/.test(name) && k.control === 'code') out.control = 'event'
  const def = defaults?.get(name)
  if (def !== undefined) out.default = def
  if (doc) applyTags(out, doc)
  return out
}

function applyTags(out: PropSchema, doc: Doc): void {
  const tg = doc.tags
  if (doc.description) out.description = doc.description
  if (tg.control && (CONTROLS as readonly string[]).includes(tg.control.trim())) {
    out.control = tg.control.trim() as Control
    if (out.control === 'slot') out.isSlot = true
    delete out.reason
  }
  if (tg.options) {
    out.options = tg.options.split('|').map((x) => x.trim()).filter(Boolean)
    if (!tg.control && (out.control === 'text' || out.control === 'code')) out.control = 'select'
  }
  if (tg.default !== undefined) out.default = scalarOf(tg.default)
  const g = tg.group?.trim()
  if (g && (GROUPS as readonly string[]).includes(g)) out.group = g as Group
  for (const k of ['min', 'max', 'step'] as const) {
    const v = num(tg[k])
    if (v !== undefined) out[k] = v
  }
}

// ── Components ───────────────────────────────────────────────────────────────

type Fn = t.FunctionDeclaration | t.ArrowFunctionExpression | t.FunctionExpression

function fnOf(n: t.Node | null | undefined): Fn | null {
  if (!n) return null
  if (n.type === 'FunctionDeclaration' || n.type === 'ArrowFunctionExpression' || n.type === 'FunctionExpression') return n
  // React.memo(...), forwardRef(...), memo(...)
  if (n.type === 'CallExpression') {
    const c = n.callee
    const cn = c.type === 'Identifier' ? c.name : c.type === 'MemberExpression' && c.property.type === 'Identifier' ? c.property.name : ''
    if (cn === 'memo' || cn === 'forwardRef') return fnOf(n.arguments[0] as t.Node)
  }
  if (n.type === 'TSAsExpression' || n.type === 'TSSatisfiesExpression') return fnOf(n.expression)
  return null
}

function returnsJsx(fn: Fn): boolean {
  if (fn.body.type !== 'BlockStatement') return true
  let found = false
  const visit = (n: t.Node | null | undefined, inner: boolean): void => {
    if (!n || found || typeof n !== 'object') return
    // JSX, or `React.createElement(as, …)` (the kit's polymorphic layout primitives)
    const created = n.type === 'CallExpression' && ((n.callee.type === 'MemberExpression' && n.callee.property.type === 'Identifier' && n.callee.property.name === 'createElement') || (n.callee.type === 'Identifier' && n.callee.name === 'createElement'))
    if (n.type === 'JSXElement' || n.type === 'JSXFragment' || created) {
      found = true
      return
    }
    for (const k of Object.keys(n)) {
      if (k === 'loc' || k.endsWith('Comments')) continue
      const v = (n as unknown as Record<string, unknown>)[k]
      if (Array.isArray(v)) for (const c of v) visit(c as t.Node, inner)
      else if (v && typeof v === 'object' && typeof (v as t.Node).type === 'string') visit(v as t.Node, inner)
    }
  }
  visit(fn.body, false)
  return found
}

interface Candidate {
  name: string
  fn: Fn
  exported: boolean
  doc: Doc | null
  /** `const X: React.FC<P> = …` */
  fcType?: t.TSType
  node: t.Node
}

function candidates(ast: t.File): Candidate[] {
  const out: Candidate[] = []
  for (const s of ast.program.body) {
    const exported = s.type === 'ExportNamedDeclaration' || s.type === 'ExportDefaultDeclaration'
    const d = stmtDecl(s)
    const doc = docOf(s) ?? (d !== s ? docOf(d) : null)
    if (d?.type === 'FunctionDeclaration' && d.id && /^[A-Z]/.test(d.id.name)) out.push({ name: d.id.name, fn: d, exported, doc, node: s })
    else if (d?.type === 'VariableDeclaration') {
      for (const v of d.declarations) {
        if (v.id.type !== 'Identifier' || !/^[A-Z]/.test(v.id.name)) continue
        const fn = fnOf(v.init)
        if (!fn) continue
        const ann = v.id.typeAnnotation?.type === 'TSTypeAnnotation' ? v.id.typeAnnotation.typeAnnotation : null
        const fcType = ann?.type === 'TSTypeReference' && /(^|\.)(FC|FunctionComponent)$/.test(refName(ann.typeName)) ? ann.typeParameters?.params[0] : undefined
        out.push({ name: v.id.name, fn, exported, doc: doc ?? docOf(v), fcType, node: s })
      }
    }
  }
  return out.filter((c) => returnsJsx(c.fn))
}

function defaultsOf(fn: Fn): Map<string, Scalar> {
  const out = new Map<string, Scalar>()
  const p = fn.params[0]
  const pat = p?.type === 'AssignmentPattern' ? p.left : p
  if (pat?.type !== 'ObjectPattern') return out
  for (const prop of pat.properties) {
    if (prop.type !== 'ObjectProperty' || prop.key.type !== 'Identifier' || prop.value.type !== 'AssignmentPattern') continue
    const v = literalScalar(prop.value.right)
    if (v !== undefined) out.set(prop.key.name, v)
  }
  return out
}

function category(tag: string | undefined): Category | undefined {
  const c = tag?.trim()
  return c && (CATEGORIES as readonly string[]).includes(c) ? (c as Category) : undefined
}

/** A prop with no type at all (an untyped destructured parameter): typed from its default, else code. */
function untypedProp(name: string, def: Scalar | undefined): PropSchema {
  const control: Control = HIDDEN_NAMES.test(name) ? 'hidden' : typeof def === 'string' ? 'text' : typeof def === 'number' ? 'number' : typeof def === 'boolean' ? 'boolean' : /^on[A-Z]/.test(name) ? 'event' : name === 'children' ? 'slot' : 'code'
  const out: PropSchema = { name, type: 'unknown', control, isSlot: control === 'slot', required: false }
  if (control === 'code') out.reason = 'untyped'
  if (def !== undefined) out.default = def
  return out
}

function componentOf(ctx: Ctx, file: string, c: Candidate): ComponentSchema {
  const p0 = c.fn.params[0]
  const p = p0?.type === 'AssignmentPattern' ? p0.left : p0
  const annNode = c.fcType ?? (p && 'typeAnnotation' in p && p.typeAnnotation?.type === 'TSTypeAnnotation' ? p.typeAnnotation.typeAnnotation : undefined)
  const defaults = defaultsOf(c.fn)
  let props: PropSchema[] = []
  if (annNode) {
    const m = membersOf(ctx, file, annNode)
    if (m) props = m.list.map((s) => propOf(ctx, file, s, defaults))
    else props = [{ name: '(props)', type: typeText(ctx, file, annNode), control: 'code', reason: 'unresolved props type', isSlot: false, required: false }]
    // an HTML attributes base type: the common design-relevant attributes become props
    const open = m?.open ?? []
    if (open.some((o) => /ButtonHTMLAttributes/.test(o))) {
      for (const [name, control] of [['title', 'text'], ['disabled', 'boolean'], ['onClick', 'event'], ['type', 'select']] as const) {
        if (!props.some((x) => x.name === name)) {
          const ps: PropSchema = { name, type: 'html', control, isSlot: false, required: false }
          if (name === 'type') ps.options = ['button', 'submit', 'reset']
          props.push(ps)
        }
      }
    }
    // HTML attributes bring `children` along, unless the type omits it (`Omit<React.HTMLAttributes<…>, 'title' | 'children'>`)
    if (m && !props.some((x) => x.name === 'children') && open.some((o) => /HTMLAttributes|PropsWithChildren/.test(o) && !/['"]children['"]/.test(o))) {
      props.push({ name: 'children', type: 'React.ReactNode', control: 'slot', isSlot: true, required: false })
    }
  } else if (p?.type === 'ObjectPattern') {
    for (const prop of p.properties) {
      if (prop.type === 'ObjectProperty' && prop.key.type === 'Identifier') props.push(untypedProp(prop.key.name, defaults.get(prop.key.name)))
    }
  }
  const tg = c.doc?.tags ?? {}
  const out: ComponentSchema = {
    name: c.name,
    file,
    exported: c.exported,
    props,
    children: props.some((x) => x.name === 'children'),
    start: c.node.start ?? 0,
    end: c.node.end ?? 0,
  }
  const cat = category(tg.category)
  if (cat) out.category = cat
  if (c.doc?.description) out.description = c.doc.description
  if (tg.example) out.example = tg.example
  const parts = partsOf(c.node)
  if (parts.length) out.parts = parts
  return out
}

/** The literal `data-vpart` names used in a component's body, in source order. */
function partsOf(node: t.Node): string[] {
  const out: string[] = []
  const seen = new Set<unknown>()
  const visit = (n: unknown) => {
    if (!n || typeof n !== 'object' || seen.has(n)) return
    seen.add(n)
    if (Array.isArray(n)) { for (const x of n) visit(x); return }
    const o = n as { type?: string; name?: { type?: string; name?: string }; value?: { type?: string; value?: unknown } }
    if (o.type === 'JSXAttribute' && o.name?.type === 'JSXIdentifier' && o.name.name === 'data-vpart' && o.value?.type === 'StringLiteral') {
      const v = String(o.value.value)
      if (!out.includes(v)) out.push(v)
      return
    }
    for (const k in o) if (k !== 'loc' && k !== 'leadingComments' && k !== 'trailingComments' && k !== 'innerComments' && k !== 'extra') visit((o as Record<string, unknown>)[k])
  }
  visit(node)
  return out
}

/**
 * Extract every component of the given files (all files when `only` is omitted). `files` is the whole file map so named
 * types can be followed through imports.
 */
export function extractComponents(files: Record<string, string>, parse: ParseFn, only?: string[]): ComponentSchema[] {
  const ctx: Ctx = { files, parse, asts: new Map() }
  const out: ComponentSchema[] = []
  for (const file of only ?? Object.keys(files)) {
    if (!/\.(t|j)sx?$/.test(file)) continue
    const ast = astOf(ctx, file)
    if (!ast) continue
    for (const c of candidates(ast)) out.push(componentOf(ctx, file, c))
  }
  return out
}

/** Read-only controls: a prop with one of these has no editor in the panel. */
export function isReadOnly(c: Control): boolean {
  return c === 'event' || c === 'code'
}
