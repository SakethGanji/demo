import type { ReactNode } from 'react'
import { SAMPLES, type Dataset } from '@/features/quick-train/datasets'

/** The script is written for text classification, so only the text samples are offered. */
export const TEXT_SAMPLES = SAMPLES.filter((d) => d.kind === 'text')

export type Autonomy = 'Ask First' | 'Ground Truth Only' | 'Fully Autonomous'

export type Brief = { f1: number; recall: number; latency: number; budget: number; synthetic: boolean; autonomy: Autonomy }

export type Metrics = { f1: number; rec: number; lat: number }

export type Diff = { removed: number; relabelled: number; synthetic: number }

export type Evidence = { text: string; given: string; says: string; conf: number }

export type Step = {
  id: string
  kind: 'step' | 'branch' | 'ask'
  short: string
  title: string
  finding: ReactNode
  action?: string
  after?: Metrics            // metrics once this step is kept
  tried?: Metrics            // metrics of a dropped branch
  diff?: Partial<Diff>
  ms: number
  // ask only
  gt?: boolean               // a ground-truth question (asked unless Fully Autonomous)
  options?: { id: string; label: string }[]
  evidence?: Evidence[]
  answer?: string
  auto?: boolean             // answered by Vela, not by you
}

/** Resolve the run as far as the answers allow. An ask with no answer ends the list. */
export function buildRun(ds: Dataset, b: Brief, answers: Record<string, string>): { steps: Step[]; total: number } {
  const weak = [...ds.classes].sort((a, c) => a.share - c.share)[0].name
  const big = [...ds.classes].sort((a, c) => c.share - a.share)
  const near = big[1]?.name ?? big[0].name, other = big[0].name
  const dup = Math.round(ds.rows * 0.0154), flip = Math.round(ds.rows * 0.0033), amb = Math.round(ds.rows * 0.0022), syn = Math.round(ds.rows * 0.0145)
  const steps: Step[] = []
  let m: Metrics = { f1: 0, rec: 0, lat: 0 }
  const keep = (s: Omit<Step, 'after'>, d: Partial<Metrics>) => { m = { ...m, ...d }; steps.push({ ...s, after: { ...m } }) }
  const ask = (s: Step, def: string): string | null => {
    const mustAsk = b.autonomy === 'Ask First' || (s.gt && b.autonomy === 'Ground Truth Only')
    if (!mustAsk) { steps.push({ ...s, answer: def, auto: true }); return def }
    steps.push({ ...s, answer: answers[s.id] })
    return answers[s.id] ?? null
  }
  const total = 9
  // training iterations are counted against the budget; once it is spent Vela goes straight to the holdout
  let spent = 0, cut = false
  const left = () => { if (spent < b.budget) return true; cut = true; return false }
  let ver = 2 // v1 is the file as given; profiling writes v2

  keep({ id: 'profile', kind: 'step', short: 'Profile', title: 'Profiled The Data', ms: 2200, diff: { removed: dup },
    finding: <>{ds.rows.toLocaleString()} rows, <b>K = {ds.classes.length}</b>. Found <b>{dup.toLocaleString()}</b> exact duplicates and a class at <b>{(Math.min(...ds.classes.map((c) => c.share)) * 100).toFixed(1)}%</b> of rows ({weak}).</>,
    action: `drop_duplicates() · impute(most_frequent) · StratifiedKFold(k=5)` }, {})
  if (left()) { spent++; keep({ id: 'base', kind: 'step', short: 'Baseline', title: 'Fit A Baseline', ms: 2600,
    finding: <>A plain linear model sets the floor. {weak} recall is <b>0.79</b>, the weakest class by far.</>, action: 'TF-IDF → LogReg (OvR) · C=1.0' }, { f1: 0.893, rec: 0.79, lat: 2.9 }) }
  if (left()) { spent++; keep({ id: 'weight', kind: 'step', short: 'Rebalance', title: 'Rebalanced Classes', ms: 2200,
    finding: <>{weak} is drowned out by {other}. Weighting by inverse frequency lifts its recall <b>Δ +0.05</b> at no cost in fit time.</>, action: "class_weight=None → class_weight='balanced'" }, { f1: 0.901, rec: 0.84 }) }
  if (left()) { spent++; steps.push({ id: 'xgb', kind: 'branch', short: 'XGBoost', title: 'Tried XGBoost, Dropped It', ms: 3000, tried: { f1: 0.896, rec: 0.86, lat: 11.2 },
    finding: <>Boosted trees reach F1 <b>0.896</b>, below the linear model, {b.latency < 11.2 ? <>and predict in <b>11.2 ms</b>, over your <b>{b.latency.toFixed(1)} ms</b> limit.</> : <>at <b>8×</b> the fit time. Not worth it.</>}</>, action: 'TF-IDF → SVD → XGBoost · M=600 · dropped' }) }
  if (left()) { spent++; keep({ id: 'tune', kind: 'step', short: 'Tune', title: 'Tuned Regularization', ms: 2600,
    finding: <>Swept <span className="math">C</span> ∈ [10⁻², 10¹] over 12 values. Validation peaks at <span className="math">C</span> = 2.0; past that the train–validation gap widens.</>, action: 'C=1.0 → C=2.0 · ngram=(1,2)' }, { f1: 0.908, rec: 0.85, lat: 3.2 }) }

  const a1 = !left() ? 'skip' : ask({ id: 'gt-labels', kind: 'ask', gt: true, short: 'Labels?', title: `${flip} Labels Look Wrong`, ms: 1800,
    finding: <><b>{flip}</b> rows labelled <b>{near}</b> read like <b>{weak}</b>. Five folds agree at ≥ 0.90 confidence. I can tune around noise, but I should not decide what is true.</>,
    options: [{ id: 'relabel', label: `Relabel ${flip} As ${weak}` }, { id: 'keep', label: 'Keep Labels As Given' }],
    evidence: [
      { text: 'Got a login alert from a country I have never been to, please lock it', given: near, says: weak, conf: 0.97 },
      { text: '2FA codes stopped arriving after someone changed my recovery email', given: near, says: weak, conf: 0.95 },
      { text: 'Password reset link was requested 14 times overnight, not by me', given: near, says: weak, conf: 0.93 },
    ] }, 'relabel')
  if (a1 === null) return { steps, total }
  if (a1 === 'relabel') { spent++; keep({ id: 'relabel', kind: 'step', short: 'Relabel', title: 'Applied Your Label Fix', ms: 2400, diff: { relabelled: flip },
    finding: <>Retrained on the corrected labels. {weak} recall <b>Δ +0.04</b>; the {near} classifier got cleaner too.</>, action: `${flip} rows: ${near} → ${weak} · dataset v${++ver}` }, { f1: 0.917, rec: 0.89 }) }

  if (b.synthetic && left()) {
    const a2 = ask({ id: 'syn', kind: 'ask', short: 'Synthetic?', title: 'Generate Synthetic Examples?', ms: 1400,
      finding: <>{weak} still has too few real examples. I can write <b>{syn.toLocaleString()}</b> synthetic ones from its real rows. They go into training folds only, tagged, never into validation or holdout.</>,
      options: [{ id: 'yes', label: `Generate ${syn.toLocaleString()}` }, { id: 'no', label: 'Skip' }] }, 'yes')
    if (a2 === null) return { steps, total }
    if (a2 === 'yes') { spent++; keep({ id: 'synth', kind: 'step', short: 'Synthesize', title: 'Added Synthetic Examples', ms: 2800, diff: { synthetic: syn },
      finding: <>Paraphrased and recombined real {weak} rows, then filtered <b>{Math.round(syn * 0.19).toLocaleString()}</b> that a held-out model could not tell from {near}. Scores below are on real rows only.</>, action: `+${syn.toLocaleString()} synthetic · train folds only · tagged source=vela · dataset v${++ver}` }, { f1: m.f1 + 0.004, rec: m.rec + 0.02 }) }
  }

  const a3 = !left() ? 'skip' : ask({ id: 'gt-overlap', kind: 'ask', gt: true, short: 'Overlap?', title: 'Two Classes Share Texts', ms: 1800,
    finding: <><b>{amb}</b> near-identical rows carry two different labels, <b>{big[2]?.name ?? near}</b> and <b>{big[3]?.name ?? other}</b>. The model cannot be right on both. Which label is the truth?</>,
    options: [{ id: 'a', label: `They Are ${big[2]?.name ?? near}` }, { id: 'b', label: `They Are ${big[3]?.name ?? other}` }, { id: 'leave', label: 'Leave Both' }],
    evidence: [
      { text: 'Need to update the card on file for our team plan', given: big[2]?.name ?? near, says: 'either', conf: 0.51 },
      { text: 'need to update card on file for the team plan', given: big[3]?.name ?? other, says: 'either', conf: 0.52 },
    ] }, 'a')
  if (a3 === null) return { steps, total }
  if (a3 === 'a' || a3 === 'b') { spent++; keep({ id: 'merge', kind: 'step', short: 'Resolve', title: 'Resolved The Overlap', ms: 2200, diff: { relabelled: amb },
    finding: <>One label per text now. The two classifiers stop fighting over the same rows.</>, action: `${amb} rows unified · dataset v${++ver}` }, { f1: m.f1 + 0.005, rec: m.rec + 0.01 }) }

  const fails = [
    m.f1 < b.f1 && `F1 ${m.f1.toFixed(3)} is below your ${b.f1.toFixed(2)}`,
    m.rec < b.recall && `${weak} recall ${m.rec.toFixed(2)} is below your ${b.recall.toFixed(2)}`,
    m.lat > b.latency && `latency ${m.lat.toFixed(1)} ms is over your ${b.latency.toFixed(1)} ms`,
  ].filter(Boolean) as string[]
  const ok = fails.length === 0
  const why = cut ? `The budget of ${b.budget} iterations ran out before I could try more. Raise it and run again.`
    : m.lat > b.latency ? 'No model I tried predicts that fast. Relax the latency limit or accept a smaller vocabulary.'
    : `More real ${weak} rows, or accepting the label fixes, would close it.`
  keep({ id: 'done', kind: 'step', short: 'Holdout', title: ok ? 'All Requirements Met' : cut ? 'Stopped: Budget Reached' : 'Stopped: A Requirement Is Not Met', ms: 2400,
    finding: ok
      ? <>Scored once on the untouched holdout: F1 <b>{m.f1.toFixed(3)}</b>, {weak} recall <b>{m.rec.toFixed(2)}</b>, <b>{m.lat.toFixed(1)} ms</b>. Ready to register.</>
      : <>Scored once on the untouched holdout: <b>{fails.join('; ')}</b>. {why}</>,
    action: 'evaluate(holdout) · n = 15% of rows · used once' }, {})
  return { steps, total }
}
