/**
 * Pure helpers behind the Plan page: issue lookup by path, the approve gate, the breaking-change summary, each module's
 * build state from the conformance check, the Build link and the question form's starting answers.
 */
import type { ModuleBuildState, PlanBody, PlanCheck, PlanDiffResult, PlanIssue, PlanQuestion } from './planTypes'

export const errorsOf = (issues: PlanIssue[] | undefined) => (issues ?? []).filter((i) => i.severity === 'error')

/** Issues on `path` itself or anything under it (`data.tables.runs` also gets `data.tables.runs.columns.x`). */
export function issuesAt(issues: PlanIssue[] | undefined, path: string, exact = false): PlanIssue[] {
  return (issues ?? []).filter((i) => i.path === path || (!exact && i.path.startsWith(`${path}.`)))
}

export interface ApproveGate {
  allowed: boolean
  reason: string | null
}

/** Approve is possible only for a draft with no open questions, no unsaved edits and no error issue. */
export function approveGate(opts: { status: string; questions: PlanQuestion[]; issues: PlanIssue[] | undefined; dirty: boolean }): ApproveGate {
  if (opts.status !== 'draft') return { allowed: false, reason: `This version is ${opts.status}` }
  if (opts.questions.length) return { allowed: false, reason: 'Answer the open questions first' }
  if (opts.dirty) return { allowed: false, reason: 'Save your edits first' }
  const n = errorsOf(opts.issues).length
  if (n) return { allowed: false, reason: `Fix ${n} error${n === 1 ? '' : 's'} first` }
  return { allowed: true, reason: null }
}

/** One sentence for the breaking-change confirmation, naming what has to be rebuilt. */
export function breakingSummary(diff: PlanDiffResult): string {
  const n = diff.changes.filter((c) => c.breaking).length
  const parts: string[] = []
  if (diff.affected.modules.length) parts.push(`modules ${diff.affected.modules.join(', ')}`)
  if (diff.affected.apps.length) parts.push(`apps ${diff.affected.apps.join(', ')}`)
  const what = `${n} breaking change${n === 1 ? '' : 's'}`
  return parts.length ? `${what}; affects ${parts.join(' and ')}` : what
}

/** Each module's state against the approved plan; `unknown` when no check result is available. */
export function moduleStates(body: PlanBody, check: PlanCheck | null | undefined): Record<string, ModuleBuildState> {
  const out: Record<string, ModuleBuildState> = {}
  for (const m of body.modules) {
    if (!check || !check.has_plan) {
      out[m.key] = 'unknown'
      continue
    }
    const missing = check.missing
    if (missing?.modules?.includes(m.key)) out[m.key] = 'not built'
    else if ((check.stale ?? []).some((s) => s.module === m.key)) out[m.key] = 'stale'
    else if ((missing?.routes ?? []).some((r) => r.module === m.key)) out[m.key] = 'partial'
    else out[m.key] = 'built'
  }
  return out
}

/** Search params for "Build" on a module: the backend editor opens a new module and generates it from the plan. */
export function buildSearch(key: string): { prompt: string; generate: 1 } {
  return { prompt: `Build the ${key} module from the approved plan [plan:${key}]`, generate: 1 }
}

/** Which module "Build" opens: a new one until the module exists, then that module, so a rebuild saves over it. */
export function buildModule(key: string, state: ModuleBuildState): string {
  return state === 'not built' || state === 'unknown' ? '~new' : key
}

export function buildHref(project: string, key: string): string {
  const s = buildSearch(key)
  return `/p/${encodeURIComponent(project)}/backend/~new?prompt=${encodeURIComponent(s.prompt)}&generate=1`
}

/** The answers a question form starts with: each question's default (or its first option). */
export function initialAnswers(questions: PlanQuestion[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const q of questions) out[q.id] = q.suggested?.length ? q.suggested.join(ANSWER_SEP) : (q.default ?? q.options?.[0] ?? '')
  return out
}

/** The picks of a checklist answer are joined by this; free text ("other") rides along as one more part. */
export const ANSWER_SEP = '; '

/** A checklist answer split into the options picked and the free text typed next to them. */
export function parseAnswer(q: PlanQuestion, answer: string): { picked: string[]; other: string } {
  const opts = new Set(q.options ?? [])
  const parts = answer.split(';').map((x) => x.trim()).filter(Boolean)
  return { picked: parts.filter((x) => opts.has(x)), other: parts.filter((x) => !opts.has(x)).join(ANSWER_SEP) }
}

export const composeAnswer = (picked: string[], other: string) => [...picked, other.trim()].filter(Boolean).join(ANSWER_SEP)

/** Click an option: a multi question toggles it, a single one picks it (clicking the pick again clears it). Free text stays. */
export function toggleChoice(q: PlanQuestion, answer: string, option: string): string {
  const { picked, other } = parseAnswer(q, answer)
  const on = picked.includes(option)
  const next = q.multi ? (on ? picked.filter((x) => x !== option) : [...picked, option]) : on ? [] : [option]
  return composeAnswer(next, other)
}

export function setOther(q: PlanQuestion, answer: string, text: string): string {
  return composeAnswer(parseAnswer(q, answer).picked, text)
}

/** "Accept all suggestions": every question's suggested picks (or its default, or its first option). */
export const suggestedAnswers = initialAnswers

/** True when a question is a checklist item: options to pick from. */
export const isChoice = (q: PlanQuestion) => !!q.options?.length

/** A round's questions grouped under the checklist headings (`group`, else the topic), in the order they first appear. */
export function groupByHeading(questions: PlanQuestion[]): Array<{ heading: string; questions: PlanQuestion[] }> {
  const by = new Map<string, PlanQuestion[]>()
  for (const q of questions) {
    const h = q.group || humanize(q.topic || 'other')
    by.set(h, [...(by.get(h) ?? []), q])
  }
  return [...by].map(([heading, qs]) => ({ heading, questions: qs }))
}

export const unanswered = (questions: PlanQuestion[], answers: Record<string, string>) =>
  questions.filter((q) => !(answers[q.id] ?? '').trim()).map((q) => q.id)

/** Comma list <-> array, for the inline enum / route-list editors. */
export const splitList = (s: string) =>
  s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)

/* ── the requirements-first page: steps, question rounds, requirement statements ─────────────────────────────── */

export type PlanStepKey = 'describe' | 'questions' | 'review' | 'approved' | 'building'
export type PlanStepState = 'done' | 'current' | 'pending'

export const PLAN_STEPS: Array<{ key: PlanStepKey; label: string }> = [
  { key: 'describe', label: 'Describe' },
  { key: 'questions', label: 'Questions' },
  { key: 'review', label: 'Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'building', label: 'Building' },
]

const STARTED: ModuleBuildState[] = ['built', 'partial', 'stale']

/**
 * Where the project is. A draft (a first plan or a revision) is Questions while it has open questions, else Review;
 * an approved plan is Approved until the conformance check finds any module built, then Building.
 */
export function planStep(opts: {
  draft: { questions: PlanQuestion[] } | null
  approved: object | null
  states: Record<string, ModuleBuildState>
}): PlanStepKey {
  if (opts.draft) return opts.draft.questions.length ? 'questions' : 'review'
  if (opts.approved) return Object.values(opts.states).some((s) => STARTED.includes(s)) ? 'building' : 'approved'
  return 'describe'
}

/** Every step's state for the indicator; Building is done once every module is built. */
export function stepStates(current: PlanStepKey, opts: { buildDone?: boolean } = {}) {
  const at = PLAN_STEPS.findIndex((s) => s.key === current)
  return PLAN_STEPS.map((s, n) => ({
    ...s,
    state: (n < at || (n === at && current === 'building' && opts.buildDone) ? 'done' : n === at ? 'current' : 'pending') as PlanStepState,
  }))
}

/** `tests_library` -> `Tests library`. */
export function humanize(s: string): string {
  const t = s.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : t
}

const sentence = (s: string) => {
  const t = s.trim()
  return t ? t[0].toUpperCase() + t.slice(1) : t
}

/** A round's questions grouped by topic, in the order topics first appear. */
export function groupByTopic(questions: PlanQuestion[]): Array<{ topic: string; label: string; questions: PlanQuestion[] }> {
  const by = new Map<string, PlanQuestion[]>()
  for (const q of questions) by.set(q.topic, [...(by.get(q.topic) ?? []), q])
  return [...by].map(([topic, qs]) => ({ topic, label: humanize(topic || 'other'), questions: qs }))
}

/**
 * How far a round is: `answered` were confirmed by the user (typed, picked or accepted), `suggested` still hold an
 * unconfirmed prefilled default, `empty` have nothing yet.
 */
export function questionProgress(questions: PlanQuestion[], answers: Record<string, string>, confirmed: ReadonlySet<string>) {
  let answered = 0
  let suggested = 0
  let empty = 0
  for (const q of questions) {
    if (!(answers[q.id] ?? '').trim()) empty++
    else if (confirmed.has(q.id)) answered++
    else suggested++
  }
  return { total: questions.length, answered, suggested, empty }
}

/** "Use suggested answers for the rest": every question with an answer counts as confirmed. */
export function confirmSuggested(questions: PlanQuestion[], answers: Record<string, string>, confirmed: ReadonlySet<string>): Set<string> {
  const out = new Set(confirmed)
  for (const q of questions) if ((answers[q.id] ?? '').trim()) out.add(q.id)
  return out
}

export interface RequirementGroup {
  key: string
  title: string
  purpose: string
  items: Array<{ text: string; route: string }>
}

/** What the system must do, as statements: each planned route's purpose, grouped by the module (area) that serves it. */
export function requirementGroups(body: PlanBody): RequirementGroup[] {
  const keys = new Set(body.modules.map((m) => m.key))
  const item = (r: PlanBody['routes'][number]) => ({ text: sentence(r.purpose ?? '') || r.key, route: r.key })
  const out: RequirementGroup[] = []
  for (const m of body.modules) {
    const items = body.routes.filter((r) => r.module === m.key).map(item)
    if (!items.length && m.kind === 'shared') continue
    out.push({ key: m.key, title: humanize(m.key), purpose: sentence(m.purpose ?? ''), items })
  }
  const stray = body.routes.filter((r) => !keys.has(r.module)).map(item)
  if (stray.length) out.push({ key: '', title: 'Other', purpose: '', items: stray })
  return out
}

/** An acceptance criterion's `covers` entry in plain words. */
export function coversLabel(c: string): string {
  if (c.startsWith('route:')) return c.slice(6)
  if (c.startsWith('screen:')) return `${c.slice(7)} screen`
  return c
}

/** Where an issue is, in plain words, for the issue summary. */
export function issueWhere(path: string): string {
  const col = /^data\.tables\.([^.]+)\.columns\.(.+)$/.exec(path)
  if (col) return `Table ${col[1]}, column ${col[2]}`
  const screen = /^apps\.[^.]+\.screens\.(.+)$/.exec(path)
  if (screen) return `Screen ${screen[1]}`
  const named: Array<[string, string]> = [
    ['data.tables.', 'Table'],
    ['routes.', 'Route'],
    ['modules.', 'Module'],
    ['apps.', 'App'],
    ['secrets.', 'Secret'],
    ['integrations.', 'Integration'],
    ['environments.', 'Environment'],
  ]
  for (const [p, label] of named) if (path.startsWith(p)) return `${label} ${path.slice(p.length)}`
  return humanize(path)
}

/** Modules in the build order that are built (apps are built in the app builder and not counted). */
export function buildProgress(order: string[], states: Record<string, ModuleBuildState>) {
  const mods = order.filter((k) => !k.startsWith('app:'))
  return { built: mods.filter((k) => states[k] === 'built').length, total: mods.length }
}

/**
 * Whether the integrations' smoke runs have passed. The check reports one status per integration
 * (`{jobs: "not_run"}`, or `{jobs: {status}}`); a single `{status}` covers them all.
 */
export function smokeProven(smoke: unknown, integrations: string[]): boolean {
  if (!integrations.length) return true
  if (!smoke || typeof smoke !== 'object') return false
  const s = smoke as Record<string, unknown>
  const passed = (v: unknown) => v === 'passed' || (!!v && typeof v === 'object' && (v as { status?: unknown }).status === 'passed')
  if (typeof s.status === 'string') return s.status === 'passed'
  return integrations.every((k) => passed(s[k]))
}
