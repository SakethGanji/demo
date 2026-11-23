/**
 * Reading the generated app's source for the visual editor: parse with @babel/parser, find the JSX element a preview click
 * points at (`file:line:col` from the jsxDEV tags), give it a structural key that survives most edits, and classify its
 * props as literal (editable) or not. Nothing here prints code; edits are byte-offset splices (splice.ts).
 */
import { parse } from '@babel/parser'
import type * as t from '@babel/types'

export type FileMap = Record<string, string>

const cache = new Map<string, t.File>()

/** Parse (cached by text; throws on a syntax error). */
export function parseSource(src: string): t.File {
  const hit = cache.get(src)
  if (hit) return hit
  const ast = parse(src, { sourceType: 'module', plugins: ['typescript', 'jsx'] })
  if (cache.size > 64) cache.delete(cache.keys().next().value as string)
  cache.set(src, ast)
  return ast
}

export function tryParse(src: string): t.File | null {
  try {
    return parseSource(src)
  } catch {
    return null
  }
}

const SKIP = new Set(['loc', 'start', 'end', 'leadingComments', 'trailingComments', 'innerComments', 'extra', 'range'])

/** Depth-first over every node; `visit` gets the node and its ancestors (outermost first). */
export function walk(root: t.Node, visit: (node: t.Node, parents: t.Node[]) => void): void {
  const parents: t.Node[] = []
  const go = (n: t.Node) => {
    visit(n, parents)
    parents.push(n)
    for (const k of Object.keys(n)) {
      if (SKIP.has(k)) continue
      const v = (n as unknown as Record<string, unknown>)[k]
      if (Array.isArray(v)) {
        for (const c of v) if (c && typeof c === 'object' && typeof (c as t.Node).type === 'string') go(c as t.Node)
      } else if (v && typeof v === 'object' && typeof (v as t.Node).type === 'string') go(v as t.Node)
    }
    parents.pop()
  }
  go(root)
}

export function jsxName(n: t.JSXIdentifier | t.JSXMemberExpression | t.JSXNamespacedName): string {
  if (n.type === 'JSXIdentifier') return n.name
  if (n.type === 'JSXNamespacedName') return `${n.namespace.name}:${n.name.name}`
  return `${jsxName(n.object)}.${n.property.name}`
}

// ── Structural keys ─────────────────────────────────────────────────────────

export interface IndexedElement {
  node: t.JSXElement
  /** `<top-level declaration>/<Outer>Inner>Name#n`: stable across line shifts; n counts same-chain elements in source order */
  key: string
  name: string
  /** names of the enclosing JSX elements, outermost first */
  chain: string[]
}

const indexCache = new WeakMap<t.File, IndexedElement[]>()

function topName(stmt: t.Node | undefined): string {
  if (!stmt) return 'module'
  if (stmt.type === 'ExportDefaultDeclaration' || stmt.type === 'ExportNamedDeclaration') {
    const d = stmt.declaration
    if (!d) return 'module'
    return stmt.type === 'ExportDefaultDeclaration' && !('id' in d && d.id) ? 'default' : topName(d)
  }
  if ((stmt.type === 'FunctionDeclaration' || stmt.type === 'ClassDeclaration') && stmt.id) return stmt.id.name
  if (stmt.type === 'VariableDeclaration') {
    const id = stmt.declarations[0]?.id
    if (id?.type === 'Identifier') return id.name
  }
  return 'module'
}

/** Every JSX element of a file with its structural key. */
export function indexElements(ast: t.File): IndexedElement[] {
  const hit = indexCache.get(ast)
  if (hit) return hit
  const out: IndexedElement[] = []
  const seen = new Map<string, number>()
  walk(ast.program, (n, parents) => {
    if (n.type !== 'JSXElement') return
    const top = topName(parents[1])
    const chain = parents.filter((p): p is t.JSXElement => p.type === 'JSXElement').map((p) => jsxName(p.openingElement.name))
    const name = jsxName(n.openingElement.name)
    const base = `${top}/${[...chain, name].join('>')}`
    const i = seen.get(base) ?? 0
    seen.set(base, i + 1)
    out.push({ node: n, key: `${base}#${i}`, name, chain })
  })
  indexCache.set(ast, out)
  return out
}

/** The element a jsxDEV tag points at: tags carry the 1-based line and Babel's 0-based column + 1. */
export function elementAt(ast: t.File, line: number, col: number): IndexedElement | null {
  return indexElements(ast).find((e) => e.node.loc?.start.line === line && e.node.loc.start.column === col - 1) ?? null
}

export function elementByKey(ast: t.File, key: string): IndexedElement | null {
  return indexElements(ast).find((e) => e.key === key) ?? null
}

/** `views/A.tsx:12:5` → parts (file names may contain colons only in theory; split from the right). */
export function parseVsrc(vsrc: string): { file: string; line: number; col: number } | null {
  const m = /^(.*):(\d+):(\d+)$/.exec(vsrc)
  return m ? { file: m[1], line: Number(m[2]), col: Number(m[3]) } : null
}

export function vsrcOf(file: string, el: t.JSXElement): string {
  return `${file}:${el.loc!.start.line}:${el.loc!.start.column + 1}`
}

// ── Imports and constants ───────────────────────────────────────────────────

/** Local name → module specifier and imported name, for the file's imports. */
export function importsOf(ast: t.File): Map<string, { source: string; imported: string }> {
  const out = new Map<string, { source: string; imported: string }>()
  for (const s of ast.program.body) {
    if (s.type !== 'ImportDeclaration') continue
    for (const sp of s.specifiers) {
      const imported = sp.type === 'ImportSpecifier' ? (sp.imported.type === 'Identifier' ? sp.imported.name : sp.imported.value) : 'default'
      out.set(sp.local.name, { source: s.source.value, imported })
    }
  }
  return out
}

/** A kit component: imported from `kit` (any relative depth), e.g. `./kit`, `../kit`, `../kit/ui`. */
export function isKitImport(source: string): boolean {
  return /^(\.{1,2}\/)+kit(\/|$)/.test(source)
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

export function resolveImport(files: FileMap, from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null
  const p = joinPath(dirname(from), spec)
  for (const c of [p, `${p}.ts`, `${p}.tsx`, `${p}/index.ts`, `${p}/index.tsx`]) if (c in files) return c
  return null
}

/** Strip `as const`, `satisfies T`, `<T>x` and parentheses. */
export function unwrap(n: t.Node): t.Node {
  let x = n
  while (x.type === 'TSAsExpression' || x.type === 'TSSatisfiesExpression' || x.type === 'TSTypeAssertion' || x.type === 'TSNonNullExpression' || x.type === 'ParenthesizedExpression') {
    x = x.expression
  }
  return x
}

function constIn(stmts: t.Statement[], name: string): t.Expression | null {
  for (const s0 of stmts) {
    const s = s0.type === 'ExportNamedDeclaration' && s0.declaration ? s0.declaration : s0
    if (s.type !== 'VariableDeclaration' || s.kind !== 'const') continue
    for (const d of s.declarations) if (d.id.type === 'Identifier' && d.id.name === name && d.init) return d.init
  }
  return null
}

export interface ConstRef {
  file: string
  name: string
  /** the initializer, unwrapped */
  node: t.Node
  /** declared at module level (shared by every use) or inside a component */
  scope: 'module' | 'local'
  /** the declaring statement list (Program or BlockStatement): where uses are counted */
  block?: t.Node
}

/**
 * Follow an identifier to the `const` that defines it: the enclosing function bodies first, then the module, then one
 * relative import hop. Null for parameters, state, `let` and anything computed elsewhere.
 */
export function resolveConst(files: FileMap, file: string, ast: t.File, at: t.Node, name: string): ConstRef | null {
  let found: ConstRef | null = null
  walk(ast.program, (n, parents) => {
    if (found || n !== at) return
    for (let i = parents.length - 1; i >= 0; i--) {
      const p = parents[i]
      if (p.type === 'BlockStatement' || p.type === 'Program') {
        const init = constIn(p.body, name)
        if (init) {
          found = { file, name, node: unwrap(init), scope: p.type === 'Program' ? 'module' : 'local', block: p }
          return
        }
      }
    }
  })
  if (found) return found
  const imp = importsOf(ast).get(name)
  if (!imp || isKitImport(imp.source)) return null
  const target = resolveImport(files, file, imp.source)
  if (!target) return null
  const other = tryParse(files[target])
  const init = other && constIn(other.program.body, imp.imported)
  return init ? { file: target, name: imp.imported, node: unwrap(init), scope: 'module', block: other.program } : null
}

// ── Following a variable to its declaration (same file) ─────────────────────

export type ConstFollow = { ok: true; ref: ConstRef } | { ok: false; reason: string }

const MUTATORS = new Set(['push', 'pop', 'shift', 'unshift', 'splice', 'sort', 'reverse', 'fill', 'set', 'delete', 'clear'])

function lineOf(n: t.Node): number {
  return n.loc?.start.line ?? 0
}

function clip(s: string, n = 40): string {
  const one = s.replace(/\s+/g, ' ')
  return one.length > n ? `${one.slice(0, n)}…` : one
}

/** Names a binding pattern declares (`a`, `{ a, b: c }`, `[a, ...rest]`). */
function patternNames(p: t.Node): string[] {
  switch (p.type) {
    case 'Identifier':
      return [p.name]
    case 'ObjectPattern':
      return p.properties.flatMap((q) => patternNames(q.type === 'RestElement' ? q.argument : q.value))
    case 'ArrayPattern':
      return p.elements.flatMap((e) => (e ? patternNames(e) : []))
    case 'AssignmentPattern':
      return patternNames(p.left)
    case 'RestElement':
      return patternNames(p.argument)
    case 'TSParameterProperty':
      return patternNames(p.parameter)
    default:
      return []
  }
}

function calleeText(src: string, c: t.Node): string {
  return clip(src.slice(c.start!, c.end!), 30)
}

/** Why a const initializer can't be edited as a value, or null when it is a literal, an array / object literal or JSX. */
export function constInitRefusal(src: string, name: string, init: t.Node): string | null {
  const x = unwrap(init)
  if (literalValue(x) !== undefined) return null
  if (x.type === 'JSXElement' || x.type === 'JSXFragment') return null
  if (x.type === 'ArrayExpression') {
    return x.elements.some((e) => e?.type === 'SpreadElement') ? `${name} is built with a spread (...); edit it with AI / code` : null
  }
  if (x.type === 'ObjectExpression') {
    return x.properties.some((p) => p.type === 'SpreadElement') ? `${name} is built with a spread (...); edit it with AI / code` : null
  }
  if (x.type === 'CallExpression' || x.type === 'NewExpression') return `${name} is the result of ${calleeText(src, x.callee)}(); edit it with AI / code`
  if (x.type === 'AwaitExpression') return `${name} is loaded at runtime; edit it with AI / code`
  if (x.type === 'TemplateLiteral') return `${name} is a template with values in it (${clip(src.slice(x.start!, x.end!))}); edit it with AI / code`
  if (x.type === 'ArrowFunctionExpression' || x.type === 'FunctionExpression') return `${name} is a function; edit it with AI / code`
  return `${name} is computed (${clip(src.slice(x.start!, x.end!))}); edit it with AI / code`
}

/** Whether the file reassigns `name` or mutates what it holds (`X.a = …`, `X.push(…)`): then its literal isn't what renders. */
function mutationOf(ast: t.File, name: string): t.Node | null {
  const rootOf = (n: t.Node): t.Node => {
    let x = unwrap(n)
    while (x.type === 'MemberExpression' || x.type === 'OptionalMemberExpression') x = unwrap(x.object)
    return x
  }
  let hit: t.Node | null = null
  walk(ast.program, (n) => {
    if (hit) return
    if (n.type === 'AssignmentExpression' || n.type === 'UpdateExpression') {
      const target = n.type === 'AssignmentExpression' ? n.left : n.argument
      const r = rootOf(target)
      if (r.type === 'Identifier' && r.name === name) hit = n
    } else if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && n.callee.property.type === 'Identifier' && MUTATORS.has(n.callee.property.name)) {
      const r = rootOf(n.callee.object)
      if (r.type === 'Identifier' && r.name === name) hit = n
    }
  })
  return hit
}

/**
 * Follow an identifier used at `at` to its declaration IN THIS FILE, innermost scope first, and say whether its value can be
 * edited where it is declared: a `const` holding a literal, an array / object literal (no spread) or JSX. Everything else
 * is refused with the reason (let / var, reassignment or mutation, imports, parameters and props, destructuring such as
 * state, function results, computed values). Null when the name isn't declared in the file at all (a global).
 */
export function followConst(files: FileMap, file: string, ast: t.File, at: t.Node, name: string): ConstFollow | null {
  const src = files[file]
  let chain: t.Node[] | null = null
  walk(ast.program, (n, parents) => {
    if (!chain && n === at) chain = [...parents]
  })
  if (!chain) return null
  const parents = chain as t.Node[]
  for (let i = parents.length - 1; i >= 0; i--) {
    const p = parents[i]
    if (p.type === 'BlockStatement' || p.type === 'Program') {
      for (const s0 of p.body) {
        const s = (s0.type === 'ExportNamedDeclaration' || s0.type === 'ExportDefaultDeclaration') && s0.declaration ? s0.declaration : s0
        if (s.type === 'ImportDeclaration') {
          const sp = s.specifiers.find((x) => x.local.name === name)
          if (sp) return { ok: false, reason: `${name} is imported from ${s.source.value}; edit it in that file or with AI` }
        }
        if (s.type === 'FunctionDeclaration' && s.id?.name === name) return { ok: false, reason: `${name} is a function; edit it with AI / code` }
        if (s.type !== 'VariableDeclaration') continue
        for (const d of s.declarations) {
          if (!patternNames(d.id).includes(name)) continue
          if (s.kind !== 'const') return { ok: false, reason: `${name} is declared with ${s.kind} (line ${lineOf(d)}), so it can change while the app runs; edit it with AI / code` }
          if (d.id.type !== 'Identifier') {
            const from = d.init ? clip(src.slice(d.init.start!, d.init.end!), 30) : 'a value'
            return { ok: false, reason: `${name} is unpacked from ${from}${/^use[A-Z]/.test(from) ? ' (app state)' : ''}; edit it with AI / code` }
          }
          if (!d.init) return { ok: false, reason: `${name} has no value in the code` }
          const why = constInitRefusal(src, name, d.init)
          if (why) return { ok: false, reason: why }
          const m = mutationOf(ast, name)
          if (m) return { ok: false, reason: `${name} is changed by code at line ${lineOf(m)}; edit it with AI / code` }
          return { ok: true, ref: { file, name, node: unwrap(d.init), scope: p.type === 'Program' ? 'module' : 'local', block: p } }
        }
      }
    }
    if (p.type === 'ArrowFunctionExpression' || p.type === 'FunctionExpression' || p.type === 'FunctionDeclaration' || p.type === 'ObjectMethod' || p.type === 'ClassMethod') {
      if (p.params.some((q) => patternNames(q).includes(name))) {
        const owner = p.type === 'FunctionDeclaration' && p.id ? p.id.name : null
        const parent = parents[i - 1]
        const named = owner ?? (parent?.type === 'VariableDeclarator' && parent.id.type === 'Identifier' ? parent.id.name : null)
        const comp = named && /^[A-Z]/.test(named)
        return { ok: false, reason: comp ? `${name} is a prop of ${named}; set it where ${named} is used` : `${name} is a parameter${parent?.type === 'CallExpression' ? ' of a list callback (one value per item)' : ''}; edit it with AI / code` }
      }
    }
    if ((p.type === 'ForOfStatement' || p.type === 'ForInStatement') && p.left.type === 'VariableDeclaration' && p.left.declarations.some((d) => patternNames(d.id).includes(name))) {
      return { ok: false, reason: `${name} is a loop variable; edit it with AI / code` }
    }
  }
  return null
}

/** Whether identifier `n` (child of `parent`) reads a variable, rather than naming a property, a declaration or a type. */
function isReference(n: t.Identifier, parent: t.Node | undefined): boolean {
  if (!parent) return true
  switch (parent.type) {
    case 'VariableDeclarator':
      return parent.id !== n
    case 'MemberExpression':
    case 'OptionalMemberExpression':
      return parent.object === n || parent.computed
    case 'ObjectProperty':
      // `{ title }` reads title (counted once: countRefs dedupes the key and value of a shorthand by position)
      return parent.key !== n || parent.computed || parent.shorthand
    case 'ObjectMethod':
    case 'ClassMethod':
    case 'ClassProperty':
      return parent.key !== n || parent.computed
    case 'ImportSpecifier':
    case 'ImportDefaultSpecifier':
    case 'ImportNamespaceSpecifier':
    case 'ExportSpecifier':
    case 'FunctionDeclaration':
    case 'LabeledStatement':
    case 'BreakStatement':
    case 'ContinueStatement':
      return false
    default:
      return !parent.type.startsWith('TS') || ['TSAsExpression', 'TSSatisfiesExpression', 'TSNonNullExpression', 'TSTypeAssertion'].includes(parent.type)
  }
}

function countRefs(root: t.Node, name: string): number {
  const at = new Set<number>()
  walk(root, (x, parents) => {
    if (x.type === 'Identifier' && x.name === name && isReference(x, parents[parents.length - 1])) at.add(x.start!)
  })
  return at.size
}

/**
 * How many places read a const: references in its declaring scope, plus (for a module-level const) in every file that
 * imports it. Shadowing by an inner declaration of the same name is not subtracted (rare in generated apps).
 */
export function constUses(files: FileMap, ref: ConstRef): number {
  const src = files[ref.file]
  const ast = src == null ? null : tryParse(src)
  if (!ast) return 0
  const block = ref.block ?? ast.program
  let n = countRefs(block, ref.name)
  if (ref.scope !== 'module') return n
  for (const [path, other] of Object.entries(files)) {
    if (path === ref.file || !/\.(t|j)sx?$/.test(path)) continue
    const oast = tryParse(other)
    if (!oast) continue
    for (const [local, imp] of importsOf(oast)) {
      if (imp.imported === ref.name && resolveImport(files, path, imp.source) === ref.file) n += countRefs(oast.program, local)
    }
  }
  return n
}

// ── Value classification ────────────────────────────────────────────────────

export type ValueKind =
  | 'string' | 'number' | 'boolean' | 'null' | 'array' | 'object'
  | 'bound' | 'event' | 'lambda' | 'template' | 'jsx' | 'expr'

export interface Value {
  kind: ValueKind
  /** the literal's value for string/number/boolean */
  value?: string | number | boolean | null
  /** the node to splice (literal, array or object), in `file` */
  node: t.Node
  file: string
  /** set when the value comes through a named const */
  via?: ConstRef
  /** source text of the expression as written at the use site */
  src: string
  /** a variable that couldn't be followed to an editable const: why (let, a prop, state, a call…) */
  reason?: string
}

export function isLiteralKind(k: ValueKind): boolean {
  return k === 'string' || k === 'number' || k === 'boolean' || k === 'null' || k === 'array' || k === 'object'
}

/** Classify an expression (no const following). */
export function classifyExpr(n: t.Node): ValueKind {
  const x = unwrap(n)
  switch (x.type) {
    case 'StringLiteral':
      return 'string'
    case 'TemplateLiteral':
      return x.expressions.length ? 'template' : 'string'
    case 'NumericLiteral':
      return 'number'
    case 'UnaryExpression':
      return x.operator === '-' && x.argument.type === 'NumericLiteral' ? 'number' : 'expr'
    case 'BooleanLiteral':
      return 'boolean'
    case 'NullLiteral':
      return 'null'
    case 'ArrayExpression':
      return 'array'
    case 'ObjectExpression':
      return 'object'
    case 'Identifier':
      return x.name === 'undefined' ? 'null' : 'bound'
    case 'MemberExpression':
    case 'OptionalMemberExpression':
      return 'bound'
    case 'ArrowFunctionExpression':
    case 'FunctionExpression':
      return 'lambda'
    case 'JSXElement':
    case 'JSXFragment':
      return 'jsx'
    default:
      return 'expr'
  }
}

export function literalValue(n: t.Node): string | number | boolean | null | undefined {
  const x = unwrap(n)
  if (x.type === 'StringLiteral') return x.value
  if (x.type === 'TemplateLiteral' && !x.expressions.length) return x.quasis.map((q) => q.value.cooked ?? q.value.raw).join('')
  if (x.type === 'NumericLiteral') return x.value
  if (x.type === 'UnaryExpression' && x.operator === '-' && x.argument.type === 'NumericLiteral') return -x.argument.value
  if (x.type === 'BooleanLiteral') return x.value
  if (x.type === 'NullLiteral') return null
  return undefined
}

/** Classify a value at a use site, following a bare identifier to its const. */
export function valueOf(files: FileMap, file: string, ast: t.File, n: t.Node, propName = ''): Value {
  const src = files[file].slice(n.start!, n.end!)
  const x = unwrap(n)
  let kind = classifyExpr(x)
  if (kind === 'lambda' && /^on[A-Z]/.test(propName)) kind = 'event'
  if (x.type === 'Identifier' && kind === 'bound') {
    const ref = resolveConst(files, file, ast, x, x.name)
    if (ref) {
      const k = classifyExpr(ref.node)
      if (isLiteralKind(k)) return { kind: k, value: literalValue(ref.node), node: ref.node, file: ref.file, via: ref, src }
      // a const holding JSX is a slot defined elsewhere in the file (its elements are selectable in the preview)
      if (k === 'jsx') return { kind: 'jsx', node: ref.node, file: ref.file, via: ref, src }
    }
    const f = followConst(files, file, ast, x, x.name)
    if (f && !f.ok) return { kind, value: literalValue(x), node: x, file, src, reason: f.reason }
  }
  // `MAP.key` / `MAP['key']` on a const object: the entry's literal, spliced where the map is declared
  if ((x.type === 'MemberExpression' || x.type === 'OptionalMemberExpression') && unwrap(x.object).type === 'Identifier') {
    const obj = unwrap(x.object) as t.Identifier
    const key = !x.computed && x.property.type === 'Identifier' ? x.property.name : x.computed && x.property.type === 'StringLiteral' ? x.property.value : null
    const ref = resolveConst(files, file, ast, obj, obj.name)
    if (ref && ref.node.type === 'ObjectExpression') {
      const entry = key !== null ? objectProp(ref.node, key) : null
      if (entry && !mutationOf(tryParse(files[ref.file]) ?? ast, ref.name)) {
        const v = unwrap(entry.value)
        const k = classifyExpr(v)
        if (isLiteralKind(k) && k !== 'array' && k !== 'object') return { kind: k, value: literalValue(v), node: v, file: ref.file, via: { ...ref, node: v }, src }
      }
      if (key === null) {
        const by = files[file].slice(x.property.start!, x.property.end!)
        return { kind, value: undefined, node: x, file, src, reason: `Picked from ${ref.name} by ${clip(by, 30)} (one value per case); edit the ${ref.name} map with AI / code` }
      }
    }
  }
  return { kind, value: literalValue(x), node: x, file, src }
}

export interface PropRead {
  name: string
  attr: t.JSXAttribute
  value: Value
}

/** The element's named attributes (spreads are reported separately). */
export function readProps(files: FileMap, file: string, ast: t.File, el: t.JSXElement): { props: PropRead[]; spreads: number } {
  const props: PropRead[] = []
  let spreads = 0
  for (const a of el.openingElement.attributes) {
    if (a.type === 'JSXSpreadAttribute') {
      spreads++
      continue
    }
    const name = a.name.type === 'JSXIdentifier' ? a.name.name : `${a.name.namespace.name}:${a.name.name.name}`
    if (!a.value) {
      props.push({ name, attr: a, value: { kind: 'boolean', value: true, node: a, file, src: '' } })
    } else if (a.value.type === 'StringLiteral') {
      props.push({ name, attr: a, value: { kind: 'string', value: a.value.value, node: a.value, file, src: files[file].slice(a.value.start!, a.value.end!) } })
    } else if (a.value.type === 'JSXExpressionContainer') {
      const e = a.value.expression
      if (e.type === 'JSXEmptyExpression') continue
      props.push({ name, attr: a, value: valueOf(files, file, ast, e, name) })
    } else {
      props.push({ name, attr: a, value: { kind: 'jsx', node: a.value, file, src: files[file].slice(a.value.start!, a.value.end!) } })
    }
  }
  return { props, spreads }
}

/** Literal text children (whitespace-only text is skipped). */
export function textChildren(el: t.JSXElement): t.JSXText[] {
  return el.children.filter((c): c is t.JSXText => c.type === 'JSXText' && c.value.trim() !== '')
}

/** The key of an object property, when it is a plain name or string. */
export function propKey(p: t.ObjectProperty): string | null {
  if (p.computed) return null
  if (p.key.type === 'Identifier') return p.key.name
  if (p.key.type === 'StringLiteral') return p.key.value
  return null
}

export function objectProp(obj: t.ObjectExpression, key: string): t.ObjectProperty | null {
  for (const p of obj.properties) if (p.type === 'ObjectProperty' && propKey(p) === key) return p
  return null
}

/** Find a node of `type` starting at `start` (re-locating a node in a fresh parse). */
export function nodeAt<T extends t.Node['type']>(ast: t.File, start: number, type: T): Extract<t.Node, { type: T }> | null {
  let hit: t.Node | null = null
  walk(ast.program, (n) => {
    if (!hit && n.start === start && n.type === type) hit = n
  })
  return hit as Extract<t.Node, { type: T }> | null
}
