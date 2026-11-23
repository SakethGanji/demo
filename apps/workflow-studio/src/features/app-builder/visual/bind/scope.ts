/**
 * Shared reading for the inspector's wiring sections (data, on click, visible when): where the selected element sits (its
 * component function and the blocks around it), what that scope declares (state, hooks, params), the app's API operations
 * read from the generated `api/client.ts`, its resources from `api/resources.ts`, and the import / declaration splices
 * every binding needs. Pure: no React, no printing; edits are byte-offset splices like the rest of the editor.
 */
import type * as t from '@babel/types'
import { elementByKey, importsOf, indexElements, isKitImport, tryParse, unwrap, walk, type FileMap } from '../source'
import { applyChecked, indentAt, quoteStyle, type TextEdit } from '../splice'
import { relSpec } from '../structure'
import type { FilePatch } from '../history'

export interface Sel {
  file: string
  key: string
  part?: { prop: string; index: number } | null
}

export type Fn = t.ArrowFunctionExpression | t.FunctionExpression | t.FunctionDeclaration

export function isFn(n: t.Node): n is Fn {
  return n.type === 'ArrowFunctionExpression' || n.type === 'FunctionExpression' || n.type === 'FunctionDeclaration'
}

/** The selected element with its file, ancestors, kit-ness and the component function it renders in. */
export interface Ctx {
  files: FileMap
  file: string
  src: string
  ast: t.File
  el: t.JSXElement
  name: string
  kit: boolean
  /** ancestors of the element, outermost first */
  parents: t.Node[]
  /** the component function (a capitalised function, or the outermost function around the element) */
  comp: Fn | null
  compName: string | null
  /** the component's body block (null for `() => <jsx/>` components) */
  body: t.BlockStatement | null
}

export function parentsOf(ast: t.File, node: t.Node): t.Node[] {
  let out: t.Node[] = []
  walk(ast.program, (n, parents) => {
    if (n === node) out = [...parents]
  })
  return out
}

/** The name a function is known by: its id, or the variable / export it is assigned to. */
function fnName(fn: Fn, parent: t.Node | undefined): string | null {
  if (fn.type !== 'ArrowFunctionExpression' && fn.id) return fn.id.name
  if (parent?.type === 'VariableDeclarator' && parent.id.type === 'Identifier') return parent.id.name
  if (parent?.type === 'ExportDefaultDeclaration') return 'default'
  return null
}

export function locateCtx(files: FileMap, sel: Sel): Ctx | null {
  const src = files[sel.file]
  const ast = src == null ? null : tryParse(src)
  const hit = ast && elementByKey(ast, sel.key)
  if (!ast || !hit) return null
  const parents = parentsOf(ast, hit.node)
  let comp: Fn | null = null
  let compName: string | null = null
  for (let i = 0; i < parents.length; i++) {
    const p = parents[i]
    if (!isFn(p)) continue
    const name = fnName(p, parents[i - 1])
    if (name && (/^[A-Z]/.test(name) || name === 'default')) {
      comp = p
      compName = name
    } else if (!comp) {
      comp = p
      compName = name
    }
    if (compName && /^[A-Z]|^default$/.test(compName)) break
  }
  const imp = importsOf(ast).get(hit.name.split('.')[0])
  const body = comp && comp.body.type === 'BlockStatement' ? comp.body : null
  return { files, file: sel.file, src, ast, el: hit.node, name: hit.name, kit: !!imp && isKitImport(imp.source), parents, comp, compName, body }
}

export const text = (src: string, n: t.Node) => src.slice(n.start!, n.end!)

export function clip(s: string, n = 48): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > n ? `${one.slice(0, n)}…` : one
}

/** Names a binding pattern declares. */
export function patternNames(p: t.Node | null | undefined): string[] {
  if (!p) return []
  switch (p.type) {
    case 'Identifier':
      return [p.name]
    case 'ObjectPattern':
      return p.properties.flatMap((q) => patternNames(q.type === 'RestElement' ? q.argument : q.value))
    case 'ArrayPattern':
      return p.elements.flatMap((e) => patternNames(e))
    case 'AssignmentPattern':
      return patternNames(p.left)
    case 'RestElement':
      return patternNames(p.argument)
    default:
      return []
  }
}

export type Decl =
  | { kind: 'var'; name: string; decl: t.VariableDeclarator; stmt: t.VariableDeclaration; block: t.BlockStatement | t.Program }
  | { kind: 'param'; name: string; fn: Fn }
  | { kind: 'function'; name: string; fn: t.FunctionDeclaration }

/** Where `name` is declared as seen from the element: the innermost block or function first, then the module. */
export function findDecl(ctx: Ctx, name: string, from: t.Node[] = ctx.parents): Decl | null {
  for (let i = from.length - 1; i >= 0; i--) {
    const p = from[i]
    if (p.type === 'BlockStatement' || p.type === 'Program') {
      for (const s0 of p.body) {
        const s = (s0.type === 'ExportNamedDeclaration' || s0.type === 'ExportDefaultDeclaration') && s0.declaration ? s0.declaration : s0
        if (s.type === 'FunctionDeclaration' && s.id?.name === name) return { kind: 'function', name, fn: s }
        if (s.type !== 'VariableDeclaration') continue
        for (const d of s.declarations) if (patternNames(d.id).includes(name)) return { kind: 'var', name, decl: d, stmt: s, block: p }
      }
    }
    if (isFn(p) && p.params.some((q) => patternNames(q).includes(name))) return { kind: 'param', name, fn: p }
  }
  return null
}

/** The callee name of a call (`usePoll`, `api.getX` → `getX` with object `api`), or null. */
export function calleeName(c: t.CallExpression): string | null {
  const x = c.callee
  if (x.type === 'Identifier') return x.name
  if ((x.type === 'MemberExpression' || x.type === 'OptionalMemberExpression') && !x.computed && x.property.type === 'Identifier') return x.property.name
  return null
}

export function isHookCall(n: t.Node): n is t.CallExpression {
  return n.type === 'CallExpression' && n.callee.type === 'Identifier' && /^use[A-Z]/.test(n.callee.name)
}

/** The statements directly in the component body (where hooks are declared). */
export function bodyStatements(ctx: Ctx): t.Statement[] {
  return ctx.body?.body ?? []
}

// ── State and hooks in scope ────────────────────────────────────────────────

export interface StateVar {
  name: string
  setter: string
  /** the initial value's source text */
  init: string
  boolean: boolean
  stmt: t.VariableDeclaration
}

/** `const [x, setX] = useState(…)` (or usePersistedUiState) declared directly in the component body. */
export function stateVars(ctx: Ctx): StateVar[] {
  const out: StateVar[] = []
  for (const s of bodyStatements(ctx)) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations) {
      if (d.id.type !== 'ArrayPattern' || !d.init) continue
      const init = unwrap(d.init)
      if (init.type !== 'CallExpression' || !['useState', 'usePersistedUiState'].includes(calleeName(init) ?? '')) continue
      const [a, b] = d.id.elements
      if (a?.type !== 'Identifier' || b?.type !== 'Identifier') continue
      const arg = init.arguments[calleeName(init) === 'useState' ? 0 : 1]
      const initText = arg ? text(ctx.src, arg) : ''
      const typeArg = init.typeParameters?.params[0]
      const boolean = /^(true|false)$/.test(initText) || typeArg?.type === 'TSBooleanKeyword'
      out.push({ name: a.name, setter: b.name, init: initText, boolean, stmt: s })
    }
  }
  return out
}

/** The local name the file uses for the generated client's `api` object, when it imports it. */
export function apiLocal(ctx: Ctx): string | null {
  for (const [local, imp] of importsOf(ctx.ast)) if (imp.imported === 'api' && /(^|\/)client$/.test(imp.source.replace(/\.(t|j)sx?$/, ''))) return local
  return null
}

/** `api.op(...)` calls inside `root` (any depth). */
export function apiCallsIn(root: t.Node, api: string): t.CallExpression[] {
  const out: t.CallExpression[] = []
  walk(root, (n) => {
    if (n.type !== 'CallExpression') return
    const c = n.callee
    if ((c.type === 'MemberExpression' || c.type === 'OptionalMemberExpression') && c.object.type === 'Identifier' && c.object.name === api && !c.computed && c.property.type === 'Identifier') out.push(n)
  })
  return out
}

export function apiOpOf(call: t.CallExpression): string {
  return ((call.callee as t.MemberExpression).property as t.Identifier).name
}

/** A way to load data again: `polls.refresh()`, `reload()`… with the name of the data it refreshes. */
export interface Refetch {
  /** what it reloads (the variable shown in the panel) */
  label: string
  /** the call to write: `runs.refresh()` */
  call: string
}

const REFRESH_KEY: Record<string, string> = { usePoll: 'refresh', useJob: 'refresh', useResourceList: 'reload' }

/**
 * Ways to reload data in the component: kit hooks held in a variable (`x.refresh()` / `x.reload()`), destructured
 * `refresh` / `reload`, and a local function that bumps a state used as a `useEffect` dependency (`reload()`).
 */
export function refetches(ctx: Ctx): Refetch[] {
  const out: Refetch[] = []
  const stmts = bodyStatements(ctx)
  for (const s of stmts) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations) {
      const init = d.init && unwrap(d.init)
      if (!init || !isHookCall(init)) continue
      const hook = calleeName(init)!
      const key = REFRESH_KEY[hook]
      if (d.id.type === 'Identifier' && key) out.push({ label: d.id.name, call: `${d.id.name}.${key}()` })
      if (d.id.type === 'ObjectPattern') {
        for (const p of d.id.properties) {
          if (p.type !== 'ObjectProperty' || p.key.type !== 'Identifier' || p.value.type !== 'Identifier') continue
          if (['refresh', 'reload', 'refetch'].includes(p.key.name)) out.push({ label: `${hook}()`, call: `${p.value.name}()` })
        }
      }
    }
  }
  // a state that a useEffect depends on, bumped by a local function: that function reloads
  const deps = new Set<string>()
  for (const s of stmts) {
    if (s.type !== 'ExpressionStatement' || s.expression.type !== 'CallExpression' || calleeName(s.expression) !== 'useEffect') continue
    const arr = s.expression.arguments[1]
    if (arr?.type === 'ArrayExpression') for (const e of arr.elements) if (e?.type === 'Identifier') deps.add(e.name)
  }
  const bumpers = new Set(stateVars(ctx).filter((v) => deps.has(v.name)).map((v) => v.setter))
  for (const s of stmts) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations) {
      if (d.id.type !== 'Identifier' || !d.init) continue
      let f: t.Node = unwrap(d.init)
      if (f.type === 'CallExpression' && calleeName(f) === 'useCallback' && f.arguments[0]) f = f.arguments[0]
      if (!isFn(f) || f.params.length) continue
      let bumps = false
      walk(f.body, (n) => {
        if (n.type === 'CallExpression' && n.callee.type === 'Identifier' && bumpers.has(n.callee.name)) bumps = true
      })
      if (bumps) out.push({ label: d.id.name, call: `${d.id.name}()` })
    }
  }
  return out
}

// ── API operations (the generated client) ──────────────────────────────────

export interface ApiOp {
  name: string
  method: string
  path: string
  /** path placeholders (`{id}`) the call needs as its first argument */
  params: string[]
  body: boolean
  /** the call's positional arguments by name: `params`, `body`, `query` */
  args: string[]
  doc: string
  /** the list's key in the response (`rows`, `items`), '' when the response is the array itself, null when unknown */
  listKey: string | null
  /** fields of one row (or of the object returned), when knowable */
  fields: string[]
  /** top-level keys of the response object */
  top: string[]
}

export function clientFile(files: FileMap): string | null {
  if ('api/client.ts' in files) return 'api/client.ts'
  return Object.keys(files).find((p) => /\.tsx?$/.test(p) && /export const api\s*=\s*\{/.test(files[p])) ?? null
}

const LIST_KEYS = ['rows', 'items', 'results', 'data', 'records', 'list']

function typeKeys(n: t.Node | null | undefined): string[] {
  if (n?.type !== 'TSTypeLiteral') return []
  return n.members.flatMap((m) => (m.type === 'TSPropertySignature' && m.key.type === 'Identifier' ? [m.key.name] : m.type === 'TSPropertySignature' && m.key.type === 'StringLiteral' ? [m.key.value] : []))
}

function arrayElem(n: t.Node | null | undefined): t.Node | null {
  if (!n) return null
  if (n.type === 'TSArrayType') return n.elementType
  if (n.type === 'TSTypeReference' && n.typeName.type === 'Identifier' && n.typeName.name === 'Array') return n.typeParameters?.params[0] ?? null
  return null
}

function memberType(lit: t.TSTypeLiteral, key: string): t.Node | null {
  for (const m of lit.members) if (m.type === 'TSPropertySignature' && m.key.type === 'Identifier' && m.key.name === key) return m.typeAnnotation?.typeAnnotation ?? null
  return null
}

/** Response shape from the `XResponse` type alias. */
function shapeFromType(n: t.Node | null): Pick<ApiOp, 'listKey' | 'fields' | 'top'> | null {
  if (!n) return null
  const el = arrayElem(n)
  if (el) return { listKey: '', fields: typeKeys(el), top: [] }
  if (n.type !== 'TSTypeLiteral') return null
  const top = typeKeys(n)
  for (const k of LIST_KEYS) {
    const e = arrayElem(memberType(n, k))
    if (e) return { listKey: k, fields: typeKeys(e), top }
  }
  return { listKey: null, fields: top, top }
}

/** Keys at depth 1 of the first `{…}` starting at `i` in a doc sentence: `{rows: [{id, status}], total}`. */
function braceKeys(s: string, i: number): { keys: string[]; inner: Record<string, string>; end: number } {
  const keys: string[] = []
  const inner: Record<string, string> = {}
  let depth = 0
  let cur = ''
  let key: string | null = null
  let start = -1
  for (let j = i; j < s.length; j++) {
    const ch = s[j]
    if (ch === '{' || ch === '[') {
      depth++
      if (depth === 2 && key && start < 0) start = j
      if (depth === 1) continue
    } else if (ch === '}' || ch === ']') {
      depth--
      if (depth === 1 && key && start >= 0) {
        inner[key] = s.slice(start, j + 1)
        start = -1
      }
      if (depth === 0) {
        const k = /^\s*\.{0,3}\s*([A-Za-z_][\w]*)/.exec(cur)?.[1]
        if (k && !keys.includes(k)) keys.push(k)
        return { keys, inner, end: j }
      }
    }
    if (depth === 1) {
      if (ch === ',') {
        const k = /^\s*\.{0,3}\s*([A-Za-z_][\w]*)/.exec(cur)?.[1]
        if (k && !keys.includes(k)) keys.push(k)
        cur = ''
        key = null
      } else if (ch === ':') {
        key = /([A-Za-z_][\w]*)\s*$/.exec(cur)?.[1] ?? null
        cur += ch
      } else cur += ch
    }
  }
  return { keys, inner, end: s.length }
}

/** Response shape from the route's doc comment: `Returns {rows: [{id, status, …}], total, limit, offset}` or `Returns [{…}]`. */
export function shapeFromDoc(doc: string): Pick<ApiOp, 'listKey' | 'fields' | 'top'> | null {
  const m = /Returns\b[^{[]*?([{[])/.exec(doc)
  if (!m) return null
  const at = m.index + m[0].length - 1
  if (m[1] === '[') {
    const b = doc.indexOf('{', at)
    return b < 0 ? null : { listKey: '', fields: braceKeys(doc, b).keys, top: [] }
  }
  const { keys, inner } = braceKeys(doc, at)
  for (const k of LIST_KEYS) {
    if (!(k in inner)) continue
    const b = inner[k].indexOf('{')
    return { listKey: k, fields: b < 0 ? [] : braceKeys(inner[k], b).keys, top: keys }
  }
  return { listKey: null, fields: keys, top: keys }
}

/** The operations of the generated client (`export const api = { name: (params?, body?, query?) => call('GET', `/x`) }`). */
export function apiOps(files: FileMap): ApiOp[] {
  const file = clientFile(files)
  const ast = file ? tryParse(files[file]) : null
  if (!file || !ast) return []
  const src = files[file]
  const aliases = new Map<string, t.Node>()
  let obj: t.ObjectExpression | null = null
  for (const s0 of ast.program.body) {
    const s = s0.type === 'ExportNamedDeclaration' && s0.declaration ? s0.declaration : s0
    if (s.type === 'TSTypeAliasDeclaration') aliases.set(s.id.name, s.typeAnnotation)
    if (s.type === 'VariableDeclaration') {
      for (const d of s.declarations) if (d.id.type === 'Identifier' && d.id.name === 'api' && d.init && unwrap(d.init).type === 'ObjectExpression') obj = unwrap(d.init) as t.ObjectExpression
    }
  }
  if (!obj) return []
  const out: ApiOp[] = []
  for (const p of (obj as t.ObjectExpression).properties) {
    if (p.type !== 'ObjectProperty' || p.key.type !== 'Identifier' || !isFn(p.value)) continue
    const fn = p.value
    const names = fn.params.map((q) => (q.type === 'Identifier' ? q.name : ''))
    const first = fn.params[0]
    const params = first?.type === 'Identifier' && first.name === 'params' && first.typeAnnotation?.type === 'TSTypeAnnotation' ? typeKeys(first.typeAnnotation.typeAnnotation) : []
    let method = ''
    let path = ''
    walk(fn.body, (n) => {
      if (method || n.type !== 'CallExpression') return
      const [m, pth] = n.arguments
      if (m?.type === 'StringLiteral' && /^[A-Z]+$/.test(m.value)) {
        method = m.value
        if (pth?.type === 'TemplateLiteral') path = pth.quasis.map((q, i) => q.value.raw + (i < pth.expressions.length ? `{${/params\.(\w+)/.exec(text(src, pth.expressions[i]))?.[1] ?? '?'}}` : '')).join('')
        else if (pth?.type === 'StringLiteral') path = pth.value
      }
    })
    const doc = (p.leadingComments ?? []).map((c) => c.value.replace(/^\*+/, '').replace(/\n\s*\*?/g, ' ').trim()).join(' ')
    const ret = fn.returnType?.type === 'TSTypeAnnotation' ? fn.returnType.typeAnnotation : null
    const inner = ret?.type === 'TSTypeReference' && ret.typeParameters?.params[0]
    const alias = inner && inner.type === 'TSTypeReference' && inner.typeName.type === 'Identifier' ? aliases.get(inner.typeName.name) ?? null : inner || null
    const shape = shapeFromType(alias) ?? shapeFromDoc(doc) ?? { listKey: null, fields: [], top: [] }
    out.push({ name: p.key.name, method, path, params, body: names.includes('body'), args: names, doc, ...shape })
  }
  return out
}

// ── Resources (`api/resources.ts`) ──────────────────────────────────────────

export interface ResourceInfo {
  /** the exported const */
  name: string
  /** the def's own `name` (what `resource="…"` strings use, with the registry key) */
  key: string
  file: string
  fields: string[]
}

export function resources(files: FileMap): ResourceInfo[] {
  const out: ResourceInfo[] = []
  for (const [file, src] of Object.entries(files)) {
    if (!/\.tsx?$/.test(file) || !/ResourceDef</.test(src) || /(^|\/)kit\//.test(file)) continue
    const ast = tryParse(src)
    if (!ast) continue
    for (const s of ast.program.body) {
      if (s.type !== 'ExportNamedDeclaration' || s.declaration?.type !== 'VariableDeclaration') continue
      for (const d of s.declaration.declarations) {
        const ann = d.id.type === 'Identifier' && d.id.typeAnnotation?.type === 'TSTypeAnnotation' ? d.id.typeAnnotation.typeAnnotation : null
        const isDef = ann?.type === 'TSTypeReference' && ann.typeName.type === 'Identifier' && ann.typeName.name === 'ResourceDef'
        const init = d.init && unwrap(d.init)
        if (!isDef || d.id.type !== 'Identifier' || init?.type !== 'ObjectExpression') continue
        const fields: string[] = []
        let key = d.id.name
        for (const p of init.properties) {
          if (p.type === 'ObjectProperty' && p.key.type === 'Identifier' && p.key.name === 'name' && p.value.type === 'StringLiteral') key = p.value.value
          if (p.type !== 'ObjectProperty' || p.key.type !== 'Identifier' || p.key.name !== 'fields' || p.value.type !== 'ArrayExpression') continue
          for (const f of p.value.elements) {
            if (f?.type !== 'ObjectExpression') continue
            for (const q of f.properties) if (q.type === 'ObjectProperty' && q.key.type === 'Identifier' && q.key.name === 'name' && q.value.type === 'StringLiteral') fields.push(q.value.value)
          }
        }
        out.push({ name: d.id.name, key, file, fields })
      }
    }
  }
  return out
}

// ── Splices shared by the sections ──────────────────────────────────────────

/** Edits that make `names` importable in the file from module `spec` (`kit` matches any kit path), merging into an existing import. */
export function ensureImports(ctx: Ctx, names: string[], spec: string, isSame: (source: string) => boolean = (s) => s === spec): TextEdit[] {
  const have = importsOf(ctx.ast)
  const need = names.filter((n) => !have.has(n))
  if (!need.length) return []
  const imports = ctx.ast.program.body.filter((s): s is t.ImportDeclaration => s.type === 'ImportDeclaration')
  const target = imports.find((s) => s.importKind !== 'type' && isSame(s.source.value) && s.specifiers.some((x) => x.type === 'ImportSpecifier'))
  if (target) {
    const last = [...target.specifiers].reverse().find((x) => x.type === 'ImportSpecifier')!
    return [{ start: last.end!, end: last.end!, text: need.map((n) => `, ${n}`).join('') }]
  }
  const q = quoteStyle(ctx.src)
  const line = `import { ${need.join(', ')} } from ${q}${spec}${q}`
  const last = imports[imports.length - 1]
  return [last ? { start: last.end!, end: last.end!, text: `\n${line}` } : { start: 0, end: 0, text: `${line}\n` }]
}

/** Import kit exports into the file (from its existing kit import, or a new one to `kit/`). */
export function ensureKit(ctx: Ctx, names: string[]): TextEdit[] {
  const kitIndex = Object.keys(ctx.files).find((p) => /(^|\/)kit\/index\.tsx?$/.test(p))
  const spec = relSpec(ctx.file, kitIndex ? kitIndex.replace(/\/index\.tsx?$/, '') : 'kit')
  return ensureImports(ctx, names, spec, (s) => isKitImport(s) && /kit\/?$/.test(s))
}

export function ensureReact(ctx: Ctx, names: string[]): TextEdit[] {
  return ensureImports(ctx, names, 'react')
}

/** Import the generated client's `api` when the file doesn't have it; null when the app has no client. */
export function ensureApi(ctx: Ctx): { local: string; edits: TextEdit[] } | null {
  const local = apiLocal(ctx)
  if (local) return { local, edits: [] }
  const client = clientFile(ctx.files)
  if (!client) return null
  if (importsOf(ctx.ast).has('api') || declared(ctx.ast).has('api')) return null
  return { local: 'api', edits: ensureImports(ctx, ['api'], relSpec(ctx.file, client)) }
}

/** Every name declared anywhere in the file (for picking a fresh one). */
export function declared(ast: t.File): Set<string> {
  const out = new Set<string>()
  walk(ast.program, (n) => {
    if (n.type === 'VariableDeclarator') for (const x of patternNames(n.id)) out.add(x)
    else if ((n.type === 'FunctionDeclaration' || n.type === 'ClassDeclaration') && n.id) out.add(n.id.name)
    else if (isFn(n)) for (const q of n.params) for (const x of patternNames(q)) out.add(x)
    else if (n.type === 'ImportSpecifier' || n.type === 'ImportDefaultSpecifier' || n.type === 'ImportNamespaceSpecifier') out.add(n.local.name)
  })
  return out
}

export function freshName(ast: t.File, base: string): string {
  const taken = declared(ast)
  const b = /^[A-Za-z_$][\w$]*$/.test(base) ? base : 'value'
  if (!taken.has(b)) return b
  for (let i = 2; ; i++) if (!taken.has(`${b}${i}`)) return `${b}${i}`
}

/**
 * Insert a statement in the component body: after the last of its leading hook / state declarations (so it reads in the
 * file's order), or first in the body. `after` narrows to statements matching it (e.g. only useState ones).
 */
export function insertStatement(ctx: Ctx, line: string, after?: (s: t.Statement) => boolean): TextEdit | null {
  const body = ctx.body
  if (!body) return null
  const stmts = body.body
  const isHookDecl = (s: t.Statement) => s.type === 'VariableDeclaration' && s.declarations.some((d) => !!d.init && isHookCall(unwrap(d.init)))
  const pick = after ?? isHookDecl
  let anchor: t.Statement | null = null
  for (const s of stmts) {
    if (pick(s)) anchor = s
    else if (anchor && !after) break
  }
  if (anchor) return { start: anchor.end!, end: anchor.end!, text: `\n${indentAt(ctx.src, anchor.start!)}${line}` }
  const first = stmts[0]
  if (first) return { start: first.start!, end: first.start!, text: `${line}\n${indentAt(ctx.src, first.start!)}` }
  const ind = indentAt(ctx.src, body.start!) + '  '
  return { start: body.start! + 1, end: body.start! + 1, text: `\n${ind}${line}` }
}

/** The result of a wiring edit: whole-file patches for the shared history, and where the selected element starts afterwards. */
export type Built = { ok: true; label: string; patches: FilePatch[]; focus: number | null } | { ok: false; error: string }

const NOTHING = 'Nothing to change'

/** Apply edits to the element's file and re-parse; `focus` follows the element's start through the edits. */
export function finish(ctx: Ctx, label: string, edits: TextEdit[]): Built {
  if (!edits.length) return { ok: false, error: NOTHING }
  let after: string | null
  try {
    after = applyChecked(ctx.src, edits)
  } catch {
    return { ok: false, error: 'Those changes overlap; use AI or code' }
  }
  if (after === null) return { ok: false, error: 'That edit would break the file; use AI or code' }
  if (after === ctx.src) return { ok: false, error: NOTHING }
  const at = ctx.el.start!
  const focus = at + edits.filter((e) => e.end <= at && !(e.start === at && e.end === at && e.text.startsWith('<'))).reduce((d, e) => d + e.text.length - (e.end - e.start), 0)
  return { ok: true, label: `${ctx.name}: ${label}`, patches: [{ path: ctx.file, before: ctx.src, after }], focus }
}

/** The structural key of the element starting at `focus` in the patched file (to keep the selection on it). */
export function keyAt(src: string, focus: number): string | null {
  const ast = tryParse(src)
  return ast ? indexElements(ast).find((e) => e.node.start === focus)?.key ?? null : null
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^\w/, (c) => c.toUpperCase())
}
