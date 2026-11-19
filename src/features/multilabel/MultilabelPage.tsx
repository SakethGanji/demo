import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type MlDetect, type MlResult, type MlTry } from '@/shared/api/flows'
import type { ModelVersion } from '@/shared/api/types'
import { Register } from '@/shared/components/FlowParts'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ApiOnly, ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { fix, pct } from '@/shared/lib/format'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import '@/features/expected-loss/expected-loss.css'
import '@/features/segmented/segmented.css'

const COLORS = ['var(--viz-a)', 'var(--viz-b)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-e)', 'var(--status-danger)', 'var(--accent-primary)', 'var(--plot-axis)']
const color = (i: number) => COLORS[i % COLORS.length]
const ci = (c: [number, number] | undefined, d = 3) => (c ? `[${fix(c[0], d)}, ${fix(c[1], d)}]` : '')

/**
 * G8 · Multi-label, a choice inside Build. Without ?run: the setup, pre-filled from the G0 roles (the label set, the text
 * columns, what to leave out, the split), one primary action. With ?run: one AutoGluon model per label on the same holdout:
 * F1 per label at its own threshold, micro/macro F1, Hamming loss, PR curves. Registry → Labels shows the same view; Try It
 * returns a label set.
 */
export function MultilabelPage({ go }: { go: (r: string) => void }) {
  const runId = useHashParam('run')
  return (
    <ApiOnly mock={(
      <div className="page"><Head />
        <div className="card card-body"><p className="helper">▲ mock mode: multi-label models train on the API, which is offline. Start it with <span className="mono">make dev</span>.</p></div></div>
    )}>
      {runId ? <RunView key={runId} runId={runId} go={go} /> : <Setup go={go} />}
    </ApiOnly>
  )
}

function Head({ right }: { right?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div><h1 className="page-title">Multi-Label Classifier</h1><p className="helper sub">Each row can carry several labels at once (complaint topics, say): one model per label, a label set per row.</p></div>
      {right}
    </div>
  )
}

function Formula() {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="ml-formula">
        Ŷ(x) = {'{'} l : p̂<sub>l</sub>(x) ≥ t<sub>l</sub> {'}'} &nbsp;·&nbsp; F1<sub>l</sub> = 2TP<sub>l</sub> / (2TP<sub>l</sub> + FP<sub>l</sub> + FN<sub>l</sub>) &nbsp;·&nbsp; macro F1 = (1/L) Σ<sub>l</sub> F1<sub>l</sub> &nbsp;·&nbsp; Hamming = (1/nL) Σ<sub>i,l</sub> [Ŷ<sub>il</sub> ≠ Y<sub>il</sub>]
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------------ setup

function Setup({ go }: { go: (r: string) => void }) {
  const dsId = useHashParam('dataset') ?? 'complaint-topics'
  const det = useFetch(() => flows.ml.detect(dsId), [dsId])
  const ds = useFetch(() => api.datasets.get(dsId), [dsId])
  const [col, setCol] = useState<string>('')
  const [secs, setSecs] = useState(15)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (det.data) setCol(det.data.label_column ?? '') }, [det.data])
  if (det.err || ds.err) return <div className="page"><Head /><ErrorCard err={det.err || ds.err} /></div>
  if (!det.data || !ds.data) return <div className="page"><Head /><Loading>Reading the column roles…</Loading></div>
  const d: MlDetect = det.data
  const own = col && col !== d.label_column
  const options = Array.from(new Set([...(d.candidates ?? []), ...ds.data.profile.columns.filter((c) => c.type !== 'number' && c.type !== 'id').map((c) => c.name)]))
  const start = async () => {
    setBusy(true); setErr('')
    try {
      const body = col.endsWith('_*') ? { dataset_id: dsId, label_columns: d.label_columns } : { dataset_id: dsId, label_column: col }
      const r = await flows.ml.create({ ...body, drop_columns: d.drop_columns.filter((c) => c !== col), time_limit: secs })
      go(`multilabel?run=${r.id}`)
    } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  return (
    <div className="page" data-testid="ml-setup">
      <Head />
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">What we use</h3><span className="helper" style={{ fontSize: 12 }}>{ds.data.name} · {ds.data.rows.toLocaleString()} rows · detected from the column roles; change any of them</span></div>
          <button className="btn primary" disabled={!col || busy} onClick={start} data-testid="ml-start">{busy ? 'Starting…' : `Train ${own ? '' : `${d.labels.length} `}label models →`}</button></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <Formula />
          <div className="table-wrap"><table className="table compact" data-testid="ml-detected">
            <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
            <tbody>
              <tr><td>label set Y</td>
                <td><select className="input mono" value={col} onChange={(e) => setCol(e.target.value)} data-testid="ml-label">
                  {!col && <option value="">—</option>}{options.map((c) => <option key={c}>{c}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12 }}>{own ? 'set by you: each value is split into labels on ; | or ,' : d.source ? d.evidence.join(' · ') : 'nothing detected: pick a column of label lists'}</td></tr>
              {!own && d.counts && <tr><td>labels</td><td colSpan={2}><div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {d.counts.map((c) => <span key={c.label} className="chip" title={`${c.n} rows`}>{c.label} <span className="helper">{pct(c.share)}</span></span>)}</div>
                <span className="helper" style={{ fontSize: 12 }}>{pct(d.multi_share ?? 0)} of rows carry two or more · {fix(d.mean_labels ?? 0, 2)} labels per row</span></td></tr>}
              <tr><td>read as text</td><td className="mono" colSpan={2}>{d.text_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· declared text once, for every label's model (n-grams + TF-IDF models)</span></td></tr>
              <tr><td>left out</td><td className="mono" colSpan={2}>{d.drop_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· IDs, dates, leaks · {d.split_why}</span></td></tr>
            </tbody>
          </table></div>
          <details className="an-adv">
            <summary className="micro-label">Advanced · {secs} s per label</summary>
            <div className="row" style={{ gap: 8, padding: 'var(--space-8) 0' }}>
              <label className="helper">seconds per label model</label>
              <input className="input mono" type="number" min={5} max={600} value={secs} onChange={(e) => setSecs(Number(e.target.value) || 15)} style={{ width: 90 }} data-testid="ml-secs" />
              <span className="helper" style={{ fontSize: 12 }}>AutoGluon medium_quality, log loss, the same config for every label.</span>
            </div>
          </details>
          <p className="helper" style={{ fontSize: 12.5 }}>Each label gets its own AutoGluon yes/no model on the same rows and inputs, and its own threshold t<sub>l</sub>, the one with the best F1 on that model's validation rows. A row's prediction is every label whose p̂ clears its threshold, so it can be one label, several or none.</p>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------------------------------------- run page

function RunView({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const r = useFetch(() => flows.ml.get(runId), [runId, live.done], { pollMs: 3000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <div className="page"><Head /><ErrorCard err={r.err} /></div>
  if (!r.data) return <div className="page"><Head /><Loading>Loading the run…</Loading></div>
  const run = r.data
  const labels = run.config.labels_spec?.labels ?? []
  const done = new Set(live.events.filter((e) => e.type === 'ml_model_finished').map((e) => e.label as string))
  const on = new Set(live.events.filter((e) => e.type === 'ml_model_started').map((e) => e.label as string))
  return (
    <div className="page" data-testid="ml-run" data-status={run.status}>
      <Head right={run.status === 'succeeded' ? <Register runId={runId} go={go} name={`${run.dataset_id}.multilabel`} /> : undefined} />
      <p className="helper mono" style={{ fontSize: 12, marginTop: -8 }}>{run.name} · {runId} · Y = {run.config.label}</p>
      {run.status === 'failed' && <ErrorCard err={run.error ?? 'The run failed.'} />}
      {(run.status === 'queued' || run.status === 'running') && (
        <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
          <div className="row"><ResonanceWave /> <span>Fitting one model per label, then the holdout checks · {live.stage}</span></div>
          <div className="el-steps">{labels.map((l) => (
            <div key={l} className={`el-step ${done.has(l) ? 'done' : on.has(l) ? 'on' : ''}`}><b>{l}</b><span className="mono">{done.has(l) ? '✓' : on.has(l) ? '…' : ''}</span></div>
          ))}</div>
        </div></div>
      )}
      {run.status === 'succeeded' && run.multilabel && <MlResults m={run.multilabel} />}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- results (shared)

/** The holdout checks: used by the run page and by Registry → Labels. */
export function MlResults({ m }: { m: MlResult }) {
  const [sel, setSel] = useState<string | null>(null)
  const per = m.per_label
  // zoom to where the curves bend: below the lowest recall any curve reaches with precision < 1, nothing moves
  const lo = (v: number) => Math.max(0, Math.floor(v * 4) / 4)
  const xMin = lo(Math.min(...per.flatMap((p) => p.pr.recall.filter((_, i) => p.pr.precision[i] < 0.999)), 0.75) - 0.05)
  const yMin = lo(Math.min(...per.flatMap((p) => p.pr.precision)))
  const series: XYSeries[] = per.filter((p) => !sel || p.label === sel).map((p) => ({
    name: p.label, color: color(per.indexOf(p)), width: sel ? 2.5 : 1.6,
    points: p.pr.recall.map((r, i) => [Math.max(r, xMin), p.pr.precision[i]] as [number, number]),
    tip: (i: number) => `${p.label} · t = ${fix(p.pr.threshold[i], 3)} · precision ${fix(p.pr.precision[i], 3)} · recall ${fix(p.pr.recall[i], 3)}`,
  }))
  const marks = per.filter((p) => (!sel || p.label === sel) && p.precision != null && p.recall != null)
    .map((p) => ({ x: p.recall as number, y: p.precision as number, label: sel ? `t = ${fix(p.threshold, 2)}` : '', color: color(per.indexOf(p)) }))
  const worst = per.reduce((a, b) => (b.f1 < a.f1 ? b : a), per[0])
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="ml-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">On the Holdout</h3><span className="helper" style={{ fontSize: 12 }}>{m.holdout_rows.toLocaleString()} rows no model saw · {m.labels.length} labels · thresholds: {m.threshold_rule}</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="ml-hero">
          <HeroCell label="macro F1" sub={`95% CI ${ci(m.ci.macro_f1)} · every label counts the same · weakest ${worst.label} ${fix(worst.f1, 3)}`}>{fix(m.macro_f1, 3)}</HeroCell>
          <HeroCell label="micro F1" sub={`95% CI ${ci(m.ci.micro_f1)} · every (row, label) decision counts the same`}>{fix(m.micro_f1, 3)}</HeroCell>
          <HeroCell label="Hamming loss" sub={`95% CI ${ci(m.ci.hamming_loss, 4)} · predicting no labels: ${fix(m.baseline_hamming, 3)}`}>{fix(m.hamming_loss, 4)}</HeroCell>
          <HeroCell label="exact label set" sub={`share of rows with Ŷ = Y · Jaccard ${fix(m.jaccard, 3)}`}>{pct(m.subset_accuracy)}</HeroCell>
          <HeroCell label="labels per row" sub={`true ${fix(m.cardinality.true, 2)} · ${pct(m.cardinality.multi_share_pred)} predicted multi-label · ${pct(m.cardinality.empty_pred)} none`}>{fix(m.cardinality.predicted, 2)}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula />
          <p className="helper" style={{ fontSize: 12.5 }}>
            Y is the true label set of a row ({m.source === 'delimited' ? <>the labels in <span className="mono">{m.label_column}</span>, split on '{m.delimiter}'</> : <>the 0/1 columns <span className="mono">{m.label_columns.join(', ')}</span></>}); L = {m.labels.length} labels; n = {m.holdout_rows.toLocaleString()} holdout rows.
            {' '}p̂<sub>l</sub>(x) is label l's own AutoGluon model's probability that l applies; t<sub>l</sub> its threshold (the best F1 on that model's validation rows, never the holdout).
            {' '}TP<sub>l</sub>, FP<sub>l</sub>, FN<sub>l</sub> count rows where l is in both Ŷ and Y, only in Ŷ, only in Y; micro F1 pools them over labels first.
            {' '}CIs resample holdout rows (300 draws). At t = 0.5 for every label instead, macro F1 would be {fix(m.at_05.macro_f1, 3)}.
          </p>
        </div>
      </div>
      <div className="card" data-testid="ml-per-label">
        <div className="card-head"><div className="left"><h3 className="card-title">Per Label</h3><span className="helper" style={{ fontSize: 12 }}>F1 at the label's own threshold [95% CI] · AP: area under its precision–recall curve, at every threshold · click a row to see its curve</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>label</th><th className="r">support</th><th className="r">t<sub>l</sub></th><th className="r">precision</th><th className="r">recall</th><th className="r">F1 [95% CI]</th><th className="r">AP</th><th className="r">F1 at 0.5</th></tr></thead>
          <tbody>{per.map((p, i) => (
            <tr key={p.label} data-testid="ml-label-row" onClick={() => setSel(sel === p.label ? null : p.label)} style={{ cursor: 'pointer', background: sel === p.label ? 'var(--bg-subtle)' : undefined }}>
              <td><b style={{ color: color(i) }}>●</b> {p.label}</td><td className="r mono">{p.support} <span className="helper">{pct(p.prevalence)}</span></td>
              <td className="r mono">{fix(p.threshold, 3)}</td><td className="r mono">{fix(p.precision, 3)}</td><td className="r mono">{fix(p.recall, 3)}</td>
              <td className="r mono">{fix(p.f1, 3)} <span className="helper">{ci(p.f1_ci)}</span></td>
              <td className="r mono">{fix(p.pr.ap, 3)}</td><td className="r mono">{fix(p.f1_at_05, 3)}</td></tr>))}
          </tbody>
        </table></div>
        {m.skipped.length > 0 && <div className="card-body"><p className="helper el-bad" style={{ fontSize: 12 }}>Not modelled: {m.skipped.map((s) => `${s.label} (${s.why})`).join('; ')}.</p></div>}
      </div>
      <PlotFrame title="Precision–Recall per Label" caption={<>each label's precision against recall as its threshold moves; the dot is where t<sub>l</sub> puts it on the holdout{sel ? ` · ${sel} only (click the row again for all)` : ''}</>}
        legend={per.map((p, i) => ({ name: p.label, color: color(i), data: [] }))}>
        <XYPlot testId="ml-pr" height={260} series={series} marks={marks}
          x={{ min: xMin, max: 1, ticks: niceTicks(xMin, 1, 5), title: 'recall', fmt: (v) => v.toFixed(2) }}
          y={{ min: yMin, max: 1, ticks: niceTicks(yMin, 1, 5), title: 'precision', fmt: (v) => v.toFixed(2) }} />
      </PlotFrame>
    </div>
  )
}

/** Registry → Labels: the version's run results (same view as the run page). */
export function MlVersionTab({ runId }: { runId: string }) {
  const r = useFetch(() => flows.ml.get(runId), [runId])
  if (r.err) return <ErrorCard err={r.err} />
  if (!r.data?.multilabel) return <Loading>Loading the label checks…</Loading>
  return <MlResults m={r.data.multilabel} />
}

// ----------------------------------------------------------------------------------------------- Registry · Try It

/** Try It for a multi-label version: one row's inputs → p̂ per label against its threshold, and the label set. */
export function MlTryIt({ v }: { v: ModelVersion }) {
  const r = useFetch(() => flows.ml.get(v.run_id), [v.run_id])
  const [row, setRow] = useState<Record<string, unknown> | null>(null)
  const [truth, setTruth] = useState<string[] | null>(null)
  const [out, setOut] = useState<MlTry | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const m = r.data?.multilabel
  useEffect(() => { if (m && !row) { setRow(m.examples[0].row); setTruth(m.examples[0].true) } }, [m, row])
  if (r.err) return <ErrorCard err={r.err} />
  if (!m || !row) return <Loading>Loading the inputs…</Loading>
  const predict = async (x = row) => {
    setBusy(true); setErr('')
    try { setOut(await flows.ml.tryIt(v.id, x)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const inputs = Object.keys(m.examples[0].row)
  const text = inputs.filter((c) => m.text_columns.includes(c))
  return (
    <div className="card" data-testid="ml-try">
      <div className="card-head"><div className="left"><h3 className="card-title">Try It · One Row's Label Set</h3>
        <span className="helper" style={{ fontSize: 12 }}>start from a holdout row:</span>
        {m.examples.map((x, i) => <button key={i} className="btn sm" title={`true: ${x.true.join(', ')}`} onClick={() => { setRow(x.row); setTruth(x.true); predict(x.row) }}>{x.true.length > 1 ? `${x.true.length} labels` : x.true[0] ?? 'none'}</button>)}</div>
        <button className="btn primary" disabled={busy} onClick={() => predict()} data-testid="ml-try-predict">{busy ? 'Scoring…' : 'Predict labels'}</button></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {text.map((c) => (
          <label key={c} className="stack" style={{ gap: 2 }}><span className="micro-label">{c}</span>
            <textarea className="input" rows={3} value={row[c] == null ? '' : String(row[c])} onChange={(e) => { setRow({ ...row, [c]: e.target.value }); setTruth(null) }} data-testid="ml-try-text" /></label>
        ))}
        <div className="tp-form" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 8 }}>
          {inputs.filter((c) => !text.includes(c)).map((c) => (
            <label key={c} className="stack" style={{ gap: 2 }}><span className="micro-label">{c}</span>
              <input className="input mono" value={row[c] == null ? '' : String(row[c])} onChange={(e) => { setRow({ ...row, [c]: typeof m.examples[0].row[c] === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value }); setTruth(null) }} /></label>
          ))}
        </div>
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        {out && <>
          <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }} data-testid="ml-try-hero">
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="micro-label" style={{ color: 'inherit' }}>Ŷ = {out.n_labels} of {out.scores.length} labels</span>
              {out.labels.map((l) => <span key={l} className="chip" data-testid="ml-try-label">{l}</span>)}
              {!out.labels.length && <span className="chip">none{out.nearest_miss ? ` · closest ${out.nearest_miss.label} ${fix(out.nearest_miss.p, 2)} < t ${fix(out.nearest_miss.t, 2)}` : ''}</span>}
              {truth && <span className="helper" style={{ fontSize: 12, color: 'inherit', opacity: 0.8 }}>true labels of this holdout row: {truth.join(', ') || 'none'}</span>}
            </div>
          </div>
          <div className="table-wrap"><table className="table compact" data-testid="ml-try-scores">
            <thead><tr><th>label</th><th className="r">p̂</th><th className="r">t</th><th style={{ width: '40%' }}>p̂ against t</th><th>in Ŷ</th></tr></thead>
            <tbody>{out.scores.map((s) => (
              <tr key={s.label}><td className="mono">{s.label}</td><td className="r mono">{fix(s.p, 3)}</td><td className="r mono">{fix(s.t, 3)}</td>
                <td><div className="seg-ci"><i className="zero" style={{ left: `${s.t * 100}%` }} /><i className="bar" style={{ left: 0, width: `${s.p * 100}%`, opacity: s.on ? 1 : 0.35 }} /></div></td>
                <td className={s.on ? 'el-ok' : 'helper'}>{s.on ? '✓' : '—'}</td></tr>))}
            </tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12 }}>Each label has its own model and its own threshold t (the tick); a label is in the set when p̂ ≥ t. The same numbers come from <span className="mono">POST /api/registry/versions/{v.id}/score</span> and batch scoring (columns labels, n_labels, p_&lt;label&gt;). {out.predict_ms.toFixed(0)} ms.</p>
        </>}
      </div>
    </div>
  )
}
