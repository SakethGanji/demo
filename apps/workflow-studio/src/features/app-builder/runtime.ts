import type { EventHandlerConfig, EventAction, TransformStep, FilterOp, RepeatScope } from './types'
import { useRuntimeStateStore, useConsoleStore, useAppDocumentStore } from './stores'

/**
 * Expression engine for resolving {{ }} template expressions.
 *
 * Supports full JavaScript expressions inside {{ }}:
 *   {{ stores.count + 1 }}
 *   {{ item.role === 'user' ? 'right' : 'left' }}
 *   {{ components.input1.value }}
 *   {{ stores.messages.length }}
 *
 * Context variables available in expressions:
 *   components  — per-component exposed state
 *   stores      — user-defined global stores
 *   item        — current item (inside a List)
 *   index       — current index (inside a List)
 *   items       — full array (inside a List)
 */

export interface ExpressionContext {
  components: Record<string, Record<string, unknown>>
  stores: Record<string, unknown>
  /** Current item when inside a List component */
  item?: unknown
  /** Current iteration index when inside a List component */
  index?: number
  /** The full array being iterated in a List component */
  items?: unknown[]
}

// Matches {{ ... }} allowing any characters inside (not just [\w.[\]])
const EXPR_REGEX = /\{\{([\s\S]*?)\}\}/g

// Simple path-only pattern: stores.x.y, item.name, components.a.b[0]
const SIMPLE_PATH = /^[\w.[\]]+$/

/**
 * Evaluate a JS expression string against the context.
 * Uses new Function for sandboxed eval — context vars are the only globals.
 */
// Cache compiled functions to avoid re-parsing on every render
const fnCache = new Map<string, Function>()

function evaluate(expr: string, ctx: ExpressionContext): unknown {
  const trimmed = expr.trim()
  if (!trimmed) return undefined

  // Fast path: simple dot-path resolution (no operators, no method calls)
  // This covers the common case and avoids Function construction overhead
  if (SIMPLE_PATH.test(trimmed)) {
    return resolvePath(trimmed, ctx)
  }

  // Full JS expression evaluation
  let fn = fnCache.get(trimmed)
  if (!fn) {
    try {
      fn = new Function(
        'components', 'stores', 'item', 'index', 'items',
        `"use strict"; try { return (${trimmed}); } catch(e) { return undefined; }`
      )
      fnCache.set(trimmed, fn)
    } catch {
      // Syntax error in expression
      return undefined
    }
  }
  try {
    return fn(ctx.components, ctx.stores, ctx.item, ctx.index, ctx.items)
  } catch {
    return undefined
  }
}

/**
 * Resolve a dot-path like "components.input1.value" against the context.
 * Fast path for simple property access — no Function overhead.
 */
function resolvePath(path: string, ctx: ExpressionContext): unknown {
  const parts = path.split('.')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let current: any = ctx

  for (const part of parts) {
    if (current == null) return undefined
    // Handle array bracket notation: items[0]
    const bracketMatch = part.match(/^(\w+)\[(\d+)\]$/)
    if (bracketMatch) {
      current = current[bracketMatch[1]]
      if (Array.isArray(current)) {
        current = current[Number(bracketMatch[2])]
      } else {
        return undefined
      }
    } else {
      current = current[part]
    }
  }
  return current
}

/**
 * Resolve a single template string.
 *
 * - Pure expression `{{ x.y === 'foo' }}` returns the raw value (preserves type).
 * - Mixed template `"Hello {{ x.y }}"` returns an interpolated string.
 * - No expressions: returns the original value as-is.
 */
export function resolveExpression(template: string, ctx: ExpressionContext): unknown {
  if (typeof template !== 'string') return template

  // Check if the entire string is a single pure expression
  const trimmed = template.trim()
  const pureMatch = trimmed.match(/^\{\{([\s\S]*?)\}\}$/)
  if (pureMatch) {
    return evaluate(pureMatch[1], ctx)
  }

  // Mixed template — interpolate all expressions as strings
  const result = template.replace(EXPR_REGEX, (_, expr) => {
    const val = evaluate(expr, ctx)
    if (val == null) return ''
    if (typeof val === 'object') {
      try { return JSON.stringify(val) } catch { return String(val) }
    }
    return String(val)
  })

  return result
}

/**
 * Coerce a resolved value to a display-safe string.
 * Objects/arrays are JSON-stringified so React can render them.
 */
function toDisplayValue(val: unknown): unknown {
  if (val === null || val === undefined) return ''
  if (typeof val === 'object') {
    try { return JSON.stringify(val, null, 2) } catch { return String(val) }
  }
  return val
}

/**
 * Resolve all props, replacing any {{ }} expressions with their values.
 */
export function resolveAllProps(
  props: Record<string, unknown>,
  ctx: ExpressionContext
): Record<string, unknown> {
  const resolved: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(props)) {
    if (key === '__eventHandlers') {
      resolved[key] = value
      continue
    }
    if (typeof value === 'string' && value.includes('{{')) {
      resolved[key] = toDisplayValue(resolveExpression(value, ctx))
    } else {
      resolved[key] = value
    }
  }
  return resolved
}

/**
 * Extract which stores and components a set of props depends on.
 * Used by useResolvedProps to subscribe only to relevant state slices.
 *
 * Note: For expressions referencing `item`/`index` (inside List), these
 * don't map to store/component deps — the List parent handles re-rendering.
 */
export function extractDependencies(props: Record<string, unknown>): {
  stores: string[]
  components: string[]
} {
  const stores = new Set<string>()
  const components = new Set<string>()

  for (const [key, value] of Object.entries(props)) {
    if (key === '__eventHandlers' || typeof value !== 'string') continue
    if (!value.includes('{{')) continue

    // Extract store/component references from expression text
    // Match patterns like stores.xxx or components.xxx
    const storeRegex = /\bstores\.(\w+)/g
    const compRegex = /\bcomponents\.(\w+)/g

    let match: RegExpExecArray | null
    while ((match = storeRegex.exec(value)) !== null) {
      stores.add(match[1])
    }
    while ((match = compRegex.exec(value)) !== null) {
      components.add(match[1])
    }
  }

  return {
    stores: Array.from(stores),
    components: Array.from(components),
  }
}

/**
 * Execute a transform pipeline: each step takes the output of the previous step.
 *
 * Steps:
 *   pick      — extract a nested field by dot-path (e.g. "data.items")
 *   filter    — keep array items where field matches condition
 *   sort      — sort array by field
 *   map       — keep only specified fields per item (comma-separated)
 *   slice     — take a range of array items
 *   find      — return first matching item (array → single item)
 *   count     — return array length (array → number)
 *   expression — resolve a {{ }} expression (escape hatch)
 */
export function executeTransform(
  steps: TransformStep[],
  input: unknown,
  ctx: ExpressionContext
): unknown {
  let current: unknown = input

  for (const step of steps) {
    current = executeStep(step, current, ctx)
  }

  return current
}

function executeStep(step: TransformStep, input: unknown, ctx: ExpressionContext): unknown {
  switch (step.type) {
    case 'pick':
      return pickPath(input, step.path)

    case 'filter': {
      if (!Array.isArray(input)) return input
      const resolvedValue = resolveStepValue(step.value, ctx)
      return input.filter((item) => {
        const fieldVal = pickPath(item, step.field)
        return compare(fieldVal, step.op, resolvedValue)
      })
    }

    case 'sort': {
      if (!Array.isArray(input)) return input
      const sorted = [...input]
      sorted.sort((a, b) => {
        const aVal = pickPath(a, step.field)
        const bVal = pickPath(b, step.field)
        const cmp = comparePrimitive(aVal, bVal)
        return step.direction === 'desc' ? -cmp : cmp
      })
      return sorted
    }

    case 'map': {
      if (!Array.isArray(input)) return input
      const fields = step.fields.split(',').map((f) => f.trim()).filter(Boolean)
      if (fields.length === 0) return input
      return input.map((item) => {
        if (item == null || typeof item !== 'object') return item
        const picked: Record<string, unknown> = {}
        for (const field of fields) {
          picked[field] = pickPath(item, field)
        }
        return picked
      })
    }

    case 'slice': {
      if (!Array.isArray(input)) return input
      const start = step.start ? Number(resolveStepValue(step.start, ctx)) : 0
      const end = step.end ? Number(resolveStepValue(step.end, ctx)) : undefined
      return input.slice(
        isNaN(start) ? 0 : start,
        end !== undefined && !isNaN(end) ? end : undefined
      )
    }

    case 'find': {
      if (!Array.isArray(input)) return input
      const resolvedValue = resolveStepValue(step.value, ctx)
      return input.find((item) => {
        const fieldVal = pickPath(item, step.field)
        return compare(fieldVal, step.op, resolvedValue)
      }) ?? null
    }

    case 'count':
      return Array.isArray(input) ? input.length : 0

    case 'expression': {
      // Inject the current pipeline value as $value in context
      const extendedCtx: ExpressionContext = {
        ...ctx,
        stores: { ...ctx.stores, $value: input },
      }
      return resolveExpression(step.expr, extendedCtx)
    }

    default:
      return input
  }
}

/**
 * Navigate a dot-path on any value.
 * Supports: "data.items", "0.name", "users[0].email"
 */
function pickPath(obj: unknown, path: string): unknown {
  if (!path) return obj
  const parts = path.split('.')
  let current: unknown = obj

  for (const part of parts) {
    if (current == null) return undefined
    // Handle bracket notation: items[0]
    const bracketMatch = part.match(/^(\w+)\[(\d+)\]$/)
    if (bracketMatch) {
      current = (current as Record<string, unknown>)[bracketMatch[1]]
      if (Array.isArray(current)) {
        current = current[Number(bracketMatch[2])]
      } else {
        return undefined
      }
    } else {
      current = (current as Record<string, unknown>)[part]
    }
  }
  return current
}

/**
 * Compare two values using a filter operator.
 */
function compare(fieldVal: unknown, op: FilterOp, target: unknown): boolean {
  switch (op) {
    case 'eq':
      // eslint-disable-next-line eqeqeq
      return fieldVal == target
    case 'neq':
      // eslint-disable-next-line eqeqeq
      return fieldVal != target
    case 'gt':
      return Number(fieldVal) > Number(target)
    case 'lt':
      return Number(fieldVal) < Number(target)
    case 'gte':
      return Number(fieldVal) >= Number(target)
    case 'lte':
      return Number(fieldVal) <= Number(target)
    case 'contains':
      return typeof fieldVal === 'string' && typeof target === 'string'
        ? fieldVal.toLowerCase().includes(target.toLowerCase())
        : false
    case 'startsWith':
      return typeof fieldVal === 'string' && typeof target === 'string'
        ? fieldVal.toLowerCase().startsWith(target.toLowerCase())
        : false
    case 'exists':
      return fieldVal !== undefined && fieldVal !== null
    default:
      return false
  }
}

function comparePrimitive(a: unknown, b: unknown): number {
  if (a === b) return 0
  if (a == null) return -1
  if (b == null) return 1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b))
}

/**
 * Resolve a step config value — if it contains {{ }}, resolve against context.
 * Otherwise try to parse as JSON, fall back to string.
 */
function resolveStepValue(value: string, ctx: ExpressionContext): unknown {
  if (typeof value === 'string' && value.includes('{{')) {
    return resolveExpression(value, ctx)
  }
  // Try JSON parse for numbers, booleans, null
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

export interface WebhookResult {
  ok: boolean
  status: number
  statusText: string
  durationMs: number
  rawData: unknown
  transformedData: unknown
  error?: string
}

/**
 * Execute an API query by its definition ID.
 * Resolves {{ }} in URL, headers, and body before fetching.
 * Runs any transform steps on the response.
 * Writes the final result into the specified global store.
 */
export async function executeWebhook(webhookId: string, resultStore: string): Promise<WebhookResult> {
  const log = useConsoleStore.getState().log
  const docState = useAppDocumentStore.getState()
  const webhookDef = docState.webhookDefinitions.find((w) => w.id === webhookId)
  if (!webhookDef) {
    log('error', 'api', `API query "${webhookId}" not found`)
    return { ok: false, status: 0, statusText: '', durationMs: 0, rawData: undefined, transformedData: undefined, error: `API query "${webhookId}" not found` }
  }

  const store = useRuntimeStateStore.getState()
  const ctx: ExpressionContext = store.getContext()

  // Resolve expressions in URL, headers, body
  const rawUrl = webhookDef.url.includes('{{')
    ? String(resolveExpression(webhookDef.url, ctx) ?? webhookDef.url)
    : webhookDef.url

  if (!rawUrl || !rawUrl.startsWith('http')) {
    const msg = rawUrl ? `Invalid URL: "${rawUrl}" (must start with http)` : 'No URL provided'
    log('error', 'api', msg)
    return { ok: false, status: 0, statusText: '', durationMs: 0, rawData: undefined, transformedData: undefined, error: msg }
  }

  const url = rawUrl

  const headers: Record<string, string> = {}
  for (const [key, value] of Object.entries(webhookDef.headers)) {
    headers[key] = String(resolveExpression(value, ctx) ?? value)
  }

  let body: string | undefined
  if (webhookDef.method !== 'GET' && webhookDef.body) {
    const resolved = resolveExpression(webhookDef.body, ctx) ?? webhookDef.body
    body = typeof resolved === 'object' && resolved !== null
      ? JSON.stringify(resolved)
      : String(resolved)
  }

  log('info', 'api', `${webhookDef.method} ${url}`)

  const start = performance.now()

  try {
    const response = await fetch(url, {
      method: webhookDef.method,
      headers,
      body,
    })

    const durationMs = Math.round(performance.now() - start)

    const contentType = response.headers.get('content-type') ?? ''
    const rawData: unknown = contentType.includes('json')
      ? await response.json()
      : await response.text()

    let transformedData: unknown = rawData

    // Run transform steps on the response
    if (webhookDef.steps?.length) {
      const freshCtx = useRuntimeStateStore.getState().getContext()
      transformedData = executeTransform(webhookDef.steps, rawData, freshCtx)
      const summary = Array.isArray(transformedData) ? `[${(transformedData as unknown[]).length} items]` : typeof transformedData
      log('info', 'api', `Transform: ${webhookDef.steps.length} step(s) → ${summary}`)
    }

    if (resultStore) {
      store.setGlobalStore(resultStore, transformedData)
    }

    log('success', 'api', `${webhookDef.name} → ${response.status}`, transformedData)
    return { ok: response.ok, status: response.status, statusText: response.statusText, durationMs, rawData, transformedData }
  } catch (err) {
    const durationMs = Math.round(performance.now() - start)
    const error = err instanceof Error ? err.message : 'Request failed'
    if (resultStore) {
      store.setGlobalStore(resultStore, { error })
    }
    log('error', 'api', `${webhookDef.name} failed: ${error}`)
    return { ok: false, status: 0, statusText: '', durationMs, rawData: undefined, transformedData: undefined, error }
  }
}

/**
 * Dispatch an event for a node. Reads eventHandlers from the node's props
 * and executes matching actions sequentially, awaiting async actions
 * (API calls, etc.) before proceeding to the next action in the chain.
 *
 * When called from inside a List, repeatScope provides item/index context
 * so expressions in actions can reference the current list item.
 */
export function dispatchEvent(
  eventHandlers: EventHandlerConfig[] | undefined,
  eventName: string,
  _payload?: unknown,
  repeatScope?: RepeatScope
): void {
  if (!eventHandlers?.length) return

  const matching = eventHandlers.filter((h) => h.event === eventName)
  if (!matching.length) return

  const store = useRuntimeStateStore.getState()
  const baseCtx = store.getContext()
  const ctx: ExpressionContext = repeatScope
    ? { ...baseCtx, item: repeatScope.item, index: repeatScope.index, items: repeatScope.items }
    : baseCtx
  const log = useConsoleStore.getState().log

  log('info', 'event', `${eventName} → ${matching.reduce((n, h) => n + h.actions.length, 0)} action(s)`)

  // Run async so actions can await each other sequentially.
  // Re-fetch context before each action so that state changes from
  // prior actions (e.g. runWebhook storing a result) are visible.
  void (async () => {
    for (const handler of matching) {
      for (const action of handler.actions) {
        const freshBase = useRuntimeStateStore.getState().getContext()
        const freshCtx: ExpressionContext = repeatScope
          ? { ...freshBase, item: repeatScope.item, index: repeatScope.index, items: repeatScope.items }
          : freshBase
        await executeAction(action, freshCtx, log)
      }
    }
  })()
}

async function executeAction(
  action: EventAction,
  ctx: ExpressionContext,
  log: (level: 'info' | 'warn' | 'error' | 'success', source: string, message: string, detail?: unknown) => void
): Promise<void> {
  const store = useRuntimeStateStore.getState()

  switch (action.type) {
    case 'setState': {
      const value = typeof action.value === 'string' && action.value.includes('{{')
        ? resolveExpression(action.value, ctx)
        : action.value
      store.setGlobalStore(action.storeId, value)
      log('info', 'setState', `${action.storeId} = ${JSON.stringify(value)}`)
      break
    }
    case 'mergeState': {
      let parsed: Record<string, unknown>
      try {
        const raw = typeof action.value === 'string' && action.value.includes('{{')
          ? resolveExpression(action.value, ctx)
          : action.value
        parsed = typeof raw === 'string' ? JSON.parse(raw) : raw as Record<string, unknown>
      } catch {
        log('error', 'mergeState', `Invalid merge value for ${action.storeId}`)
        break
      }
      store.mergeGlobalStore(action.storeId, parsed)
      log('info', 'mergeState', `${action.storeId} merged`, parsed)
      break
    }
    case 'setProperty': {
      const value = typeof action.value === 'string' && action.value.includes('{{')
        ? resolveExpression(action.value, ctx)
        : action.value
      let parsed = value
      if (typeof parsed === 'string') {
        try { parsed = JSON.parse(parsed) } catch { /* keep as string */ }
      }
      const resolvedPath = typeof action.path === 'string' && action.path.includes('{{')
        ? String(resolveExpression(action.path, ctx) ?? action.path)
        : action.path
      store.setStoreProperty(action.storeId, resolvedPath, parsed)
      log('info', 'setProperty', `${action.storeId}.${resolvedPath} = ${JSON.stringify(parsed)}`)
      break
    }
    case 'appendArray': {
      const value = typeof action.value === 'string' && action.value.includes('{{')
        ? resolveExpression(action.value, ctx)
        : action.value
      let parsed = value
      if (typeof parsed === 'string') {
        try { parsed = JSON.parse(parsed) } catch { /* keep as string */ }
      }
      store.appendToArray(action.storeId, action.path, parsed)
      log('info', 'appendArray', `${action.storeId}${action.path ? '.' + action.path : ''}.push(...)`, parsed)
      break
    }
    case 'removeArrayIndex': {
      const idx = typeof action.index === 'string' && action.index.includes('{{')
        ? Number(resolveExpression(action.index, ctx))
        : Number(action.index)
      store.removeFromArray(action.storeId, action.path, idx)
      log('info', 'removeArrayIndex', `${action.storeId}${action.path ? '.' + action.path : ''}.splice(${idx}, 1)`)
      break
    }
    case 'setComponentState': {
      const value = typeof action.value === 'string' && action.value.includes('{{')
        ? resolveExpression(action.value, ctx)
        : action.value
      store.setComponentState(action.nodeId, action.property, value)
      log('info', 'setComponentState', `${action.nodeId}.${action.property} = ${JSON.stringify(value)}`)
      break
    }
    case 'runWebhook': {
      log('info', 'webhook', `Running webhook ${action.webhookId}...`)
      const result = await executeWebhook(action.webhookId, action.resultStore)
      // Auto-push alert if alertStore is configured
      if (action.alertStore) {
        const alertItem = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          message: result.ok ? 'Request succeeded' : (result.error || `Request failed (${result.status})`),
          variant: result.ok ? 'success' : 'error',
          timestamp: Date.now(),
        }
        store.appendToArray(action.alertStore, '', alertItem)
      }
      break
    }
    case 'pushAlert': {
      const message = typeof action.message === 'string' && action.message.includes('{{')
        ? String(resolveExpression(action.message, ctx) ?? '')
        : action.message
      const storeName = action.store || 'alerts'
      const alertItem = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        message,
        variant: action.variant || 'info',
        timestamp: Date.now(),
      }
      store.appendToArray(storeName, '', alertItem)
      log('info', 'pushAlert', `[${action.variant}] ${message}`)
      break
    }
    case 'alert': {
      const message = typeof action.message === 'string' && action.message.includes('{{')
        ? String(resolveExpression(action.message, ctx) ?? '')
        : action.message
      log('info', 'alert', message)
      window.alert(message)
      break
    }
    case 'condition': {
      const resolved = resolveExpression(action.expression, ctx)
      // Truthy check: non-empty string, non-zero number, true boolean, non-null object
      const isTruthy = Boolean(resolved) && resolved !== 'false' && resolved !== '0' && resolved !== ''
      log('info', 'condition', `{{ ${action.expression} }} → ${isTruthy}`)
      const actions = isTruthy ? action.thenActions : action.elseActions
      // Re-fetch context after condition evaluation (state may have changed)
      const freshBase = useRuntimeStateStore.getState().getContext()
      const freshCtx: ExpressionContext = ctx.item !== undefined
        ? { ...freshBase, item: ctx.item, index: ctx.index, items: ctx.items }
        : freshBase
      for (const subAction of actions) {
        await executeAction(subAction, freshCtx, log)
      }
      break
    }
  }
}
