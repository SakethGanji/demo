import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run, ScorecardReport, ScorecardScore } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const ivBand = (iv: number) => (iv < 0.02 ? 'not predictive' : iv < 0.1 ? 'weak' : iv < 0.3 ? 'medium' : 'strong')

/** F11: the scorecard challenger. The deployable scorecard (binning → WoE → logistic regression → points, refitted on
 *  the training rows), IV per feature, its holdout AUC beside the served model's with ΔAUC and a bootstrap CI, the score
 *  bands, and the points and reason codes of one holdout row. Every number is GET /api/runs/{id}/scorecard or
 *  POST …/scorecard/score. */
export function ScorecardPanel({ run }: { run: Run }) {
  const [sc, setSc] = useState<ScorecardReport | null>(null)
  const [err, setErr] = useState('')
  const [feature, setFeature] = useState<string>('')
  const [row, setRow] = useState(0)
  const [scored, setScored] = useState<ScorecardScore | null>(null)
  const [rowErr, setRowErr] = useState('')

  useEffect(() => {
    let alive = true
    setErr(''); setSc(null)
    api.runs.scorecard(run.id).then((r) => {
      if (!alive) return
      setSc(r)
      const inModel = [...r.table.features].filter((f) => f.in_model).sort((a, b) => b.iv - a.iv)
      setFeature(inModel[0]?.feature ?? r.table.features[0]?.feature ?? '')
    }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id])
  useEffect(() => {
    if (!sc) return
    let alive = true
    setRowErr('')
    api.runs.scorecardScore(run.id, [row]).then((r) => alive && setScored(r)).catch((e) => alive && setRowErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, sc, row])

  const feats = useMemo(() => (sc ? [...sc.table.features].sort((a, b) => b.iv - a.iv) : []), [sc])
  if (err) return <div className="card card-body" role="alert" data-testid="scorecard-error"><p className="helper">✕ {err}</p></div>
  if (!sc) return <div className="card card-body row"><ResonanceWave /> Reading the scorecard…</div>

  const t = sc.table
  const s = t.scaling
  const h = sc.holdout
  const d = h.delta_auc
  const maxIv = Math.max(...feats.map((f) => f.iv), 0.001)
  const bins = t.rows.filter((r) => r.feature === feature)
  const fsel = t.features.find((f) => f.feature === feature)
  const r0 = scored?.rows[0]
  const maxBand = Math.max(...h.bands.map((b) => b.event_rate), 0.001)
  const significant = d.ci[0] > 0
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="scorecard-panel">
      <div className="card navy explain-hero" data-testid="scorecard-hero">
        <div className="eh-cell lead">
          <span className="micro-label">ΔAUC served − scorecard</span>
          <b className="mono" data-testid="scorecard-delta">{sgn(d.delta)}</b>
          <small>95% CI [{sgn(d.ci[0])}, {sgn(d.ci[1])}] · B = {d.B} paired resamples</small>
        </div>
        <div className="eh-cell"><span className="micro-label">scorecard AUC</span><b className="mono" data-testid="scorecard-auc">{fmtScore(h.scorecard.auc)}</b><small>Gini {fmtScore(h.scorecard.gini, 3)} · KS {fmtScore(h.scorecard.ks, 3)}</small></div>
        <div className="eh-cell"><span className="micro-label">{h.served.model} AUC</span><b className="mono">{fmtScore(h.served.auc)}</b><small>Gini {fmtScore(h.served.gini, 3)} · KS {fmtScore(h.served.ks, 3)}</small></div>
        <div className="eh-cell"><span className="micro-label">features in the card</span><b className="mono">{t.n_features} / {t.features.length}</b><small>{t.rows.filter((r) => r.in_model).length} bins · fit on {t.n_train.toLocaleString()} training rows</small></div>
        {sc.cost && <div className="eh-cell"><span className="micro-label">cost per 1,000 decisions</span><b className="mono">{sc.cost.scorecard_per_1000.toFixed(0)}</b><small>served {sc.cost.served_per_1000.toFixed(0)} · c_FN = {sc.cost.c_fn}, c_FP = {sc.cost.c_fp}</small></div>}
      </div>
      <p className="helper" style={{ fontSize: 12.5 }} data-testid="scorecard-verdict">
        {significant
          ? <>The ensemble ranks applicants better than the scorecard: the interval for ΔAUC excludes 0 (P(Δ ≤ 0) = {d.p_le_0.toFixed(3)} over the resamples). Its gain is the price of the scorecard's transparency.</>
          : <>The interval for ΔAUC includes 0: on this holdout the ensemble does not rank applicants measurably better than the scorecard (P(Δ ≤ 0) = {d.p_le_0.toFixed(3)}).</>}
        {' '}In the run itself the bagged <span className="mono">{h.scorecard_in_run.model}</span> scored AUC {fmtScore(h.scorecard_in_run.auc)} on the holdout (ΔAUC {sgn(h.delta_auc_in_run.delta)}).
      </p>

      <div className="grid mono-grid">
        <div className="card" data-testid="scorecard-table">
          <div className="card-head"><div className="left"><h3 className="card-title">Scorecard</h3><span className="helper" style={{ fontSize: 12 }}>points per bin; an applicant's score is the sum of the points of their bins</span></div>
            <a className="btn" href={`/api/runs/${encodeURIComponent(run.id)}/scorecard.csv`} download data-testid="scorecard-export">Export CSV ↓</a></div>
          <div className="card-body">
            <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-8)' }}>
              <span className="micro-label">feature</span>
              <select className="select" aria-label="Scorecard feature" value={feature} onChange={(e) => setFeature(e.target.value)} data-testid="scorecard-feature">
                {feats.map((f) => <option key={f.feature} value={f.feature}>{f.feature}{f.in_model ? '' : ' (not in the card)'}</option>)}
              </select>
              {fsel && <span className="helper" style={{ fontSize: 12 }}>IV {fsel.iv.toFixed(3)} ({ivBand(fsel.iv)}) · {fsel.in_model ? <>β = {fsel.coefficient?.toFixed(4)}</> : <>left out: {fsel.dropped}</>}
                {fsel.kind === 'numeric' && fsel.direction ? <> · event rate {fsel.direction > 0 ? 'rises' : 'falls'} with x{fsel.constraint ? ' (F8 constraint)' : ''}</> : null}</span>}
            </div>
            <div style={{ overflowX: 'auto' }}><table className="table compact"><thead><tr><th>bin</th><th className="r">n</th><th className="r">share</th><th className="r">event rate</th><th className="r">WoE</th><th className="r">IV part</th><th className="r">points</th></tr></thead>
              <tbody>{bins.map((b) => <tr key={b.bin}>
                <td className="mono">{b.label}</td><td className="r mono">{b.n.toLocaleString()}</td><td className="r mono">{(b.share * 100).toFixed(1)}%</td>
                <td className="r mono">{b.event_rate == null ? '—' : `${(b.event_rate * 100).toFixed(1)}%`}</td><td className="r mono">{sgn(b.woe, 3)}</td>
                <td className="r mono">{b.iv.toFixed(4)}</td><td className="r mono"><b>{b.points_rounded ?? '—'}</b></td>
              </tr>)}</tbody></table></div>
            <p className="helper" style={{ fontSize: 12 }}>Bins are fitted on the training rows only (in the run, on each fold's training rows). Missing values have their own bin; a value with no bin (an unseen category, or missing when training had none) scores WoE 0.</p>
          </div>
        </div>
        <div className="formula" data-testid="scorecard-formula">
          <div className="micro-label">The scorecard</div>
          <div className="eq">WoE<sub><i>ij</i></sub> = ln <span className="up">(<i>g<sub>i</sub></i> + α) ⁄ (<i>G</i> + <i>k</i>α)</span> − ln <span className="up">(<i>b<sub>i</sub></i> + α) ⁄ (<i>B</i> + <i>k</i>α)</span></div>
          <div className="eq">logit P(<i>y</i> = 1 | <i>x</i>) = β<sub>0</sub> + Σ<sub><i>j</i></sub> β<sub><i>j</i></sub> · WoE<sub><i>j</i></sub>(<i>x<sub>j</sub></i>)</div>
          <div className="eq">score = <i>offset</i> − <i>factor</i> · (β<sub>0</sub> + Σ<sub><i>j</i></sub> β<sub><i>j</i></sub> WoE<sub><i>j</i></sub>) = Σ<sub><i>j</i></sub> points<sub><i>j</i></sub></div>
          <dl className="where">
            <dt>y = 1</dt><dd>the event, <b className="mono">{sc.positive_class}</b> ({(t.event_rate * 100).toFixed(1)}% of training rows)</dd>
            <dt>g<sub>i</sub>, b<sub>i</sub></dt><dd>non-events and events in bin i; G, B their totals; k bins; α = 0.5</dd>
            <dt>β<sub>0</sub></dt><dd><b className="mono">{t.intercept.toFixed(4)}</b>; every β<sub>j</sub> ≤ 0 (more WoE, less risk)</dd>
            <dt>factor</dt><dd>PDO ⁄ ln 2 = {s.pdo} ⁄ 0.6931 = <b className="mono">{s.factor.toFixed(4)}</b></dd>
            <dt>offset</dt><dd>{s.base_score} − factor · ln {s.base_odds} = <b className="mono">{s.offset.toFixed(4)}</b></dd>
            <dt>points<sub>ij</sub></dt><dd>−factor · β<sub>j</sub> · WoE<sub>ij</sub> + (offset − factor · β<sub>0</sub>) ⁄ n, n = {t.n_features}</dd>
          </dl>
          <p className="helper" style={{ fontSize: 12 }}>{s.base_score} points means odds of {s.base_odds} : 1 against the event; every {s.pdo} points doubles them. P(event) = 1 ⁄ (1 + e<sup>(score − offset) ⁄ factor</sup>) is the logistic regression's own probability (largest gap on the holdout {h.proba_check.toExponential(1)}).</p>
          <div className="mono-stats">
            <div><span className="micro-label">score range (holdout)</span><b className="mono">{h.score.min.toFixed(0)} – {h.score.max.toFixed(0)}</b></div>
            <div><span className="micro-label">mean score, events</span><b className="mono">{h.score.mean_event?.toFixed(1) ?? '—'}</b></div>
            <div><span className="micro-label">mean score, non-events</span><b className="mono">{h.score.mean_non_event?.toFixed(1) ?? '—'}</b></div>
          </div>
        </div>
      </div>

      <div className="grid mono-grid">
        <div className="card" data-testid="scorecard-iv">
          <div className="card-head"><div className="left"><h3 className="card-title">Information Value per Feature</h3><span className="helper" style={{ fontSize: 12 }}>IV<sub>j</sub> = Σ<sub>i</sub> (g<sub>i</sub>⁄G − b<sub>i</sub>⁄B) · WoE<sub>ij</sub> · below {sc.params.min_iv} left out</span></div></div>
          <div className="card-body stack" style={{ gap: 6 }}>
            {feats.map((f) => <button key={f.feature} className="row sc-iv" onClick={() => setFeature(f.feature)} title={f.dropped ?? `β = ${f.coefficient?.toFixed(4)}`} style={{ gap: 'var(--space-8)', background: 'none', border: 0, padding: 0, cursor: 'pointer', textAlign: 'left' }}>
              <span className="mono" style={{ width: 150, fontSize: 12, color: f.in_model ? 'var(--text-primary)' : 'var(--text-muted)' }}>{f.feature}</span>
              <span style={{ flex: 1, height: 10, background: 'var(--bg-subtle)', borderRadius: 3 }}><span style={{ display: 'block', height: 10, width: `${(f.iv / maxIv) * 100}%`, borderRadius: 3, background: f.in_model ? 'var(--viz-a)' : 'var(--border-strong, var(--text-muted))' }} /></span>
              <span className="mono" style={{ width: 120, fontSize: 12 }}>{f.iv.toFixed(3)} <span className="helper">{ivBand(f.iv)}</span></span>
            </button>)}
            <p className="helper" style={{ fontSize: 12 }}>Grey: not in the scorecard ({feats.filter((f) => !f.in_model).map((f) => `${f.feature}: ${f.dropped}`).join('; ') || 'none'}).</p>
          </div>
        </div>
        <div className="card" data-testid="scorecard-bands">
          <div className="card-head"><div className="left"><h3 className="card-title">Rank Ordering on the Holdout</h3><span className="helper" style={{ fontSize: 12 }}>event rate in 10 equal-size score bands, lowest score first</span></div></div>
          <div className="card-body stack" style={{ gap: 6 }}>
            {h.bands.map((b, i) => <div key={i} className="row" style={{ gap: 'var(--space-8)' }}>
              <span className="mono" style={{ width: 110, fontSize: 12 }}>{b.lo.toFixed(0)} – {b.hi.toFixed(0)}</span>
              <span style={{ flex: 1, height: 10, background: 'var(--bg-subtle)', borderRadius: 3 }}><span style={{ display: 'block', height: 10, width: `${(b.event_rate / maxBand) * 100}%`, borderRadius: 3, background: 'var(--viz-b)' }} /></span>
              <span className="mono" style={{ width: 120, fontSize: 12 }}>{(b.event_rate * 100).toFixed(1)}% <span className="helper">({b.events}/{b.n})</span></span>
            </div>)}
          </div>
        </div>
      </div>

      <div className="card" data-testid="scorecard-row">
        <div className="card-head"><div className="left"><h3 className="card-title">One Applicant's Points and Reasons</h3><span className="helper" style={{ fontSize: 12 }}>holdout row by position; reason codes rank the points lost against each feature's best bin</span></div>
          <div className="row" style={{ gap: 'var(--space-8)' }}><span className="micro-label">holdout row</span>
            <NumberInput value={row} min={0} max={sc.n_holdout - 1} step={1} onChange={(v) => setRow(Math.round(v))} label="Holdout row" /></div></div>
        <div className="card-body">
          {rowErr && <p className="helper" role="alert">✕ {rowErr}</p>}
          {!r0 && !rowErr && <div className="row"><ResonanceWave /> Scoring…</div>}
          {r0 && <>
            <div className="row" style={{ gap: 'var(--space-12)', flexWrap: 'wrap', marginBottom: 'var(--space-8)' }}>
              <ParamChip sym="score" value={r0.score.toFixed(1)} /><ParamChip sym={`P(${scored?.positive_class})`} value={r0.p_event.toFixed(4)} />
              <ParamChip sym={`served P`} value={r0.served_p_event.toFixed(4)} />
              {r0.served_adverse ? <StatusPill status="danger">served model: adverse at t = {scored?.served_threshold.toFixed(3)}</StatusPill> : <StatusPill status="success">served model: approved at t = {scored?.served_threshold.toFixed(3)}</StatusPill>}
              {r0.truth != null && <span className="helper">truth: <b className="mono">{r0.truth}</b></span>}
            </div>
            <div style={{ overflowX: 'auto' }}><table className="table compact"><thead><tr><th>feature</th><th>value</th><th>bin</th><th className="r">points</th><th className="r">best bin</th><th className="r">points lost</th><th>reason</th></tr></thead>
              <tbody>{[...r0.points].sort((a, b) => b.points_lost - a.points_lost).map((p) => {
                const k = r0.reasons.findIndex((x) => x.feature === p.feature)
                return <tr key={p.feature}>
                  <td className="mono">{p.feature}</td><td className="mono">{p.value == null ? 'missing' : String(p.value)}</td><td className="mono">{p.bin}</td>
                  <td className="r mono">{p.points.toFixed(1)}</td><td className="r mono">{p.max_points.toFixed(1)}</td><td className="r mono">{p.points_lost.toFixed(1)}</td>
                  <td>{k >= 0 ? <b className="mono">#{k + 1}</b> : ''}</td>
                </tr>
              })}</tbody></table></div>
            <p className="helper" style={{ fontSize: 12 }}>Σ points = {r0.points.reduce((a, p) => a + p.points, 0).toFixed(2)} = score. The scorecard's reason codes are the top {scored?.top_k} features by points lost (lost &gt; 0); they explain the scorecard's decision, not the served model's (its reasons are the version's SHAP reason codes in the Registry).</p>
          </>}
        </div>
      </div>
    </div>
  )
}
