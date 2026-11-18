// Mock mode: a simulated AutoGluon run in the browser, with the same events and result shape as the API, so the
// race and the results draw exactly as they do live. Nothing is fitted; the stage says "simulated".

import { rng } from '@/shared/lib/random'
import type { LeaderboardRow, Run, RunEvent } from '@/shared/api/types'
import type { Dataset } from '../datasets'
import type { Choices, Task } from '../wizard'
import { metricInfo } from '../wizard'
import type { EventSourceFn } from './useRace'

const ZOO: [string, string, number][] = [
  // name, family, typical fit seconds
  ['LightGBMXT', 'GBM', 0.7], ['LightGBM', 'GBM', 0.5], ['RandomForestGini', 'RF', 1.1], ['RandomForestEntr', 'RF', 1.2],
  ['CatBoost', 'CAT', 2.3], ['ExtraTreesGini', 'XT', 0.8], ['ExtraTreesEntr', 'XT', 0.8], ['NeuralNetFastAI', 'FASTAI', 4.1],
  ['XGBoost', 'XGB', 0.4], ['NeuralNetTorch', 'NN_TORCH', 5.2], ['LightGBMLarge', 'GBM', 1.3],
]

type Sim = { events: Omit<RunEvent, 'seq' | 'ts'>[]; delays: number[]; run: Run }

export function simulate(ds: Dataset, ch: Choices, task: Task, runId: string): Sim {
  const r = rng(ds.name.length * 131 + ch.label.length * 17 + ch.preset.length)
  const reg = task === 'regression'
  const m = metricInfo(ch.metric)
  const greater = m?.greater ?? true
  const n = ds.rows, holdout = Math.round(n * 0.2), train = n - holdout
  const zoo = [...ZOO, ...(ds.kind === 'text' && !reg ? [['TFIDF_LR', 'TFIDF_LR', 1.6], ['TFIDF_SVM', 'TFIDF_SVM', 2.1]] as [string, string, number][] : [])]
  // a plausible score per model on the chosen metric
  const base = reg ? (greater ? 0.82 : 1000) : greater ? (ch.metric === 'roc_auc' ? 0.86 : ch.metric === 'mcc' ? 0.62 : 0.9) : 0.28
  const spread = reg ? (greater ? 0.05 : 140) : greater ? 0.03 : 0.06
  const strength: Record<string, number> = { GBM: 0.9, CAT: 1, XGB: 0.85, RF: 0.55, XT: 0.5, NN_TORCH: 0.8, FASTAI: 0.7, TFIDF_LR: 0.75, TFIDF_SVM: 0.72 }
  const score = (fam: string) => {
    const q = (strength[fam] ?? 0.6) + (r() - 0.5) * 0.3
    return greater ? base - spread + spread * q : base + spread - spread * q
  }
  const scores = zoo.map(([, fam]) => score(fam))
  const order = scores.map((v, i) => [v, i] as const).sort((a, b) => (greater ? b[0] - a[0] : a[0] - b[0]))
  const top = order.slice(0, 3).map(([, i]) => i)
  const raw = [0.52, 0.3, 0.18].map((w) => w + (r() - 0.5) * 0.08)
  const tot = raw.reduce((a, b) => a + b, 0)
  const weights: Record<string, number> = {}
  top.forEach((i, j) => { weights[zoo[i][0]] = Math.round((raw[j] / tot) * 1000) / 1000 })
  weights[zoo[top[0]][0]] = Math.round((1 - Object.values(weights).slice(1).reduce((a, b) => a + b, 0)) * 1000) / 1000
  const ensScore = greater ? scores[top[0]] + spread * 0.08 : scores[top[0]] - spread * 0.08
  const pt = task
  const metric = ch.metric
  const features = ds.cols.filter((c) => c.name !== ch.label && c.type !== 'id').map((c) => c.name)

  const events: Sim['events'] = []
  const delays: number[] = []
  const push = (e: Omit<RunEvent, 'seq' | 'ts'>, delay: number) => { events.push(e); delays.push(delay) }
  push({ type: 'run_queued', time_limit: ch.budget, presets: ch.preset }, 0)
  push({ type: 'run_started', pid: 0 }, 200)
  push({ type: 'data_ready', train_rows: train, holdout_rows: holdout, features, dropped_columns: ds.cols.filter((c) => c.type === 'id').map((c) => c.name), dropped_missing_label: 0 }, 300)
  push({ type: 'config_resolved', notes: ['simulated in the browser: no model is fitted in mock mode'] }, 300)
  push({ type: 'fit_started', phase: 'main', problem_type: pt, eval_metric: metric, greater_is_better: greater, num_classes: reg ? null : ds.classes.length, k_fold: 0 }, 400)
  zoo.forEach(([name, fam, secs], i) => {
    push({ type: 'model_started', phase: 'main', model: name, family: fam, model_type: fam, stack_level: 1, stack_name: 'core', time_limit: ch.budget, bagged: false, order: i + 1 }, 50)
    push({ type: 'log', level: 'info', message: `Fitting model: ${name} ... Training model for up to ${(ch.budget - i * 2).toFixed(2)}s of the ${ch.budget}s of remaining time.` }, 10)
    push({ type: 'model_finished', phase: 'main', model: name, family: fam, stack_level: 1, val_score: greater ? scores[i] : -scores[i], val_score_natural: scores[i], fit_time: secs * (0.8 + r() * 0.4), predict_time: 0.002 + r() * 0.08, fold_scores: null, fold_scores_natural: null, val_score_std: null, n_children: 1, elapsed: 0 }, secs * 260)
    push({ type: 'log', level: 'info', message: `\t${scores[i].toFixed(4)}\t = Validation score   (${metric})` }, 10)
  })
  push({ type: 'model_started', phase: 'main', model: 'WeightedEnsemble_L2', family: 'ENS_WEIGHTED', model_type: 'GreedyWeightedEnsembleModel', stack_level: 2, stack_name: 'aux1', time_limit: ch.budget, bagged: true, order: zoo.length + 1 }, 100)
  push({ type: 'model_finished', phase: 'main', model: 'WeightedEnsemble_L2', family: 'ENS_WEIGHTED', stack_level: 2, val_score: greater ? ensScore : -ensScore, val_score_natural: ensScore, fit_time: 0.08, predict_time: 0.001, fold_scores: null, fold_scores_natural: null, val_score_std: null, n_children: 1, elapsed: 0 }, 150)
  push({ type: 'ensemble', phase: 'main', model: 'WeightedEnsemble_L2', stack_level: 2, weights, val_score: greater ? ensScore : -ensScore, val_score_natural: ensScore }, 20)
  push({ type: 'fit_finished', best_model: 'WeightedEnsemble_L2', fit_seconds: 20, n_models: zoo.length + 1 }, 300)
  push({ type: 'leaderboard' }, 400)

  // holdout: scores a little below validation; a confusion matrix whose accuracy matches
  const lb: LeaderboardRow[] = [...zoo.map(([name, fam, secs], i) => ({ name, fam, secs, v: scores[i], lvl: 1 })), { name: 'WeightedEnsemble_L2', fam: 'ENS_WEIGHTED', secs: 0.08, v: ensScore, lvl: 2 }]
    .map((x) => {
      const t = greater ? x.v - spread * (0.05 + r() * 0.15) : x.v + spread * (0.05 + r() * 0.15)
      return {
        model: x.name, family: x.fam, rank: 0, score_val: greater ? x.v : -x.v, score_test: greater ? t : -t, score_val_natural: x.v, score_test_natural: t,
        fit_time: x.secs, pred_time_val: 0.01, pred_time_test: holdout * (2e-6 + r() * 4e-5), stack_level: x.lvl, can_infer: true,
        fold_scores: null, val_score_std: null, weights: x.fam === 'ENS_WEIGHTED' ? weights : null,
      }
    })
    .sort((a, b) => (b.score_val as number) - (a.score_val as number))
    .map((row, i) => ({ ...row, rank: i + 1 }))
  const best = lb[0]
  const classes = ds.classes.map((c) => c.name)
  const acc = reg ? 0 : Math.min(0.97, Math.max(0.55, metric === 'accuracy' ? (best.score_test_natural as number) : 0.9))
  const cm = reg ? undefined : classes.map((_, i) => {
    const tot = Math.max(1, Math.round(holdout * ds.classes[i].share))
    const hit = Math.round(tot * Math.min(0.99, acc + (r() - 0.5) * 0.08 - (ds.classes[i].share < 0.1 ? 0.1 : 0)))
    const rest = classes.map((__, j) => (j === i ? 0 : r() + 0.1)); const rs = rest.reduce((a, b) => a + b, 0)
    return classes.map((__, j) => (j === i ? hit : Math.round(((tot - hit) * rest[j]) / rs)))
  })
  const metrics: Record<string, number> = { [metric]: best.score_test_natural as number }
  if (!reg && metric !== 'accuracy') metrics.accuracy = acc
  if (!reg) metrics.balanced_accuracy = Math.max(0.5, acc - 0.06)
  if (reg && metric !== 'r2') metrics.r2 = 0.81
  const holdoutOut = { n_rows: holdout, model: best.model, eval_metric: metric, scores: metrics, metrics, classes: reg ? undefined : classes, confusion_matrix: cm }
  push({ type: 'evaluation', ...holdoutOut }, 300)
  push({ type: 'run_succeeded', best_model: best.model }, 200)

  const run: Run = {
    id: runId, name: `${ds.name} · ${ch.label}`, dataset_id: ds.id ?? ds.name, label: ch.label, status: 'succeeded', created_at: '', started_at: '', finished_at: '',
    error: null, config: { presets: ch.preset, time_limit: ch.budget }, predictor_path: '(simulated)', n_events: events.length, problem_type: pt,
    eval_metric: { name: metric, greater_is_better: greater, optimum: null, scores_note: '' }, classes: reg ? null : classes,
    positive_class: task === 'binary' ? classes[classes.length - 1] : null, best_model: best.model, fit_seconds: 20, elapsed_seconds: 22,
    data: { rows: n, train_rows: train, holdout_rows: holdout, dropped_missing_label: 0, dropped_missing_label_train: 0, dropped_missing_label_holdout: 0, dropped_columns: [], features, label: ch.label },
    features, ensemble: { model: 'WeightedEnsemble_L2', stack_level: 2, weights, best: true }, ensembles: null, holdout: holdoutOut,
    oof: { available: false, models: [], note: null }, text_models: null, feature_importance: null, leaderboard: lb, resolved: null, custom_ensembles: [],
  }
  return { events, delays, run }
}

/** An event source that plays a simulation with its own timing (the race's playback adds the readable minimum). */
export function mockSource(sim: Sim): EventSourceFn {
  return (_id, onEvent) => {
    let i = 0, alive = true
    let timer: ReturnType<typeof setTimeout>
    const next = () => {
      if (!alive || i >= sim.events.length) return
      const e = { ...sim.events[i], seq: i, ts: Date.now() / 1000 } as RunEvent
      i++
      onEvent(e)
      if (i < sim.events.length) timer = setTimeout(next, sim.delays[i])
    }
    timer = setTimeout(next, 0)
    return () => { alive = false; clearTimeout(timer) }
  }
}
