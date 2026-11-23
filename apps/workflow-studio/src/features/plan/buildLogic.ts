/**
 * Pure helpers behind the build screen: the SSE event reducer, progress and elapsed time, the plan's primary build
 * action, where a step links to, the report's plan-check lines, and where "New app" goes.
 */
import type { Build, BuildEvent, BuildStatus, BuildStep, StepStatus } from './buildTypes'
import type { PlanApp } from './planTypes'

const LOG_LINES = 50

/** Keep the progress lines already streamed when an update arrives without them (only GET of one build carries logs). */
const keepLog = (prev: BuildStep | undefined, next: BuildStep): BuildStep =>
  next.log === undefined && prev?.log ? { ...next, log: prev.log } : next

/** The build after one event. Events before the snapshot have nothing to apply to and are ignored. */
export function applyBuildEvent(state: Build | null, ev: BuildEvent): Build | null {
  if (ev.type === 'snapshot' || ev.type === 'done') {
    if (!state) return ev.build
    const prev = new Map(state.steps.map((s) => [s.key, s]))
    return { ...ev.build, steps: ev.build.steps.map((s) => keepLog(prev.get(s.key), s)) }
  }
  if (!state) return null
  if (ev.type === 'step') {
    const at = state.steps.findIndex((s) => s.key === ev.step.key)
    if (at < 0) return { ...state, steps: [...state.steps, ev.step] }
    const steps = state.steps.slice()
    steps[at] = keepLog(steps[at], ev.step)
    return { ...state, steps }
  }
  // files, routes and previews are the live view's (liveBuild.ts), not the build's
  if (ev.type !== 'log') return state
  return {
    ...state,
    steps: state.steps.map((s) => (s.key === ev.step_key ? { ...s, log: [...(s.log ?? []), ev.message].slice(-LOG_LINES) } : s)),
  }
}

/** Not finished yet: a build waiting for input still holds the project's one build slot. */
export const isActive = (s: BuildStatus) => s === 'queued' || s === 'running' || s === 'waiting_for_input'
export const stepFinished = (s: StepStatus) => s !== 'pending' && s !== 'running' && s !== 'waiting_for_input'

/** The step the build is paused on, when it waits for input. */
export const waitingStep = (b: Build | null | undefined): BuildStep | null =>
  b?.steps.find((s) => s.status === 'waiting_for_input') ?? null

/** The newest active build in a newest-first list. */
export const activeBuild = (builds: Build[] | undefined): Build | null => (builds ?? []).find((b) => isActive(b.status)) ?? null

/** Finished steps out of all (the step list can grow: verify and fix rounds are added as they start). */
export function buildCounts(b: Build) {
  return {
    finished: b.steps.filter((s) => stepFinished(s.status)).length,
    total: b.steps.length,
    failed: b.steps.filter((s) => s.status === 'failed').length,
  }
}

/** Milliseconds a step (or build) has run: to now while running, to `finished_at` after; null before it started. */
export function stepElapsed(s: { started_at: string | null; finished_at: string | null }, now: number): number | null {
  if (!s.started_at) return null
  const end = s.finished_at ? Date.parse(s.finished_at) : now
  return Math.max(0, end - Date.parse(s.started_at))
}

const pad = (n: number) => String(n).padStart(2, '0')

export function formatElapsed(ms: number): string {
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ${pad(sec % 60)}s`
  return `${Math.floor(min / 60)}h ${pad(min % 60)}m`
}

export type BuildAction = { kind: 'start'; label: string } | { kind: 'open'; label: string; id: string }

/** The approved plan's one primary action: open the running build, else build what is not built yet. */
export function buildAction(progress: { built: number; total: number }, active: Build | null): BuildAction {
  if (active) return { kind: 'open', label: 'Open the build', id: active.id }
  if (progress.built === 0) return { kind: 'start', label: 'Build it' }
  return { kind: 'start', label: progress.built < progress.total ? 'Build remaining' : 'Build again' }
}

/** Where a step's result lives: a module by name (from the step key) or an app by id. */
export function stepLink(s: BuildStep): { kind: 'module'; name: string } | { kind: 'app'; id: string } | null {
  if (s.kind === 'module' || s.kind === 'fix') {
    const name = s.key.split(':')[1]
    return name ? { kind: 'module', name } : null
  }
  if (s.kind === 'app' && s.app_id) return { kind: 'app', id: s.app_id }
  return null
}

const label = (k: string) => {
  const t = k.replace(/[_-]+/g, ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : t
}

/** One plan-check gap in a few words: a string as is, else its route / screen / name / key. */
function gapText(v: unknown): string {
  if (typeof v === 'string') return v
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    for (const k of ['route', 'screen', 'name', 'key', 'module', 'message']) if (typeof o[k] === 'string') return o[k] as string
  }
  return JSON.stringify(v)
}

/** What the final plan check still lists, per kind, skipping empty kinds. */
export function missingLines(missing: Record<string, unknown[]> | undefined): Array<{ kind: string; items: string[] }> {
  return Object.entries(missing ?? {})
    .filter(([, v]) => Array.isArray(v) && v.length)
    .map(([k, v]) => ({ kind: label(k), items: v.map(gapText) }))
}

/**
 * Where "New app" goes on the Plan page: Describe without a plan, a revision of the approved plan (the description
 * becomes the revise prompt), or the open draft when one is already in progress.
 */
export function newAppTarget(opts: { draft: object | null; approved: object | null }): 'describe' | 'revise' | 'draft' {
  if (opts.draft) return 'draft'
  return opts.approved ? 'revise' : 'describe'
}

export type FlowPhase = 'describe' | 'drafting' | 'questions' | 'review' | 'building' | 'input' | 'finished'

/**
 * Where the app builder's plan-and-build conversation is: the build when there is one (waiting for input is its own
 * card), else the open draft (questions, then review), else waiting for the first draft once the user has described the
 * app, else Describe.
 */
export function flowPhase(opts: { started: boolean; draft: { questions: unknown[] } | null; build: Build | null }): FlowPhase {
  if (opts.build) return opts.build.status === 'waiting_for_input' ? 'input' : isActive(opts.build.status) ? 'building' : 'finished'
  if (opts.draft) return opts.draft.questions.length ? 'questions' : 'review'
  return opts.started ? 'drafting' : 'describe'
}

/** The app a build after approval is for: the one planned app not built yet; otherwise every app (undefined). */
export function pickAppKey(apps: PlanApp[]): string | undefined {
  const open = apps.filter((a) => !a.app_id)
  return open.length === 1 ? open[0].key : undefined
}

export const BUILD_STATUS: Record<BuildStatus, { label: string; badge: 'success' | 'warning' | 'error' | 'neutral' | 'primary' }> = {
  queued: { label: 'queued', badge: 'neutral' },
  running: { label: 'building', badge: 'primary' },
  waiting_for_input: { label: 'needs your input', badge: 'warning' },
  done: { label: 'done', badge: 'success' },
  partial: { label: 'partly built', badge: 'warning' },
  failed: { label: 'failed', badge: 'error' },
  cancelled: { label: 'cancelled', badge: 'neutral' },
}
