/**
 * The Page document's words, beyond the step sentences (pageSentences): the kind label over each step ("Look up",
 * "Ask a person"), the "simulated in tests" mark on steps that write to the outside world, the "If this fails" rule
 * read from the engine's retry fields, the If / Is / Otherwise heads of a choice's cases, and value references
 * (`{{ $node["Step"].json.field }}`) for the token picker. Pure.
 */
import { conditionWords, kindFor, opWords, prettyExpr, isTriggerNode, UNARY_OPS, type Sentence } from './pageSentences'
import type { PageNode } from './pageModel'
import type { RunConn } from './runPath'

const str = (v: unknown) => (v === undefined || v === null ? '' : typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : '')

const PG_WRITE = /^\s*(insert|update|delete|merge|upsert|create|drop|alter|truncate)\b/i
const DT_READ = new Set(['get', 'list', 'find', 'search', 'count'])

function pgWrites(p: Record<string, unknown>): boolean {
  return str(p.operation) === 'transaction' || PG_WRITE.test(str(p.query)) || ['insert', 'update', 'delete', 'upsert', 'execute'].includes(str(p.operation))
}

/** The small uppercase label over a step. `inLoop`: a loop nested in another loop. */
export function docKind(node: PageNode, opts: { inLoop?: boolean } = {}): string {
  const p = node.parameters ?? {}
  if (isTriggerNode(node.type) && node.type !== 'ErrorTrigger') return 'When'
  switch (node.type) {
    case 'ErrorTrigger':
      return 'When it fails'
    case 'If':
    case 'Switch':
      return 'Choose one path'
    case 'Loop':
    case 'SplitInBatches': {
      const times = Number(p.maxIterations) > 0 || (typeof p.maxIterations === 'string' && p.maxIterations.includes('{{'))
      if (times) return opts.inLoop ? 'Repeat, inside a loop' : 'Repeat'
      return opts.inLoop ? 'For each, inside a for each' : 'For each'
    }
    case 'Poll':
      return 'Repeat until'
    case 'HttpRequest': {
      const m = (str(p.method) || 'GET').toUpperCase()
      return m === 'GET' || m === 'HEAD' ? 'Look up' : m === 'DELETE' ? 'Delete' : 'Call an API'
    }
    case 'DataTable': {
      const op = str(p.operation) || 'list'
      if (op === 'insert') return 'Add a row'
      if (op === 'upsert') return 'Add or update'
      if (op === 'update') return 'Update'
      if (op === 'delete') return 'Delete'
      return 'Find rows'
    }
    case 'Postgres':
    case 'MongoDB':
    case 'Neo4j':
      return pgWrites(p) ? 'Update' : 'Find rows'
    case 'Approval':
      return 'Ask a person'
    case 'SendEmail':
      return 'Send an email'
    case 'StopAndError':
      return 'Stop'
    case 'Code':
    case 'PythonStep':
      return 'Run code'
    case 'Set':
      return 'Set values'
    case 'Filter':
      return 'Keep only'
    case 'Merge':
      return 'Combine'
    case 'Wait':
      return 'Wait'
    case 'ExecuteWorkflow':
      return 'Run a workflow'
    case 'Respond':
    case 'RespondToWebhook':
      return 'Reply'
    case 'Aggregate':
      return 'Summarise'
    case 'ItemLists':
      return 'Reshape'
    case 'AIAgent':
    case 'LLMChat':
      return 'Ask AI'
    case 'NoOp':
      return 'Carry on'
    default: {
      const k = kindFor(node).kind
      return k.charAt(0).toUpperCase() + k.slice(1)
    }
  }
}

/** A step that changes something outside the workflow: a test run never calls it (it needs a mock). */
export function isSimulated(node: PageNode): boolean {
  const p = node.parameters ?? {}
  switch (node.type) {
    case 'Approval':
    case 'SendEmail':
      return true
    case 'DataTable':
      return !DT_READ.has(str(p.operation) || 'list')
    case 'Postgres':
    case 'MongoDB':
    case 'Neo4j':
      return pgWrites(p)
    case 'HttpRequest': {
      const m = (str(p.method) || 'GET').toUpperCase()
      return m !== 'GET' && m !== 'HEAD'
    }
    default:
      return false
  }
}

export interface FailRule {
  /** Tries after the first failure. */
  retries: number
  /** Wait between tries (ms). */
  delayMs: number
  /** After the last try: stop the run, carry on with the next step, or follow the step's `error` path. */
  then: 'stop' | 'continue' | 'error'
}

/** The step's "If this fails" rule, or null for the engine default (no retries, stop the run). */
export function failRule(node: PageNode): FailRule | null {
  const retries = Math.max(0, Math.floor(Number(node.retry_on_fail) || 0))
  const then: FailRule['then'] = node.on_error === 'error_output' ? 'error' : node.on_error === 'continue' || node.continue_on_fail ? 'continue' : 'stop'
  if (!retries && then === 'stop') return null
  const d = Number(node.retry_delay)
  return { retries, delayMs: Number.isFinite(d) && d > 0 ? d : 1000, then }
}

/** 5 minutes, 1 second, 250 ms. */
export function humanMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  const unit = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  const s = ms / 1000
  if (s % 3600 === 0) return unit(s / 3600, 'hour')
  if (s % 60 === 0 && s >= 120) return unit(s / 60, 'minute')
  if (s === 60) return '1 minute'
  return unit(Math.round(s), 'second')
}

export interface CaseHead {
  cs: 'If' | 'Is' | 'Otherwise'
  text: string
  tone?: 'bad'
}

type Rule = { output?: number; field?: unknown; operation?: string; value?: unknown; output_name?: string }

/** How a case of a choice is introduced: If <condition>, Is <value>, Otherwise. */
export function caseHead(node: PageNode, port: string, label: string): CaseHead {
  const p = node.parameters ?? {}
  if (port === 'error') return { cs: 'If', text: 'it fails', tone: 'bad' }
  if (port === 'timeout') return { cs: 'If', text: 'it times out', tone: 'bad' }
  switch (node.type) {
    case 'If': {
      if (port === 'false') return { cs: 'Otherwise', text: "it isn't" }
      const cond = p.condition !== undefined && str(p.condition) !== '' ? conditionWords(p.condition).text : [prettyExpr(p.field), opWords(p.operation || 'equals'), UNARY_OPS.has(str(p.operation)) ? '' : str(p.value)].filter(Boolean).join(' ')
      return { cs: 'If', text: cond || 'yes' }
    }
    case 'Switch': {
      if (port === 'fallback') return { cs: 'Otherwise', text: 'any other value' }
      const m = port.match(/^output(\d+)$/)
      const rules = (Array.isArray(p.rules) ? p.rules : []) as Rule[]
      const rule = m ? rules.find((r, j) => (r?.output ?? j) === Number(m[1])) : undefined
      const op = rule?.operation ?? 'equals'
      if (rule && !rule.output_name && op !== 'equals' && op !== 'eq') return { cs: 'If', text: [prettyExpr(rule.field), opWords(op), UNARY_OPS.has(op) ? '' : str(rule.value)].filter(Boolean).join(' ') }
      return { cs: 'Is', text: label }
    }
    case 'Approval':
      if (port === 'timed_out') return { cs: 'If', text: 'no answer in time' }
      return { cs: 'If', text: label }
    default:
      if (port === 'main') return { cs: 'If', text: 'it works' }
      return { cs: 'If', text: label }
  }
}

/** The document's sentence for a choice (Go one way depending on…); null: use `sentenceFor`. */
export function docSentence(node: PageNode): Sentence | null {
  const p = node.parameters ?? {}
  if (node.type === 'If') {
    if (p.condition !== undefined && str(p.condition) !== '') {
      const w = conditionWords(p.condition)
      return { text: 'Go one way depending on whether {0}.', tokens: [{ path: 'parameters.condition', value: w.text, label: 'condition', ...(w.source ? { source: w.source } : {}) }] }
    }
    return null
  }
  if (node.type === 'Switch' && str(p.mode) !== 'expression') {
    const rules = (Array.isArray(p.rules) ? p.rules : []) as Rule[]
    const fields = [...new Set(rules.map((r) => prettyExpr(r?.field)).filter(Boolean))]
    if (fields.length === 1) return { text: 'Go one way depending on the {0}.', tokens: [{ path: 'parameters.rules.0.field', value: fields[0], label: 'field' }] }
    return null
  }
  return null
}

export interface TokenRef {
  field: string
  /** The step the value comes from; absent: the step just before (`$json`). */
  source?: string
}

/** A token's value read as a reference to an earlier step's field; null when it is anything else. */
export function tokenRef(raw: unknown): TokenRef | null {
  const s = str(raw).trim()
  let m = s.match(/^\{\{\s*\$node\[\s*["']([^"']+)["']\s*\]\.json\.([\w$]+(?:\.[\w$]+)*)\s*\}\}$/)
  if (m) return { field: m[2], source: m[1] }
  m = s.match(/^\{\{\s*\$json\.([\w$]+(?:\.[\w$]+)*)\s*\}\}$/)
  if (m) return { field: m[1] }
  return null
}

/** The expression a picked value writes. */
export function refExpr(source: string, field: string): string {
  return `{{ $node["${source}"].json.${field} }}`
}

/** The steps that run before `node` (everything that can reach it), nearest first. */
export function upstreamOf(connections: RunConn[], node: string): string[] {
  const into = new Map<string, string[]>()
  for (const c of connections) {
    if (!into.has(c.target_node)) into.set(c.target_node, [])
    into.get(c.target_node)!.push(c.source_node)
  }
  const out: string[] = []
  const seen = new Set([node])
  let q = [node]
  while (q.length) {
    const next: string[] = []
    for (const n of q)
      for (const s of into.get(n) ?? []) {
        if (seen.has(s)) continue
        seen.add(s)
        out.push(s)
        next.push(s)
      }
    q = next
  }
  return out
}
