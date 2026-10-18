/**
 * Pure helpers for the approvals inbox and task page (DESIGN-STAGE5-UIS §5.4, §6.4, §11.4). No React here so the
 * rules are unit-tested: keyset merge, decide-outcome wording, approver-form validation, request body.
 */
import { splitPath } from '@/features/interfaces/lib/binding'
import { clock } from './clock'
import { taskStatusLabel } from '@/features/interfaces/lib/format'
import type {
  ApprovalFieldSpec, DecideResult, TaskDetail, TaskPage, TaskStatus, TaskSummary,
} from '@/features/interfaces/lib/types'

export type InboxStatus = 'open' | 'closed' | 'all'

/** Pages -> one list. Keyset paging can repeat a row when the head moves, so dedupe by id (first copy wins). */
export function flattenPages(pages: TaskPage[] | undefined): TaskSummary[] {
  const seen = new Set<string>()
  const out: TaskSummary[] = []
  for (const p of pages ?? []) {
    for (const t of p.items) {
      if (seen.has(t.id)) continue
      seen.add(t.id)
      out.push(t)
    }
  }
  return out
}

/** `getNextPageParam` for the keyset hook: the opaque cursor, or undefined to stop. */
export function nextCursor(last: TaskPage | undefined): string | undefined {
  return last?.next_cursor ? last.next_cursor : undefined
}

export function assigneesLabel(a: string[] | undefined): string {
  if (!a || a.length === 0) return 'Anyone'
  return a.length > 2 ? `${a[0]}, ${a[1]} +${a.length - 2}` : a.join(', ')
}

export type OutcomeTone = 'ok' | 'error' | 'info' | 'warn'
export interface OutcomeView {
  tone: OutcomeTone
  text: string
  outcome: DecideResult['outcome'] | 'closed'
}

const VERB: Partial<Record<TaskStatus, string>> = { approved: 'approved', rejected: 'rejected' }

/** "Approved by Dana at 14:02" / "Already approved by Dana at 14:02" / "This task is already closed (timed out)". */
export function describeOutcome(r: DecideResult): OutcomeView {
  const t = r.task
  const at = t.decided_at ? ` at ${clock(t.decided_at)}` : ''
  const by = t.decided_by ? ` by ${t.decided_by}` : ''
  const verb = VERB[t.status]
  if (r.outcome === 'decided') {
    return { tone: t.status === 'rejected' ? 'error' : 'ok', text: `${verb ? cap(verb) : taskStatusLabel(t.status)}${by}${at}`, outcome: 'decided' }
  }
  if (r.outcome === 'already_decided') {
    return { tone: 'warn', text: `Already ${verb ?? taskStatusLabel(t.status).toLowerCase()}${by}${at}`, outcome: 'already_decided' }
  }
  return { tone: 'warn', text: `This task is already closed (${taskStatusLabel(t.status).toLowerCase()})${at}`, outcome: 'already_closed' }
}

/** The banner for a task opened after it was decided. */
export function describeTaskState(t: TaskDetail | TaskSummary): OutcomeView | null {
  if (t.status === 'open') return null
  const at = t.decided_at ? ` at ${clock(t.decided_at)}` : ''
  const by = t.decided_by && t.decided_by !== 'system:timeout' ? ` by ${t.decided_by}` : ''
  const verb = VERB[t.status]
  const text = verb ? `${cap(verb)}${by}${at}` : t.status === 'timed_out' ? `Timed out${at}` : `${taskStatusLabel(t.status)}${at}`
  return { tone: t.status === 'rejected' ? 'error' : t.status === 'approved' ? 'ok' : 'info', text, outcome: 'closed' }
}

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s
}

export interface OptionView {
  value: string
  label: string
}

export function optionsOf(f: ApprovalFieldSpec): OptionView[] {
  return (f.options ?? []).map((o) => (typeof o === 'string' ? { value: o, label: o } : { value: o.value, label: o.label ?? o.value }))
}

export function initialFormData(form: ApprovalFieldSpec[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of form) if (f.type === 'checkbox') out[f.name] = false
  return out
}

/** Mirrors §5.4 "form_data validated against the task form (required, types, options)"; the server is the authority. */
export function validateApproverForm(form: ApprovalFieldSpec[], data: Record<string, unknown>): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const f of form) {
    const v = data[f.name]
    const empty = v === undefined || v === null || (typeof v === 'string' && v.trim() === '')
    if (f.type === 'checkbox') {
      if (f.required && v !== true) errors[f.name] = 'This must be checked'
      continue
    }
    if (empty) {
      if (f.required) errors[f.name] = 'This field is required'
      continue
    }
    if (f.type === 'number' && !Number.isFinite(typeof v === 'number' ? v : Number(v))) errors[f.name] = 'Enter a number'
    if (f.type === 'select' && !optionsOf(f).some((o) => o.value === String(v))) errors[f.name] = 'Choose one of the options'
  }
  return errors
}

export interface DecisionInput {
  decision: 'approved' | 'rejected'
  decidedBy: string
  comment: string
  formData: Record<string, unknown>
  form: ApprovalFieldSpec[]
  allowComment: boolean
  requireCommentOnReject: boolean
}

export function validateDecision(i: DecisionInput): Record<string, string> {
  const errors: Record<string, string> = {}
  const who = i.decidedBy.trim()
  if (!who) errors.decided_by = 'Type your name or email'
  else if (who.length > 200) errors.decided_by = 'Keep it under 200 characters'
  if (i.decision === 'rejected' && i.requireCommentOnReject && !i.comment.trim()) errors.comment = 'A comment is required to reject'
  // The approver's form only has to be complete to approve; a rejection may leave it blank.
  if (i.decision === 'approved') Object.assign(errors, validateApproverForm(i.form, i.formData))
  return errors
}

/** The decide request body (form values are typed: numbers as numbers, empty optionals dropped). */
export function decisionBody(i: DecisionInput) {
  const form_data: Record<string, unknown> = {}
  for (const f of i.form) {
    const v = i.formData[f.name]
    if (f.type === 'checkbox') form_data[f.name] = v === true
    else if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) continue
    else form_data[f.name] = f.type === 'number' ? Number(v) : String(v)
  }
  const comment = i.allowComment ? i.comment.trim() : ''
  return {
    decision: i.decision,
    ...(comment ? { comment } : {}),
    ...(Object.keys(form_data).length ? { form_data } : {}),
    decided_by: i.decidedBy.trim(),
  }
}

/** Server field errors may be keyed `form_data.note`; the page keys them by bare field name. */
export function stripFormDataPrefix(errors: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(errors)) out[k.startsWith('form_data.') ? k.slice('form_data.'.length) : k] = v
  return out
}

/**
 * `task.context._bindings` -> the `node` root of a task screen (§6.3: public task pages never read node outputs;
 * the Approval node captured the bound paths at suspend time). ASSUMED shapes, both accepted:
 *   { "Classify": { "summary": "..." } }   or   { "node.Classify.summary": "..." } / { "Classify.summary": "..." }
 */
export function nodeDataFromBindings(bindings: unknown): Record<string, unknown> {
  const node: Record<string, unknown> = {}
  if (!bindings || typeof bindings !== 'object' || Array.isArray(bindings)) return node
  for (const [rawKey, value] of Object.entries(bindings as Record<string, unknown>)) {
    const key = rawKey.startsWith('node.') ? rawKey.slice(5) : rawKey
    const segs = splitPath(key)
    if (segs.length === 0 || segs.some((s) => s === '__proto__' || s === 'constructor' || s === 'prototype')) continue
    if (segs.length === 1 && value && typeof value === 'object' && !Array.isArray(value)) {
      node[segs[0]] = { ...(node[segs[0]] as object | undefined), ...(value as object) }
      continue
    }
    let cur = node as Record<string, unknown>
    segs.slice(0, -1).forEach((s) => {
      const next = cur[s]
      cur[s] = next && typeof next === 'object' && !Array.isArray(next) ? next : {}
      cur = cur[s] as Record<string, unknown>
    })
    cur[segs[segs.length - 1]] = value
  }
  return node
}

/** Context rows shown to the approver: internal `_`-prefixed keys (e.g. `_bindings`, `_truncated`) stay hidden. */
export function visibleContext(ctx: Record<string, unknown> | undefined): [string, unknown][] {
  return Object.entries(ctx ?? {}).filter(([k]) => !k.startsWith('_'))
}
