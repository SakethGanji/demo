/**
 * Data binding for kit data components (DataTable rows, Stat value, chart data, resource layouts): trace the prop back to the
 * API call that loads it, and re-point it — rename the op inside the existing call, or declare a new query following the
 * file's own pattern (`const x = usePoll(() => api.op(), …)`) and bind the prop to it. Anything the trace can't follow is
 * refused with the reason, never guessed at.
 */
import type * as t from '@babel/types'
import { literalValue, unwrap, walk } from '../source'
import { findAttr, printAttrValue, quoteStyle, setAttr, type TextEdit } from '../splice'
import { relSpec } from '../structure'
import {
  apiCallsIn, apiLocal, apiOpOf, calleeName, clip, ensureApi, ensureImports, ensureKit, findDecl, finish, freshName, isFn, isHookCall,
  insertStatement, refetches, stateVars, text, titleCase, type ApiOp, type Built, type Ctx, type ResourceInfo,
} from './scope'

/** The prop that carries a kit component's data. */
export const DATA_PROPS: Record<string, string> = {
  DataTable: 'rows',
  Stat: 'value',
  Sparkline: 'values',
  TrendBars: 'data',
  LineChart: 'data',
  BarChart: 'data',
  PieChart: 'data',
  Kanban: 'items',
  Calendar: 'events',
  Timeline: 'events',
  StepList: 'steps',
  NetworkList: 'rows',
}

/** Props whose value is a specific shape a raw API response won't have: they can be re-pointed, not newly bound. */
const SHAPED = new Set(['Sparkline', 'StepList', 'TrendBars'])

/** Kit blocks that load a resource themselves, and the prop listing their columns (null: none). */
export const RESOURCE_COMPONENTS: Record<string, string | null> = {
  ResourceTable: 'columns',
  ListDetailLayout: 'columns',
  DashboardLayout: 'recentColumns',
  SettingsPage: 'fields',
  FormPage: null,
  InboxLayout: null,
  Inbox: null,
  StatTiles: null,
  GroupChart: null,
  RecordPanel: null,
  ResourceForm: null,
  ExportButton: null,
  StatusActions: null,
}

const ARRAY_METHODS = new Set(['map', 'filter', 'slice', 'sort', 'toSorted', 'reverse', 'concat', 'flat', 'flatMap', 'find'])
const REFRESH_KEY: Record<string, string> = { usePoll: 'refresh', useJob: 'refresh', useResourceList: 'reload' }

/** Where a prop's data comes from. */
export interface Source {
  kind: 'hook' | 'state'
  /** the variable the prop reads (`runs`, `rows`) */
  variable: string
  /** the hook that loads it (`usePoll`), or `useState` */
  hook: string
  /** the API op called, when there is one */
  op: string
  /** the API calls that load it: rebinding renames these */
  calls: t.CallExpression[]
  /** property names read along the way (`data`, `rows`): the list key is swapped here */
  path: t.Identifier[]
  /** helpers the value passes through (`rowsOf()`, `.filter()`) */
  via: string[]
  line: number
  /** a call that loads it again (`runs.refresh()`), when known */
  refetch: string | null
  /** places in the component that read the variable (more than one: re-pointing it changes them all) */
  uses: number
  /** why the source can't be re-pointed in place, when it can't */
  fixed?: string
}

export type Trace = { ok: true; source: Source } | { ok: false; reason: string }

export type DataRead =
  | { kind: 'none' }
  | { kind: 'api'; prop: string; component: string; src: string | null; trace: Trace | null; shaped: boolean }
  | { kind: 'resource'; prop: 'resource'; component: string; current: string | null; quoted: boolean; options: ResourceInfo[]; columnsProp: string | null }

function refuse(reason: string): Trace {
  return { ok: false, reason }
}

function countUses(ctx: Ctx, name: string): number {
  let n = 0
  const root = ctx.comp ?? ctx.ast.program
  walk(root, (x, parents) => {
    if (x.type !== 'Identifier' || x.name !== name) return
    const p = parents[parents.length - 1]
    if (p?.type === 'VariableDeclarator' && p.id === x) return
    if (p?.type === 'ArrayPattern' || p?.type === 'ObjectProperty' && parents[parents.length - 2]?.type === 'ObjectPattern') return
    if ((p?.type === 'MemberExpression' || p?.type === 'OptionalMemberExpression') && p.property === x && !p.computed) return
    if (p?.type === 'ObjectProperty' && p.key === x && !p.shorthand) return
    n++
  })
  return n
}

function distinctOps(calls: t.CallExpression[]): string[] {
  return [...new Set(calls.map(apiOpOf))]
}

/** A kit hook's declaration: `const x = useH(() => api.op())` or `const { data, refresh } = useH(…)`. */
function hookSource(ctx: Ctx, variable: string, init: t.CallExpression, decl: t.VariableDeclarator, path: t.Identifier[], via: string[]): Trace {
  const hook = calleeName(init) ?? 'hook'
  const api = apiLocal(ctx)
  const calls = api ? apiCallsIn(init, api) : []
  if (!calls.length) return refuse(`Loaded by ${hook}(…) without a call to the app's API; use AI or code`)
  const ops = distinctOps(calls)
  if (ops.length > 1) return refuse(`Loaded from several API calls (${ops.join(', ')}); use AI or code`)
  let refetch: string | null = null
  if (decl.id.type === 'Identifier' && REFRESH_KEY[hook]) refetch = `${decl.id.name}.${REFRESH_KEY[hook]}()`
  if (decl.id.type === 'ObjectPattern') {
    for (const p of decl.id.properties) {
      if (p.type === 'ObjectProperty' && p.key.type === 'Identifier' && ['refresh', 'reload', 'refetch'].includes(p.key.name) && p.value.type === 'Identifier') refetch = `${p.value.name}()`
    }
  }
  return { ok: true, source: { kind: 'hook', variable, hook, op: ops[0], calls, path, via, line: decl.loc?.start.line ?? 0, refetch, uses: countUses(ctx, variable) } }
}

/** The API calls that feed a setter call: the `.then(…)` it sits in, or the (async) function around it. */
function feedingCalls(ctx: Ctx, call: t.CallExpression, parents: t.Node[], api: string): t.CallExpression[] {
  for (let i = parents.length - 1; i >= 0; i--) {
    const p = parents[i]
    if (p === ctx.comp) return []
    if (!isFn(p)) continue
    const up = parents[i - 1]
    if (up?.type === 'CallExpression' && up.arguments.includes(p as t.Expression)) {
      const c = up.callee
      if ((c.type === 'MemberExpression' || c.type === 'OptionalMemberExpression') && c.property.type === 'Identifier' && c.property.name === 'then') {
        const found = apiCallsIn(c.object, api)
        if (found.length) return found
      }
    }
    const before = apiCallsIn(p.body, api).filter((x) => x.start! < call.start!)
    if (before.length) return before
  }
  return []
}

function inEffect(parents: t.Node[]): boolean {
  return parents.some((p) => p.type === 'CallExpression' && ['useEffect', 'useLayoutEffect'].includes(calleeName(p) ?? ''))
}

/** The function a setter call sits in, by name when it has one (`loadMore`), for reasons. */
function feedName(parents: t.Node[]): string {
  for (let i = parents.length - 1; i >= 0; i--) {
    const p = parents[i]
    if (p.type === 'VariableDeclarator' && p.id.type === 'Identifier') return p.id.name
    if (p.type === 'FunctionDeclaration' && p.id) return p.id.name
  }
  return 'a handler'
}

/**
 * `const [rows, setRows] = useState(…)` filled by `api.op(…).then((r) => setRows(r.items))` (preferring the ones in an effect).
 * Every call feeding the setter (an effect's load and a `loadMore`'s append alike) is re-pointed together, so the state
 * never mixes two endpoints; when they already call different ops the source is shown but marked `fixed`.
 */
function stateSource(ctx: Ctx, variable: string, setter: string, decl: t.VariableDeclarator, path: t.Identifier[], via: string[]): Trace {
  const api = apiLocal(ctx)
  if (!api || !ctx.comp) return refuse(`${variable} is component state not loaded from the app's API; use AI or code`)
  // names bound to a response (`const res = await api.x()`, `.then((res) => …)`): an updater `r => [...r, ...res.items]`
  // fills the state only when it reads one (a local `r => r.filter(…)` after a delete does not)
  const results = new Set<string>()
  walk(ctx.comp.body, (n) => {
    if (n.type === 'VariableDeclarator' && n.id.type === 'Identifier' && n.init && apiCallsIn(n.init, api).length) results.add(n.id.name)
    if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && n.callee.property.type === 'Identifier' && n.callee.property.name === 'then' && apiCallsIn(n.callee.object, api).length) {
      const f = n.arguments[0]
      if (f && isFn(f) && f.params[0]?.type === 'Identifier') results.add(f.params[0].name)
    }
  })
  const readsResult = (arg: t.Node) => {
    let hit = false
    walk(arg, (n) => { if (n.type === 'Identifier' && results.has(n.name)) hit = true })
    return hit
  }
  const feeds: { calls: t.CallExpression[]; arg: t.Node; effect: boolean; where: string }[] = []
  walk(ctx.comp.body, (n, parents) => {
    if (n.type !== 'CallExpression' || n.callee.type !== 'Identifier' || n.callee.name !== setter) return
    const arg = n.arguments[0]
    if (!arg || arg.type === 'SpreadElement' || literalValue(unwrap(arg)) !== undefined) return
    if (arg.type === 'ArrayExpression' && !arg.elements.length) return
    if (isFn(arg) && !readsResult(arg)) return
    const calls = feedingCalls(ctx, n, parents, api)
    if (calls.length) feeds.push({ calls, arg, effect: inEffect(parents), where: feedName(parents) })
  })
  const use = feeds.some((f) => f.effect) ? feeds.filter((f) => f.effect) : feeds
  if (!use.length) return refuse(`${variable} is component state not loaded from the app's API; use AI or code`)
  const ops = distinctOps(use.flatMap((f) => f.calls))
  if (ops.length > 1) return refuse(`${variable} is loaded from several API calls (${ops.join(', ')}); use AI or code`)
  const others = feeds.filter((f) => !use.includes(f))
  const odd = others.find((f) => distinctOps(f.calls).some((o) => o !== ops[0]))
  const all = odd ? use : feeds
  // the member names read off the response (`res.items`, also inside an append `r => [...r, ...res.items]`) carry the list key
  const keys: t.Identifier[] = [...path]
  for (const f of all) {
    walk(f.arg, (n) => {
      if ((n.type === 'MemberExpression' || n.type === 'OptionalMemberExpression') && !n.computed && n.property.type === 'Identifier') keys.push(n.property)
    })
  }
  const bump = refetches(ctx).find((r) => !r.call.includes('.'))
  const source: Source = {
    kind: 'state', variable, hook: 'useState', op: ops[0], calls: all.flatMap((f) => f.calls), path: keys, via,
    line: decl.loc?.start.line ?? 0, refetch: bump?.call ?? null, uses: countUses(ctx, variable),
  }
  if (odd) source.fixed = `${variable} is also filled by api.${distinctOps(odd.calls).join(', api.')}() in ${odd.where}; re-pointing one load would mix records, so use AI or code`
  return { ok: true, source }
}

/** Follow an expression back to the hook or state that loads it. */
export function trace(ctx: Ctx, expr: t.Node): Trace {
  let e: t.Node = expr
  const path: t.Identifier[] = []
  const via: string[] = []
  const seen = new Set<string>()
  for (let guard = 0; guard < 24; guard++) {
    e = unwrap(e)
    if (e.type === 'JSXExpressionContainer') {
      if (e.expression.type === 'JSXEmptyExpression') return refuse('Empty value')
      e = e.expression
      continue
    }
    if (e.type === 'LogicalExpression' && (e.operator === '??' || e.operator === '||')) {
      e = e.left
      continue
    }
    if (e.type === 'ConditionalExpression') return refuse(`Chosen by a condition (${clip(text(ctx.src, e.test), 30)}); use AI or code`)
    if ((e.type === 'MemberExpression' || e.type === 'OptionalMemberExpression') && !e.computed && e.property.type === 'Identifier') {
      path.unshift(e.property)
      e = e.object
      continue
    }
    if (e.type === 'CallExpression' || e.type === 'OptionalCallExpression') {
      const c = e.callee
      if ((c.type === 'MemberExpression' || c.type === 'OptionalMemberExpression') && !c.computed && c.property.type === 'Identifier' && ARRAY_METHODS.has(c.property.name)) {
        via.push(`.${c.property.name}()`)
        e = c.object
        continue
      }
      if (c.type === 'Identifier' && !/^use[A-Z]/.test(c.name) && e.arguments.length === 1 && e.arguments[0].type !== 'SpreadElement' && via.length < 3) {
        via.push(`${c.name}()`)
        e = e.arguments[0]
        continue
      }
      return refuse(`Computed by ${clip(text(ctx.src, e), 36)}; use AI or code`)
    }
    if (e.type !== 'Identifier') return refuse(`Computed (${clip(text(ctx.src, e), 36)}); use AI or code`)
    const name = e.name
    if (seen.has(name)) return refuse(`${name} refers to itself`)
    seen.add(name)
    const d = findDecl(ctx, name)
    if (!d) return refuse(`Can't find where ${name} comes from`)
    if (d.kind === 'param') return refuse(`${name} is passed in${ctx.compName && ctx.compName !== 'default' ? ` to ${ctx.compName}` : ''}; bind it where that component is used`)
    if (d.kind === 'function') return refuse(`${name} is a function; use AI or code`)
    const init = d.decl.init ? unwrap(d.decl.init) : null
    if (!init) return refuse(`${name} has no starting value to follow`)
    if (d.decl.id.type === 'ArrayPattern') {
      const [a, b] = d.decl.id.elements
      const hook = init.type === 'CallExpression' ? calleeName(init) : null
      if (hook === 'useState' && a?.type === 'Identifier' && a.name === name && b?.type === 'Identifier') return stateSource(ctx, name, b.name, d.decl, path, via)
      return refuse(`${name} comes from ${clip(text(ctx.src, init), 30)}; use AI or code`)
    }
    if (isHookCall(init)) return hookSource(ctx, name, init, d.decl, path, via)
    if (d.decl.id.type !== 'Identifier') return refuse(`${name} is unpacked from ${clip(text(ctx.src, init), 30)}; use AI or code`)
    if (init.type === 'AwaitExpression' || isFn(init)) return refuse(`${name} is computed in code; use AI or code`)
    e = init
  }
  return refuse('Too many steps to follow; use AI or code')
}

/** What the inspector's Data section shows for the element. */
export function readData(ctx: Ctx): DataRead {
  if (!ctx.kit) return { kind: 'none' }
  const component = ctx.name
  if (component in RESOURCE_COMPONENTS) {
    const a = findAttr(ctx.el, 'resource')
    const v = a?.value
    let current: string | null = null
    let quoted = false
    if (v?.type === 'StringLiteral') {
      current = v.value
      quoted = true
    } else if (v?.type === 'JSXExpressionContainer' && v.expression.type === 'Identifier') current = v.expression.name
    else if (v?.type === 'JSXExpressionContainer' && v.expression.type === 'StringLiteral') {
      current = v.expression.value
      quoted = true
    }
    return { kind: 'resource', prop: 'resource', component, current, quoted: quoted || !v, options: [], columnsProp: RESOURCE_COMPONENTS[component] }
  }
  const prop = DATA_PROPS[component]
  if (!prop) return { kind: 'none' }
  const a = findAttr(ctx.el, prop)
  if (!a) return { kind: 'api', prop, component, src: null, trace: null, shaped: SHAPED.has(component) }
  if (!a.value || a.value.type !== 'JSXExpressionContainer') return { kind: 'api', prop, component, src: a.value ? text(ctx.src, a.value) : 'true', trace: refuse('A fixed value, not loaded data'), shaped: SHAPED.has(component) }
  const src = text(ctx.src, a.value.expression)
  const lit = a.value.expression.type !== 'JSXEmptyExpression' && literalValue(unwrap(a.value.expression)) !== undefined
  const tr = lit || a.value.expression.type === 'ArrayExpression' || a.value.expression.type === 'ObjectExpression' ? refuse('A fixed value, not loaded data') : trace(ctx, a.value.expression)
  return { kind: 'api', prop, component, src, trace: tr, shaped: SHAPED.has(component) }
}

/** One line for the panel: `usePoll(() => api.listRuns()) → runs.data.rows`. */
export function describeSource(s: Source): string {
  // a state source's path is read off the response (`res.items`, once per load), not off the state variable
  if (s.kind === 'state') {
    const keys = [...new Set(s.path.map((p) => p.name))]
    return `api.${s.op}()${keys.length === 1 ? `.${keys[0]}` : ''} via state → ${s.variable}${s.via.length ? ` via ${s.via.join(', ')}` : ''}`
  }
  const chain = [s.variable, ...s.path.map((p) => p.name)].join('.')
  const load = `${s.hook}(() => api.${s.op}())`
  return `${load} → ${chain}${s.via.length ? ` via ${s.via.join(', ')}` : ''}`
}

/** The ops a data prop can be bound to: reads that need no path parameters. */
export function bindableOps(ops: ApiOp[]): ApiOp[] {
  return ops.filter((o) => o.method === 'GET' && !o.params.length)
}

/** The new call's arguments, carrying the old ones across by name (`query` stays the query). */
function reargs(src: string, call: t.CallExpression, from: ApiOp | undefined, to: ApiOp): string {
  const by: Record<string, string> = {}
  if (from) call.arguments.forEach((a, i) => {
    if (from.args[i]) by[from.args[i]] = text(src, a)
  })
  const list = to.args.map((n) => (n === 'params' ? null : by[n] ?? null))
  while (list.length && list[list.length - 1] === null) list.pop()
  return list.map((x) => x ?? 'undefined').join(', ')
}

/** Re-point the existing source's API call(s) at another op (and swap the list key when both responses name it). */
export function rebindOp(ctx: Ctx, s: Source, ops: ApiOp[], next: string): Built {
  const to = ops.find((o) => o.name === next)
  if (!to) return { ok: false, error: `No API operation named ${next}` }
  if (to.params.length) return { ok: false, error: `${next} needs ${to.params.join(', ')} to call; use AI or code` }
  if (next === s.op) return { ok: false, error: 'Nothing to change' }
  if (s.fixed) return { ok: false, error: s.fixed }
  const from = ops.find((o) => o.name === s.op)
  const edits: TextEdit[] = []
  for (const call of s.calls) {
    const callee = call.callee as t.MemberExpression
    edits.push({ start: callee.property.start!, end: callee.property.end!, text: next })
    const old = call.arguments.map((a) => text(ctx.src, a)).join(', ')
    const args = reargs(ctx.src, call, from, to)
    if (args !== old) {
      const open = ctx.src.indexOf('(', callee.end!)
      edits.push({ start: open + 1, end: call.end! - 1, text: args })
    }
  }
  if (from?.listKey && to.listKey && from.listKey !== to.listKey) {
    const seen = new Set<number>()
    for (const keyNode of s.path) {
      if (keyNode.name !== from.listKey || seen.has(keyNode.start!)) continue
      seen.add(keyNode.start!)
      edits.push({ start: keyNode.start!, end: keyNode.end!, text: to.listKey })
    }
  }
  return finish(ctx, `data from ${next}`, edits.sort((a, b) => a.start - b.start))
}

/** A variable name for an op's data: `listExpenses` → `expenses`, `getStats` → `stats`. */
export function nameForOp(op: string): string {
  const base = op.replace(/^(list|get|fetch|load|search|query)(?=[A-Z])/, '')
  return base.charAt(0).toLowerCase() + base.slice(1) || 'data'
}

/** How the file already declares API queries: the hook and its arguments after the loader (`usePoll`, `30000`). */
function queryPattern(ctx: Ctx, api: string): { hook: string; rest: string } | null {
  for (const s of ctx.body?.body ?? []) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations) {
      const init = d.init && unwrap(d.init)
      if (!init || !isHookCall(init) || d.id.type !== 'Identifier') continue
      const [fn, ...rest] = init.arguments
      if (!fn || !isFn(fn) || !apiCallsIn(fn, api).length) continue
      const hook = calleeName(init)!
      if (hook === 'useJob' || hook === 'useResourceList') continue
      return { hook, rest: rest.map((r) => text(ctx.src, r)).join(', ') }
    }
  }
  return null
}

/** The prop's new value reading the query `name` of op `op`. */
export function propValueFor(component: string, name: string, op: ApiOp): string {
  if (component === 'Stat') {
    if (op.top.includes('total')) return `{${name}.data?.total ?? '—'}`
    if (op.top.includes('count')) return `{${name}.data?.count ?? '—'}`
    if (op.listKey) return `{${name}.data?.${op.listKey}.length ?? '—'}`
    if (op.listKey === '') return `{${name}.data?.length ?? '—'}`
    return `{${name}.data != null ? String(${name}.data) : '—'}`
  }
  if (op.listKey) return `{${name}.data?.${op.listKey} ?? []}`
  return `{${name}.data ?? []}`
}

/** Declare a new query for `op` after the component's hooks and bind the prop to it. */
export function bindNew(ctx: Ctx, prop: string, ops: ApiOp[], next: string): Built {
  const op = ops.find((o) => o.name === next)
  if (!op) return { ok: false, error: `No API operation named ${next}` }
  if (op.params.length) return { ok: false, error: `${next} needs ${op.params.join(', ')} to call; use AI or code` }
  if (SHAPED.has(ctx.name)) return { ok: false, error: `${ctx.name} ${prop} needs a specific shape, not a raw API response; use AI or code` }
  const api = ensureApi(ctx)
  if (!api) return { ok: false, error: "This app has no generated API client (api/client.ts) to load from" }
  if (!ctx.body) return { ok: false, error: 'The component has no body to declare the query in; use AI or code' }
  const pat = queryPattern(ctx, api.local) ?? { hook: 'usePoll', rest: '30000' }
  const name = freshName(ctx.ast, nameForOp(op.name))
  const line = `const ${name} = ${pat.hook}(() => ${api.local}.${op.name}()${pat.rest ? `, ${pat.rest}` : ''})`
  const ins = insertStatement(ctx, line)
  if (!ins) return { ok: false, error: 'No place to declare the query; use AI or code' }
  const edits: TextEdit[] = [...api.edits, ...(pat.hook === 'usePoll' ? ensureKit(ctx, ['usePoll']) : []), ins, setAttr(ctx.src, ctx.el, prop, propValueFor(ctx.name, name, op))]
  return finish(ctx, `${prop} from ${op.name}`, edits)
}

/** Point a resource block at another resource (`resource="x"` stays a string; `{x}` is imported). */
export function rebindResource(ctx: Ctx, read: Extract<DataRead, { kind: 'resource' }>, res: ResourceInfo): Built {
  if (read.quoted) {
    if (read.current === res.key || read.current === res.name) return { ok: false, error: 'Nothing to change' }
    return finish(ctx, `resource ${res.name}`, [setAttr(ctx.src, ctx.el, 'resource', printAttrValue(res.name, quoteStyle(ctx.src)))])
  }
  if (read.current === res.name) return { ok: false, error: 'Nothing to change' }
  const imp = res.file === ctx.file ? [] : ensureImports(ctx, [res.name], relSpec(ctx.file, res.file))
  return finish(ctx, `resource ${res.name}`, [...imp, setAttr(ctx.src, ctx.el, 'resource', `{${res.name}}`)])
}

/** The resource a block shows, by its `resource` value (a const name or the registry name). */
export function resourceOf(list: ResourceInfo[], current: string | null): ResourceInfo | null {
  return current == null ? null : list.find((r) => r.name === current) ?? list.find((r) => r.key === current) ?? null
}

/** Fields of the row type named in the file (`DataTable<Run>` or `useState<Run[]>`), when declared there. */
export function rowTypeFields(ctx: Ctx, s: Source | null): string[] {
  let typeName: string | null = null
  const tp = ctx.el.openingElement.typeParameters?.params[0]
  if (tp?.type === 'TSTypeReference' && tp.typeName.type === 'Identifier') typeName = tp.typeName.name
  if (!typeName && s?.kind === 'state') {
    const v = stateVars(ctx).find((x) => x.name === s.variable)
    const d = v?.stmt.declarations[0]
    const init = d?.init && unwrap(d.init)
    const arg = init?.type === 'CallExpression' ? init.typeParameters?.params[0] : null
    const el = arg?.type === 'TSArrayType' ? arg.elementType : null
    if (el?.type === 'TSTypeReference' && el.typeName.type === 'Identifier') typeName = el.typeName.name
  }
  if (!typeName) return []
  for (const s0 of ctx.ast.program.body) {
    const st = s0.type === 'ExportNamedDeclaration' && s0.declaration ? s0.declaration : s0
    if (st.type === 'TSInterfaceDeclaration' && st.id.name === typeName) {
      return st.body.body.flatMap((m) => (m.type === 'TSPropertySignature' && m.key.type === 'Identifier' ? [m.key.name] : []))
    }
    if (st.type === 'TSTypeAliasDeclaration' && st.id.name === typeName && st.typeAnnotation.type === 'TSTypeLiteral') {
      return st.typeAnnotation.members.flatMap((m) => (m.type === 'TSPropertySignature' && m.key.type === 'Identifier' ? [m.key.name] : []))
    }
  }
  return []
}

/** Row fields a DataTable bound to `s` can show: the op's response fields, else the row type's. */
export function tableFields(ctx: Ctx, s: Source | null, ops: ApiOp[]): string[] {
  const op = s ? ops.find((o) => o.name === s.op) : null
  const fromOp = op ? op.fields : []
  return [...new Set([...fromOp, ...rowTypeFields(ctx, s)])]
}

/** A new DataTable column for a field. */
export function columnFor(field: string): { key: string; header: string } {
  return { key: field, header: titleCase(field) }
}
