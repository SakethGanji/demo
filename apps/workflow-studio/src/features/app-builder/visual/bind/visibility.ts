/**
 * "Visible when" for any element: always, a viewer role, a true/false state, or "has data". The element is wrapped as
 * `{cond && <El/>}` (or the condition of an existing wrapper is replaced); "always" removes the wrapper again. Conditions we
 * didn't write are shown and left alone.
 */
import type * as t from '@babel/types'
import { tryParse, unwrap, walk, type FileMap } from '../source'
import { quote, quoteStyle, type TextEdit } from '../splice'
import { apiCallsIn, apiLocal, bodyStatements, calleeName, findDecl, finish, isFn, isHookCall, stateVars, text, type Built, type Ctx } from './scope'

export type Cond =
  | { kind: 'always' }
  | { kind: 'role'; role: string }
  | { kind: 'state'; name: string; negate: boolean }
  | { kind: 'data'; expr: string }

export type VisibleRead =
  | { ok: false; reason: string }
  | {
      ok: true
      /** the existing `cond && <El/>` wrapper, if any */
      wrapper: { logical: t.LogicalExpression; container: t.JSXExpressionContainer | null } | null
      current: Cond | { kind: 'custom'; src: string }
    }

/** The helper a role check calls: the preview's View-as user, everyone when the app has no viewer (like AppShell's `roles`). */
export const VIEWER_HELPER = 'viewerHas'
const HELPER_SRC = `const ${VIEWER_HELPER} = (role: string) => {\n  const roles = (globalThis as { __studioViewer?: { roles?: string[] } }).__studioViewer?.roles\n  return !roles || roles.includes(role)\n}`

/** Where the element sits, and whether it can be wrapped in a condition there. */
export function readVisible(ctx: Ctx, data: DataCond[] = dataConds(ctx)): VisibleRead {
  const ps = ctx.parents
  const p = ps[ps.length - 1]
  if (p?.type === 'LogicalExpression' && p.operator === '&&' && p.right === ctx.el) {
    const up = ps[ps.length - 2]
    const container = up?.type === 'JSXExpressionContainer' ? up : null
    return { ok: true, wrapper: { logical: p, container }, current: classify(ctx, p.left, data) }
  }
  if (p?.type === 'JSXElement' || p?.type === 'JSXFragment') return { ok: true, wrapper: null, current: { kind: 'always' } }
  if (p?.type === 'JSXExpressionContainer' && ps[ps.length - 2]?.type === 'JSXAttribute') return { ok: false, reason: 'Passed in a prop; set its visibility with AI or code' }
  if (p?.type === 'ConditionalExpression') return { ok: false, reason: `Already shown by a condition (${text(ctx.src, p.test).slice(0, 40)}); use AI or code` }
  const fn = [...ps].reverse().find(isFn)
  if (fn && fn !== ctx.comp) return { ok: false, reason: 'The top element of a list item; select an element inside it, or use AI' }
  return { ok: false, reason: 'The top element of its component; set visibility where the component is used, or select an inner element' }
}

export interface DataCond {
  /** what the panel shows */
  label: string
  expr: string
}

/** "Has data" conditions for the queries and list states in the component. */
export function dataConds(ctx: Ctx): DataCond[] {
  const out: DataCond[] = []
  const api = apiLocal(ctx)
  for (const v of stateVars(ctx)) if (v.init === '[]') out.push({ label: `${v.name} has rows`, expr: `${v.name}.length > 0` })
  for (const s of bodyStatements(ctx)) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations) {
      const init = d.init && unwrap(d.init)
      if (d.id.type !== 'Identifier' || !init || !isHookCall(init)) continue
      const loads = ['usePoll', 'useJob', 'useResourceList'].includes(calleeName(init) ?? '') || (!!api && init.arguments.some((a) => isFn(a) && apiCallsIn(a, api).length > 0))
      if (loads) {
        out.push({ label: `${d.id.name} has loaded`, expr: calleeName(init) === 'useResourceList' ? `${d.id.name}.rows.length > 0` : `!!${d.id.name}.data` })
      }
    }
  }
  return out
}

function classify(ctx: Ctx, left: t.Node, data: DataCond[]): Cond | { kind: 'custom'; src: string } {
  const src = text(ctx.src, left)
  const x = unwrap(left)
  if (x.type === 'CallExpression') {
    const a = x.arguments[0]
    const role = a?.type === 'StringLiteral' && x.arguments.length === 1 ? a.value : null
    if (role && x.callee.type === 'Identifier' && x.callee.name === VIEWER_HELPER) return { kind: 'role', role }
    if (role && x.callee.type === 'MemberExpression' && x.callee.property.type === 'Identifier' && x.callee.property.name === 'includes' && /roles\b/i.test(text(ctx.src, x.callee.object))) return { kind: 'role', role }
  }
  const neg = x.type === 'UnaryExpression' && x.operator === '!'
  const id = neg ? unwrap((x as t.UnaryExpression).argument) : x
  if (id.type === 'Identifier' && stateVars(ctx).some((v) => v.boolean && v.name === id.name)) return { kind: 'state', name: id.name, negate: neg }
  const flat = src.replace(/\s+/g, ' ').trim()
  if (data.some((d) => d.expr === flat)) return { kind: 'data', expr: flat }
  return { kind: 'custom', src }
}

/** Roles the app talks about (AppShell screens' `roles`, `roles={[…]}` props) plus the preview's View-as users'. */
export function appRoles(files: FileMap): string[] {
  const out = new Set<string>()
  for (const [file, src] of Object.entries(files)) {
    if (!/\.tsx?$/.test(file) || !/roles/.test(src) || /(^|\/)kit\//.test(file)) continue
    const ast = tryParse(src)
    if (!ast) continue
    walk(ast.program, (n) => {
      let arr: t.Node | null = null
      if (n.type === 'ObjectProperty' && n.key.type === 'Identifier' && n.key.name === 'roles') arr = n.value
      if (n.type === 'JSXAttribute' && n.name.name === 'roles' && n.value?.type === 'JSXExpressionContainer') arr = n.value.expression
      if (arr?.type === 'ArrayExpression') for (const e of arr.elements) if (e?.type === 'StringLiteral') out.add(e.value)
    })
  }
  for (const r of ['staff', 'manager', 'admin']) out.add(r)
  return [...out]
}

/** The text of a condition, and any edits it needs first (the role helper). */
function condText(ctx: Ctx, c: Exclude<Cond, { kind: 'always' }>): { text: string; edits: TextEdit[] } {
  const q = quoteStyle(ctx.src)
  switch (c.kind) {
    case 'state':
      return { text: `${c.negate ? '!' : ''}${c.name}`, edits: [] }
    case 'data':
      return { text: c.expr, edits: [] }
    case 'role': {
      const r = quote(c.role, q)
      const roles = findDecl(ctx, 'roles')
      if (roles) return { text: `roles.includes(${r})`, edits: [] }
      if (findDecl(ctx, VIEWER_HELPER)) return { text: `${VIEWER_HELPER}(${r})`, edits: [] }
      const imports = ctx.ast.program.body.filter((s) => s.type === 'ImportDeclaration')
      const last = imports[imports.length - 1]
      const at = last ? last.end! : 0
      const helper = `\n\n/** Whether the viewer has a role (the Studio preview's View-as user; everyone when there is none). */\n${HELPER_SRC}`
      return { text: `${VIEWER_HELPER}(${r})`, edits: [last ? { start: at, end: at, text: helper } : { start: 0, end: 0, text: `${helper.trimStart()}\n\n` }] }
    }
  }
}

/** Show the element always (remove our wrapper) or only when `c` holds. */
export function buildVisible(ctx: Ctx, read: VisibleRead, c: Cond): Built {
  if (!read.ok) return { ok: false, error: read.reason }
  const el = ctx.el
  const w = read.wrapper
  if (w && read.current.kind === 'custom') return { ok: false, error: 'Shown by a custom condition; change it with AI' }
  if (c.kind === 'always') {
    if (!w) return { ok: false, error: 'Nothing to change' }
    // `{cond && <El/>}` → `<El/>` inside JSX; `cond && <El/>` → `<El/>` elsewhere
    const outer = w.container && ctx.parents.at(-3)?.type !== 'JSXAttribute' ? w.container : w.logical
    return finish(ctx, 'always visible', [
      { start: outer.start!, end: el.start!, text: '' },
      { start: el.end!, end: outer.end!, text: '' },
    ])
  }
  const ct = condText(ctx, c)
  const label = c.kind === 'role' ? `visible to ${c.role}` : c.kind === 'state' ? `visible when ${c.negate ? 'not ' : ''}${c.name}` : 'visible with data'
  if (w) return finish(ctx, label, [...ct.edits, { start: w.logical.left.start!, end: w.logical.left.end!, text: ct.text }])
  return finish(ctx, label, [...ct.edits, { start: el.start!, end: el.start!, text: `{${ct.text} && ` }, { start: el.end!, end: el.end!, text: '}' }])
}
