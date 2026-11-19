import { useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { LearningCurve, Run } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { niceTicks, padDomain, XYPlot } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { pctTrim } from '@/shared/lib/format'
import { useFetch } from '@/shared/hooks/useFetch'

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const nfmt = (n: number) => Math.round(n).toLocaleString()
const VERDICT = {
  'data-limited': { pill: 'warning', head: 'Data-limited', say: 'More rows of the same kind are expected to help.' },
  saturated: { pill: 'success', head: 'Saturated', say: 'More rows of the same kind are not expected to help measurably: look at features, labels or the model instead.' },
  noisy: { pill: 'neutral', head: 'Noisy', say: 'The subsamples disagree too much to tell; more seeds (Advanced) narrow the interval.' },
} as const

/** F12: "do I need more data?". The run's exact fit call on nested subsamples of its training rows, every fit scored on
 *  the run's holdout; a power law u(n) = A − b·(n/N)^(−c) through the means, extrapolated to 2N and 4N with bootstrap
 *  intervals, and the verdict. Every number is GET /api/runs/{id}/learning-curve. */
export function LearningCurvePanel({ run }: { run: Run }) {
  const [busy, setBusy] = useState(false)
  const [fractions, setFractions] = useState('0.1, 0.25, 0.5, 1')
  const [seeds, setSeeds] = useState(3)
  const [timeLimit, setTimeLimit] = useState<number | null>(null)
  const [minGain, setMinGain] = useState('')

  const { data: lc, setData: setLc, err, setErr, reload } = useFetch(() => api.runs.learningCurve(run.id), [run.id], {
    pollMs: 2000, while: (d) => d?.status === 'queued' || d?.status === 'running',
    onData: (r) => { if (timeLimit == null && r.defaults.time_limit) setTimeLimit(r.defaults.time_limit) },
  })
  const active = lc?.status === 'queued' || lc?.status === 'running'

  const start = async () => {
    setBusy(true); setErr('')
    try {
      const fr = fractions.split(/[,\s]+/).filter(Boolean).map(Number)
      const r = await api.runs.startLearningCurve(run.id, {
        fractions: fr, seeds, time_limit: timeLimit ?? undefined, min_gain: minGain.trim() ? Number(minGain) : undefined,
      })
      setLc(r)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const cancel = async () => { try { await api.runs.cancelLearningCurve(run.id); reload() } catch (e) { setErr((e as Error).message) } }

  if (!lc && !err) return <div className="card card-body row"><ResonanceWave /> Reading the learning curve…</div>
  const d = lc?.defaults
  const metric = lc?.metric?.name ?? run.eval_metric?.name ?? 'score'
  const done = lc?.status === 'succeeded'

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="lc-panel" data-status={lc?.status}>
      {err && <div className="card card-body" role="alert" data-testid="lc-error"><p className="helper">✕ {err}</p></div>}
      {lc && done && lc.verdict && <Verdict lc={lc} />}
      {lc && (done || (lc.points?.length ?? 0) > 1) && <Chart lc={lc} metric={metric} />}
      {lc && done && lc.verdict && <Formula lc={lc} metric={metric} />}
      {lc && active && (
        <div className="card card-body row" data-testid="lc-progress" style={{ gap: 'var(--space-12)', flexWrap: 'wrap' }}>
          <ResonanceWave />
          <span>Fitting <b className="mono">{lc.fits_done ?? 0} / {lc.fits_total}</b> subsamples
            {lc.current ? <> · now {pctTrim(lc.current.fraction)} of the rows, seed {lc.current.seed}</> : null}
            {' '}· the run's own fit is the 100% point</span>
          <button className="btn" onClick={cancel} data-testid="lc-cancel">Cancel</button>
        </div>
      )}
      {lc && done && <PointsTable lc={lc} metric={metric} />}
      {lc && !active && (
        <div className="card" data-testid="lc-start">
          <div className="card-head"><div className="left"><h3 className="card-title">{done ? 'Run It Again' : 'Do I Need More Data?'}</h3>
            <span className="helper" style={{ fontSize: 12 }}>the run's exact fit call on nested subsamples of its {d?.n_full?.toLocaleString() ?? ''} training rows, each scored on the same holdout</span></div></div>
          <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
            {(lc.status === 'failed' || lc.status === 'cancelled') && <p className="helper" role="alert">✕ The last curve {lc.status === 'failed' ? `failed: ${lc.error}` : 'was cancelled'}.</p>}
            <p className="helper" style={{ fontSize: 12.5 }}>
              Fits {fractions} of the training rows ({seeds} seeds below 100%; the run itself is 100%): {d ? <b className="mono">{fractionsCount(fractions) * seeds} fits</b> : null}
              {d?.time_limit ? <>, each capped at {timeLimit ?? d.time_limit} s</> : null}
              {d?.estimate_seconds ? <> · about {Math.max(1, Math.round(d.estimate_seconds / 60))} min here (the run took {d.run_fit_seconds?.toFixed(0)} s)</> : null}.
              The same presets, hyperparameters, metric{run.config && (run.config as Record<string, unknown>).cost ? ' (the business cost)' : ''}, constraints, scorecard and split as the run.
            </p>
            <details className="an-adv" data-testid="lc-advanced"><summary className="micro-label">Advanced · sample sizes, seeds, budget, ε</summary>
              <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">fractions of the rows (must include 1)</span>
                  <input className="input mono" value={fractions} onChange={(e) => setFractions(e.target.value)} aria-label="Fractions" style={{ width: 200 }} /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">seeds per fraction</span>
                  <NumberInput value={seeds} min={1} max={10} step={1} onChange={(v) => setSeeds(Math.round(v))} label="Seeds" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">time limit per fit, s</span>
                  <NumberInput value={timeLimit ?? d?.time_limit ?? 120} min={5} max={7200} step={5} onChange={(v) => setTimeLimit(v)} label="Time limit" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">ε, smallest gain that counts</span>
                  <input className="input mono" value={minGain} placeholder="2.5% of the gap" onChange={(e) => setMinGain(e.target.value)} aria-label="Minimum gain" style={{ width: 140 }} /></label>
              </div>
            </details>
            <div className="row"><button className="btn primary" onClick={start} disabled={busy} data-testid="lc-run">{busy ? 'Starting…' : done ? 'Run the curve again' : 'Run the learning curve'}</button></div>
          </div>
        </div>
      )}
    </div>
  )
}

function fractionsCount(text: string) {
  return text.split(/[,\s]+/).filter(Boolean).map(Number).filter((f) => f > 0 && f < 1).length
}

function Verdict({ lc }: { lc: LearningCurve }) {
  const v = lc.verdict!
  const s = v.slope_per_doubling
  const x = v.extrapolation
  const ev = v.evidence
  const meta = VERDICT[v.label]
  return <>
    <div className="card navy explain-hero" data-testid="lc-hero">
      <div className="eh-cell lead">
        <span className="micro-label">verdict</span>
        <b data-testid="lc-verdict">{meta.head}</b>
        <small>{meta.say}</small>
      </div>
      <div className="eh-cell"><span className="micro-label">Δ₂ gain per doubling</span><b className="mono" data-testid="lc-slope">{sgn(s.value)}</b><small>95% CI [{sgn(s.ci[0])}, {sgn(s.ci[1])}] · ε = {ev.min_gain.toFixed(4)}</small></div>
      <div className="eh-cell"><span className="micro-label">{v.metric} at N = {nfmt(x.n_full)}</span><b className="mono">{fmtScore(x.score_at_n)}</b><small>on the curve · the run scored {fmtScore(lc.points?.find((p) => p.source === 'run')?.score)}</small></div>
      <div className="eh-cell"><span className="micro-label">at 2N = {nfmt(x.x2.n)}</span><b className="mono" data-testid="lc-x2">{fmtScore(x.x2.score)}</b><small>95% CI [{fmtScore(x.x2.ci[0])}, {fmtScore(x.x2.ci[1])}]</small></div>
      <div className="eh-cell"><span className="micro-label">at 4N = {nfmt(x.x4.n)}</span><b className="mono" data-testid="lc-x4">{fmtScore(x.x4.score)}</b><small>95% CI [{fmtScore(x.x4.ci[0])}, {fmtScore(x.x4.ci[1])}]</small></div>
    </div>
    <p className="helper" style={{ fontSize: 12.5 }} data-testid="lc-reason">
      <StatusPill status={meta.pill}>{v.label}</StatusPill>{' '}Because {v.reason}.
      {ev.observed_last_step && <> Measured directly, going from {pctTrim(ev.observed_last_step.from_fraction)} to 100% of the rows gained {sgn(ev.observed_last_step.gain)} in {v.metric} ({v.greater_is_better ? 'higher' : 'lower'} is better; + = better; 95% CI [{sgn(ev.observed_last_step.ci[0])}, {sgn(ev.observed_last_step.ci[1])}]).</>}
    </p>
  </>
}

function Chart({ lc, metric }: { lc: LearningCurve; metric: string }) {
  const pts = lc.points ?? []
  const N = lc.n_full ?? Math.max(...pts.map((p) => p.n))
  const lg = (n: number) => Math.log2(n)
  const curve = lc.curve ?? []
  const band = lc.band ?? []
  const sum = lc.summary ?? []
  const v = lc.verdict
  const ys = [...pts.map((p) => p.score), ...band.flatMap((b) => [b[1], b[2]]), ...sum.flatMap((s) => s.ci ?? [])]
  const flat = Math.max(...ys) - Math.min(...ys) < 1e-9
  const [y0, y1] = flat ? [ys[0] - Math.max(0.005, Math.abs(ys[0]) * 0.01), ys[0] + Math.max(0.005, Math.abs(ys[0]) * 0.01)] : padDomain(ys, 0.08)
  const xmin = lg(Math.min(...pts.map((p) => p.n), ...(curve.length ? [curve[0][0]] : []))) - 0.25
  const xmax = lg(curve.length ? curve[curve.length - 1][0] : N) + 0.25
  const xt = [...new Set([...sum.map((s) => s.n), ...(v ? [v.extrapolation.x2.n, v.extrapolation.x4.n] : [])])].map(lg)
  const fit = useMemo(() => curve.filter(([n]) => n <= N * 1.0001).map(([n, y]) => [lg(n), y] as [number, number]), [curve, N])
  const ext = useMemo(() => curve.filter(([n]) => n >= N * 0.9999).map(([n, y]) => [lg(n), y] as [number, number]), [curve, N])
  const series = [
    { name: 'subsample fits', color: 'var(--viz-b)', width: 0, dots: true, dotR: () => 2.5, points: pts.filter((p) => p.source === 'subsample').map((p) => [lg(p.n), p.score] as [number, number]).sort((a, b) => a[0] - b[0]), tip: () => 'one seed' },
    { name: 'mean per n', color: 'var(--text-primary)', width: 0, dots: true, dotR: () => 4, points: sum.length ? sum.map((s) => [lg(s.n), s.mean] as [number, number]) : pts.filter((p) => p.source === 'run').map((p) => [lg(p.n), p.score] as [number, number]), tip: (i: number) => `k = ${sum[i]?.k}${sum[i]?.ci ? ` · 95% t [${fmtScore(sum[i].ci![0])}, ${fmtScore(sum[i].ci![1])}]` : ' · the run itself'}` },
    ...(fit.length ? [{ name: 'power law', color: 'var(--viz-a)', points: fit }] : []),
    ...(ext.length ? [{ name: 'extrapolation', color: 'var(--viz-a)', dash: '5 4', points: ext }] : []),
  ]
  return (
    <PlotFrame title="Score Against Training Rows"
      chips={<><ParamChip sym="N" value={nfmt(N)} /><ParamChip sym="holdout" value={`${lc.holdout_rows?.toLocaleString()} rows`} /><ParamChip sym="fits" value={`${pts.length}`} /></>}
      legend={[{ name: 'one subsample fit (seed)', color: 'var(--viz-b)', data: [] }, { name: 'mean ± 95% t-interval', color: 'var(--text-primary)', data: [] }, { name: 'u(n) fit, 95% band', color: 'var(--viz-a)', data: [] }, { name: 'extrapolated', color: 'var(--viz-a)', dash: '5 4', data: [] }]}
      caption={<>holdout {metric} of each fit against n on a log₂ axis (one step = a doubling); all fits are scored on the same {lc.holdout_rows?.toLocaleString()} holdout rows. Shaded: the bootstrap 95% band of the fitted curve.</>}>
      <div data-testid="lc-chart">
        <XYPlot height={280} series={series}
          x={{ min: xmin, max: xmax, ticks: xt, title: 'training rows n', fmt: (t) => nfmt(2 ** t) }}
          y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 5), title: metric, fmt: (t) => fmtScore(t, 3) }}
          refs={[{ x: lg(N), label: `N = ${nfmt(N)} (the run)`, color: 'var(--text-muted)' }]}
          marks={v ? [{ x: lg(v.extrapolation.x2.n), y: v.extrapolation.x2.score, label: `2N: ${fmtScore(v.extrapolation.x2.score)}` }, { x: lg(v.extrapolation.x4.n), y: v.extrapolation.x4.score, label: `4N: ${fmtScore(v.extrapolation.x4.score)}` }] : undefined}>
          {(sx, sy) => <g>
            {band.length > 1 && <path d={`M${band.map((b) => `${sx(lg(b[0])).toFixed(1)},${sy(b[2]).toFixed(1)}`).join('L')}L${[...band].reverse().map((b) => `${sx(lg(b[0])).toFixed(1)},${sy(b[1]).toFixed(1)}`).join('L')}Z`} fill="var(--viz-a)" opacity={0.12} />}
            {sum.filter((s) => s.ci).map((s) => <g key={s.fraction} stroke="var(--text-primary)" strokeWidth={1.25}>
              <line x1={sx(lg(s.n))} x2={sx(lg(s.n))} y1={sy(s.ci![0])} y2={sy(s.ci![1])} />
              <line x1={sx(lg(s.n)) - 4} x2={sx(lg(s.n)) + 4} y1={sy(s.ci![0])} y2={sy(s.ci![0])} />
              <line x1={sx(lg(s.n)) - 4} x2={sx(lg(s.n)) + 4} y1={sy(s.ci![1])} y2={sy(s.ci![1])} />
            </g>)}
          </g>}
        </XYPlot>
      </div>
    </PlotFrame>
  )
}

function Formula({ lc, metric }: { lc: LearningCurve; metric: string }) {
  const v = lc.verdict!
  const f = lc.fit!
  const ev = v.evidence
  const gib = v.greater_is_better
  const A = f.A, b = f.b, c = f.c
  const g2 = b * (1 - 2 ** -c)
  return (
    <div className="grid mono-grid">
      <div className="formula" data-testid="lc-formula">
        <div className="micro-label">The curve and the rule</div>
        <div className="eq"><i>u</i>(<i>n</i>) = <i>A</i> − <i>b</i> · (<i>n</i> ⁄ <i>N</i>)<sup>−<i>c</i></sup> = {A.toFixed(4)} − {b.toFixed(4)} · (<i>n</i> ⁄ {nfmt(lc.n_full ?? 0)})<sup>−{c.toFixed(2)}</sup></div>
        <div className="eq">Δ<sub>2</sub> = <i>u</i>(2<i>N</i>) − <i>u</i>(<i>N</i>) = <i>b</i> · (1 − 2<sup>−<i>c</i></sup>) = {b.toFixed(4)} · (1 − 2<sup>−{c.toFixed(2)}</sup>) = <b className="mono">{g2.toFixed(4)}</b></div>
        <div className="eq">verdict = data-limited if CI(Δ<sub>2</sub>) &gt; ε · saturated if CI(Δ<sub>2</sub>) &lt; ε · else noisy</div>
        <dl className="where">
          <dt>u</dt><dd>{gib ? <>the holdout {metric} (greater is better)</> : <>−{metric}: the holdout {metric} with its sign flipped, so up is better</>}</dd>
          <dt>n, N</dt><dd>training rows of a fit; N = {nfmt(lc.n_full ?? 0)}, the run's own training rows</dd>
          <dt>A</dt><dd>the plateau u(∞) = <b className="mono">{A.toFixed(4)}</b>{lc.fit?.u_optimum != null ? <> (held at or below the optimum u* = {lc.fit.u_optimum}){Math.abs(A - lc.fit.u_optimum) < 1e-9 ? <>: the fit hit it</> : null}</> : null}</dd>
          <dt>b, c</dt><dd>how far below the plateau the curve starts, b = <b className="mono">{b.toFixed(4)}</b> ≥ 0, and how fast it closes, c = <b className="mono">{c.toFixed(2)}</b> ∈ (0, 2]; least squares through the {lc.summary?.length} means (fit RMSE {ev.fit_rmse.toFixed(4)})</dd>
          <dt>CI</dt><dd>95% over B = {ev.B.toLocaleString()} replicates: each mean redrawn from N(mean, σ<sub>pop</sub>(f)² ⁄ k), σ<sub>pop</sub>(f) = sd<sub>f</sub> ⁄ √(1 − f) (seeds share one pool of N rows), the 100% point with σ<sub>pop</sub> = {ev.sd_population_full.toFixed(4)}; the curve refitted each time</dd>
          <dt>ε</dt><dd><b className="mono">{ev.min_gain.toFixed(4)}</b>, the smallest gain per doubling that counts: {ev.min_gain_rule}</dd>
        </dl>
      </div>
      <div className="card" data-testid="lc-split">
        <div className="card-head"><div className="left"><h3 className="card-title">How the Subsamples Were Drawn</h3></div></div>
        <div className="card-body stack" style={{ gap: 6 }}>
          <p className="helper" style={{ fontSize: 12.5 }}>Inside the run's own {lc.split?.strategy} split <span className="mono">{lc.split?.key}</span>: {lc.split?.subsample}. For each seed the sets are nested ({pctTrim(lc.summary?.[0]?.fraction ?? 0)} ⊂ … ⊂ 100%){lc.nested ? ' ✓' : ' ✕'}{lc.groups_whole != null ? <>; every {lc.split?.group_column} is either wholly in or wholly out {lc.groups_whole ? '✓' : '✕'}</> : null}.
            {lc.tuning_rows ? <> The split's tuning slice ({lc.tuning_rows.toLocaleString()} rows) is kept whole in every fit.</> : null} Every fit is scored on the same {lc.holdout_rows?.toLocaleString()} holdout rows (<span className="mono">{lc.holdout_sha}</span>).</p>
          <details className="an-adv"><summary className="micro-label">The fit call ({lc.fit_call?.notes.length ?? 0} notes)</summary>
            <ul className="helper" style={{ fontSize: 12, paddingLeft: 16 }}>{lc.fit_call?.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{JSON.stringify({ predictor_kwargs: lc.fit_call?.predictor_kwargs, fit_kwargs: lc.fit_call?.fit_kwargs }, null, 1)}</pre>
          </details>
        </div>
      </div>
    </div>
  )
}

function PointsTable({ lc, metric }: { lc: LearningCurve; metric: string }) {
  return (
    <div className="card" data-testid="lc-table">
      <div className="card-head"><div className="left"><h3 className="card-title">Every Sample Size</h3><span className="helper" style={{ fontSize: 12 }}>mean x̄ of k fits with the 95% t-interval x̄ ± t<sub>k−1</sub> · s ⁄ √k (t: Student's 97.5% quantile)</span></div></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact"><thead><tr><th>share</th><th className="r">n</th><th className="r">k</th><th className="r">mean {metric}</th><th className="r">sd s</th><th className="r">95% t-interval</th><th>each fit</th></tr></thead>
          <tbody>{lc.summary?.map((s) => <tr key={s.fraction}>
            <td className="mono">{pctTrim(s.fraction)}</td><td className="r mono">{nfmt(s.n)}</td><td className="r mono">{s.k}</td>
            <td className="r mono"><b>{fmtScore(s.mean)}</b></td><td className="r mono">{s.sd == null ? '—' : s.sd.toFixed(4)}</td>
            <td className="r mono">{s.ci ? `[${fmtScore(s.ci[0])}, ${fmtScore(s.ci[1])}]` : 'the run itself'}</td>
            <td className="mono" style={{ fontSize: 12 }}>{s.scores.map((v) => fmtScore(v)).join(' · ')}</td>
          </tr>)}</tbody></table>
      </div>
    </div>
  )
}
