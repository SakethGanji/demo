// The live race: a run's events folded into what the stage draws, played back at a readable pace.

import { useEffect, useRef, useState } from 'react'
import { followRun } from '@/shared/api/client'
import type { EnsembleEvent, Holdout, ModelFinishedEvent, ModelStartedEvent, RunEvent } from '@/shared/api/types'

export type ModelRow = {
  model: string; family: string | null; level: number; order: number; bagged: boolean
  status: 'fitting' | 'done' | 'failed' | 'skipped'
  done?: ModelFinishedEvent; error?: string
  /** performance.now() when its start was shown; -1 for rows that arrived in a replay. */
  shownAt: number
}

export type RaceStatus = 'connecting' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type RaceState = {
  status: RaceStatus
  /** Server timestamps (s since epoch) of run_started and the terminal event. */
  startTs: number | null; endTs: number | null; queuedTs: number | null
  trainRows: number | null; holdoutRows: number | null; features: string[]; droppedColumns: string[]; droppedMissing: number
  /** F4: the (label, strategy) split the run trains and is scored on. */
  split?: { key: string; target: string; strategy: string; sha256: string; holdout_rows: number; params?: { group_column?: string; time_column?: string } | null; tuning_rows?: number; excluded_rows?: number; cutoff?: string | number | null; tuning_cutoff?: string | number | null } | null
  problemType: string | null; metric: string | null; greater: boolean; numClasses: number | null; kFold: number | null
  timeLimit: number | null; notes: string[]
  models: Record<string, ModelRow>; order: string[]
  ensembles: Record<string, EnsembleEvent>; lastEnsemble: string | null
  bestModel: string | null; holdout: Holdout | null
  stage: 'waiting' | 'fitting' | 'calibrating' | 'ranking' | 'evaluating' | 'explaining' | 'done'
  logs: { level: string; message: string }[]
  error: string | null
  lastSeq: number
}

export const EMPTY_RACE: RaceState = {
  status: 'connecting', startTs: null, endTs: null, queuedTs: null, trainRows: null, holdoutRows: null, features: [],
  droppedColumns: [], droppedMissing: 0, problemType: null, metric: null, greater: true, numClasses: null, kFold: null,
  timeLimit: null, notes: [], models: {}, order: [], ensembles: {}, lastEnsemble: null, bestModel: null, holdout: null,
  stage: 'waiting', logs: [], error: null, lastSeq: -1,
}

/** Fold one event into the race. Events of AutoGluon's dynamic-stacking sub-fit are left out (they are a rehearsal). */
export function applyEvent(s: RaceState, e: RunEvent, shownAt: number): RaceState {
  if (e.seq <= s.lastSeq) return s
  s = { ...s, lastSeq: e.seq }
  if (e.phase === 'dystack') return s
  switch (e.type) {
    case 'run_queued': return { ...s, status: s.status === 'connecting' ? 'queued' : s.status, queuedTs: e.ts, timeLimit: (e.time_limit as number) ?? null }
    case 'run_started': return { ...s, status: 'running', startTs: e.ts }
    case 'data_ready': return {
      ...s, trainRows: e.train_rows as number, holdoutRows: e.holdout_rows as number, features: (e.features as string[]) ?? [],
      droppedColumns: (e.dropped_columns as string[]) ?? [], droppedMissing: (e.dropped_missing_label as number) ?? 0,
      split: (e.split as RaceState['split']) ?? null,
    }
    case 'config_resolved': return { ...s, notes: (e.notes as string[]) ?? [] }
    case 'fit_started': return {
      ...s, stage: 'fitting', problemType: e.problem_type as string, metric: e.eval_metric as string,
      greater: e.greater_is_better as boolean, numClasses: (e.num_classes as number) ?? null, kFold: (e.k_fold as number) ?? null,
    }
    case 'model_started': {
      const m = e as ModelStartedEvent
      const row: ModelRow = { model: m.model, family: m.family, level: m.stack_level, order: m.order, bagged: m.bagged, status: 'fitting', shownAt }
      return { ...s, models: { ...s.models, [m.model]: row }, order: s.order.includes(m.model) ? s.order : [...s.order, m.model] }
    }
    case 'model_finished': {
      const m = e as ModelFinishedEvent
      const prev = s.models[m.model] ?? { model: m.model, family: m.family, level: m.stack_level, order: s.order.length + 1, bagged: false, shownAt }
      return { ...s, models: { ...s.models, [m.model]: { ...prev, status: 'done', done: m } }, order: s.order.includes(m.model) ? s.order : [...s.order, m.model] }
    }
    case 'model_failed': {
      const prev = s.models[e.model as string]
      if (!prev) return s
      return { ...s, models: { ...s.models, [prev.model]: { ...prev, status: e.skipped ? 'skipped' : 'failed', error: String(e.error ?? 'failed') } } }
    }
    case 'ensemble': {
      const en = e as EnsembleEvent
      return { ...s, ensembles: { ...s.ensembles, [en.model]: en }, lastEnsemble: en.model }
    }
    case 'calibrating': return { ...s, stage: 'calibrating' }
    case 'fit_finished': return { ...s, stage: 'ranking', bestModel: e.best_model as string }
    case 'leaderboard': return { ...s, stage: 'evaluating' }
    case 'evaluation': return { ...s, holdout: e as unknown as Holdout }
    case 'explaining': return { ...s, stage: 'explaining' }
    case 'log': return { ...s, logs: [...s.logs.slice(-79), { level: String(e.level ?? 'info'), message: String(e.message ?? '') }] }
    case 'run_succeeded': return { ...s, status: 'succeeded', stage: 'done', endTs: e.ts, bestModel: (e.best_model as string) ?? s.bestModel }
    case 'run_failed': return { ...s, status: 'failed', stage: 'done', endTs: e.ts, error: String(e.error ?? 'The run failed.') }
    case 'run_cancelled': return { ...s, status: 'cancelled', stage: 'done', endTs: e.ts }
    default: return s
  }
}

/** Minimum time a model is on screen as "fitting" before its score shows, at 1×. */
export const MIN_ON_SCREEN_MS = 1400

/** Something that produces a run's events: the live API (followRun) or the mock simulation. */
export type EventSourceFn = (runId: string, onEvent: (e: RunEvent) => void, hooks: { onError: () => void; onOpen: () => void }) => () => void

export const liveSource: EventSourceFn = (runId, onEvent, hooks) => followRun(runId, onEvent, hooks)

/**
 * Follow a run and play its events back so that every model is on screen for at least MIN_ON_SCREEN_MS / speed.
 * Events from before we connected (a replay after a reload or reconnect) are applied at once, so resuming a run
 * mid-way catches up immediately and then continues live.
 */
export function useRace(runId: string | null, speed: number, source: EventSourceFn = liveSource) {
  const [state, setState] = useState<RaceState>(EMPTY_RACE)
  const [reconnecting, setReconnecting] = useState(false)
  const queue = useRef<RunEvent[]>([])
  const shown = useRef<Record<string, number>>({})
  const connectedAt = useRef(0)
  const speedRef = useRef(speed)
  speedRef.current = speed

  useEffect(() => {
    setState(EMPTY_RACE)
    queue.current = []
    shown.current = {}
    if (!runId) return
    connectedAt.current = Date.now() / 1000
    const stop = source(runId, (e) => { queue.current.push(e) }, {
      onError: () => setReconnecting(true), onOpen: () => setReconnecting(false),
    })
    const pump = () => {
      const now = performance.now()
      const batch: [RunEvent, number][] = []
      while (queue.current.length) {
        const e = queue.current[0]
        const replayed = e.ts < connectedAt.current - 0.5
        if (!replayed && e.type === 'model_finished' && e.phase !== 'dystack') {
          const t0 = shown.current[e.model as string]
          if (t0 != null && t0 >= 0 && now - t0 < MIN_ON_SCREEN_MS / speedRef.current) break
        }
        queue.current.shift()
        let at = now
        if (e.type === 'model_started') { at = replayed ? -1 : now; shown.current[e.model as string] = at }
        batch.push([e, at])
      }
      if (batch.length) setState((s) => batch.reduce((acc, [e, at]) => applyEvent(acc, e, at), s))
    }
    const iv = setInterval(pump, 40)
    return () => { stop(); clearInterval(iv) }
  }, [runId, source])

  return { state, reconnecting, behind: queue.current.length }
}

// ---------------------------------------------------------------- derived

export const isEnsemble = (m: string, family?: string | null) => family === 'ENS_WEIGHTED' || m.startsWith('WeightedEnsemble')

/** Rows grouped into stack layers, in the order they started. Skipped rows are counted, not drawn. */
export function layers(s: RaceState) {
  const rows = s.order.map((m) => s.models[m]).filter(Boolean)
  const levels = [...new Set(rows.map((r) => r.level))].sort((a, b) => a - b)
  return levels.map((level) => {
    const at = rows.filter((r) => r.level === level)
    return {
      level,
      base: at.filter((r) => !isEnsemble(r.model, r.family) && r.status !== 'skipped'),
      ensembles: at.filter((r) => isEnsemble(r.model, r.family) && r.status !== 'skipped'),
      skipped: at.filter((r) => r.status === 'skipped').length,
    }
  })
}

/** The shared score axis: [lo, hi] in natural units over every finished model, padded, and a map to [0, 1] where 1 is best. */
export function scoreAxis(s: RaceState) {
  const vals: number[] = []
  for (const r of Object.values(s.models)) {
    const d = r.done
    if (!d || d.val_score_natural == null) continue
    vals.push(d.val_score_natural)
    for (const f of d.fold_scores_natural ?? []) if (f != null) vals.push(f)
  }
  if (!vals.length) return null
  let lo = Math.min(...vals), hi = Math.max(...vals)
  if (hi - lo < 1e-9) { const p = Math.max(Math.abs(hi) * 0.01, 1e-3); lo -= p; hi += p }
  const pad = (hi - lo) * 0.12
  lo -= pad; hi += pad
  const frac = (v: number) => (s.greater ? (v - lo) / (hi - lo) : (hi - v) / (hi - lo))
  return { lo, hi, frac }
}

export function bestSoFar(s: RaceState): ModelRow | null {
  let best: ModelRow | null = null
  for (const r of Object.values(s.models)) {
    const v = r.done?.val_score
    if (v == null) continue
    if (!best || v > (best.done!.val_score as number)) best = r
  }
  return best
}
