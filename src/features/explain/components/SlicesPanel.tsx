import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run, SliceRequest, SliceRows, SliceSearch, SliceShap, WeakSlice } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const f3 = (v: number | null | undefined) => (v == null ? '—' : fmtScore(v, 3))
const pct = (v: number) => `${(v * 100).toFixed(1)}%`
const pfmt = (p: number) => (p < 0.001 ? p.toExponential(1) : p.toFixed(3))
const LOSSES: Record<string, string> = { auto: 'the run’s metric (auto)', error: '0/1 error', log_loss: 'log loss', absolute_error: 'absolute error', squared_error: 'squared error', cost: 'business cost (F7)', pinball: 'pinball (F9)' }

export type SliceLinks = {
  /** Open one holdout row in Features & SHAP. */
  onRow: (row: number) => void
  /** Open the Labelling Queue pre-filtered to the slice. */
  onLabel: (s: WeakSlice) => void
  /** Open Fairness grouped by the column. */
  onFairness: (column: string) => void
}

/** F16: weak-slice discovery. Every feature value and pair of values on the holdout, numbers in quantile bins, tested
 *  against the rest with Welch's t, Benjamini–Hochberg over all of them, ranked by the lower bound of the effect.
 *  Every number is GET /api/runs/{id}/slices. */
export function SlicesPanel({ run, links }: { run: Run; links: SliceLinks }) {
  const [res, setRes] = useState<SliceSearch | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [body, setBody] = useState<SliceRequest>({})

  useEffect(() => {
    let alive = true
    setRes(null); setErr('')
    api.runs.slices(run.id).then((r) => { if (alive) { setRes(r); setSel(r.slices[0]?.id ?? null) } }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, run.threshold?.value])
  const search = async () => {
    setBusy(true); setErr('')
    try { const r = await api.runs.searchSlices(run.id, { ...(res?.request ?? {}), ...body }); setRes(r); setSel(r.slices[0]?.id ?? null) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  if (err && !res) return <div className="card card-body" role="alert" data-testid="slices-error"><p className="helper">✕ {err}</p></div>
  if (!res) return <div className="card card-body row"><ResonanceWave /> Searching the holdout for weak slices…</div>
  const s = res.slices.find((x) => x.id === sel) ?? null
  const req = { ...res.params, loss: res.request.loss, ...body }
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="slices-panel" data-weak={res.n_weak ?? res.slices.length} data-tested={res.tested.total}>
      {err && <div className="card card-body" role="alert"><p className="helper">✕ {err}</p></div>}
      <Hero r={res} />
      <Table r={res} sel={sel} onPick={setSel} links={links} />
      {s && <Detail key={s.id} run={run} r={res} s={s} links={links} />}
      {res.slices[0] && <Formula r={res} s={s ?? res.slices[0]} />}
      <div className="card" data-testid="slices-searched">
        <div className="card-head"><div className="left"><h3 className="card-title">What Was Searched</h3>
          <span className="helper" style={{ fontSize: 12 }}>{res.tested.features} features, {res.tested.single} single-value slices and {res.tested.pairs.toLocaleString()} pairs with at least {res.params.min_support} rows ({res.seconds.toFixed(2)} s)</span></div></div>
        <div className="card-body stack" style={{ gap: 8 }}>
          <table className="table compact"><thead><tr><th>feature</th><th>slices</th><th>levels (rows on the holdout)</th></tr></thead>
            <tbody>{res.columns.map((c) => <tr key={c.column}><td className="mono">{c.column}</td><td className="muted">{c.how}</td>
              <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{c.levels.map((l, i) => `${l.replace(`${c.column} `, '').replace(c.column, '·')} (${c.counts[i]})`).join(' · ')}</td></tr>)}</tbody></table>
          {res.skipped.length > 0 && <p className="helper" style={{ fontSize: 12 }}>Left out: {res.skipped.map((x) => <span key={x.column}><span className="mono">{x.column}</span> ({x.reason}) </span>)}</p>}
          <details className="an-adv" data-testid="slices-advanced"><summary className="micro-label">Advanced · support, bins, α, pairs, loss</summary>
            <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">min support (rows)</span>
                <NumberInput value={req.min_support ?? 30} min={5} max={10000} step={5} onChange={(v) => setBody({ ...body, min_support: Math.round(v) })} label="Minimum support" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">bins per number</span>
                <NumberInput value={req.bins ?? 5} min={2} max={50} step={1} onChange={(v) => setBody({ ...body, bins: Math.round(v) })} label="Bins" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">FDR α</span>
                <NumberInput value={req.alpha ?? 0.05} min={0.001} max={0.5} step={0.01} onChange={(v) => setBody({ ...body, alpha: v })} label="Alpha" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">category values kept</span>
                <NumberInput value={req.max_levels ?? 30} min={2} max={500} step={1} onChange={(v) => setBody({ ...body, max_levels: Math.round(v) })} label="Max levels" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">null shuffles</span>
                <NumberInput value={req.null_permutations ?? 5} min={0} max={200} step={1} onChange={(v) => setBody({ ...body, null_permutations: Math.round(v) })} label="Null permutations" /></label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">loss ℓ</span>
                <select className="select" value={req.loss ?? 'auto'} onChange={(e) => setBody({ ...body, loss: e.target.value as SliceRequest['loss'] })} aria-label="Loss">
                  {Object.entries(LOSSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
              <label className="row" style={{ gap: 6, alignSelf: 'end' }}><input type="checkbox" checked={req.pairs ?? true} onChange={(e) => setBody({ ...body, pairs: e.target.checked })} /> <span className="micro-label">pairs of features</span></label>
            </div>
          </details>
          <div className="row"><button className="btn" onClick={search} disabled={busy} data-testid="slices-search">{busy ? 'Searching…' : 'Search again'}</button></div>
        </div>
      </div>
    </div>
  )
}

function Hero({ r }: { r: SliceSearch }) {
  const top = r.slices[0]
  const weak = r.n_weak ?? r.slices.length
  return <>
    <div className="card navy explain-hero" data-testid="slices-hero">
      <div className="eh-cell lead"><span className="micro-label">weak slices</span><b className="mono" data-testid="slices-weak">{weak}</b><small>of m = {r.tested.total.toLocaleString()} slices tested, q ≤ {r.params.alpha}</small></div>
      <div className="eh-cell"><span className="micro-label">worst slice</span><b style={{ fontSize: top && top.label.length > 22 ? 15 : 18, overflowWrap: 'anywhere' }} data-testid="slices-top">{top ? top.label : '—'}</b><small>{top ? <>n = {top.n} · Δ = +{f3(top.delta)} [{f3(top.ci[0])}, {f3(top.ci[1])}]</> : 'nothing significantly worse than the rest'}</small></div>
      <div className="eh-cell"><span className="micro-label">overall ℓ̄</span><b className="mono">{f3(r.overall)}</b><small>{r.loss.unit} over n = {r.n.toLocaleString()} holdout rows</small></div>
      {r.null && <div className="eh-cell"><span className="micro-label">permutation null</span><b className="mono">{r.null.mean?.toFixed(1)}</b><small>weak slices found with ℓ shuffled ({r.null.weak_found.join(', ')}; R = {r.null.permutations})</small></div>}
    </div>
    <p className="helper" style={{ fontSize: 12.5 }}>{weak
      ? <><StatusPill status="warning">{weak} weak</StatusPill>{' '}{r.n_significant} slices are significantly worse than the rest after Benjamini–Hochberg; {r.n_explained_by_parent} of them are pairs no worse than one of their single-feature parents and are folded into it.</>
      : <><StatusPill status="success">none</StatusPill>{' '}No slice of at least {r.params.min_support} rows is significantly worse than the rest after Benjamini–Hochberg.</>}</p>
  </>
}

function Table({ r, sel, onPick, links }: { r: SliceSearch; sel: string | null; onPick: (id: string) => void; links: SliceLinks }) {
  const hi = Math.max(1e-9, ...r.slices.map((s) => s.ci[1]))
  const lo = Math.min(0, ...r.slices.map((s) => s.ci[0]))
  const X = (v: number) => 2 + ((v - lo) / (hi - lo)) * 156
  if (!r.slices.length) return null
  return (
    <div className="card" data-testid="slices-table">
      <div className="card-head"><div className="left"><h3 className="card-title">Where the Model Is Worse</h3>
        <span className="helper" style={{ fontSize: 12 }}>ranked by the lower 95% bound of Δ = ℓ̄<sub>S</sub> − ℓ̄<sub>C</sub>: at least this much worse than the rest; ℓ = {r.loss.unit}</span></div></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact"><thead><tr><th className="r">#</th><th>slice S</th><th className="r">n<sub>S</sub></th><th className="r">ℓ̄<sub>S</sub></th><th className="r">ℓ̄<sub>C</sub></th><th className="r">Δ</th><th style={{ width: 160 }}>95% interval of Δ (dashed: 0)</th><th className="r">q</th><th>open</th></tr></thead>
          <tbody>{r.slices.map((s) => (
            <tr key={s.id} data-testid={`slice-${s.rank}`} data-id={s.id} onClick={() => onPick(s.id)} style={{ cursor: 'pointer', background: s.id === sel ? 'var(--surface-sunken, rgba(0,0,0,0.04))' : undefined }}>
              <td className="r mono">{s.rank}</td>
              <td className="mono" style={{ whiteSpace: 'normal', maxWidth: 320 }}>{s.label}</td>
              <td className="r mono">{s.n} <span className="muted" style={{ fontSize: 11 }}>{pct(s.share)}</span></td>
              <td className="r mono"><b>{f3(s.mean)}</b></td><td className="r mono">{f3(s.mean_rest)}</td>
              <td className="r mono">+{f3(s.delta)}</td>
              <td><svg width={160} height={14} aria-label={`interval of ${s.label}`}>
                <line x1={0} x2={160} y1={7} y2={7} stroke="var(--border-subtle)" />
                <line x1={X(0)} x2={X(0)} y1={1} y2={13} stroke="var(--text-muted)" strokeDasharray="2 2" />
                <line x1={X(s.ci[0])} x2={X(s.ci[1])} y1={7} y2={7} stroke="var(--viz-b)" strokeWidth={3} />
                <circle cx={X(s.delta)} cy={7} r={3} fill="var(--viz-b)" />
              </svg></td>
              <td className="r mono">{pfmt(s.q)}</td>
              <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                <button className="btn sm" onClick={() => onPick(s.id)}>Rows & SHAP</button>{' '}
                {(run_is_classifier(r)) && <button className="btn sm" onClick={() => links.onLabel(s)} data-testid={`slice-label-${s.rank}`}>Label more</button>}{' '}
                {s.fairness_columns.map((c) => <button key={c} className="btn sm" onClick={() => links.onFairness(c)} data-testid={`slice-fair-${s.rank}-${c}`}>Fairness · {c}</button>)}
              </td>
            </tr>))}</tbody></table>
      </div>
    </div>
  )
}

const run_is_classifier = (r: SliceSearch) => r.problem_type === 'binary' || r.problem_type === 'multiclass'

function Detail({ run, r, s, links }: { run: Run; r: SliceSearch; s: WeakSlice; links: SliceLinks }) {
  const [rows, setRows] = useState<SliceRows | null>(null)
  const [shap, setShap] = useState<SliceShap | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    api.runs.sliceRows(run.id, s.id, 25).then((x) => alive && setRows(x)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, s.id])
  const doShap = async () => {
    setBusy(true); setErr('')
    try { setShap(await api.runs.sliceShap(run.id, s.id, 6)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const feats = rows?.rows[0] ? Object.keys(rows.rows[0].features) : []
  const maxd = Math.max(1e-9, ...(shap?.features ?? []).map((f) => Math.abs(f.diff)))
  return (
    <div className="card" data-testid="slice-detail" data-id={s.id}>
      <div className="card-head"><div className="left"><h3 className="card-title">Slice #{s.rank}: <span className="mono">{s.label}</span></h3>
        <span className="helper" style={{ fontSize: 12 }}>{s.n} holdout rows · ℓ̄ {f3(s.mean)} vs {f3(s.mean_rest)} elsewhere{s.ratio ? ` (${s.ratio.toFixed(2)}× the overall ${f3(r.overall)})` : ''}</span></div>
        <div className="row" style={{ gap: 8 }}>
          {run_is_classifier(r) && <button className="btn sm primary" onClick={() => links.onLabel(s)} data-testid="slice-label">Label more rows like these →</button>}
          {s.fairness_columns.map((c) => <button key={c} className="btn sm" onClick={() => links.onFairness(c)}>Fairness by {c} →</button>)}
        </div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {err && <p className="helper" role="alert">✕ {err}</p>}
        {s.parents.length > 0 && <p className="helper" style={{ fontSize: 12.5 }}>Its single-feature parents: {s.parents.map((p) => <span key={p.label}><span className="mono">{p.label}</span> (n {p.n ?? '—'}, ℓ̄ {f3(p.mean)}{p.q != null ? `, q ${pfmt(p.q)}` : ''}) </span>)}: the lower 95% bound of the pair's own ℓ̄<sub>S</sub>, {f3(s.mean_lo)}, is above both, so the pair is worse than either alone.</p>}
        <div className="row" style={{ gap: 'var(--space-12)', alignItems: 'center' }}>
          <b>SHAP inside vs outside</b>
          <span className="helper" style={{ fontSize: 12 }}>Kernel SHAP (the Features & SHAP worker, cached per row) on 6 rows sampled from the slice and 6 from the rest: what pushes the model differently here</span>
          {!shap && <button className="btn sm" onClick={doShap} disabled={busy} data-testid="slice-shap">{busy ? 'Computing…' : 'Compute SHAP'}</button>}
          {busy && <ResonanceWave />}
        </div>
        {shap && <table className="table compact" data-testid="slice-shap-table"><thead><tr><th>feature</th><th className="r">φ̄ in S</th><th className="r">φ̄ in C</th><th className="r">φ̄<sub>S</sub> − φ̄<sub>C</sub></th><th style={{ width: 160 }} /></tr></thead>
          <tbody>{shap.features.map((f) => <tr key={f.feature}><td className="mono">{f.feature}{f.in_slice_definition ? <span className="muted"> (defines S)</span> : null}</td>
            <td className="r mono">{f.phi_slice.toFixed(4)}</td><td className="r mono">{f.phi_rest.toFixed(4)}</td><td className="r mono"><b>{f.diff >= 0 ? '+' : ''}{f.diff.toFixed(4)}</b></td>
            <td><svg width={160} height={12}><line x1={80} x2={80} y1={0} y2={12} stroke="var(--text-muted)" strokeDasharray="2 2" />
              <rect x={f.diff >= 0 ? 80 : 80 - (Math.abs(f.diff) / maxd) * 78} y={2} width={(Math.abs(f.diff) / maxd) * 78} height={8} fill={f.diff >= 0 ? 'var(--viz-e)' : 'var(--viz-b)'} /></svg></td></tr>)}</tbody></table>}
        {shap && <p className="helper" style={{ fontSize: 12 }}>f = {shap.output === 'probability' ? `p̂(${shap.target} | x)` : 'ŷ(x)'} of <span className="mono">{shap.model}</span>; φ₀ = {shap.base_value.toFixed(4)}; mean f in S {f3(shap.fx_slice)}, in C {f3(shap.fx_rest)}. Rows {shap.rows_slice.join(', ')} (S) and {shap.rows_rest.join(', ')} (C); {shap.background_size} background rows, {shap.nsamples} coalitions each; {shap.seconds.toFixed(0)} s.</p>}
        {rows && <>
          <div className="row" style={{ gap: 8 }}><b>Its rows</b><span className="helper" style={{ fontSize: 12 }}>worst {rows.rows.length} of {rows.total} by ℓᵢ; click a row for its own SHAP waterfall</span></div>
          <div style={{ overflowX: 'auto' }}>
            <table className="table compact" data-testid="slice-rows"><thead><tr><th className="r">row</th><th>y</th><th>ŷ</th><th className="r">ℓᵢ</th>{feats.map((f) => <th key={f}>{f}</th>)}</tr></thead>
              <tbody>{rows.rows.map((x) => <tr key={x.row} onClick={() => links.onRow(x.row)} style={{ cursor: 'pointer' }} title="Open in Features & SHAP">
                <td className="r mono">{x.row}</td><td className="mono">{String(x.y_true)}</td><td className="mono">{String(x.y_pred)}</td><td className="r mono"><b>{x.loss.toFixed(3)}</b></td>
                {feats.map((f) => <td key={f} className="mono" style={{ fontSize: 12, color: s.columns.includes(f) ? 'var(--accent)' : undefined }}>{fmtCell(x.features[f])}</td>)}</tr>)}</tbody></table>
          </div>
        </>}
        <div className="chips-row"><ParamChip sym="n_S" value={String(s.n)} /><ParamChip sym="t" value={s.t == null ? '∞' : s.t.toFixed(2)} /><ParamChip sym="ν" value={s.df.toFixed(0)} /><ParamChip sym="p" value={pfmt(s.p)} /><ParamChip sym="q" value={pfmt(s.q)} /></div>
      </div>
    </div>
  )
}

const fmtCell = (v: unknown) => (typeof v === 'number' ? (Number.isInteger(v) ? String(v) : fmtScore(v, 3)) : v == null ? '—' : String(v))

function Formula({ r, s }: { r: SliceSearch; s: WeakSlice }) {
  const nC = r.n - s.n
  return (
    <div className="formula" data-testid="slices-formula">
      <div className="micro-label">The test of slice #{s.rank}, and the correction for testing {r.tested.total.toLocaleString()} slices</div>
      <div className="eq">Δ = ℓ̄<sub>S</sub> − ℓ̄<sub>C</sub> = {s.mean.toFixed(4)} − {s.mean_rest.toFixed(4)} = <b className="mono">{s.delta.toFixed(4)}</b></div>
      <div className="eq">se = √(s²<sub>S</sub> ⁄ n<sub>S</sub> + s²<sub>C</sub> ⁄ n<sub>C</sub>) = {s.se.toFixed(4)} · t = Δ ⁄ se = {s.t == null ? '∞' : s.t.toFixed(3)} · ν = {s.df.toFixed(1)} (Welch–Satterthwaite)</div>
      <div className="eq">p = P(T<sub>ν</sub> ≥ t) = {pfmt(s.p)} · 95% CI = Δ ± t<sub>ν,0.975</sub>·se = [{s.ci[0].toFixed(4)}, {s.ci[1].toFixed(4)}]</div>
      <div className="eq">q<sub>(k)</sub> = min<sub>j≥k</sub> p<sub>(j)</sub> · m ⁄ j = <b className="mono">{pfmt(s.q)}</b> ≤ α = {r.params.alpha} (Benjamini–Hochberg, m = {r.tested.total.toLocaleString()})</div>
      <dl className="where">
        <dt>ℓᵢ</dt><dd>{r.loss.formula}: the run's metric ({r.eval_metric}) per holdout row, so greater is worse; {r.loss.unit}</dd>
        <dt>S, C</dt><dd>the slice ({s.label}, n<sub>S</sub> = {s.n}) and the other holdout rows (n<sub>C</sub> = {nC.toLocaleString()})</dd>
        <dt>ℓ̄, s²</dt><dd>the mean and the sample variance of ℓᵢ in each; s<sub>S</sub> = {s.sd.toFixed(4)}</dd>
        <dt>m</dt><dd>every slice tested: {r.tested.single} single values and {r.tested.pairs.toLocaleString()} pairs with n ≥ {r.params.min_support} rows (and a complement as large); q controls the expected share of false discoveries among the slices called weak at α</dd>
        <dt>rank</dt><dd>by the lower bound of the CI of Δ; a pair is listed only if the lower bound of its own ℓ̄ is above both parents' ℓ̄</dd>
        <dt>null</dt><dd>{r.null ? <>the same search with ℓ shuffled across rows {r.null.permutations} times found {r.null.weak_found.join(', ')} weak slices: what the procedure calls weak when nothing is</> : 'not run'}</dd>
      </dl>
    </div>
  )
}
