import { useCallback, useEffect, useState } from 'react'
import { flows, type AnCompare, type AnReviewSummary, type AnRow, type AnSupervised, type AnTruth, type AnVelocity } from '@/shared/api/flows'
import { useActor } from '@/features/registry/lib'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fix, sig4 } from '@/shared/lib/format'


/** F2 · the velocity features this run added: the formulas bound to the run's entity, time, amount and category
 *  columns, and each column's mean / p99 / max over the rows, with the ones the forest sees marked. */
export function VelocityCard({ v }: { v: AnVelocity }) {
  const a = v.amount_column
  return (
    <div className="formula an-formula" data-testid="an-velocity-card">
      <div className="eq">n<sub>W</sub>(i) = #{'{'}j : e<sub>j</sub> = e<sub>i</sub>, t<sub>i</sub> − W ≤ t<sub>j</sub> &lt; t<sub>i</sub>{'}'}, &nbsp; Σ<sub>W</sub>(i) = Σ x<sub>j</sub> over the same j, &nbsp; ρ<sub>i</sub> = x<sub>i</sub> / x̄<sub>e,&lt;i</sub>, &nbsp; Δ<sub>i</sub> = ln(1 + h<sub>i</sub>)</div>
      <dl className="where">
        <dt>e, t</dt><dd>{v.entity_column}, {v.time_column}: only the entity’s <b>earlier</b> rows count (t<sub>j</sub> &lt; t<sub>i</sub>), so each value is known when the transaction arrives</dd>
        <dt>W</dt><dd>{Object.keys(v.windows_hours).join(' and ')} windows</dd>
        <dt>x</dt><dd>{a}{v.category_column ? <>; k<sub>24h</sub>(i) = distinct {v.category_column} among the same rows of the last 24 h</> : null}</dd>
        <dt>x̄<sub>e,&lt;i</sub></dt><dd>mean of x over all of e’s earlier rows (running mean)</dd>
        <dt>h<sub>i</sub></dt><dd>hours since e’s previous transaction; {v.rows_without_history.toLocaleString()} first rows have none (imputed + a missing flag)</dd>
        <dt>forest</dt><dd>sees one column per signal ({v.in_forest.join(', ')}): each isolation split picks a feature at random, so near-duplicates (Σ<sub>W</sub> ≈ n<sub>W</sub>·x̄, k ≈ n) would multiply one signal’s say. All columns go to the supervised model.</dd>
      </dl>
      <table className="table" style={{ marginTop: 8 }}><thead><tr><th>column</th><th className="r">mean</th><th className="r">p99</th><th className="r">max</th><th>forest</th></tr></thead>
        <tbody>{v.added.map((c) => { const s = v.summary[c]; return <tr key={c}><td className="mono" style={{ fontSize: 11.5 }}>{c}</td><td className="r table-cell">{s ? sig4(s.mean) : '—'}</td><td className="r table-cell">{s ? sig4(s.p99) : '—'}</td><td className="r table-cell">{s ? sig4(s.max) : '—'}</td><td>{v.in_forest.includes(c) ? '✓' : ''}</td></tr> })}</tbody></table>
    </div>
  )
}

/** Confirm (fraud) / dismiss (not fraud) buttons for one row. */
export function ReviewButtons({ row, onReview, size = 'xs' }: { row: AnRow; onReview: (row: number, label: 'fraud' | 'not_fraud' | null) => void; size?: 'xs' | 'sm' }) {
  const l = row.review?.label
  const stop = (e: React.MouseEvent) => e.stopPropagation()
  return (
    <span className="an-review" onClick={stop}>
      <button className={`chip-btn ${size} ${l === 1 ? 'on danger' : ''}`} title="Confirm: fraud" data-testid={`an-confirm-${row.row}`} onClick={() => onReview(row.row, l === 1 ? null : 'fraud')}>{size === 'sm' ? 'Confirm fraud' : '✓'}</button>
      <button className={`chip-btn ${size} ${l === 0 ? 'on' : ''}`} title="Dismiss: not fraud" data-testid={`an-dismiss-${row.row}`} onClick={() => onReview(row.row, l === 0 ? null : 'not_fraud')}>{size === 'sm' ? 'Dismiss' : '✕'}</button>
    </span>
  )
}

/** The review state, "Train supervised" once N confirmed frauds exist, and each supervised run compared with the
 *  isolation forest on the rows nobody reviewed. */
export function SupervisedPanel({ runId, review, go }: { runId: string; review: AnReviewSummary | undefined; go: (r: string) => void }) {
  const [actor] = useActor()
  const [runs, setRuns] = useState<AnSupervised[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [timeLimit, setTimeLimit] = useState(120)
  const [presets, setPresets] = useState('medium_quality')
  const [negatives, setNegatives] = useState<'unlabeled' | 'dismissed_only'>('unlabeled')
  const [withScore, setWithScore] = useState(true)
  const load = useCallback(() => flows.anomaly.supervised(runId).then((r) => setRuns(r.runs)).catch((e) => setErr(e.message)), [runId])
  useEffect(() => { load() }, [load])
  const pending = runs?.some((r) => r.status === 'queued' || r.status === 'running' || (r.status === 'succeeded' && !r.comparison && !r.comparison_error))
  useEffect(() => { if (!pending) return; const t = setInterval(load, 4000); return () => clearInterval(t) }, [pending, load])
  const train = async () => {
    setBusy(true); setErr('')
    try { await flows.anomaly.trainSupervised(runId, { time_limit: timeLimit, presets, negatives, include_score: withScore, by: actor || 'Analyst' }); await load() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const r = review
  return (
    <div className="card" data-testid="an-supervised">
      <div className="card-head"><div className="left"><h3 className="card-title">Review → supervised model</h3>
        {r && <span className="helper" style={{ fontSize: 12 }}><b>{r.confirmed}</b> confirmed fraud · <b>{r.dismissed}</b> dismissed · labels are kept with the dataset and audited{actor ? ` (acting as ${actor})` : ''}</span>}</div></div>
      <div className="card-body stack" style={{ gap: 12 }}>
        <div className="formula">
          <div className="eq" style={{ fontSize: 16 }}>ŷ = 1 for confirmed, 0 for {negatives === 'unlabeled' ? 'dismissed and unreviewed' : 'dismissed'} rows; &nbsp; P(ŷ = 1 | x) = c · P(fraud | x)</div>
          <dl className="where">
            <dt>ŷ</dt><dd>the analyst’s label: “Confirm” = 1, “Dismiss” = 0{negatives === 'unlabeled' ? '; rows nobody reviewed count as 0 (positive-unlabeled learning)' : '; unreviewed rows are left out of training'}</dd>
            <dt>c</dt><dd>the share of frauds that get confirmed. If confirmations are a random sample of frauds (SCAR), c is a constant, so ranking by P(ŷ = 1 | x) ranks by P(fraud | x)</dd>
            <dt>x</dt><dd>the row’s columns, the group-relative and velocity features{withScore ? ', and the isolation score s (and LOF)' : ''}; AutoGluon binary, bagged so every training row gets an out-of-fold score</dd>
          </dl>
        </div>
        {r && <div className="an-progress"><span className="bar"><i style={{ width: `${Math.min(100, (100 * r.confirmed) / r.min_positives)}%` }} /></span>
          <span className="mono">{r.confirmed} / {r.min_positives} confirmed</span>
          <span className="helper" style={{ fontSize: 12 }}>{r.can_train ? 'enough to train' : `confirm ${r.needed} more to train`}</span></div>}
        <details className="an-adv"><summary className="micro-label">Advanced</summary>
          <div className="row" style={{ flexWrap: 'wrap', gap: 16, marginTop: 8 }}>
            <label className="col"><span className="micro-label">time_limit (s)</span><input className="num-input" type="number" min={10} value={timeLimit} onChange={(e) => setTimeLimit(Math.max(10, Number(e.target.value)))} /></label>
            <label className="col"><span className="micro-label">presets</span><select className="select sm" value={presets} onChange={(e) => setPresets(e.target.value)}>{['medium_quality', 'good_quality', 'high_quality', 'best_quality'].map((p) => <option key={p}>{p}</option>)}</select></label>
            <label className="col"><span className="micro-label">negatives</span><select className="select sm" value={negatives} onChange={(e) => setNegatives(e.target.value as 'unlabeled')}><option value="unlabeled">unreviewed = 0 (PU)</option><option value="dismissed_only">dismissed only</option></select></label>
            <label className="row" style={{ marginTop: 16 }}><input type="checkbox" checked={withScore} onChange={(e) => setWithScore(e.target.checked)} /> anomaly score as a feature</label>
          </div>
          <p className="helper" style={{ fontSize: 12 }}>eval_metric average_precision · num_bag_folds 5 · a new dataset version holds the labels (stratified holdout and folds on ŷ).</p>
        </details>
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        <div className="row"><span className="spacer" />
          <button className="btn primary" disabled={busy || !r?.can_train} onClick={train} data-testid="an-train-supervised">{busy ? 'Starting…' : 'Train supervised'}</button></div>
        {runs == null ? <div className="row"><ResonanceWave /> Reading supervised runs…</div> : runs.map((s) => <SupervisedRun key={s.run_id} s={s} go={go} />)}
      </div>
    </div>
  )
}

function SupervisedRun({ s, go }: { s: AnSupervised; go: (r: string) => void }) {
  const c = s.comparison
  return (
    <div className="an-sup" data-testid="an-sup-run">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <b className="mono">{s.run_id}</b>
        <StatusPill status={s.status === 'succeeded' ? 'success' : s.status === 'failed' ? 'danger' : 'info'}>{s.status}</StatusPill>
        <span className="helper" style={{ fontSize: 12 }}>{s.confirmed} confirmed · {s.dismissed} dismissed · {s.unreviewed.toLocaleString()} unreviewed{s.negatives === 'unlabeled' ? ' (as 0)' : ' (left out)'} · by {s.by}{s.best_model ? ` · best ${s.best_model}` : ''}</span>
        <span className="spacer" />
        {s.status === 'succeeded' && <button className="chip-btn" onClick={() => go(`models?run=${s.run_id}&tab=Explain`)}>Explain →</button>}
        <button className="chip-btn" onClick={() => go(`models?run=${s.run_id}`)}>Open run</button>
      </div>
      {s.error && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {s.error}</p>}
      {s.status !== 'succeeded' && !s.error && <div className="row"><ResonanceWave /> AutoGluon is fitting…</div>}
      {s.comparison_error && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {s.comparison_error}</p>}
      {c && <Comparison c={c} />}
    </div>
  )
}

function Comparison({ c }: { c: AnCompare }) {
  if (!c.iforest || !c.supervised) return <p className="helper" style={{ fontSize: 12 }}>Scored {c.rows_scored.toLocaleString()} rows out of sample ({c.source}); {c.overlap_top_k} of the top {c.k} unreviewed rows are shared with the isolation forest. No hidden truth for this dataset: review the new top rows.</p>
  const k = c.hidden_in_unreviewed ?? 0
  const at = (t: AnTruth | undefined, kk: number) => t?.precision_at_k.find((x) => x.k === kk)
  const rows: { name: string; f: (t: AnTruth | undefined, t2: AnTruth | undefined) => string }[] = [
    { name: `hits in the top ${k} (R-precision)`, f: (t) => `${at(t, k)?.hits ?? '—'} / ${k}` },
    { name: `hits in the top ${c.k}`, f: (_t, t2) => `${at(t2, c.k)?.hits ?? '—'} / ${k}` },
    { name: 'average precision', f: (t) => fix(t?.average_precision) },
    { name: 'ROC AUC', f: (t) => fix(t?.roc_auc) },
  ]
  const kinds = Object.keys(c.supervised_at_k?.by_kind ?? {})
  return (
    <div className="stack" style={{ gap: 8 }} data-testid="an-compare">
      <p className="helper" style={{ fontSize: 12 }}>On the <b>{c.unreviewed.toLocaleString()}</b> rows nobody reviewed, which still hide <b>{k}</b> planted frauds (sample only: the hidden truth is never given to either model). Supervised scores are out of sample: {c.source} ({c.n_oof.toLocaleString()} out-of-fold).</p>
      <table className="table"><thead><tr><th>on unreviewed rows</th><th className="r">IsolationForest s</th><th className="r">supervised P(ŷ = 1 | x)</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.name}><td>{r.name}</td><td className="r table-cell">{r.f(c.iforest, c.iforest_at_k)}</td><td className="r table-cell"><b>{r.f(c.supervised, c.supervised_at_k)}</b></td></tr>)}
          {kinds.map((kd) => <tr key={kd}><td className="mono" style={{ fontSize: 11.5 }}>· {kd} in the top {c.k}</td><td className="r table-cell">{c.iforest_at_k?.by_kind[kd]?.found ?? 0} / {c.iforest_at_k?.by_kind[kd]?.planted ?? 0}</td><td className="r table-cell">{c.supervised_at_k?.by_kind[kd]?.found ?? 0} / {c.supervised_at_k?.by_kind[kd]?.planted ?? 0}</td></tr>)}</tbody></table>
      <p className="helper" style={{ fontSize: 12 }}>The holdout score of this run (predicting ŷ) is near perfect by construction: the reviewed rows were chosen by s, so s nearly determines ŷ. That is selection, not leakage of fraud; the fair test is this table, on rows nobody reviewed. {c.overlap_top_k} of the {c.k} top unreviewed rows are shared. The supervised model learns what the analyst confirmed, so it is strongest on kinds that were confirmed often and cannot learn a kind nobody confirmed.</p>
    </div>
  )
}
