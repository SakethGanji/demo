import { useEffect, useState } from 'react'
import { followRun } from '@/shared/api/client'
import type { RunEvent } from '@/shared/api/types'

export type LiveModel = { model: string; family?: string | null; status: 'running' | 'done' | 'failed'; order: number; val?: number | null; fit?: number | null; weights?: Record<string, number> | null; error?: string }
export type LiveState = { events: RunEvent[]; models: LiveModel[]; stage: string; done: boolean; status: 'running' | 'succeeded' | 'failed' | 'cancelled'; error?: string; t0: number | null; last: number | null }

/** A run's events over SSE (replayed from the start), folded into the model list the live views draw. Used by the
 *  forecast, anomaly and import pages; a tabular run's race has its own richer state (quick-train/race/useRace). */
export function useRunEvents(runId: string | null): LiveState {
  const [st, setSt] = useState<LiveState>({ events: [], models: [], stage: 'queued', done: false, status: 'running', t0: null, last: null })
  useEffect(() => {
    if (!runId) return
    setSt({ events: [], models: [], stage: 'queued', done: false, status: 'running', t0: null, last: null })
    return followRun(runId, (e) => setSt((s) => fold(s, e)))
  }, [runId])
  return st
}

function fold(s: LiveState, e: RunEvent): LiveState {
  const models = [...s.models]
  const m = e.model as string | undefined
  const at = (name: string) => models.findIndex((x) => x.model === name)
  let stage = s.stage
  if (e.type === 'run_started') stage = 'starting'
  if (e.type === 'data_ready') stage = 'data ready'
  if (e.type === 'fit_started') stage = 'fitting'
  if (e.type === 'model_started' && m) {
    if (at(m) < 0) models.push({ model: m, family: e.family as string, status: 'running', order: (e.order as number) ?? models.length + 1 })
    stage = `fitting ${m}`
  }
  if (e.type === 'model_finished' && m) {
    const i = at(m)
    const rec: LiveModel = { ...(i >= 0 ? models[i] : { model: m, order: models.length + 1, family: e.family as string }), status: 'done',
      val: (e.val_score_natural as number) ?? null, fit: (e.fit_time as number) ?? null }
    if (i >= 0) models[i] = rec; else models.push(rec)
  }
  if (e.type === 'model_failed' && m) { const i = at(m); if (i >= 0) models[i] = { ...models[i], status: 'failed', error: e.error as string } }
  if (e.type === 'ensemble' && m) { const i = at(m); if (i >= 0) models[i] = { ...models[i], weights: e.weights as Record<string, number> } }
  if (e.type === 'fit_finished') stage = 'fit finished'
  if (e.type === 'explaining') stage = `computing ${e.what}`
  if (e.type === 'importing') stage = 'loading the predictor'
  if (e.type === 'run_succeeded') return { ...s, events: [...s.events, e], models, stage: 'done', done: true, status: 'succeeded', last: e.ts }
  if (e.type === 'run_failed' || e.type === 'run_cancelled') return { ...s, events: [...s.events, e], models, stage: e.type, done: true, status: e.type === 'run_failed' ? 'failed' : 'cancelled', error: e.error as string, last: e.ts }
  return { ...s, events: e.type === 'log' ? s.events : [...s.events, e], models, stage, t0: s.t0 ?? e.ts, last: e.ts }
}
