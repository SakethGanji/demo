/**
 * The Page view's sentences: one deterministic template per node type, written from the node's own parameters.
 * No AI and no network: change a value and the sentence rewrites itself.
 *
 * A sentence is `{text, tokens}`. `text` holds `{0}`, `{1}`… where each token goes; a token is one parameter value
 * (`path` is where it lives on the node, e.g. `parameters.url`), rendered as a dotted value the reader can click.
 * Unknown types fall back to their type name, so every node reads as something.
 */
import { personOf, readKind } from '../../lib/readView'

export interface SentenceToken {
  /** Where the value lives on the node, e.g. `parameters.cronExpression`. */
  path: string
  /** The value as text ('' when it is not set yet). */
  value: string
  /** What the value is: `schedule`, `url`, `table`. */
  label: string
  /** The step the value reads from, when it is a condition on an earlier step's field (shows as `· step N`). */
  source?: string
}

export interface Sentence {
  text: string
  tokens: SentenceToken[]
}

/** The minimum of a node a sentence needs (a backend node definition fits). */
export interface SentenceNode {
  name: string
  type: string
  label?: string
  parameters?: Record<string, unknown>
  notes?: string
}

export type SentencePart = string | { token: SentenceToken; index: number }

const str = (v: unknown): string => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** `{{ $json.amount > 500 }}` → `amount > 500`: what a reader recognises, not the template syntax. */
export function prettyExpr(v: unknown): string {
  let s = str(v).trim()
  const m = s.match(/^\{\{\s*([\s\S]*?)\s*\}\}$/)
  if (m) s = m[1]
  return s
    .replace(/\{\{\s*([\s\S]*?)\s*\}\}/g, '$1')
    .replace(/\$json\.(\w+)/g, '$1')
    .replace(/\$json\[['"]([^'"]+)['"]\]/g, '$1')
    .replace(/\$node\[['"]([^'"]+)['"]\]\.json\.(\w+)/g, '$1.$2')
    .replace(/\s+/g, ' ')
    .trim()
}

/** An operation name (If / Switch / Filter rules) in words: `lt` → `is under`. */
export const OP_WORDS: Record<string, string> = {
  equals: 'is',
  eq: 'is',
  notEquals: 'is not',
  not_equals: 'is not',
  ne: 'is not',
  contains: 'contains',
  notContains: 'does not contain',
  not_contains: 'does not contain',
  gt: 'is over',
  gte: 'is at least',
  lt: 'is under',
  lte: 'is at most',
  isEmpty: 'is empty',
  is_empty: 'is empty',
  isNotEmpty: 'is not empty',
  is_not_empty: 'is not empty',
  isTrue: 'is true',
  isFalse: 'is false',
  regex: 'matches',
  startsWith: 'starts with',
  starts_with: 'starts with',
  endsWith: 'ends with',
  ends_with: 'ends with',
}

/** Operations that take no value to compare with. */
export const UNARY_OPS = new Set(['isEmpty', 'is_empty', 'isNotEmpty', 'is_not_empty', 'isTrue', 'isFalse'])

export function opWords(op: unknown): string {
  const o = str(op)
  return OP_WORDS[o] ?? (o ? o.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase() : 'is')
}

const CMP: Array<[string, string]> = [
  ['===', 'is'],
  ['!==', 'is not'],
  ['==', 'is'],
  ['!=', 'is not'],
  ['>=', 'is at least'],
  ['<=', 'is at most'],
  ['>', 'is over'],
  ['<', 'is under'],
]

/** A field reference in words: `$node["Normalize"].json.amount` → amount (from Normalize); `$json.risk_level` → risk level. */
function fieldOf(e: string): { name: string; source?: string } | null {
  const t = e.trim()
  let m = t.match(/^\$node\[\s*["']([^"']+)["']\s*\]\.json\.([\w$]+(?:\.[\w$]+)*)$/)
  if (m) return { name: m[2].split('.').pop()!.replace(/_/g, ' '), source: m[1] }
  m = t.match(/^\$json\.([\w$]+(?:\.[\w$]+)*)$/) ?? t.match(/^\$json\[["']([^"']+)["']\]$/)
  if (m) return { name: m[1].split('.').pop()!.replace(/_/g, ' ') }
  return null
}

/** A literal in words: 5000 → 5,000; 'ach' → ach; true → true. */
function literalOf(e: string): string | null {
  const t = e.trim()
  if (/^-?\d+(\.\d+)?$/.test(t)) {
    const n = Number(t)
    return Math.abs(n) >= 1000 ? n.toLocaleString('en-US', { maximumFractionDigits: 10 }) : t
  }
  const q = t.match(/^(['"])(.*)\1$/)
  if (q) return q[2]
  if (/^(true|True)$/.test(t)) return 'true'
  if (/^(false|False)$/.test(t)) return 'false'
  if (/^(null|None)$/.test(t)) return 'empty'
  return null
}

function sideOf(e: string): { text: string; source?: string } | null {
  const f = fieldOf(e)
  if (f) return { text: f.name, source: f.source }
  const l = literalOf(e)
  return l === null ? null : { text: l }
}

/** One comparison in words; null when it is not a shape we know. */
function clauseWords(e: string): { text: string; source?: string } | null {
  let t = e.trim()
  while (/^\(.*\)$/.test(t) && !/\)\s*.*\(/.test(t.slice(1, -1))) t = t.slice(1, -1).trim()
  let neg = false
  const nm = t.match(/^(?:not\s+|!)\s*(.+)$/)
  if (nm) {
    neg = true
    t = nm[1].trim()
  }
  // a method: x.includes('a') / x.contains('a') / x.startswith('a') / x.endsWith('a')
  const mm = t.match(/^(.+?)\.(includes|contains|startswith|startsWith|endswith|endsWith)\(\s*(.+?)\s*\)$/)
  if (mm) {
    const l = sideOf(mm[1])
    const r = sideOf(mm[3])
    if (!l || !r || r.source) return null
    const verb = /^(includes|contains)$/.test(mm[2]) ? (neg ? 'does not contain' : 'contains') : /^starts/i.test(mm[2]) ? (neg ? 'does not start with' : 'starts with') : neg ? 'does not end with' : 'ends with'
    return { text: `${l.text} ${verb} ${r.text}`, source: l.source }
  }
  // 'a' in x
  const im = t.match(/^(['"].*['"])\s+(not\s+in|in)\s+(.+)$/)
  if (im) {
    const l = sideOf(im[3])
    const r = literalOf(im[1])
    if (!l || r === null) return null
    const no = neg !== /^not/.test(im[2])
    return { text: `${l.text} ${no ? 'does not contain' : 'contains'} ${r}`, source: l.source }
  }
  // len(x) == 0 / len(x) > 0
  const lm = t.match(/^len\((.+)\)\s*(==|>|!=)\s*0$/)
  if (lm) {
    const f = fieldOf(lm[1])
    if (!f) return null
    const empty = (lm[2] === '==') !== neg
    return { text: `${f.name} is ${empty ? 'empty' : 'not empty'}`, source: f.source }
  }
  for (const [op, words] of CMP) {
    const i = t.indexOf(op)
    if (i <= 0) continue
    const l = sideOf(t.slice(0, i))
    const r = sideOf(t.slice(i + op.length))
    if (!l || !r) return null
    if (l.source && r.source) return null
    let w = words
    if (neg) w = w === 'is' ? 'is not' : w === 'is not' ? 'is' : `is not ${w.replace(/^is /, '')}`
    if ((op === '==' || op === '===') && r.text === 'true' && !neg) return { text: `${l.text} is true`, source: l.source }
    return { text: `${l.text} ${w} ${r.text}`, source: l.source ?? r.source }
  }
  const f = fieldOf(t)
  if (f) return { text: `${f.name} is ${neg ? 'false' : 'true'}`, source: f.source }
  return null
}

/**
 * A condition in plain words: `{{ $node["Normalize"].json.amount < 5000 }}` → `amount is under 5,000` (from
 * Normalize). Joins `and` / `or` / `&&` / `||`. Falls back to `prettyExpr` for anything it cannot read.
 */
export function conditionWords(v: unknown): { text: string; source?: string } {
  let s = str(v).trim()
  const m = s.match(/^\{\{\s*([\s\S]*?)\s*\}\}$/)
  if (!m) return { text: prettyExpr(v) }
  s = m[1].replace(/\s+/g, ' ').trim()
  if (/^(true|True)$/.test(s)) return { text: 'true' }
  const parts = s.split(/\s+(and|or|&&|\|\|)\s+/)
  const out: string[] = []
  const sources = new Set<string>()
  for (let i = 0; i < parts.length; i++) {
    if (i % 2) {
      out.push(parts[i] === '&&' || parts[i] === 'and' ? 'and' : 'or')
      continue
    }
    const c = clauseWords(parts[i])
    if (!c) return { text: prettyExpr(v) }
    out.push(c.text)
    if (c.source) sources.add(c.source)
  }
  return { text: out.join(' '), source: sources.size === 1 ? [...sources][0] : undefined }
}

/** A condition token for a sentence: the words, plus the step it reads from. */
function condTok(path: string, v: unknown, label = 'condition'): SentenceToken {
  const w = conditionWords(v)
  return { path: `parameters.${path}`, value: w.text, label, ...(w.source ? { source: w.source } : {}) }
}

const clip = (s: string, n = 64) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

const DOW: Record<string, string> = { '0': 'Sunday', '1': 'Monday', '2': 'Tuesday', '3': 'Wednesday', '4': 'Thursday', '5': 'Friday', '6': 'Saturday', '7': 'Sunday' }

/** A cron expression in words, for the common shapes; null when it is something else. */
export function describeCron(expr: string): string | null {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [mi, h, dom, mon, dow] = parts
  const at = (hh: string, mm: string) => `${hh.padStart(2, '0')}:${mm.padStart(2, '0')}`
  const num = (s: string) => /^\d+$/.test(s)
  if (mi === '*' && h === '*' && dom === '*' && mon === '*' && dow === '*') return 'every minute'
  const everyMin = mi.match(/^\*\/(\d+)$/)
  if (everyMin && h === '*' && dom === '*' && mon === '*' && dow === '*') return `every ${everyMin[1]} minutes`
  if (num(mi) && h === '*' && dom === '*' && mon === '*' && dow === '*') return mi === '0' ? 'every hour' : `every hour at :${mi.padStart(2, '0')}`
  const everyH = h.match(/^\*\/(\d+)$/)
  if (num(mi) && everyH && dom === '*' && mon === '*' && dow === '*') return `every ${everyH[1]} hours`
  if (num(mi) && num(h) && dom === '*' && mon === '*') {
    if (dow === '*') return `every day at ${at(h, mi)}`
    if (dow === '1-5') return `weekdays at ${at(h, mi)}`
    if (dow === '0,6' || dow === '6,0') return `weekends at ${at(h, mi)}`
    if (DOW[dow]) return `every ${DOW[dow]} at ${at(h, mi)}`
  }
  if (num(mi) && num(h) && num(dom) && mon === '*' && dow === '*') return `on day ${dom} of every month at ${at(h, mi)}`
  return null
}

function seconds(n: number): string {
  if (n % 86400 === 0) return n === 86400 ? 'day' : `${n / 86400} days`
  if (n % 3600 === 0) return n === 3600 ? 'hour' : `${n / 3600} hours`
  if (n % 60 === 0) return n === 60 ? 'minute' : `${n / 60} minutes`
  return `${n} seconds`
}

/** Builds a sentence from a template with `{name}` slots, each filled by one token. */
function S(text: string, ...tokens: Array<[name: string, path: string, value: unknown, label: string]>): Sentence {
  const out: SentenceToken[] = []
  let t = text
  for (const [name, path, value, label] of tokens) {
    t = t.replace(`{${name}}`, `{${out.length}}`)
    out.push({ path: path ? `parameters.${path}` : '', value: str(value), label })
  }
  return { text: t, tokens: out }
}

const plain = (text: string): Sentence => ({ text, tokens: [] })

const DT_VERB: Record<string, string> = {
  insert: 'Add a row to the table {t}.',
  get: 'Look up a row in the table {t}.',
  list: 'Read the rows of the table {t}.',
  update: 'Update rows in the table {t}.',
  delete: 'Delete rows from the table {t}.',
  upsert: 'Add or update a row in the table {t}.',
}

/** The sentence for one node. */
export function sentenceFor(node: SentenceNode): Sentence {
  const p = node.parameters ?? {}
  switch (node.type) {
    case 'Webhook':
      return S('When a {m} request arrives at {p}.', ['m', 'method', str(p.method) || 'POST', 'method'], ['p', 'path', p.path ? `/${str(p.path).replace(/^\//, '')}` : '', 'path'])
    case 'Cron': {
      if (str(p.mode) === 'cron' || (p.cronExpression && str(p.mode) !== 'interval')) {
        const expr = str(p.cronExpression) || '0 * * * *'
        const words = describeCron(expr)
        return S(words ? `On a schedule, ${words} ({c}).` : 'On the schedule {c}.', ['c', 'cronExpression', expr, 'schedule'])
      }
      const n = Number(p.interval ?? 60)
      return S('Every {i}.', ['i', 'interval', Number.isFinite(n) && n > 0 ? seconds(n) : str(p.interval), 'interval'])
    }
    case 'FormTrigger':
      return S('When someone submits the form {t}.', ['t', 'title', p.title, 'form'])
    case 'ExecuteWorkflowTrigger':
      return plain('When another workflow runs this one.')
    case 'ErrorTrigger':
      return plain('When a run of this workflow fails.')
    case 'Start':
    case 'ManualTrigger':
      return plain('When you run it by hand.')
    case 'ChatInput':
      return plain('When someone sends a chat message.')
    case 'If': {
      if (p.condition !== undefined && str(p.condition) !== '') return { text: 'If {0}:', tokens: [condTok('condition', p.condition)] }
      if (UNARY_OPS.has(str(p.operation))) return S('If {f} {o}:', ['f', 'field', prettyExpr(p.field), 'field'], ['o', 'operation', opWords(p.operation), 'test'])
      return S('If {f} {o} {v}:', ['f', 'field', prettyExpr(p.field), 'field'], ['o', 'operation', opWords(p.operation || 'equals'), 'test'], ['v', 'value', p.value, 'value'])
    }
    case 'Switch': {
      const rules = Array.isArray(p.rules) ? p.rules : []
      const n = rules.length || Number(p.numberOfOutputs) || 0
      return S('Choose a path: the first of {n} that matches.', ['n', 'rules', `${n} rule${n === 1 ? '' : 's'}`, 'rules'])
    }
    case 'Loop':
    case 'SplitInBatches': {
      const max = Number(p.maxIterations)
      if (max > 0 || (typeof p.maxIterations === 'string' && p.maxIterations.includes('{{')))
        return S('Repeat the steps below {n}.', ['n', 'maxIterations', max > 0 ? `${max} times` : prettyExpr(p.maxIterations), 'times'])
      const b = Number(p.batchSize ?? 1)
      return b > 1 ? S('For each batch of {b} items, do the steps below.', ['b', 'batchSize', b, 'batch size']) : plain('For each item, do the steps below.')
    }
    case 'Merge':
      return S('Wait for the paths to arrive, then combine them ({m}).', ['m', 'mode', str(p.mode) || 'append', 'how'])
    case 'Wait':
      return S('Wait {d} {u}.', ['d', 'duration', p.duration ?? p.amount, 'how long'], ['u', 'unit', str(p.unit) || 'seconds', 'unit'])
    case 'Approval': {
      const who = personOf(p.notify_email) || personOf(p.assignees)
      const base = S(
        p.due_in ? 'Ask {w} to approve {t}, and wait up to {d} for an answer.' : 'Ask {w} to approve {t}.',
        ['w', p.notify_email ? 'notify_email' : 'assignees', who, 'who'],
        ['t', 'title', p.title, 'what'],
        ...(p.due_in ? ([['d', 'due_in', p.due_in, 'how long']] as Array<[string, string, unknown, string]>) : []),
      )
      if (p.four_eyes) base.text += ' Two different people must approve.'
      if (p.on_timeout === 'reject') base.text += ' No answer counts as no.'
      if (p.on_timeout === 'approve') base.text += ' No answer counts as yes.'
      return base
    }
    case 'ExecuteWorkflow': {
      const inputs = p.inputs && typeof p.inputs === 'object' && !Array.isArray(p.inputs) ? Object.keys(p.inputs as object) : []
      const s = S('↳ Run the workflow {w}', ['w', 'workflowId', p.workflowId, 'workflow'])
      if (p.version !== undefined && str(p.version) !== '' && str(p.version) !== 'latest') {
        s.text += ` (version {${s.tokens.length}})`
        s.tokens.push({ path: 'parameters.version', value: str(p.version), label: 'version' })
      }
      if (inputs.length) {
        s.text += ` with {${s.tokens.length}}`
        s.tokens.push({ path: 'parameters.inputs', value: clip(inputs.join(', '), 48), label: 'inputs' })
      }
      s.text += '.'
      return s
    }
    case 'Respond':
    case 'RespondToWebhook':
      return S('Reply with status {s}.', ['s', 'statusCode', p.statusCode ?? 200, 'status'])
    case 'StopAndError':
      return S('Stop the run with the error {m}.', ['m', 'message', p.message, 'message'])
    case 'Poll':
    {
      const s = S('Check every {i} until {c}, for up to {t}.', ['i', 'interval', p.interval, 'how often'], ['c', 'condition', '', 'condition'], ['t', 'timeout', p.timeout, 'how long'])
      s.tokens[1] = condTok('condition', p.condition)
      return s
    }
    case 'Set': {
      if (str(p.mode) === 'json' || (!Array.isArray(p.fields) && p.jsonData)) return S('Shape the data as {j}.', ['j', 'jsonData', clip(prettyExpr(p.jsonData), 48), 'JSON'])
      const fields = (Array.isArray(p.fields) ? p.fields : []) as Array<{ name?: string; value?: unknown }>
      if (!fields.length) return plain('Set fields on each item.')
      const shown = fields.slice(0, 3)
      const s: Sentence = { text: 'Set ', tokens: [] }
      shown.forEach((f, i) => {
        s.text += `${i ? (i === shown.length - 1 && fields.length <= 3 ? ' and ' : ', ') : ''}${f.name || 'a field'} to {${i}}`
        s.tokens.push({ path: `parameters.fields.${i}.value`, value: clip(prettyExpr(f.value), 40), label: f.name || 'value' })
      })
      if (fields.length > 3) s.text += `, and ${fields.length - 3} more`
      s.text += p.keepOnlySet ? ', and drop everything else.' : '.'
      return s
    }
    case 'Code':
    case 'PythonStep': {
      if (node.notes?.trim()) return { text: 'Run custom code: {0}.', tokens: [{ path: 'notes', value: clip(node.notes.trim().replace(/\.$/, ''), 80), label: 'what it does' }] }
      if (node.type === 'PythonStep' && p.function) return S('Run the Python function {f}.', ['f', 'function', p.function, 'function'])
      const lines = str(p.code).split('\n').filter((l) => l.trim()).length
      return plain(lines ? `Run custom code (${lines} line${lines === 1 ? '' : 's'}).` : 'Run custom code.')
    }
    case 'Filter':
      if (p.condition !== undefined && str(p.condition) !== '') return { text: 'Keep only the items where {0}.', tokens: [condTok('condition', p.condition)] }
      return S('Keep only the items where {c}.', ['c', 'condition', Array.isArray(p.rules) ? `${p.rules.length} rules` : '', 'condition'])
    case 'DataTable': {
      const op = str(p.operation) || 'list'
      return S(DT_VERB[op] ?? `Run ${op} on the table {t}.`, ['t', 'table', p.table ?? p.tableName, 'table'])
    }
    case 'ItemLists':
      if (str(p.operation) === 'splitOut' || str(p.operation) === 'split_out') {
        const key = p.arrayField !== undefined ? 'arrayField' : p.fieldToSplitOut !== undefined ? 'fieldToSplitOut' : 'field'
        const f = prettyExpr(p[key]).split('.').pop()!.replace(/_/g, ' ')
        return f ? S('Split the list {f} into one item per entry.', ['f', key, f, 'list']) : plain('Split the list into one item per entry.')
      }
      return S('{o} the list{f}.', ['o', 'operation', str(p.operation).replace(/([a-z])([A-Z])/g, '$1 $2') || 'Process', 'operation'], ['f', '', p.field ? ` by ${str(p.field)}` : '', ''])
    case 'Aggregate': {
      const g = Array.isArray(p.groupBy) ? p.groupBy.join(', ') : str(p.groupBy)
      return g ? S('Add up the items, grouped by {g}.', ['g', 'groupBy', g, 'group by']) : plain('Add up the items.')
    }
    case 'HttpRequest': {
      const ref = p.api_ref as { operation_id?: string; api?: string } | undefined
      if (ref?.operation_id) return S('Call the API operation {o}.', ['o', 'api_ref', ref.operation_id, 'operation'])
      return S('Call {m} {u}.', ['m', 'method', str(p.method) || 'GET', 'method'], ['u', 'url', clip(str(p.url), 60), 'url'])
    }
    case 'Postgres': {
      const conn = str(p.connection)
      if (str(p.operation) === 'transaction') {
        const n = Array.isArray(p.statements) ? p.statements.length : 0
        return S('In one transaction on {c}, run {n}.', ['c', 'connection', conn, 'connection'], ['n', 'statements', `${n} statement${n === 1 ? '' : 's'}`, 'statements'])
      }
      const q = str(p.query).trim().replace(/\s+/g, ' ')
      const write = /^\s*(insert|update|delete|merge|upsert)\b/i.test(q)
      return S(write ? 'Write to {c}: {q}' : 'Pull from {c}: {q}', ['c', 'connection', conn, 'connection'], ['q', 'query', clip(q, 70), 'query'])
    }
    case 'SendEmail':
      return S('Email {t} with the subject {s}.', ['t', 'toEmail', personOf(p.toEmail) || str(p.toEmail), 'to'], ['s', 'subject', clip(prettyExpr(p.subject), 60), 'subject'])
    case 'AIAgent':
      return S('Ask an AI agent to {t}.', ['t', 'task', clip(prettyExpr(p.task ?? p.systemPrompt), 70), 'task'])
    case 'LLMChat':
      return S('Ask {m}: {u}', ['m', 'model', p.model || 'the model', 'model'], ['u', 'userMessage', clip(prettyExpr(p.userMessage), 70), 'message'])
    case 'NoOp':
      return plain('Carry on.')
    case 'Html':
      return plain('Read values out of an HTML page.')
    default: {
      const k = readKind(node.type)
      if (k === 'ai') return plain(`Use the AI tool ${node.type}.`)
      return plain(`Run the ${node.type} step.`)
    }
  }
}

/** Splits a sentence into text runs and tokens, in order, for rendering. Tokens with an empty path and value vanish. */
export function sentenceParts(s: Sentence): SentencePart[] {
  const out: SentencePart[] = []
  const re = /\{(\d+)\}/g
  let last = 0
  for (let m = re.exec(s.text); m; m = re.exec(s.text)) {
    if (m.index > last) out.push(s.text.slice(last, m.index))
    const index = Number(m[1])
    const token = s.tokens[index]
    if (token && (token.path || token.value)) {
      if (token.path) out.push({ token, index })
      else out.push(token.value)
    }
    last = m.index + m[0].length
  }
  if (last < s.text.length) out.push(s.text.slice(last))
  return out
}

/** The sentence as plain text (an empty value reads as its label in brackets). */
export function sentenceText(s: Sentence): string {
  return sentenceParts(s)
    .map((p) => (typeof p === 'string' ? p : p.token.value || `[${p.token.label}]`))
    .join('')
}

export interface KindInfo {
  kind: string
  parts: string[]
}

/** The mono kind label over a step: `query · bank-prod`, `http · POST`. */
export function kindFor(node: SentenceNode): KindInfo {
  const p = node.parameters ?? {}
  const parts = (...xs: unknown[]) => xs.map(str).filter(Boolean)
  switch (node.type) {
    case 'Webhook': return { kind: 'trigger', parts: parts('webhook') }
    case 'Cron': return { kind: 'trigger', parts: parts('schedule') }
    case 'FormTrigger': return { kind: 'trigger', parts: parts('form') }
    case 'ExecuteWorkflowTrigger': return { kind: 'trigger', parts: parts('called by a workflow') }
    case 'ErrorTrigger': return { kind: 'on error', parts: [] }
    case 'Start': case 'ManualTrigger': return { kind: 'trigger', parts: parts('manual') }
    case 'ChatInput': return { kind: 'trigger', parts: parts('chat') }
    case 'If': return { kind: 'if', parts: [] }
    case 'Switch': return { kind: 'switch', parts: [] }
    case 'Loop': case 'SplitInBatches': return { kind: 'for each', parts: [] }
    case 'Merge': return { kind: 'merge', parts: [] }
    case 'Wait': return { kind: 'wait', parts: [] }
    case 'Approval': return { kind: 'approval', parts: parts(p.four_eyes ? 'four eyes' : '') }
    case 'ExecuteWorkflow': return { kind: 'sub-workflow', parts: [] }
    case 'HttpRequest': return { kind: 'http', parts: parts((p.api_ref as { api?: string } | undefined)?.api, str(p.method) || 'GET') }
    case 'Postgres': return { kind: 'query', parts: parts('postgres', p.connection) }
    case 'DataTable': return { kind: 'table', parts: parts(p.operation) }
    case 'SendEmail': return { kind: 'email', parts: [] }
    case 'Code': return { kind: 'code', parts: parts(p.language || 'javascript') }
    case 'PythonStep': return { kind: 'code', parts: parts('python') }
    case 'Set': return { kind: 'set', parts: [] }
    case 'Filter': return { kind: 'filter', parts: [] }
    case 'Respond': case 'RespondToWebhook': return { kind: 'reply', parts: [] }
    case 'StopAndError': return { kind: 'stop', parts: [] }
    case 'Poll': return { kind: 'poll', parts: [] }
    case 'AIAgent': return { kind: 'ai agent', parts: parts(p.model) }
    case 'LLMChat': return { kind: 'ai', parts: parts(p.model) }
    default: return { kind: node.type.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase(), parts: [] }
  }
}

/** Whether a node type starts a run. */
export function isTriggerNode(type: string): boolean {
  return TRIGGER_TYPES.has(type)
}
const TRIGGER_TYPES = new Set(['Start', 'ManualTrigger', 'Webhook', 'Cron', 'FormTrigger', 'ChatInput', 'ErrorTrigger', 'ExecuteWorkflowTrigger'])
