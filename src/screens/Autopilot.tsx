import { useEffect, useMemo, useRef, useState } from 'react'
import { VelaCrest } from '../components/icons'
import { useWidth } from '../components/Strings'
import { ParamChip, ResonanceWave, Segmented, StatusPill } from '../components/ui'
import { reducedMotion, usePrefs } from '../prefs'
import { SAMPLES, type Dataset } from './Workflow'

/* ------------------------------------------------------------------ the scripted run */

type Autonomy = 'Ask First' | 'Ground Truth Only' | 'Fully Autonomous'
type Brief = { f1: number; recall: number; latency: number; budget: number; synthetic: boolean; autonomy: Autonomy }
type Metrics = { f1: number; rec: number; lat: number }
type Diff = { removed: number; relabelled: number; synthetic: number }
type Evidence = { text: string; given: string; says: string; conf: number }
type Step = {
  id: string
  kind: 'step' | 'branch' | 'ask'
  short: string
  title: string
  finding: React.ReactNode
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
function buildRun(ds: Dataset, b: Brief, answers: Record<string, string>): { steps: Step[]; total: number } {
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
  let ver = 2 // v1 is the file as given; profiling writes v2

  keep({ id: 'profile', kind: 'step', short: 'Profile', title: 'Profiled The Data', ms: 2200, diff: { removed: dup },
    finding: <>{ds.rows.toLocaleString()} rows, <b>K = {ds.classes.length}</b>. Found <b>{dup.toLocaleString()}</b> exact duplicates and a class at <b>{(Math.min(...ds.classes.map((c) => c.share)) * 100).toFixed(1)}%</b> of rows ({weak}).</>,
    action: `drop_duplicates() · impute(most_frequent) · StratifiedKFold(k=5)` }, {})
  keep({ id: 'base', kind: 'step', short: 'Baseline', title: 'Fit A Baseline', ms: 2600,
    finding: <>A plain linear model sets the floor. {weak} recall is <b>0.79</b>, the weakest class by far.</>, action: 'TF-IDF → LogReg (OvR) · C=1.0' }, { f1: 0.893, rec: 0.79, lat: 2.9 })
  keep({ id: 'weight', kind: 'step', short: 'Rebalance', title: 'Rebalanced Classes', ms: 2200,
    finding: <>{weak} is drowned out by {other}. Weighting by inverse frequency lifts its recall <b>Δ +0.05</b> at no cost in fit time.</>, action: "class_weight=None → class_weight='balanced'" }, { f1: 0.901, rec: 0.84 })
  steps.push({ id: 'xgb', kind: 'branch', short: 'XGBoost', title: 'Tried XGBoost, Dropped It', ms: 3000, tried: { f1: 0.896, rec: 0.86, lat: 11.2 },
    finding: <>Boosted trees reach F1 <b>0.896</b>, below the linear model, and predict in <b>11.2 ms</b>, over your <b>{b.latency.toFixed(1)} ms</b> limit.</>, action: 'TF-IDF → SVD → XGBoost · M=600 · dropped' })
  keep({ id: 'tune', kind: 'step', short: 'Tune', title: 'Tuned Regularization', ms: 2600,
    finding: <>Swept <span className="math">C</span> ∈ [10⁻², 10¹] over 12 values. Validation peaks at <span className="math">C</span> = 2.0; past that the train–validation gap widens.</>, action: 'C=1.0 → C=2.0 · ngram=(1,2)' }, { f1: 0.908, rec: 0.85, lat: 3.2 })

  const a1 = ask({ id: 'gt-labels', kind: 'ask', gt: true, short: 'Labels?', title: `${flip} Labels Look Wrong`, ms: 1800,
    finding: <><b>{flip}</b> rows labelled <b>{near}</b> read like <b>{weak}</b>. Five folds agree at ≥ 0.90 confidence. I can tune around noise, but I should not decide what is true.</>,
    options: [{ id: 'relabel', label: `Relabel ${flip} As ${weak}` }, { id: 'keep', label: 'Keep Labels As Given' }],
    evidence: [
      { text: 'Got a login alert from a country I have never been to, please lock it', given: near, says: weak, conf: 0.97 },
      { text: '2FA codes stopped arriving after someone changed my recovery email', given: near, says: weak, conf: 0.95 },
      { text: 'Password reset link was requested 14 times overnight, not by me', given: near, says: weak, conf: 0.93 },
    ] }, 'relabel')
  if (a1 === null) return { steps, total }
  if (a1 === 'relabel') keep({ id: 'relabel', kind: 'step', short: 'Relabel', title: 'Applied Your Label Fix', ms: 2400, diff: { relabelled: flip },
    finding: <>Retrained on the corrected labels. {weak} recall <b>Δ +0.04</b>; the {near} classifier got cleaner too.</>, action: `${flip} rows: ${near} → ${weak} · dataset v${++ver}` }, { f1: 0.917, rec: 0.89 })

  if (b.synthetic) {
    const a2 = ask({ id: 'syn', kind: 'ask', short: 'Synthetic?', title: 'Generate Synthetic Examples?', ms: 1400,
      finding: <>{weak} still has too few real examples. I can write <b>{syn.toLocaleString()}</b> synthetic ones from its real rows. They go into training folds only, tagged, never into validation or holdout.</>,
      options: [{ id: 'yes', label: `Generate ${syn.toLocaleString()}` }, { id: 'no', label: 'Skip' }] }, 'yes')
    if (a2 === null) return { steps, total }
    if (a2 === 'yes') keep({ id: 'synth', kind: 'step', short: 'Synthesize', title: 'Added Synthetic Examples', ms: 2800, diff: { synthetic: syn },
      finding: <>Paraphrased and recombined real {weak} rows, then filtered <b>{Math.round(syn * 0.19).toLocaleString()}</b> that a held-out model could not tell from {near}. Scores below are on real rows only.</>, action: `+${syn.toLocaleString()} synthetic · train folds only · tagged source=vela · dataset v${++ver}` }, { f1: m.f1 + 0.004, rec: m.rec + 0.02 })
  }

  const a3 = ask({ id: 'gt-overlap', kind: 'ask', gt: true, short: 'Overlap?', title: 'Two Classes Share Texts', ms: 1800,
    finding: <><b>{amb}</b> near-identical rows carry two different labels, <b>{big[2]?.name ?? near}</b> and <b>{big[3]?.name ?? other}</b>. The model cannot be right on both. Which label is the truth?</>,
    options: [{ id: 'a', label: `They Are ${big[2]?.name ?? near}` }, { id: 'b', label: `They Are ${big[3]?.name ?? other}` }, { id: 'leave', label: 'Leave Both' }],
    evidence: [
      { text: 'Need to update the card on file for our team plan', given: big[2]?.name ?? near, says: 'either', conf: 0.51 },
      { text: 'need to update card on file for the team plan', given: big[3]?.name ?? other, says: 'either', conf: 0.52 },
    ] }, 'a')
  if (a3 === null) return { steps, total }
  if (a3 !== 'leave') keep({ id: 'merge', kind: 'step', short: 'Resolve', title: 'Resolved The Overlap', ms: 2200, diff: { relabelled: amb },
    finding: <>One label per text now. The two classifiers stop fighting over the same rows.</>, action: `${amb} rows unified · dataset v${++ver}` }, { f1: m.f1 + 0.005, rec: m.rec + 0.01 })

  const ok = m.f1 >= b.f1 && m.rec >= b.recall && m.lat <= b.latency
  keep({ id: 'done', kind: 'step', short: 'Holdout', title: ok ? 'All Requirements Met' : 'Stopped: A Requirement Is Not Met', ms: 2400,
    finding: ok
      ? <>Scored once on the untouched holdout: F1 <b>{m.f1.toFixed(3)}</b>, {weak} recall <b>{m.rec.toFixed(2)}</b>, <b>{m.lat.toFixed(1)} ms</b>. Ready to register.</>
      : <>Holdout F1 <b>{m.f1.toFixed(3)}</b>, {weak} recall <b>{m.rec.toFixed(2)}</b>. {m.rec < b.recall ? <>Recall is below your <b>{b.recall.toFixed(2)}</b>. More real {weak} rows or the label fix would close it.</> : <>F1 is below your <b>{b.f1.toFixed(2)}</b>.</>}</>,
    action: 'evaluate(holdout) · n = 15% of rows · used once' }, {})
  return { steps, total }
}

/* ------------------------------------------------------------------ navy visuals */

/** The run as one string. Kept steps are taut nodes, a dropped experiment hangs off as a dashed catenary,
 *  a question is a diamond, and the step in progress is a travelling wave. */
function IterationString({ steps, shown, working, total }: { steps: Step[]; shown: number; working: boolean; total: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const wave = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const pad = 52, y = 34
  const slots = Math.max(total, steps.length)
  // dropped branches do not take a slot on the main string
  let col = -1
  const pos = steps.map((s) => { if (s.kind !== 'branch') col++; return col })
  const cols = Math.max(slots - steps.filter((s) => s.kind === 'branch').length, col + 1, 2)
  const x = (c: number) => pad + (c / (cols - 1)) * (w - pad * 2)
  const lastKept = (() => { for (let i = Math.min(shown, steps.length) - 1; i >= 0; i--) if (steps[i].kind !== 'branch') return pos[i]; return 0 })()
  const next = steps[shown]
  useEffect(() => {
    const el = wave.current
    if (!el || !working || !next) return
    const a = x(lastKept), bx = next.kind === 'branch' ? x(lastKept) + (x(1) - x(0)) * 0.5 : x(pos[shown])
    const by = next.kind === 'branch' ? y + 44 : y
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      let d = ''
      for (let i = 0; i <= 50; i++) {
        const f = i / 50
        d += `${i ? 'L' : 'M'}${(a + f * (bx - a)).toFixed(1)},${(y + f * (by - y) + Math.sin(Math.PI * f) * 8 * Math.sin(f * 16 - (still ? 0 : now / 110))).toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [shown, working, w, prefs.motion, steps.length])
  return (
    <div ref={ref} className="pipe-string iter-string">
      <svg width={w} height={118} aria-hidden style={{ display: 'block' }}>
        <defs><linearGradient id="it-g" x1="0" x2="1"><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <line x1={x(0)} x2={x(cols - 1)} y1={y} y2={y} stroke="#334155" strokeWidth={1.5} strokeDasharray="4 5" />
        <line x1={x(0)} x2={x(lastKept)} y1={y} y2={y} stroke="#2DD4BF" strokeWidth={2} />
        {working && next && <path ref={wave} fill="none" stroke="url(#it-g)" strokeWidth={2.25} strokeLinecap="round" />}
        {steps.slice(0, shown).map((s, i) => {
          if (s.kind === 'branch') {
            const px = x(pos[i]), bx = px + (x(1) - x(0)) * 0.5
            return (
              <g key={s.id}>
                <path d={`M${px},${y} C${px + 40},${y + 2} ${px + 26},${y + 44} ${bx},${y + 44}`} fill="none" stroke="#64748B" strokeWidth={1.5} strokeDasharray="4 4" />
                <circle cx={bx} cy={y + 44} r={5} fill="#0A1120" stroke="#64748B" strokeWidth={1.5} />
                <text x={bx + 11} y={y + 42} className="ps-name" style={{ fill: '#94A3B8', fontWeight: 500 }}>{s.short}</text>
                <text x={bx + 11} y={y + 57} className="ps-note">dropped · {s.tried!.f1.toFixed(3)}</text>
              </g>
            )
          }
          const cx = x(pos[i])
          const isAsk = s.kind === 'ask', waiting = isAsk && !s.answer
          return (
            <g key={s.id}>
              {waiting && <circle cx={cx} cy={y} r={13} fill="#FBBF24" opacity={0.2} className="live-dot" />}
              {isAsk
                ? <rect x={cx - 6} y={y - 6} width={12} height={12} transform={`rotate(45 ${cx} ${y})`} fill={waiting ? '#FBBF24' : '#0A1120'} stroke={waiting ? '#FBBF24' : '#2DD4BF'} strokeWidth={2} />
                : <circle cx={cx} cy={y} r={6} fill="#2DD4BF" stroke="#2DD4BF" strokeWidth={2} />}
              <text x={cx} y={y - 16} textAnchor="middle" className="ps-name" style={{ fill: '#F8FAFC' }}>{s.short}</text>
              <text x={cx} y={y + 24} textAnchor="middle" className="ps-note">{isAsk ? (waiting ? 'needs you' : s.auto ? 'Vela decided' : 'you answered') : s.after!.f1 ? s.after!.f1.toFixed(3) : 'data'}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/** A requirement as a fret track: the tick is your target, the node is where the model is now. */
function Gauge({ label, sym, value, target, min, max, lower, fmt }: { label: string; sym: string; value: number; target: number; min: number; max: number; lower?: boolean; fmt: (v: number) => string }) {
  const t = (v: number) => Math.min(1, Math.max(0, (v - min) / (max - min))) * 100
  const has = value > 0
  const met = has && (lower ? value <= target : value >= target)
  return (
    <div className="gauge">
      <div className="gauge-top"><span>{label}</span><b>{has ? fmt(value) : '—'}</b></div>
      <div className="gauge-track">
        <i className="fill" style={{ width: has ? `${t(value)}%` : 0, background: met ? '#2DD4BF' : '#38BDF8' }} />
        {Array.from({ length: 11 }, (_, i) => <i key={i} className="fret" style={{ left: `${i * 10}%` }} />)}
        <i className="target" style={{ left: `${t(target)}%` }} />
        {has && <i className="node" style={{ left: `${t(value)}%`, borderColor: met ? '#2DD4BF' : '#38BDF8' }} />}
      </div>
      <div className="gauge-bot"><span className="ps-note" style={{ fontFamily: 'var(--font-math)', fontSize: 13 }}><i>{sym}</i> {lower ? '≤' : '≥'} {fmt(target)}</span><span className={met ? 'g-met' : 'g-open'}>{!has ? 'no model yet' : met ? '● met' : '○ not yet'}</span></div>
    </div>
  )
}

/* ------------------------------------------------------------------ screen */

export function Autopilot({ go }: { go: (r: string) => void }) {
  const [ds, setDs] = useState<Dataset>(SAMPLES[0])
  const [goal, setGoal] = useState('Route each support ticket to the right team. Security tickets must not be missed. It has to answer fast enough to run on every incoming ticket.')
  const [brief, setBrief] = useState<Brief>({ f1: 0.9, recall: 0.9, latency: 5, budget: 8, synthetic: true, autonomy: 'Ground Truth Only' })
  const [phase, setPhase] = useState<'brief' | 'planning' | 'plan' | 'run'>('brief')
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [shown, setShown] = useState(0)
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState(1)
  const { steps, total } = useMemo(() => buildRun(ds, brief, answers), [ds, brief, answers])
  const weak = [...ds.classes].sort((a, c) => a.share - c.share)[0].name

  const next = steps[shown]
  const last = steps[shown - 1]
  const blocked = last?.kind === 'ask' && !last.answer
  const finished = phase === 'run' && shown >= steps.length && steps[steps.length - 1]?.id === 'done'
  const working = phase === 'run' && !blocked && !finished && !paused && !!next

  useEffect(() => {
    if (!working) return
    const tm = setTimeout(() => setShown((n) => n + 1), next.ms / speed)
    return () => clearTimeout(tm)
  }, [working, shown, speed, next?.id])
  useEffect(() => { if (phase !== 'planning') return; const tm = setTimeout(() => setPhase('plan'), 1300); return () => clearTimeout(tm) }, [phase])

  const done = steps.slice(0, shown)
  const kept = done.filter((s) => s.after)
  const m: Metrics = kept.length ? kept[kept.length - 1].after! : { f1: 0, rec: 0, lat: 0 }
  const diff = done.reduce<Diff>((a, s) => ({ removed: a.removed + (s.diff?.removed ?? 0), relabelled: a.relabelled + (s.diff?.relabelled ?? 0), synthetic: a.synthetic + (s.diff?.synthetic ?? 0) }), { removed: 0, relabelled: 0, synthetic: 0 })
  const version = 1 + done.filter((s) => s.diff).length
  const iterations = done.filter((s) => s.kind !== 'ask' && s.id !== 'profile' && s.id !== 'done').length
  const asked = done.filter((s) => s.kind === 'ask' && !s.auto).length
  const met = m.f1 >= brief.f1 && m.rec >= brief.recall && m.lat > 0 && m.lat <= brief.latency
  const start = () => { setAnswers({}); setShown(0); setPaused(false); setPhase('run') }
  const num = (k: 'f1' | 'recall' | 'latency' | 'budget', step: number, lo: number, hi: number) => (
    <input className="num-input" type="number" step={step} min={lo} max={hi} value={brief[k]} onChange={(e) => setBrief({ ...brief, [k]: Math.min(hi, Math.max(lo, Number(e.target.value))) })} aria-label={k} />
  )

  return (
    <div className="gym">
      <div className="stepper-bar" style={{ paddingBottom: 16 }}>
        <div className="stepper-title" style={{ marginBottom: 0 }}>
          <VelaCrest size={26} />
          <h1 className="page-title">Vela Autopilot</h1>
          <span className="helper">It trains, tunes and fixes the data, and only stops to ask you what is true.</span>
          <span className="spacer" />
          <Segmented options={['Manual', 'Autopilot']} value="Autopilot" onChange={(v) => v === 'Manual' && go('gym')} />
        </div>
      </div>

      <div className="gym-scroll">
        {phase !== 'run' ? (
          <div className="page">
            <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', alignItems: 'start' }}>
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h2 className="card-title">The Brief</h2><span className="micro-label">What you want</span></div>
                  <div className="card-body stack" style={{ gap: 12, padding: 20 }}>
                    <label className="micro-label" htmlFor="goal">Goal, in your words</label>
                    <textarea id="goal" className="goal" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
                    <span className="micro-label">Dataset</span>
                    <div className="ds-pick">
                      {SAMPLES.map((s) => (
                        <button key={s.name} className={`filter ds ${ds.name === s.name ? 'on' : ''}`} onClick={() => { setDs(s); setPhase('brief') }}>
                          <b className="mono">{s.name}</b><span>{s.rows.toLocaleString()} rows · K = {s.classes.length}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="card">
                  <div className="card-head"><h2 className="card-title">Requirements</h2><span className="micro-label">Vela stops when all are met</span></div>
                  <div className="card-body" style={{ padding: '4px 20px' }}>
                    <div className="set-row"><div><b>Macro F1 <span className="math">F1</span></b><p className="helper">Average quality across all {ds.classes.length} classes.</p></div><div className="row"><span className="math">≥</span>{num('f1', 0.01, 0.5, 0.99)}</div></div>
                    <div className="set-row"><div><b>Weakest-Class Recall <span className="math">r</span></b><p className="helper">Share of real <span className="mono">{weak}</span> rows the model catches.</p></div><div className="row"><span className="math">≥</span>{num('recall', 0.01, 0.5, 0.99)}</div></div>
                    <div className="set-row"><div><b>Latency <span className="math">τ</span></b><p className="helper">Time to predict one row, in ms. Rules out slow models.</p></div><div className="row"><span className="math">≤</span>{num('latency', 0.5, 1, 50)}</div></div>
                    <div className="set-row"><div><b>Budget <span className="math">B</span></b><p className="helper">Most training iterations before it stops and reports.</p></div><div className="row"><span className="math">≤</span>{num('budget', 1, 3, 20)}</div></div>
                  </div>
                </div>
              </div>
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h2 className="card-title">How Much To Ask</h2></div>
                  <div className="card-body" style={{ padding: '4px 20px' }}>
                    <div className="set-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
                      <Segmented options={['Ask First', 'Ground Truth Only', 'Fully Autonomous']} value={brief.autonomy} onChange={(v) => setBrief({ ...brief, autonomy: v })} />
                      <p className="helper" style={{ maxWidth: 'none' }}>{{
                        'Ask First': 'Vela asks before changing labels and before generating synthetic data.',
                        'Ground Truth Only': 'Vela decides everything about models and cleaning. It stops only when the labels themselves are in question.',
                        'Fully Autonomous': 'Vela takes its own best answer to every question and logs it for you to review afterwards.',
                      }[brief.autonomy]}</p>
                    </div>
                    <div className="set-row"><div><b>Synthetic Data</b><p className="helper">Let Vela write examples for starved classes. Training folds only, always tagged.</p></div><Segmented options={['Allowed', 'Off']} value={brief.synthetic ? 'Allowed' : 'Off'} onChange={(v) => setBrief({ ...brief, synthetic: v === 'Allowed' })} /></div>
                  </div>
                </div>
                <div className="card navy">
                  <div className="card-head"><div className="left"><VelaCrest size={18} /><h2 className="card-title">Vela's Plan</h2></div>{phase === 'plan' && <span className="micro-label">Read before it starts</span>}</div>
                  <div className="card-body plan">
                    {phase === 'brief' && <p>Vela reads the data and writes a plan before it touches anything.</p>}
                    {phase === 'planning' && <div className="thinking" style={{ color: 'var(--text-on-shell-muted)' }}><ResonanceWave width={84} height={18} fast color="#38BDF8" /> Reading {ds.name}</div>}
                    {phase === 'plan' && (
                      <ol>
                        <li><b>Profile and clean.</b> Dedupe, impute, hold out 15% that nothing else touches.</li>
                        <li><b>Baseline.</b> TF-IDF → LogReg (OvR). Text with K = {ds.classes.length} favours a sparse linear model.</li>
                        <li><b>Fix the weak class.</b> {weak} is {(Math.min(...ds.classes.map((c) => c.share)) * 100).toFixed(1)}% of rows: reweight{brief.synthetic ? ', then synthesize if still short' : ''}.</li>
                        <li><b>Challenge it.</b> Try XGBoost; keep it only if it wins inside {brief.latency} ms.</li>
                        <li><b>Audit the labels.</b> Rows where confident folds disagree with the label come to you.</li>
                        <li><b>Score once on holdout</b> and stop at F1 ≥ {brief.f1.toFixed(2)}, recall ≥ {brief.recall.toFixed(2)}, or after {brief.budget} iterations.</li>
                      </ol>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="page">
            <section className="stage">
              <div className="stage-head">
                <div>
                  <span className="hero-eyebrow" style={{ paddingLeft: 0, color: blocked ? '#FBBF24' : undefined }}>{finished ? (met ? 'DONE · REQUIREMENTS MET' : 'STOPPED · NEEDS A DECISION') : blocked ? 'WAITING ON YOU' : paused ? 'PAUSED' : 'VELA IS WORKING'}</span>
                  <h2 className="hero-title">{finished ? last!.title : blocked ? last!.title : next ? `${next.kind === 'ask' ? 'Checking' : 'Working on'}: ${next.short}` : ''}</h2>
                </div>
                <div className="stage-read">
                  <span><small>iterations</small><b>{iterations} / {brief.budget}</b></span>
                  <span><small>asked you</small><b>{asked}</b></span>
                  <span><small>dataset</small><b>v{version}</b></span>
                  <span className="stage-sim" title="This run is scripted in your browser. No model is fitted and no data is changed.">simulated run</span>
                </div>
              </div>
              <IterationString steps={steps} shown={shown} working={working} total={total} />
              <div className="gauges">
                <Gauge label="Macro F1" sym="F1" value={m.f1} target={brief.f1} min={0.8} max={1} fmt={(v) => v.toFixed(3)} />
                <Gauge label={`${weak} Recall`} sym="r" value={m.rec} target={brief.recall} min={0.7} max={1} fmt={(v) => v.toFixed(2)} />
                <Gauge label="Latency" sym="τ" value={m.lat} target={brief.latency} min={0} max={Math.max(12, brief.latency * 1.5)} lower fmt={(v) => `${v.toFixed(1)} ms`} />
              </div>
              <div className="stage-foot">
                <div className="stage-prog"><i style={{ width: `${(shown / Math.max(steps.length, total)) * 100}%` }} /></div>
                {finished ? <button className="btn sm" onClick={start}>Run Again</button> : <button className="btn sm" disabled={blocked} onClick={() => setPaused(!paused)}>{paused ? 'Resume' : 'Pause'}</button>}
                <div className="seg stage-seg">{[1, 3].map((s) => <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{s}×</button>)}</div>
              </div>
            </section>

            <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)', alignItems: 'start' }}>
              <div className="card">
                <div className="card-head"><div className="left"><h3 className="card-title">Decision Log</h3><span className="helper" style={{ fontSize: 12 }}>everything Vela did, newest first</span></div><ParamChip sym="n" value={String(done.length)} /></div>
                <div className="log-cards">
                  {working && next && <div className="thinking" style={{ padding: '4px 4px 0' }}><ResonanceWave width={84} height={18} fast /> {next.kind === 'branch' ? 'Trying' : next.kind === 'ask' ? 'Checking' : 'Working on'} {next.short.toLowerCase()}</div>}
                  {[...done].reverse().map((s) => {
                    const prev = kept[kept.indexOf(s) - 1]?.after
                    return (
                      <div key={s.id} className={`patch ${s.kind === 'branch' ? 'applied' : ''}`}>
                        <div className="ph">
                          <VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>{s.title}</b><span className="spacer" />
                          {s.kind === 'branch' ? <StatusPill status="neutral">Dropped</StatusPill> : s.kind === 'ask' ? (s.answer ? <StatusPill status={s.auto ? 'neutral' : 'success'}>{s.auto ? 'Vela Decided' : 'You Answered'}</StatusPill> : <StatusPill status="warning">Needs You</StatusPill>) : <StatusPill status="success">Kept</StatusPill>}
                        </div>
                        <p>{s.finding}</p>
                        {s.action && <div className="diffline">{s.action}</div>}
                        {s.kind === 'ask' && s.answer && <div className="diffline">{s.auto ? 'Vela' : 'You'}: {s.options!.find((o) => o.id === s.answer)!.label}</div>}
                        {s.after && prev && prev.f1 > 0 && (s.after.f1 !== prev.f1 || s.after.rec !== prev.rec) && (
                          <div className="row"><span className="chip">F1 Δ {(s.after.f1 - prev.f1 >= 0 ? '+' : '') + (s.after.f1 - prev.f1).toFixed(3)}</span><span className="chip">{weak} recall Δ {(s.after.rec - prev.rec >= 0 ? '+' : '') + (s.after.rec - prev.rec).toFixed(2)}</span></div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="stack">
                <div className={`card inbox ${blocked ? 'open' : ''}`}>
                  <div className="card-head"><div className="left"><h3 className="card-title">Ground-Truth Inbox</h3></div>{blocked ? <StatusPill status="warning">1 Needs You</StatusPill> : <StatusPill status="neutral">Empty</StatusPill>}</div>
                  {blocked ? (
                    <div className="card-body stack" style={{ gap: 12 }}>
                      <p className="helper" style={{ color: 'var(--text-primary)' }}>{last!.finding}</p>
                      {last!.evidence && (
                        <div className="table-wrap well"><table className="table">
                          <thead><tr><th>Text</th><th>Label</th><th>Model</th><th className="r">Conf.</th></tr></thead>
                          <tbody>{last!.evidence.map((e, i) => <tr key={i}><td style={{ whiteSpace: 'normal', lineHeight: '17px', padding: '6px 12px' }}>{e.text}</td><td>{e.given}</td><td>{e.says}</td><td className="r table-cell">{e.conf.toFixed(2)}</td></tr>)}</tbody>
                        </table></div>
                      )}
                      <div className="row" style={{ flexWrap: 'wrap' }}>
                        {last!.options!.map((o, i) => <button key={o.id} className={`btn ${i === 0 ? 'primary' : ''}`} onClick={() => setAnswers({ ...answers, [last!.id]: o.id })}>{o.label}</button>)}
                      </div>
                      <span className="helper" style={{ fontSize: 12 }}>Vela's suggestion is first. Training is paused until you answer.</span>
                    </div>
                  ) : (
                    <div className="card-body"><p className="helper">{finished ? `Vela asked you ${asked} time${asked === 1 ? '' : 's'} in this run.` : 'Nothing needs you. Vela stops here only when the labels themselves are in question.'}</p></div>
                  )}
                </div>

                <div className="card">
                  <div className="card-head"><div className="left"><h3 className="card-title">Data Changes</h3></div><span className="chip">v1 → v{version}</span></div>
                  <div className="card-body">
                    {([['Duplicates removed', diff.removed, 'var(--text-muted)', undefined], ['Rows relabelled', diff.relabelled, 'var(--accent-primary)', undefined], ['Synthetic rows added', diff.synthetic, 'var(--accent-secondary)', '5 4']] as [string, number, string, string | undefined][]).map(([l, v, c, dash]) => (
                      <div key={l} className="dist-row" style={{ gridTemplateColumns: '150px 1fr 60px' }}>
                        <span>{l}</span>
                        <svg width="100%" height="10" aria-hidden><line x1="0" x2="100%" y1="5" y2="5" stroke="var(--border-default)" strokeWidth="1.5" /><line x1="0" x2={`${Math.min(100, (v / (ds.rows * 0.016)) * 100)}%`} y1="5" y2="5" stroke={c} strokeWidth="2" strokeDasharray={dash} style={{ transition: 'all var(--duration-snap-max) var(--ease-pluck)' }} />{v > 0 && <circle cx={`${Math.min(100, (v / (ds.rows * 0.016)) * 100)}%`} cy="5" r="3" fill={c} />}</svg>
                        <span className="table-cell">{v.toLocaleString()}</span>
                      </div>
                    ))}
                    <p className="helper" style={{ marginTop: 8, fontSize: 12 }}>Synthetic rows are tagged <span className="mono">source=vela</span> and never enter validation or holdout.</p>
                  </div>
                </div>

                {finished && (
                  <div className="card navy">
                    <div className="card-head"><h3 className="card-title">Result</h3>{met ? <StatusPill status="success">Ready To Register</StatusPill> : <span className="micro-label">Not Yet</span>}</div>
                    <div className="minis">
                      <div className="mini"><span className="micro-label">Macro F1</span><span className="v">{m.f1.toFixed(3)}</span><span className="u">holdout</span></div>
                      <div className="mini"><span className="micro-label">{weak} Recall</span><span className="v">{m.rec.toFixed(2)}</span><span className="u">target {brief.recall.toFixed(2)}</span></div>
                      <div className="mini"><span className="micro-label">Latency</span><span className="v">{m.lat.toFixed(1)} ms</span><span className="u">limit {brief.latency} ms</span></div>
                    </div>
                    <div className="card-body row" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap' }}>
                      {met ? <button className="btn primary" onClick={() => go('registry')}>Register Model Version</button> : <button className="btn primary" onClick={start}>Run Again</button>}
                      <button className="btn" onClick={() => go('gym')}>Open In Gym →</button>
                      <button className="btn ghost" onClick={() => setPhase('brief')}>Edit Brief</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {phase !== 'run' && (
        <div className="gym-footer">
          <div />
          <div className="valid"><span className="dot">●</span> {ds.name} · {brief.autonomy}</div>
          <div className="right">
            {phase === 'plan' ? <><button className="btn" onClick={() => setPhase('brief')}>Edit Brief</button><button className="btn primary" onClick={start}>Start Autopilot</button></>
              : <button className="btn primary" disabled={phase === 'planning'} onClick={() => setPhase('planning')}>Draft Plan →</button>}
          </div>
        </div>
      )}
    </div>
  )
}
