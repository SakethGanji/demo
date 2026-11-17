import { useEffect, useMemo, useRef, useState } from 'react'
import { ConfusionMatrix, LineChart, PlotFrame, type Series } from '../components/charts'
import { VelaCrest } from '../components/icons'
import { RunString, useWidth, type RunState } from '../components/Strings'
import { ParamChip, StatusPill } from '../components/ui'
import { rng } from '../data'
import { reducedMotion, usePrefs } from '../prefs'

/* ------------------------------------------------------------------ data */

type Col = { name: string; type: 'text' | 'category' | 'number' | 'id'; sample: string }
export type Dataset = { name: string; rows: number; cols: Col[]; target: string; classes: { name: string; share: number }[]; kind: 'text' | 'tabular'; note: string }

export const SAMPLES: Dataset[] = [
  {
    name: 'zendesk_tickets_2026_q3.parquet', rows: 124500, kind: 'text', target: 'category', note: 'Support tickets · Text + Metadata',
    cols: [
      { name: 'ticket_id', type: 'id', sample: 'ZD-481920' }, { name: 'ticket_body', type: 'text', sample: 'I was charged twice for my March invoice and…' },
      { name: 'subject', type: 'text', sample: 'Double charge on invoice' }, { name: 'priority', type: 'category', sample: 'high' },
      { name: 'channel', type: 'category', sample: 'email' }, { name: 'created_at', type: 'number', sample: '2026-08-14T09:21' }, { name: 'category', type: 'category', sample: 'Billing' },
    ],
    classes: [['Billing', 0.175], ['Technical', 0.215], ['Account', 0.139], ['Security', 0.072], ['Shipping', 0.101], ['Other', 0.297]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
  {
    name: 'product_reviews_2026.csv', rows: 58200, kind: 'text', target: 'sentiment', note: 'Product reviews · Text',
    cols: [
      { name: 'review_id', type: 'id', sample: 'R-99214' }, { name: 'review_text', type: 'text', sample: 'Battery lasts two days, but the strap broke in…' },
      { name: 'stars', type: 'number', sample: '3' }, { name: 'product_line', type: 'category', sample: 'wearables' }, { name: 'sentiment', type: 'category', sample: 'Mixed' },
    ],
    classes: [['Positive', 0.58], ['Mixed', 0.17], ['Negative', 0.25]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
  {
    name: 'account_churn_2026.csv', rows: 41870, kind: 'tabular', target: 'churned', note: 'Accounts · Numeric + Categorical',
    cols: [
      { name: 'account_id', type: 'id', sample: 'A-20481' }, { name: 'seats', type: 'number', sample: '42' }, { name: 'mrr_usd', type: 'number', sample: '3,180.00' },
      { name: 'tickets_90d', type: 'number', sample: '7' }, { name: 'plan', type: 'category', sample: 'business' }, { name: 'region', type: 'category', sample: 'emea' },
      { name: 'last_login_days', type: 'number', sample: '19' }, { name: 'churned', type: 'category', sample: 'No' },
    ],
    classes: [['No', 0.86], ['Yes', 0.14]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
]

type Recipe = { id: 'ovr' | 'xgb' | 'svm'; name: string; pipeline: (d: Dataset) => string; stages: (d: Dataset) => string[]; eq: React.ReactNode; where: string; f1: number; secs: number; viz: 'strings' | 'boost'; best: 'text' | 'tabular'; chips: [string, string][] }
const RECIPES: Recipe[] = [
  {
    id: 'ovr', name: 'TF-IDF → LogReg (OvR)', viz: 'strings', best: 'text', f1: 0.914, secs: 102, chips: [['C', '1.0'], ['ngram', '(1,2)']],
    pipeline: () => 'TF-IDF → LogReg (OvR)', stages: () => ['Load', 'Split', 'TF-IDF', 'LogReg × K', 'Evaluate'],
    eq: <><i>ŷ</i> = argmax<sub><i>k</i></sub> σ(<i>w</i><sub><i>k</i></sub><sup>⊤</sup><i>x</i> + <i>b</i><sub><i>k</i></sub>)</>, where: 'One linear classifier per class; each one separates its class from the rest.',
  },
  {
    id: 'xgb', name: 'XGBoost', viz: 'boost', best: 'tabular', f1: 0.902, secs: 845, chips: [['M', '600'], ['η', '0.05']],
    pipeline: (d) => (d.kind === 'text' ? 'TF-IDF → SVD → XGBoost' : 'Encode → XGBoost'), stages: (d) => ['Load', 'Split', d.kind === 'text' ? 'TF-IDF → SVD' : 'Encode', 'Boost × M', 'Evaluate'],
    eq: <><i>F</i><sub><i>M</i></sub>(<i>x</i>) = Σ<sub><i>m</i>=1</sub><sup><i>M</i></sup> <i>η</i> <i>h</i><sub><i>m</i></sub>(<i>x</i>)</>, where: 'M small trees added one at a time; each one fits what the trees before it got wrong.',
  },
  {
    id: 'svm', name: 'TF-IDF → LinearSVC', viz: 'strings', best: 'text', f1: 0.907, secs: 138, chips: [['C', '0.5'], ['ngram', '(1,3)']],
    pipeline: () => 'TF-IDF → LinearSVC', stages: () => ['Load', 'Split', 'TF-IDF', 'SVM × K', 'Evaluate'],
    eq: <>min ½‖<i>w</i>‖² + <i>C</i> Σ max(0, 1 − <i>y</i><i>w</i><sup>⊤</sup><i>x</i>)</>, where: 'One max-margin classifier per class. Fast, strong on sparse text.',
  },
]

/* Phase boundaries on the run's progress t ∈ [0, 1]: load, split, vectorize, fit (5 folds), evaluate. */
const PH = [0, 0.06, 0.12, 0.28, 0.9, 1]
const FOLDS = 5
const phaseOf = (t: number) => (t >= 1 ? 5 : PH.findIndex((_, i) => t < PH[i + 1]))
const fitFrac = (t: number) => Math.min(1, Math.max(0, (t - PH[3]) / (PH[4] - PH[3])))
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}m ${String(Math.round(s % 60)).padStart(2, '0')}s`

async function profileCsv(file: File): Promise<Dataset> {
  const text = await file.slice(0, 256_000).text()
  const split = (l: string) => l.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map((c) => c.replace(/,$/, '').replace(/^"|"$/g, '').trim()).slice(0, -1) ?? []
  const lines = text.split(/\r?\n/).filter(Boolean)
  if (lines.length < 3) throw new Error('This file has fewer than two data rows.')
  const head = split(lines[0]); const rows = lines.slice(1, 400).map(split)
  if (head.length < 2) throw new Error('No comma-separated columns found. Give a CSV with a header row.')
  const cols: Col[] = head.map((name, i) => {
    const vals = rows.map((r) => r[i] ?? '').filter(Boolean)
    const uniq = new Set(vals).size
    const numeric = vals.length > 0 && vals.every((v) => !Number.isNaN(Number(v.replace(/,/g, ''))))
    const avg = vals.reduce((a, v) => a + v.length, 0) / (vals.length || 1)
    const type: Col['type'] = numeric ? 'number' : avg > 40 ? 'text' : uniq > vals.length * 0.9 && vals.length > 20 ? 'id' : 'category'
    return { name: name || `col_${i + 1}`, type, sample: (vals[0] ?? '').slice(0, 48) }
  })
  const cats = cols.filter((c) => c.type === 'category')
  const target = (cols.find((c) => /label|target|categor|class|sentiment|churn|^y$/i.test(c.name) && c.type !== 'text') ?? cats[cats.length - 1] ?? cols[cols.length - 1]).name
  const ti = cols.findIndex((c) => c.name === target)
  const counts = new Map<string, number>()
  rows.forEach((r) => { const v = r[ti]; if (v) counts.set(v, (counts.get(v) ?? 0) + 1) })
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  const tot = top.reduce((a, [, n]) => a + n, 0) || 1
  const bytesPerRow = text.length / lines.length
  return {
    name: file.name, rows: Math.max(rows.length, Math.round(file.size / bytesPerRow) - 1), cols, target,
    classes: top.map(([name, n]) => ({ name: name.slice(0, 18), share: n / tot })), kind: cols.some((c) => c.type === 'text') ? 'text' : 'tabular', note: 'Your file · profiled from the first rows',
  }
}

/* ------------------------------------------------------------------ stage visuals */

/** The pipeline as one string. Done spans are taut, the active span carries a travelling wave, the rest is unstrung. */
function PipelineString({ stages, phase, notes }: { stages: string[]; phase: number; notes: string[] }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const wave = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const pad = 56, y = 30
  const x = (i: number) => pad + (i / (stages.length - 1)) * (w - pad * 2)
  // phase p is the work that ends at node p: the span between node p-1 and node p (node 0 is its own load pulse)
  useEffect(() => {
    const el = wave.current
    if (!el) return
    const a = phase === 0 ? x(0) - 40 : x(phase - 1), b = x(Math.min(phase, stages.length - 1))
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      let d = ''
      for (let i = 0; i <= 60; i++) {
        const f = i / 60
        d += `${i ? 'L' : 'M'}${(a + f * (b - a)).toFixed(1)},${(y + Math.sin(Math.PI * f) * 9 * Math.sin(f * 18 - (still ? 0 : now / 110))).toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [phase, w, prefs.motion, stages.length])
  return (
    <div ref={ref} className="pipe-string">
      <svg width={w} height={84} aria-hidden style={{ display: 'block' }}>
        <defs><linearGradient id="ps-g" x1="0" x2="1"><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <line x1={x(0)} x2={x(stages.length - 1)} y1={y} y2={y} stroke="#334155" strokeWidth={1.5} strokeDasharray="4 5" />
        {phase > 0 && <line x1={x(0)} x2={x(Math.min(phase, stages.length) - 1)} y1={y} y2={y} stroke="#2DD4BF" strokeWidth={2} />}
        {phase < stages.length && <path ref={wave} fill="none" stroke="url(#ps-g)" strokeWidth={2.25} strokeLinecap="round" />}
        {stages.map((s, i) => {
          const done = i < phase, active = i === phase
          return (
            <g key={s}>
              {active && <circle cx={x(i)} cy={y} r={11} fill="#38BDF8" opacity={0.2} className="live-dot" />}
              <circle cx={x(i)} cy={y} r={6} fill={done ? '#2DD4BF' : active ? '#38BDF8' : '#0A1120'} stroke={done ? '#2DD4BF' : active ? '#38BDF8' : '#475569'} strokeWidth={2} />
              <text x={x(i)} y={y + 28} textAnchor="middle" className="ps-name" style={{ fill: done || active ? '#F8FAFC' : '#94A3B8' }}>{s}</text>
              <text x={x(i)} y={y + 44} textAnchor="middle" className="ps-note">{notes[i]}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/** One-vs-Rest: one string per classifier. It rings while its loss is high and settles taut as it converges. */
function ClassStrings({ classes, conv, f1, active }: { classes: string[]; conv: number[]; f1: number[]; active: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const paths = useRef<(SVGPathElement | null)[]>([])
  const live = useRef({ conv, active }); live.current = { conv, active }
  const { prefs } = usePrefs()
  const rowH = Math.max(34, Math.min(56, 300 / classes.length))
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000
      classes.forEach((_, k) => {
        const c = live.current.conv[k] ?? 0
        const amp = live.current.active ? (rowH / 2 - 2) * Math.pow(1 - c, 0.9) : 0
        const n = 2 + (k % 3)
        let d = ''
        for (let i = 0; i <= 80; i++) {
          const f = i / 80
          const yv = amp * (Math.sin(Math.PI * n * f) * Math.cos(t * (5 + k * 0.7)) * 0.8 + Math.sin(Math.PI * (n + 3) * f) * Math.sin(t * (9 + k)) * 0.35 * (1 - c))
          d += `${i ? 'L' : 'M'}${(f * w).toFixed(1)},${(rowH / 2 + (still ? yv * 0.5 : yv)).toFixed(2)}`
        }
        paths.current[k]?.setAttribute('d', d)
      })
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, classes.length, prefs.motion, rowH])
  return (
    <div className="cls-strings">
      {classes.map((c, k) => {
        const done = conv[k] >= 0.999, idle = !active && !done
        return (
          <div key={c} className="cls-row" style={{ height: rowH }}>
            <span className="cls-name">{c}<small>vs rest</small></span>
            <div ref={k === 0 ? ref : undefined} className="cls-track">
              <svg width={w} height={rowH} style={{ display: 'block', overflow: 'visible' }} aria-hidden>
                <path ref={(el) => { paths.current[k] = el }} fill="none" stroke={done ? '#2DD4BF' : idle ? '#475569' : '#38BDF8'} strokeWidth={done ? 2 : 1.75} strokeDasharray={idle ? '4 5' : undefined} strokeLinecap="round" />
                <circle cx={2} cy={rowH / 2} r={3} fill={idle ? '#475569' : '#2DD4BF'} /><circle cx={w - 2} cy={rowH / 2} r={3} fill={idle ? '#475569' : '#2DD4BF'} />
              </svg>
            </div>
            <span className="cls-read">{idle ? '—' : f1[k].toFixed(3)}<small>{done ? '● converged' : idle ? 'waiting' : `loss ${(0.69 * Math.pow(1 - conv[k], 1.3) + 0.08).toFixed(2)}`}</small></span>
          </div>
        )
      })}
    </div>
  )
}

/** Boosting: the residual is a wave that every new tree damps; trees stack up as frets on the round string. */
function Boosting({ frac, M, active, done }: { frac: number; M: number; active: boolean; done: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const res = useRef<SVGPathElement>(null)
  const live = useRef({ frac, active }); live.current = { frac, active }
  const { prefs } = usePrefs()
  const H = 150, mid = 70
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000, f0 = live.current.frac
      const amp = live.current.active || f0 > 0 ? 52 * Math.exp(-f0 * 3.2) + 1.5 : 0
      let d = ''
      for (let i = 0; i <= 140; i++) {
        const f = i / 140
        const yv = amp * (Math.sin(f * 9 + t * 2.2) * 0.55 + Math.sin(f * 23 - t * 3.4) * 0.3 * (1 - f0) + Math.sin(f * 47 + t * 5) * 0.15 * (1 - f0) ** 2)
        d += `${i ? 'L' : 'M'}${(f * w).toFixed(1)},${(mid + (still ? yv * 0.6 : yv)).toFixed(2)}`
      }
      res.current?.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, prefs.motion])
  const m = Math.round(frac * M)
  const shown = Math.min(14, Math.floor(m / 4))
  return (
    <div ref={ref} className="boost">
      <div className="boost-head">
        <span className="cls-name">Residual<small>y − F<sub>m</sub>(x)</small></span>
        <span className="cls-read">{(0.69 * Math.exp(-frac * 3.2) + 0.21).toFixed(3)}<small>{done ? '● converged' : active ? 'logloss, falling' : 'waiting'}</small></span>
      </div>
      <svg width={w} height={H} style={{ display: 'block' }} aria-hidden>
        <line x1={0} x2={w} y1={mid} y2={mid} stroke="#334155" strokeDasharray="4 5" />
        <path ref={res} fill="none" stroke={done ? '#2DD4BF' : '#38BDF8'} strokeWidth={2} strokeLinecap="round" />
        <line x1={0} x2={w} y1={H - 16} y2={H - 16} stroke="#334155" strokeWidth={1.5} />
        <line x1={0} x2={frac * w} y1={H - 16} y2={H - 16} stroke="#2DD4BF" strokeWidth={2} />
        {Array.from({ length: 31 }, (_, i) => <line key={i} x1={(i / 30) * w} x2={(i / 30) * w} y1={H - 12} y2={H - (i % 5 === 0 ? 4 : 7)} stroke={i / 30 <= frac ? '#2DD4BF' : '#475569'} />)}
        <circle cx={Math.max(4, Math.min(w - 4, frac * w))} cy={H - 16} r={5} fill="#0A1120" stroke="#38BDF8" strokeWidth={2} />
      </svg>
      <div className="boost-foot">
        <span className="ps-note" style={{ fontFamily: 'var(--font-mono)' }}>m = {m} of {M} trees</span>
        <div className="trees">
          {Array.from({ length: shown }, (_, i) => (
            <svg key={i} width="26" height="22" viewBox="0 0 26 22" className={i === shown - 1 && active ? 'tree new' : 'tree'} aria-hidden>
              <path d="M13 3L6 11M13 3L20 11M6 11L3 19M6 11L9 19M20 11L17 19M20 11L23 19" stroke="currentColor" strokeWidth="1.25" fill="none" />
              {[[13, 3], [6, 11], [20, 11], [3, 19], [9, 19], [17, 19], [23, 19]].map(([cx, cy], j) => <circle key={j} cx={cx} cy={cy} r={j < 3 ? 2 : 1.6} fill="currentColor" />)}
            </svg>
          ))}
          {m > 56 && <span className="ps-note">+{m - 56} earlier</span>}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ screen */

export function Workflow({ go }: { go: (r: string) => void }) {
  const [step, setStep] = useState(0)
  const [ds, setDs] = useState<Dataset>(SAMPLES[0])
  const [recipeId, setRecipeId] = useState<Recipe['id']>('ovr')
  const [t, setT] = useState(0)
  const [running, setRunning] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [err, setErr] = useState('')
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const recipe = RECIPES.find((r) => r.id === recipeId)!
  const K = ds.classes.length

  const pick = (d: Dataset) => { setDs(d); setErr(''); setRecipeId(d.kind === 'tabular' ? 'xgb' : 'ovr') }
  const onFile = async (f?: File) => {
    if (!f) return
    if (!/\.(csv|tsv|txt)$/i.test(f.name)) { setErr(`${f.name} is not a CSV. This demo profiles CSV files in your browser; pick a sample for Parquet.`); return }
    try { pick(await profileCsv(f)) } catch (e) { setErr((e as Error).message) }
  }

  useEffect(() => {
    if (!running) return
    const iv = setInterval(() => setT((v) => { const n = Math.min(1, v + 0.0045 * speed); if (n >= 1) setRunning(false); return n }), 70)
    return () => clearInterval(iv)
  }, [running, speed])
  const start = () => { setT(0); setRunning(true); setStep(2) }

  // everything below is derived from t
  const phase = phaseOf(t)
  const ff = fitFrac(t)
  const fold = Math.min(FOLDS - 1, Math.floor(ff * FOLDS))
  const inFold = ff >= 1 ? 1 : ff * FOLDS - fold
  const finals = useMemo(() => {
    const r = rng(ds.name.length * 31 + K * 7 + recipe.id.charCodeAt(0))
    const base = ds.kind === 'tabular' ? (recipe.id === 'xgb' ? 0.887 : 0.842) : recipe.f1
    const cls = ds.classes.map((c) => Math.min(0.985, base + (r() - 0.5) * 0.05 - (c.share < 0.1 ? 0.06 : 0)))
    const folds = Array.from({ length: FOLDS }, () => base + (r() - 0.5) * 0.014)
    const rates = ds.classes.map(() => 0.95 + r() * 0.5)
    return { cls, folds, rates, base }
  }, [ds, recipe.id, K])
  const conv = finals.rates.map((rt) => (phase < 3 ? 0 : phase > 3 ? 1 : Math.min(1, inFold * 1.08 * rt)))
  const f1k = finals.cls.map((v, k) => v * (0.55 + 0.45 * Math.pow(conv[k], 0.6)))
  const meanF1 = finals.folds.reduce((a, b) => a + b, 0) / FOLDS
  const spread = Math.sqrt(finals.folds.reduce((a, b) => a + (b - meanF1) ** 2, 0) / FOLDS)
  const d = ds.kind === 'text' ? Math.round(ds.rows * 1.208) : ds.cols.length * 6
  const elapsed = t * recipe.secs * (ds.rows / 124500 + 0.25)
  const notes = [
    phase > 0 ? `${ds.rows.toLocaleString()} rows` : 'reading', phase > 1 ? `k = ${FOLDS} · stratified` : '',
    phase > 2 ? `d = ${recipe.id === 'xgb' && ds.kind === 'text' ? '300' : d.toLocaleString()}` : phase === 2 ? 'fitting vocabulary' : '',
    phase === 3 ? `fold ${fold + 1} of ${FOLDS}` : phase > 3 ? `${FOLDS} folds fit` : '', phase > 4 ? `F1: ${meanF1.toFixed(3)}` : phase === 4 ? 'scoring holdout' : '',
  ]
  const foldRuns: RunState[] = Array.from({ length: FOLDS }, (_, i) => {
    const st = phase < 3 || i > fold ? 'Queued' : i < fold || ff >= 1 ? 'Completed' : 'Training'
    return { id: `fold_${i + 1}`, label: `${Math.round(ds.rows / FOLDS).toLocaleString()} validation rows`, status: st, progress: st === 'Completed' ? 1 : st === 'Training' ? Math.max(0.04, inFold) : 0, readout: st === 'Completed' ? `F1: ${finals.folds[i].toFixed(3)}` : st === 'Training' ? `fitting · ${(inFold * 100).toFixed(0)}%` : 'waiting' }
  })
  const pts = Math.round(ff * 60)
  const loss: Series[] = useMemo(() => {
    const r = rng(11)
    const full = Array.from({ length: 61 }, (_, i) => [0.2 + 0.95 * Math.exp(-i / 11) + (r() - 0.5) * 0.02, 0.27 + 0.95 * Math.exp(-i / 13) + (r() - 0.5) * 0.03])
    return [
      { name: 'Train loss', color: 'var(--viz-a)', data: full.map((p) => p[0]) },
      { name: 'Validation loss', color: 'var(--viz-c)', dash: '6 4', data: full.map((p) => p[1]) },
    ]
  }, [])
  const logs = [
    [0, 'lt', `$ ensemble train --data ${ds.name} --target ${ds.target} --recipe ${recipe.id}`],
    [0.01, 'lt', `[INFO] loading ${ds.name}`], [PH[1], 'lm', `[OK  ] ${ds.rows.toLocaleString()} rows × ${ds.cols.length} cols · target=${ds.target} · K=${K}`],
    [PH[2], 'lt', `[INFO] split StratifiedKFold(k=${FOLDS}, seed=42)`], [PH[2] + 0.02, 'li', `[FIT ] ${recipe.stages(ds)[2]} on ${Math.round(ds.rows * 0.8).toLocaleString()} rows`],
    [PH[3], 'lm', `[OK  ] features ready · X ∈ ${ds.kind === 'text' ? 'sparse ' : ''}ℝⁿˣᵈ`],
    ...Array.from({ length: FOLDS }, (_, i) => [[PH[3] + (i / FOLDS) * (PH[4] - PH[3]) + 0.01, 'li', `[FIT ] fold=${i + 1} ${recipe.viz === 'boost' ? 'boosting 600 rounds η=0.05' : `fitting ${K} one-vs-rest classifiers`}`], [PH[3] + ((i + 1) / FOLDS) * (PH[4] - PH[3]), 'lm', `[METRIC] fold=${i + 1} macro_f1=${finals.folds[i].toFixed(3)}`]]).flat(),
    [PH[4] + 0.03, 'li', '[EVAL] scoring holdout · confusion matrix'], [1, 'lm', `[DONE] macro_f1=${meanF1.toFixed(3)} ± ${spread.toFixed(3)} · artifacts written`],
  ].filter((l) => t >= (l[0] as number) && (t > 0 || running)) as [number, string, string][]
  const confusion = useMemo(() => {
    const r = rng(K * 97 + ds.rows)
    return ds.classes.map((c, i) => {
      const tot = Math.round(ds.rows * 0.15 * c.share)
      const hit = Math.round(tot * finals.cls[i])
      const rest = ds.classes.map((_, j) => (j === i ? 0 : r() + 0.1)); const rs = rest.reduce((a, b) => a + b, 0)
      return ds.classes.map((_, j) => (j === i ? hit : Math.round(((tot - hit) * rest[j]) / rs)))
    })
  }, [ds, finals, K])
  const done = t >= 1
  const stepNow = step < 2 ? step : done ? 3 : 2
  const STEPS = ['Dataset', 'Recipe', 'Train', 'Results']

  return (
    <div className="gym">
      <div className="stepper-bar">
        <div className="stepper-title">
          <h1 className="page-title">Quick Train</h1>
          <span className="helper">Give it a dataset, pick a recipe, watch it fit.</span>
          <span className="spacer" />
          <span className="chip">{ds.name}</span>{step > 0 && <span className="chip">{recipe.pipeline(ds)}</span>}
        </div>
        <div className="stepper" role="list" style={{ gridTemplateColumns: 'repeat(4, 1fr)', maxWidth: 720 }}>
          {STEPS.map((s, i) => (
            <button key={s} role="listitem" disabled={i > stepNow || running} className={`step ${i < stepNow || (done && i === 3) ? 'done' : i === stepNow ? 'current' : ''}`} onClick={() => i < 2 && setStep(i)}>
              <span className="node" /><span className="idx">{String(i + 1).padStart(2, '0')}</span><span>{s}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="gym-scroll">
        {step === 0 && (
          <div className="page">
            <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.5fr)', alignItems: 'start' }}>
              <div className="stack">
                <div className={`drop ${over ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
                  onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0]) }} onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && input.current?.click()}>
                  <svg width="120" height="34" viewBox="0 0 120 34" aria-hidden><path d="M2 17C20 -8 40 42 60 17S100 -8 118 17" stroke="var(--accent-secondary)" strokeWidth="1.75" fill="none" /><path d="M2 17C20 4 40 30 60 17S100 4 118 17" stroke="var(--accent-primary)" strokeWidth="1.75" fill="none" opacity=".6" /><circle cx="2" cy="17" r="3" fill="var(--accent-primary)" /><circle cx="118" cy="17" r="3" fill="var(--accent-primary)" /></svg>
                  <b className="card-title">Drop a CSV here</b>
                  <span className="helper">or click to browse. It is profiled in your browser and never uploaded.</span>
                  <input ref={input} type="file" accept=".csv,.tsv,.txt" hidden onChange={(e) => onFile(e.target.files?.[0])} />
                </div>
                {err && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
                <div className="card">
                  <div className="card-head"><h3 className="card-title">Sample Datasets</h3><span className="micro-label">Workspace</span></div>
                  {SAMPLES.map((s) => (
                    <button key={s.name} className={`belt-row ${ds.name === s.name ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => pick(s)}>
                      <span className="belt-text"><b className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{s.name}</b><span className="helper" style={{ fontSize: 12 }}>{s.rows.toLocaleString()} rows × {s.cols.length} cols · {s.note}</span></span>
                      <span className="pin-state">{ds.name === s.name ? '● Selected' : '○ Use'}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="card">
                <div className="card-head">
                  <div className="left"><h3 className="card-title">Profile</h3><span className="mono" style={{ fontSize: 12.5 }}>{ds.name}</span></div>
                  <div className="row"><ParamChip sym="n" value={ds.rows.toLocaleString()} /><ParamChip sym="K" value={String(K)} /></div>
                </div>
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>Column</th><th>Type</th><th>Example</th><th>Role</th></tr></thead>
                  <tbody>
                    {ds.cols.map((c) => (
                      <tr key={c.name} className={c.name === ds.target ? 'sel' : ''}>
                        <td className="table-cell">{c.name}</td><td><span className="chip" style={{ fontSize: 11 }}>{c.type === 'text' ? 'str' : c.type === 'number' ? 'num' : c.type === 'id' ? 'id' : 'cat'}</span></td>
                        <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.sample}</td>
                        <td>{c.name === ds.target ? <StatusPill status="success">Target y</StatusPill> : c.type === 'id' ? <span className="muted">Ignored</span>
                          : <button className="btn sm ghost" onClick={() => c.type !== 'text' && setDs({ ...ds, target: c.name })} disabled={c.type === 'text'}>{c.type === 'text' ? 'Feature' : 'Feature · Set As Target'}</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
                <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
                  <div className="micro-label" style={{ marginBottom: 8 }}>Classes of <span className="mono" style={{ textTransform: 'none' }}>{ds.target}</span></div>
                  {ds.classes.map((c) => (
                    <div key={c.name} className="dist-row">
                      <span>{c.name}</span>
                      <svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(c.share / Math.max(...ds.classes.map((x) => x.share))) * 100}%`} y1="5" y2="5" stroke={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={c.share < 0.1 ? '4 3' : undefined} /><circle cx={`${(c.share / Math.max(...ds.classes.map((x) => x.share))) * 100}%`} cy="5" r="3" fill={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} /></svg>
                      <span className="table-cell">{(c.share * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="page">
            <div className="patch" style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <VelaCrest size={20} />
              <p>{ds.kind === 'text'
                ? <>Your features are mostly text and <b>K = {K}</b>. A sparse linear model is the strong first string: <b>TF-IDF → LogReg (OvR)</b> usually lands within 0.01 F1 of boosted trees at a tenth of the fit time.</>
                : <>Your features are numeric and categorical with no long text. <b>XGBoost</b> handles mixed scales and interactions without scaling; start there.</>}</p>
            </div>
            <div className="lib-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
              {RECIPES.filter((r) => ds.kind === 'text' || r.id !== 'svm').map((r) => {
                const on = r.id === recipeId, rec = r.best === ds.kind && r.id !== 'svm'
                const stages = r.stages(ds)
                return (
                  <button key={r.id} className={`card lib-card recipe ${on ? 'on' : ''}`} onClick={() => setRecipeId(r.id)} aria-pressed={on}>
                    <div className="top">
                      <div className="row" style={{ justifyContent: 'space-between' }}><h3 className="card-title">{r.pipeline(ds)}</h3>{rec ? <StatusPill status="info">Vela Pick</StatusPill> : <span className="pin-state">{on ? '● Selected' : '○ Select'}</span>}</div>
                      <svg width="100%" height="40" aria-hidden>
                        <line x1="6%" x2="94%" y1="12" y2="12" stroke={on ? 'var(--accent-primary)' : 'var(--border-track)'} strokeWidth="2" />
                        {stages.map((s, i) => <g key={s}><circle cx={`${6 + (i / (stages.length - 1)) * 88}%`} cy="12" r="4" fill={on ? 'var(--accent-primary)' : 'var(--bg-surface)'} stroke={on ? 'var(--accent-primary)' : 'var(--border-track)'} strokeWidth="1.5" /><text x={`${6 + (i / (stages.length - 1)) * 88}%`} y="34" textAnchor={i === 0 ? 'start' : i === stages.length - 1 ? 'end' : 'middle'} className="tick-text" style={{ fontFamily: 'var(--font-sans)' }}>{i % 2 === 0 ? s : ''}</text></g>)}
                      </svg>
                      <div className="eqn">{r.eq}</div>
                      <p className="helper" style={{ textAlign: 'left' }}>{r.where}</p>
                      <div className="row" style={{ flexWrap: 'wrap' }}><span className="micro-label">Defaults</span>{r.chips.map(([s, v]) => <ParamChip key={s} sym={s} value={v} />)}</div>
                    </div>
                    <div className="bot"><span className="table-cell" style={{ color: 'var(--text-body)' }}>est. {mmss(r.secs * (ds.rows / 124500 + 0.25))}</span><span className="table-cell">expected F1: {(ds.kind === 'tabular' ? (r.id === 'xgb' ? 0.887 : 0.842) : r.f1).toFixed(3)}</span></div>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {step >= 2 && (
          <div className="page">
            <section className="stage">
              <div className="stage-head">
                <div>
                  <span className="hero-eyebrow" style={{ paddingLeft: 0 }}>{done ? 'RUN COMPLETE' : running ? 'TRAINING' : 'PAUSED'}</span>
                  <h2 className="hero-title">{recipe.pipeline(ds)}</h2>
                </div>
                <div className="stage-read">
                  <span><small>macro F1</small><b>{phase >= 3 ? (phase > 3 ? meanF1 : f1k.reduce((a, b) => a + b, 0) / K).toFixed(3) : '—'}</b></span>
                  <span><small>fold</small><b>{phase < 3 ? '—' : `${Math.min(FOLDS, fold + 1)} / ${FOLDS}`}</b></span>
                  <span><small>elapsed</small><b>{mmss(elapsed)}</b></span>
                  <span className="stage-sim" title="This run is simulated in your browser. No model is fitted.">simulated run</span>
                </div>
              </div>
              <PipelineString stages={recipe.stages(ds)} phase={phase} notes={notes} />
              <div className="stage-body">
                <div className="stage-cap">
                  <span className="micro-label" style={{ color: 'var(--text-on-shell-muted)' }}>{recipe.viz === 'strings' ? `${K} classifiers, one string each` : 'Boosting rounds'}</span>
                  <span className="ps-note">{recipe.viz === 'strings' ? 'A string rings while its loss is high and settles taut when it converges. Each fold plucks them again.' : 'Each tree fits the residual the trees before it left. The wave is what remains.'}</span>
                </div>
                {recipe.viz === 'strings'
                  ? <ClassStrings classes={ds.classes.map((c) => c.name)} conv={conv} f1={f1k} active={phase === 3} />
                  : <Boosting frac={phase < 3 ? 0 : phase > 3 ? 1 : inFold} M={600} active={phase === 3} done={phase > 3} />}
              </div>
              <div className="stage-foot">
                <div className="stage-prog"><i style={{ width: `${t * 100}%` }} /></div>
                {done ? <button className="btn sm" onClick={start}>Run Again</button>
                  : <button className="btn sm" onClick={() => setRunning(!running)}>{running ? 'Pause' : 'Resume'}</button>}
                <div className="seg stage-seg">{[1, 3].map((s) => <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{s}×</button>)}</div>
              </div>
            </section>

            <div className="grid two">
              <div className="stack">
                <div className="card run-strings">
                  <div className="card-head" style={{ marginTop: -4 }}><div className="left"><h3 className="card-title">Cross-Validation</h3><span className="helper" style={{ fontSize: 12 }}>one string per fold</span></div><ParamChip sym="k" value={String(FOLDS)} /></div>
                  {foldRuns.map((r) => <RunString key={r.id} run={r} folds={4} />)}
                </div>
                <PlotFrame title="Loss" legend={loss.map((s) => ({ ...s, data: [] }))} chips={<ParamChip sym="seed" value="42" />}
                  caption={<span>Cross-entropy <span className="math" style={{ fontSize: 14 }}>ℒ</span> per step <span className="math" style={{ fontSize: 14 }}>t</span>, mean of folds so far</span>}>
                  <LineChart series={loss.map((s) => ({ ...s, data: s.data.slice(0, Math.max(2, pts + 1)) }))} xMax={60} yMin={0} yMax={1.4} yTicks={[0, 0.35, 0.7, 1.05, 1.4]} xTicks={[0, 15, 30, 45, 60]} xTitle="t" yTitle="ℒ" fmtY={(v) => v.toFixed(2)} live={!done && phase === 3} height={200} />
                </PlotFrame>
              </div>
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h3 className="card-title">Run Log</h3>{done ? <StatusPill status="success">Completed</StatusPill> : <StatusPill status="info">{running ? 'Streaming' : 'Paused'}</StatusPill>}</div>
                  <div className="card-body"><div className="log-panel" style={{ height: 250 }} aria-live="polite">{logs.slice(-12).map((l, i) => <div key={i} className={l[1]}>{l[2]}</div>)}</div></div>
                </div>
                {done && (
                  <div className="card navy">
                    <div className="card-head"><h3 className="card-title">Result</h3><StatusPill status="success">Ready To Register</StatusPill></div>
                    <div className="minis">
                      <div className="mini"><span className="micro-label">Macro F1</span><span className="v">{meanF1.toFixed(3)}</span><span className="u">± {spread.toFixed(3)} · n = {FOLDS}</span></div>
                      <div className="mini"><span className="micro-label">Weakest Class</span><span className="v">{Math.min(...finals.cls).toFixed(3)}</span><span className="u">{ds.classes[finals.cls.indexOf(Math.min(...finals.cls))].name}</span></div>
                      <div className="mini"><span className="micro-label">Fit Time</span><span className="v">{mmss(elapsed)}</span><span className="u">cpu-8x</span></div>
                    </div>
                    <div className="card-body row" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap' }}>
                      <button className="btn primary" onClick={() => go('registry')}>Register Model Version</button>
                      <button className="btn" onClick={() => go('gym')}>Tune In Gym →</button>
                      <button className="btn ghost" onClick={() => { setStep(0); setT(0) }}>Train Another</button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {done && (
              <div className="card">
                <div className="card-head"><div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="helper" style={{ fontSize: 12 }}>holdout, 15% of rows</span></div><ParamChip sym="n" value={Math.round(ds.rows * 0.15).toLocaleString()} /></div>
                <div className="card-body" style={{ maxWidth: 760 }}><ConfusionMatrix labels={ds.classes.map((c) => c.name)} m={confusion} /></div>
              </div>
            )}
          </div>
        )}
      </div>

      {step < 2 && (
        <div className="gym-footer">
          <div>{step === 1 && <button className="btn ghost" onClick={() => setStep(0)}>← Back</button>}</div>
          <div className="valid"><span className="dot">●</span> {step === 0 ? `Schema Valid · target ${ds.target} · K = ${K}` : `${recipe.pipeline(ds)} · Ready`}</div>
          <div className="right">{step === 0 ? <button className="btn primary" onClick={() => setStep(1)}>Continue →</button> : <button className="btn primary" onClick={start}>Start Training</button>}</div>
        </div>
      )}
    </div>
  )
}
