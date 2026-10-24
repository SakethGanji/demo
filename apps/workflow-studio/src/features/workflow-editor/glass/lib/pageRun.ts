/**
 * What the picked run says about the Page document: which cases it took and how many items went each way, why a
 * step did not run ("not taken" vs "not reached: stopped at step N"), the end chip, the items a loop went over, and
 * the short sample line under a step. Pure: a model, the connections and a RunPath in.
 *
 * The engine keeps each node's output from its LAST run only; runs that carry the per-item loop history
 * (`loop_runs`) say what every step did for every item, so inside a loop the samples, outcomes and case counts come
 * from there. Older runs fall back to the last item only, and counts are only given where they are exact.
 */
import type { ApiLoopPass, ApiLoopRun, ApiLoopStep, ApiPollRun } from '@/shared/lib/api'
import type { Block, Case, PageModel } from './pageModel'
import { portTaken, type RunConn, type RunPath } from './runPath'

/** True / false, or null without a run. */
export function caseTaken(path: RunPath | null, branch: string, c: Case): boolean | null {
  return portTaken(path, branch, c.port)
}

function firstNode(bs: Block[]): string | undefined {
  for (const b of bs) {
    if (b.t === 'goto') continue
    if (b.t === 'parallel') return undefined
    return b.node
  }
  return undefined
}

/** Whether `node` sits inside a loop body (its data is then the last pass's only). */
export function inLoop(model: PageModel, node: string): boolean {
  return (model.ancestors[node] ?? []).some((k) => k.endsWith(':loop'))
}

/** The loops a node sits inside, nearest last (from its folds). */
export function loopsOf(model: PageModel, node: string): string[] {
  return (model.ancestors[node] ?? []).filter((k) => k.endsWith(':loop')).map((k) => k.slice(0, -':loop'.length))
}

/** The loop's per-item history in this run, or undefined (a run saved before the engine kept it). */
export function loopRun(path: RunPath | null, loop: string): ApiLoopRun | undefined {
  const r = path?.loops?.[loop]
  return r && Array.isArray(r.passes) ? r : undefined
}

/** The pass that carried item `k` (0-based) of a loop. */
export function passFor(run: ApiLoopRun | undefined, k: number): ApiLoopPass | undefined {
  if (!run) return undefined
  const bs = Math.max(1, Number(run.batchSize ?? 1) || 1)
  const idx = Math.floor(k / bs)
  return run.passes.find((p) => p.index === idx)
}

export type ItemOutcome = 'ok' | 'failed' | 'skipped' | 'running' | 'none'

/** What happened to item `k`: settled, failed, skipped, still going, or no record (older runs / over the cap). */
export function itemOutcome(run: ApiLoopRun | undefined, k: number): ItemOutcome {
  const p = passFor(run, k)
  if (!p) return 'none'
  if (p.status === 'ok' || p.status === 'failed' || p.status === 'skipped' || p.status === 'running') return p.status
  return 'none'
}

/** What one step did for item `k` of its nearest loop; null when the run has no per-item history for it. */
export function stepForItem(path: RunPath | null, model: PageModel, node: string, k: number): { pass: ApiLoopPass; step: ApiLoopStep | undefined } | null {
  const loops = loopsOf(model, node)
  const loop = loops[loops.length - 1]
  if (!loop) return null
  const pass = passFor(loopRun(path, loop), k)
  if (!pass) return null
  return { pass, step: pass.nodes?.[node] }
}

/** The Poll's tries for the record being looked at: inside a loop, the session of the item being followed (else the
 * loop's last); outside, the last session. Undefined for runs saved before the engine kept tries. */
export function pollSession(path: RunPath | null, poll: string, loop?: string, k?: number): ApiPollRun['sessions'][number] | undefined {
  const sessions = path?.polls?.[poll]?.sessions
  if (!Array.isArray(sessions) || sessions.length === 0) return undefined
  if (loop) {
    const run = loopRun(path, loop)
    const pass = k !== undefined ? passFor(run, k) : run?.passes[run.passes.length - 1]
    if (pass) {
      const s = sessions.filter((x) => x.passes?.[loop] === pass.index)
      if (s.length) return s[s.length - 1]
      if (run) return undefined
    }
  }
  return sessions[sessions.length - 1]
}

/** A try's clock time, HH:MM:SS. */
export function tryTime(at: string | undefined): string {
  if (!at) return ''
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(at)
  return m ? m[1] : at
}

/** Items the run sent down a case; undefined when the engine's data cannot say it exactly. */
export function caseCount(path: RunPath | null, model: PageModel, connections: RunConn[], branch: string, c: Case): number | undefined {
  if (!path) return undefined
  if (inLoop(model, branch)) {
    // exact from the per-item history, when the choice sits in one loop and every pass was kept
    const loops = loopsOf(model, branch)
    const run = loops.length === 1 ? loopRun(path, loops[0]) : undefined
    if (!run || run.truncated) return undefined
    let n = 0
    for (const p of run.passes) n += p.nodes?.[branch]?.portCounts?.[c.port] ?? 0
    return n
  }
  const r = path.byNode[branch]
  if (!r || r.status === 'skip' || r.status === 'pending') return undefined
  if (!r.ports.includes(c.port)) return 0
  const first = firstNode(c.blocks) ?? c.continues
  if (!first) return undefined
  const ways = connections.filter((x) => x.target_node === first)
  if (ways.length !== 1) return undefined
  return path.byNode[first]?.inputItems
}

export interface CaseSummary {
  steps: number
  choice: boolean
  stop: boolean
}

export function summarize(bs: Block[], model: PageModel): CaseSummary {
  let steps = 0
  let choice = false
  let stop = false
  const walk = (xs: Block[]) => {
    for (const b of xs) {
      if (b.t === 'goto') continue
      if (b.t === 'parallel') {
        b.lanes.forEach(walk)
        continue
      }
      steps++
      if (model.nodes[b.node]?.type === 'StopAndError') stop = true
      if (b.t === 'branch') {
        choice = true
        b.cases.forEach((c) => walk(c.blocks))
      }
      if (b.t === 'loop') {
        walk(b.body)
        b.exits.forEach((c) => walk(c.blocks))
      }
    }
  }
  walk(bs)
  return { steps, choice, stop }
}

/** "2 steps, with a choice inside", "2 steps, ends with Stop", "1 step". */
export function foldText(bs: Block[], model: PageModel): string {
  const s = summarize(bs, model)
  const base = `${s.steps} step${s.steps === 1 ? '' : 's'}`
  if (s.stop) return `${base}, ends with Stop`
  if (s.choice) return `${base}, with a choice inside`
  return base
}

/** The node the run stopped at (failed there, or is waiting there); undefined when it ran to the end or live. */
export function stoppedAt(path: RunPath | null): string | undefined {
  if (!path) return undefined
  if (path.errors[0]?.node_name) return path.errors[0].node_name
  return path.ran.find((n) => path.byNode[n]?.status === 'fail' || path.byNode[n]?.status === 'need')
}

export type ReachNote = { kind: 'not-taken' } | { kind: 'not-reached'; at: string; num: string } | { kind: 'not-run' }

/** Why a step the run skipped did not run. Null when it ran, or without a run. */
export function reachNote(path: RunPath | null, model: PageModel, node: string): ReachNote | null {
  if (!path || !path.finished) return null
  const r = path.byNode[node]
  if (r && r.status !== 'skip') return null
  const folds = (model.ancestors[node] ?? []).slice(1)
  for (const key of folds) {
    const i = key.lastIndexOf(':')
    const b = key.slice(0, i)
    const port = key.slice(i + 1)
    if (port === 'loop') continue
    if (portTaken(path, b, port) === false) return { kind: 'not-taken' }
  }
  const at = stoppedAt(path)
  if (at) return { kind: 'not-reached', at, num: model.top[at] ?? model.numbers[at] ?? '' }
  return { kind: 'not-run' }
}

export interface EndChip {
  tone: 'ok' | 'bad' | 'warn' | 'neutral'
  text: string
}

/** The chip at the top of the document: where this run ended. */
export function endChip(path: RunPath | null, model: PageModel): EndChip | null {
  if (!path) return null
  const s = String(path.status).toLowerCase()
  const numOf = (n: string) => model.top[n] ?? model.numbers[n] ?? ''
  if (s === 'waiting') {
    const at = stoppedAt(path)
    return { tone: 'warn', text: at ? `Waiting at step ${numOf(at)}` : 'Waiting on a person' }
  }
  if (!path.finished) return { tone: 'neutral', text: 'Running' }
  if (s === 'cancelled' || s === 'canceled') return { tone: 'neutral', text: 'Cancelled' }
  const at = stoppedAt(path)
  if (at && s !== 'success' && s !== 'completed') return { tone: 'bad', text: `Stops at step ${numOf(at)}` }
  const last = [...path.ran].reverse().find((n) => model.numbers[n] && model.numbers[n] !== '!')
  return { tone: 'ok', text: last ? `Ends at step ${numOf(last)}` : 'Finished' }
}

/** The items a Loop went over, from its `done` output (`{_loop_completed, total_items, items}`). */
export function loopItems(path: RunPath | null, node: string): unknown[] {
  const r = path?.byNode[node]
  if (!r) return []
  const done = r.items.find((x) => x && typeof x === 'object' && '_loop_completed' in x) as { items?: unknown[] } | undefined
  return Array.isArray(done?.items) ? done!.items : []
}

function scalar(v: unknown): string | null {
  if (v === null) return 'null'
  if (typeof v === 'string') return v.length > 40 ? `${v.slice(0, 39)}…` : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return null
}

/** Up to `max` scalar fields of an item as `[key, value]` pairs (internal `_` keys left out). */
export function itemPairs(item: unknown, max = 4): Array<[string, string]> {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    const s = scalar(item)
    return s === null ? [] : [['', s]]
  }
  const out: Array<[string, string]> = []
  for (const [k, v] of Object.entries(item as Record<string, unknown>)) {
    if (k.startsWith('_')) continue
    const s = scalar(v)
    if (s === null) {
      if (Array.isArray(v)) out.push([k, `${v.length} item${v.length === 1 ? '' : 's'}`])
      else continue
    } else out.push([k, s])
    if (out.length >= max) break
  }
  return out
}

/** The sample line under a step: how many items and the first one's fields, as text. */
export function sampleText(items: unknown[]): string {
  if (!items.length) return 'No items'
  const pairs = itemPairs(items[0])
  const head = items.length > 1 ? `${items.length} items · ` : ''
  if (!pairs.length) return `${head || '1 item'}`.replace(/ · $/, '')
  return head + pairs.map(([k, v]) => (k ? `${k} ${v}` : v)).join(' · ')
}
