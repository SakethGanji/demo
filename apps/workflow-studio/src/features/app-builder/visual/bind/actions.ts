/**
 * "On click" for buttons and links: read the handler into steps we recognise (call an API op, reload a query, open a drawer,
 * go to a screen, show a toast) or call it custom, and write a small inline arrow for a chosen action. A handler that isn't
 * one of these shapes is never overwritten: the panel offers Ask AI instead.
 */
import type * as t from '@babel/types'
import { jsxName, literalValue, tryParse, unwrap, walk, type FileMap } from '../source'
import { findAttr, indentAt, printAttrValue, quote, quoteStyle, removeAttr, setAttr, type TextEdit } from '../splice'
import { indentUnit } from '../structure'
import {
  apiLocal, apiOpOf, calleeName, clip, ensureApi, ensureKit, ensureReact, findDecl, finish, freshName, insertStatement, isFn, refetches,
  stateVars, text, type ApiOp, type Built, type Ctx,
} from './scope'

export type ClickStep =
  | { kind: 'api'; op: string }
  | { kind: 'refetch'; call: string }
  | { kind: 'open'; state: string; value: boolean | 'toggle' }
  | { kind: 'navigate'; target: string }
  | { kind: 'toast'; message: string }

export type ClickRead =
  | { kind: 'none' }
  | { kind: 'ours'; steps: ClickStep[]; summary: string }
  | { kind: 'custom'; src: string }

export type ClickChoice =
  | { kind: 'none' }
  | { kind: 'api'; op: string; refetch: string | null }
  | { kind: 'open'; state: string }
  | { kind: 'openNew' }
  | { kind: 'navigate'; target: string }
  | { kind: 'toast'; message: string }
  | { kind: 'refetch'; call: string }

/** Elements the On click section applies to. */
export function clickable(ctx: Ctx): boolean {
  return ['Button', 'button', 'a'].includes(ctx.name) || !!findAttr(ctx.el, 'onClick')
}

function strArg(c: t.CallExpression): string | null {
  const a = c.arguments[0]
  const v = a && a.type !== 'SpreadElement' ? literalValue(unwrap(a)) : undefined
  return typeof v === 'string' ? v : null
}

/** One statement of a handler as a step we know, or null. */
function stepOf(ctx: Ctx, n: t.Node): ClickStep | null {
  let e = unwrap(n)
  if (e.type === 'AwaitExpression' || e.type === 'UnaryExpression' && e.operator === 'void') e = unwrap(e.argument)
  if (e.type !== 'CallExpression') return null
  const api = apiLocal(ctx)
  const c = e.callee
  const callText = text(ctx.src, e)
  if (api && (c.type === 'MemberExpression' || c.type === 'OptionalMemberExpression') && c.object.type === 'Identifier' && c.object.name === api && c.property.type === 'Identifier') return { kind: 'api', op: apiOpOf(e) }
  if (refetches(ctx).some((r) => r.call === callText)) return { kind: 'refetch', call: callText }
  const name = calleeName(e)
  if (c.type === 'Identifier') {
    const st = stateVars(ctx).find((v) => v.setter === c.name)
    const a = e.arguments[0] ? unwrap(e.arguments[0]) : null
    if (st && st.boolean && a) {
      if (a.type === 'BooleanLiteral') return { kind: 'open', state: st.name, value: a.value }
      const neg = a.type === 'UnaryExpression' && a.operator === '!' ? unwrap(a.argument) : null
      if (neg?.type === 'Identifier' && neg.name === st.name) return { kind: 'open', state: st.name, value: 'toggle' }
      if (isFn(a) && a.params.length === 1 && a.params[0].type === 'Identifier' && a.body.type === 'UnaryExpression' && a.body.operator === '!') return { kind: 'open', state: st.name, value: 'toggle' }
    }
    if ((c.name === 'go' || c.name === 'navigate') && strArg(e) !== null) return { kind: 'navigate', target: strArg(e)! }
    if ((c.name === 'notify' || c.name === 'toast') && strArg(e) !== null) return { kind: 'toast', message: strArg(e)! }
  }
  if ((c.type === 'MemberExpression' || c.type === 'OptionalMemberExpression') && c.object.type === 'Identifier') {
    if (c.object.name === 'toast' && strArg(e) !== null) return { kind: 'toast', message: strArg(e)! }
    if (name === 'go' && strArg(e) !== null) return { kind: 'navigate', target: strArg(e)! }
  }
  return null
}

export function describeStep(s: ClickStep, screens: Screen[] = []): string {
  switch (s.kind) {
    case 'api':
      return `call ${s.op}`
    case 'refetch':
      return `reload ${s.call.replace(/\.(refresh|reload)\(\)$|\(\)$/, '')}`
    case 'open':
      return s.value === 'toggle' ? `toggle ${s.state}` : `${s.value ? 'open' : 'close'} ${s.state}`
    case 'navigate':
      return `go to ${screens.find((x) => x.id === s.target)?.label ?? s.target}`
    case 'toast':
      return `show “${clip(s.message, 24)}”`
  }
}

/** The element's onClick as steps, or custom (any statement we don't recognise). */
export function readClick(ctx: Ctx, screens: Screen[] = []): ClickRead {
  const a = findAttr(ctx.el, 'onClick')
  if (!a) return { kind: 'none' }
  const v = a.value
  if (!v || v.type !== 'JSXExpressionContainer' || v.expression.type === 'JSXEmptyExpression') return { kind: 'custom', src: v ? text(ctx.src, v) : 'onClick' }
  const src = text(ctx.src, v.expression)
  const custom: ClickRead = { kind: 'custom', src }
  const x = unwrap(v.expression)
  let stmts: t.Node[] = []
  if (x.type === 'Identifier' || x.type === 'MemberExpression') {
    const call = `${text(ctx.src, x)}()`
    const r = refetches(ctx).find((f) => f.call === call)
    if (!r) return custom
    const steps: ClickStep[] = [{ kind: 'refetch', call }]
    return { kind: 'ours', steps, summary: steps.map((s) => describeStep(s, screens)).join(', then ') }
  }
  if (!isFn(x) || x.params.length > 1) return custom
  // a parameter (the event) is fine as long as the body ignores it
  const p0 = x.params[0]
  if (p0 && p0.type !== 'Identifier') return custom
  if (x.body.type === 'BlockStatement') {
    for (const s of x.body.body) {
      if (s.type !== 'ExpressionStatement') return custom
      stmts.push(s.expression)
    }
  } else stmts = [x.body]
  if (p0) {
    let used = false
    walk(x.body, (n) => {
      if (n.type === 'Identifier' && n.name === (p0 as t.Identifier).name) used = true
    })
    if (used) return custom
  }
  const steps: ClickStep[] = []
  for (const s of stmts) {
    const st = stepOf(ctx, s)
    if (!st) return custom
    steps.push(st)
  }
  if (!steps.length) return custom
  return { kind: 'ours', steps, summary: steps.map((s) => describeStep(s, screens)).join(', then ') }
}

// ── Navigation targets ─────────────────────────────────────────────────────

export interface Screen {
  id: string
  label: string
}

/** The AppShell's screens (`screens={[{ id, label, render }]}`), from whichever file mounts it. */
export function appScreens(files: FileMap): Screen[] {
  for (const [file, src] of Object.entries(files)) {
    if (!/\.tsx$/.test(file) || !src.includes('<AppShell')) continue
    const ast = tryParse(src)
    if (!ast) continue
    const out: Screen[] = []
    walk(ast.program, (n) => {
      if (n.type !== 'JSXElement' || jsxName(n.openingElement.name) !== 'AppShell') return
      const a = findAttr(n, 'screens')
      let arr: t.Node | null = a?.value?.type === 'JSXExpressionContainer' ? a.value.expression : null
      if (arr?.type === 'Identifier') {
        const name = arr.name
        walk(ast.program, (d) => {
          if (d.type === 'VariableDeclarator' && d.id.type === 'Identifier' && d.id.name === name && d.init) arr = unwrap(d.init)
        })
      }
      if (arr?.type !== 'ArrayExpression') return
      for (const el of arr.elements) {
        if (el?.type !== 'ObjectExpression') continue
        let id: string | null = null
        let label: string | null = null
        for (const p of el.properties) {
          if (p.type !== 'ObjectProperty' || p.key.type !== 'Identifier' || p.value.type !== 'StringLiteral') continue
          if (p.key.name === 'id') id = p.value.value
          if (p.key.name === 'label') label = p.value.value
        }
        if (id) out.push({ id, label: label ?? id })
      }
    })
    if (out.length) return out
  }
  return []
}

/** How this component navigates: an in-scope `go`, a `props.go`, or a `go` to add to its `{ … }: Props` pattern. */
function navigator(ctx: Ctx): { call: string; edits: TextEdit[] } | null {
  const d = findDecl(ctx, 'go')
  if (d) return { call: 'go', edits: [] }
  const p0 = ctx.comp?.params[0]
  if (!p0) return null
  // only a screen the AppShell renders with `go={go}` receives it
  const base = ctx.file.replace(/\.(t|j)sx?$/, '').split('/').pop()!
  const isScreen = Object.entries(ctx.files).some(([f, s]) => f !== ctx.file && s.includes('go={go}') && new RegExp(`from\\s+['"][^'"]*/${base}['"]`).test(s))
  if (!isScreen) return null
  if (p0.type === 'Identifier') return { call: `${p0.name}.go`, edits: [] }
  if (p0.type !== 'ObjectPattern') return null
  const props = p0.properties
  if (props.some((p) => p.type === 'RestElement')) return null
  if (!props.length) {
    const open = ctx.src.indexOf('{', p0.start!)
    const close = ctx.src.indexOf('}', open)
    return { call: 'go', edits: [{ start: open, end: close + 1, text: '{ go }' }] }
  }
  const last = props[props.length - 1]
  return { call: 'go', edits: [{ start: last.end!, end: last.end!, text: ', go' }] }
}

/** Whether the app mounts the kit's `<Toaster/>` (else `notify()` through the AppShell's ToastHost). */
function usesToaster(files: FileMap): boolean {
  return Object.values(files).some((s) => /<Toaster\b/.test(s))
}

/** A row variable in scope (the `r` of `rows.map((r) => …)` or a column's `render: (r) => …`). */
function rowVar(ctx: Ctx): string | null {
  for (let i = ctx.parents.length - 1; i >= 0; i--) {
    const p = ctx.parents[i]
    if (p === ctx.comp) return null
    if (isFn(p) && p.params[0]?.type === 'Identifier') return p.params[0].name
  }
  return null
}

/** The button's label text, for a new drawer's title. */
function labelOf(ctx: Ctx): string {
  const s = ctx.el.children.filter((c): c is t.JSXText => c.type === 'JSXText').map((c) => c.value).join(' ').replace(/\s+/g, ' ').trim()
  return s || 'Details'
}

/** Add `<Drawer …>` as the last child of the component's returned element. */
function addDrawer(ctx: Ctx, state: string, setter: string): TextEdit | null {
  const ret = ctx.body?.body.find((s): s is t.ReturnStatement => s.type === 'ReturnStatement')
  const root = ret?.argument ? unwrap(ret.argument) : null
  if (!root || (root.type !== 'JSXElement' && root.type !== 'JSXFragment')) return null
  const closing = root.type === 'JSXElement' ? root.closingElement : root.closingFragment
  if (!closing) return null
  const q = quoteStyle(ctx.src)
  const ind = indentAt(ctx.src, closing.start!)
  const unit = indentUnit(ctx.src)
  const inner = ind + unit
  const block = `<Drawer open={${state}} onClose={() => ${setter}(false)} title=${printAttrValue(labelOf(ctx), q)}>\n${inner}${unit}Add content here\n${inner}</Drawer>`
  // before the closing tag's own line, keeping it where it is
  let at = closing.start!
  while (at > 0 && (ctx.src[at - 1] === ' ' || ctx.src[at - 1] === '\t')) at--
  if (ctx.src[at - 1] === '\n') return { start: at - 1, end: at - 1, text: `\n${inner}${block}` }
  return { start: closing.start!, end: closing.start!, text: block }
}

function capital(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Write the chosen action as the element's onClick (refusing over a custom handler). */
export function buildClick(ctx: Ctx, read: ClickRead, choice: ClickChoice, ops: ApiOp[]): Built {
  if (read.kind === 'custom') return { ok: false, error: 'This button runs custom code; change it with AI so nothing it does is lost' }
  const set = (code: string, label: string, extra: TextEdit[] = []) => finish(ctx, label, [...extra, setAttr(ctx.src, ctx.el, 'onClick', `{${code}}`)])
  switch (choice.kind) {
    case 'none': {
      const r = removeAttr(ctx.src, ctx.el, 'onClick')
      return r ? finish(ctx, 'no click action', [r]) : { ok: false, error: 'Nothing to change' }
    }
    case 'api': {
      const op = ops.find((o) => o.name === choice.op)
      if (!op) return { ok: false, error: `No API operation named ${choice.op}` }
      const api = ensureApi(ctx)
      if (!api) return { ok: false, error: 'This app has no generated API client (api/client.ts) to call' }
      let args = ''
      if (op.params.length) {
        const row = rowVar(ctx)
        if (!row) return { ok: false, error: `${op.name} needs ${op.params.join(', ')}: put the button in a list row, or use AI` }
        args = `{ ${op.params.map((p) => `${p}: ${row}.${p}`).join(', ')} }`
      }
      const call = `${api.local}.${op.name}(${args})`
      const code = choice.refetch ? `async () => { await ${call}; ${choice.refetch} }` : `() => ${call}`
      return set(code, `on click ${op.name}`, api.edits)
    }
    case 'open': {
      const st = stateVars(ctx).find((v) => v.name === choice.state && v.boolean)
      if (!st) return { ok: false, error: `${choice.state} is not a true/false state here` }
      return set(`() => ${st.setter}(true)`, `on click open ${st.name}`)
    }
    case 'openNew': {
      if (!ctx.body) return { ok: false, error: 'The component has no body to add a drawer to; use AI' }
      const state = freshName(ctx.ast, 'drawerOpen')
      const setter = freshName(ctx.ast, `set${capital(state)}`)
      const drawer = addDrawer(ctx, state, setter)
      if (!drawer) return { ok: false, error: "Can't find where this screen's layout ends to add a drawer; use AI" }
      const decl = insertStatement(ctx, `const [${state}, ${setter}] = useState(false)`)
      if (!decl) return { ok: false, error: 'No place to declare the drawer state; use AI' }
      return finish(ctx, 'on click open a new drawer', [...ensureReact(ctx, ['useState']), ...ensureKit(ctx, ['Drawer']), decl, setAttr(ctx.src, ctx.el, 'onClick', `{() => ${setter}(true)}`), drawer])
    }
    case 'navigate': {
      const nav = navigator(ctx)
      if (!nav) return { ok: false, error: "This component isn't a screen the app shell can navigate from; use AI" }
      return set(`() => ${nav.call}(${quote(choice.target, quoteStyle(ctx.src))})`, `on click go to ${choice.target}`, nav.edits)
    }
    case 'toast': {
      const msg = choice.message.trim()
      if (!msg) return { ok: false, error: 'Write the message to show' }
      const q = quote(msg, quoteStyle(ctx.src))
      if (usesToaster(ctx.files)) return set(`() => toast.success(${q})`, 'on click toast', ensureKit(ctx, ['toast']))
      return set(`() => notify(${q})`, 'on click toast', ensureKit(ctx, ['notify']))
    }
    case 'refetch': {
      if (!refetches(ctx).some((r) => r.call === choice.call)) return { ok: false, error: `${choice.call} doesn't reload anything here` }
      return set(`() => ${choice.call}`, `on click ${choice.call}`)
    }
  }
}

/** Ops a button can call: anything, but path parameters need a row in scope. */
export function callableOps(ctx: Ctx, ops: ApiOp[]): { op: ApiOp; ok: boolean }[] {
  const row = rowVar(ctx)
  return ops.map((op) => ({ op, ok: !op.params.length || !!row }))
}
