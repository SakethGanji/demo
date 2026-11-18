import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { BatchJob, BatchListItem, ModelVersion, PsiFeature } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks } from '@/shared/components/XYPlot'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { BAND_COLOR, BAND_STATUS, pct, us, when } from '../lib'

const psiFmt = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(4))

/** Batch scoring of a CSV with the version (in a worker, with progress), then PSI drift of every feature and of the
 *  model's output against what the model was built on. */
export function BatchDrift({ v, actor, onScored }: { v: ModelVersion; actor: string; onScored?: () => void }) {
  const [list, setList] = useState<BatchListItem[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [job, setJob] = useState<BatchJob | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const refresh = () => api.registry.batches(v.id).then((b) => { setList(b); setSel((s) => s ?? b[0]?.id ?? null) }).catch(() => {})
  useEffect(() => { refresh() }, [v.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!sel) return
    let alive = true
    let t: ReturnType<typeof setTimeout>
    const poll = () => api.registry.batch(sel).then((b) => {
      if (!alive) return
      setJob(b)
      if (b.status === 'queued' || b.status === 'running') t = setTimeout(poll, 350)
      else { refresh(); if (b.status === 'succeeded') onScored?.() }
    }).catch(() => { if (alive) t = setTimeout(poll, 1000) })
    poll()
    return () => { alive = false; clearTimeout(t) }
  }, [sel]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (!file) return
    setBusy(true); setErr('')
    try { const b = await api.registry.scoreBatch(v.id, file, actor); setJob(b); setSel(b.id); refresh() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const r = job?.result
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="batch">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Batch Scoring</h3><span className="helper" style={{ fontSize: 12 }}>a CSV with the model’s feature columns; extra columns are kept and passed through</span></div></div>
        <div className="card-body batch-up">
          <label className={`batch-drop ${file ? 'has' : ''}`} data-testid="batch-drop">
            <input ref={input} type="file" accept=".csv,text/csv" data-testid="batch-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ display: 'none' }} />
            <span className="mono">{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : 'Choose a CSV…'}</span>
            <button className="btn sm" onClick={(e) => { e.preventDefault(); input.current?.click() }}>Browse</button>
          </label>
          <button className="btn primary" data-testid="batch-submit" disabled={!file || busy || !actor.trim() || v.status !== 'ready'} onClick={submit}>Score {file ? file.name : 'file'} →</button>
          {!actor.trim() && <span className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ type your name in “acting as” first</span>}
          {err && <span role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</span>}
        </div>
        {job && (job.status === 'queued' || job.status === 'running') && (
          <div className="card-body batch-prog" data-testid="batch-progress">
            <div className="bp-track"><i style={{ width: `${job.progress * 100}%` }} /><b style={{ left: `${job.progress * 100}%` }} /></div>
            <span className="mono">{(job.progress * 100).toFixed(0)}% · {job.stage}</span><ResonanceWave width={50} height={12} />
          </div>
        )}
        {list.length > 0 && (
          <div className="table-wrap" style={{ borderTop: '1px solid var(--border-default)' }}>
            <table className="table compact" data-testid="batch-list">
              <thead><tr><th>batch</th><th>file</th><th>by</th><th>when</th><th className="r">rows</th><th className="r">max PSI</th><th className="r nocase">PSI(p̂)</th><th>status</th></tr></thead>
              <tbody>{list.map((b) => (
                <tr key={b.id} className={b.id === sel ? 'sel' : ''} onClick={() => setSel(b.id)} style={{ cursor: 'pointer' }}>
                  <td className="mono">{b.id}</td><td className="mono">{b.filename}</td><td>{b.created_by}</td><td className="mono">{when(b.created_at)}</td>
                  <td className="r mono">{b.n_rows?.toLocaleString() ?? '—'}</td><td className="r mono">{psiFmt(b.max_psi)}</td><td className="r mono">{psiFmt(b.prediction_psi)}</td>
                  <td><StatusPill status={b.status === 'succeeded' ? 'success' : b.status === 'failed' ? 'danger' : 'info'}>{b.status}</StatusPill></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {job?.status === 'failed' && <div className="card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {job.error}</div>}
      </div>
      {job?.status === 'succeeded' && r && <BatchResult job={job} />}
    </div>
  )
}

function BatchResult({ job }: { job: BatchJob }) {
  const r = job.result!
  const s = r.summary, d = r.drift
  const [feat, setFeat] = useState<string>(d.features.find((f) => f.psi != null)?.feature ?? '')
  const f = d.features.find((x) => x.feature === feat)
  const p = d.prediction
  const worst = d.features.find((x) => x.psi != null)
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="batch-result" data-batch={job.id}>
      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">scored</span><b className="mono" data-testid="batch-rows">{s.n_rows.toLocaleString()}</b><small>rows of {job.filename} · {s.model}</small></div>
        <div className="eh-cell"><span className="micro-label">time per row</span><b className="mono">{us(s.us_per_row)} µs</b><small className="math-s">T / n, T = {s.score_seconds.toFixed(2)} s</small></div>
        {s.mean_p != null && <div className="eh-cell"><span className="micro-label">mean p̂({s.positive_class})</span><b className="mono">{s.mean_p.toFixed(4)}</b><small className="math-s">reference {s.mean_p_reference?.toFixed(4)}</small></div>}
        {s.flagged_share != null && <div className="eh-cell"><span className="micro-label">flagged</span><b className="mono">{pct(s.flagged_share)}</b><small className="math-s">share with p̂ &gt; t = {s.threshold?.toFixed(3)}</small></div>}
        {s.mean_prediction != null && <div className="eh-cell"><span className="micro-label">mean ŷ</span><b className="mono">{fmtScore(s.mean_prediction, 2)}</b><small className="math-s">σ = {fmtScore(s.std_prediction, 2)}</small></div>}
        <div className="eh-cell"><span className="micro-label">max PSI (features)</span><b className="mono" data-testid="batch-max-psi" style={{ color: worst ? BAND_COLOR[worst.band] : undefined }}>{psiFmt(d.max_psi)}</b><small className="math-s">{worst ? `${worst.feature} · ${worst.band}` : ''}</small></div>
        <div className="eh-cell"><span className="micro-label">PSI(output)</span><b className="mono" data-testid="batch-pred-psi" style={{ color: BAND_COLOR[p.band] }}>{psiFmt(p.psi)}</b><small className="math-s">{p.ks ? `KS D = ${p.ks.statistic.toFixed(3)}` : p.band}</small></div>
      </div>

      <div className="card" data-testid="psi-table">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Population Stability Index</h3><span className="helper" style={{ fontSize: 12 }}>each feature of the batch against the training rows</span></div>
            <div className="row" style={{ gap: 6 }}>
              <StatusPill status="success">{d.counts.stable} stable</StatusPill><StatusPill status="warning">{d.counts.watch} watch</StatusPill><StatusPill status="danger">{d.counts.shift} shift</StatusPill>
              <a className="btn sm primary" href={api.registry.batchUrl(job.id)} download data-testid="batch-download">Download scored CSV</a>
            </div>
          </div>
          <div className="formula psi-formula">
            <div className="eq"><i>PSI</i> = Σ<sub><i>i</i></sub> (<i>a</i><sub><i>i</i></sub> − <i>e</i><sub><i>i</i></sub>) · ln(<i>a</i><sub><i>i</i></sub> / <i>e</i><sub><i>i</i></sub>)</div>
            <dl className="where">
              <dt>eᵢ</dt><dd>share of the n_e = {d.n_expected.toLocaleString()} training rows in bin i (expected)</dd>
              <dt>aᵢ</dt><dd>share of the n_a = {d.n_actual.toLocaleString()} batch rows in bin i (actual)</dd>
              <dt>i</dt><dd>numbers: training deciles (−∞, q₀.₁], …, (q₀.₉, ∞); categories: the 19 most frequent + other; text: length in characters; missing is its own bin</dd>
              <dt>ε</dt><dd>shares floored at ε = {d.eps} so an empty bin counts instead of dividing by zero</dd>
            </dl>
          </div>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>feature</th><th>kind</th><th className="r">PSI</th><th style={{ width: '28%' }}>0 · 0.10 · 0.25</th><th className="r nocase">μₑ → μₐ</th><th>band</th></tr></thead>
            <tbody>{d.features.map((x) => (
              <tr key={x.feature} className={x.feature === feat ? 'sel' : ''} onClick={() => x.psi != null && setFeat(x.feature)} style={{ cursor: x.psi != null ? 'pointer' : undefined }} data-testid={`psi-${x.feature}`} data-band={x.band}>
                <td className="mono">{x.feature}</td><td className="muted">{x.kind}</td><td className="r mono" data-testid={`psi-value-${x.feature}`}>{psiFmt(x.psi)}</td>
                <td><PsiBar v={x.psi} band={x.band} /></td>
                <td className="r mono muted">{x.mean_expected != null ? `${fmtScore(x.mean_expected, 3)} → ${fmtScore(x.mean_actual, 3)}` : x.categories_unseen?.length ? `new: ${x.categories_unseen.slice(0, 2).join(', ')}` : ''}</td>
                <td><StatusPill status={BAND_STATUS[x.band]}>{x.band}</StatusPill></td>
              </tr>
            ))}</tbody>
          </table></div>
          <div className="caption" style={{ padding: '8px 16px' }}><span className="helper" style={{ fontSize: 12 }}>Bands: PSI &lt; 0.10 stable · 0.10 ≤ PSI ≤ 0.25 watch · PSI &gt; 0.25 shift. Click a feature for its bins.</span></div>
      </div>
      <div className="grid psi-grid">
        {f && <BinChart f={f} title={`Bins · ${f.feature}`} />}
        <BinChart f={p} title="Output distribution" output />
      </div>

      <div className="card" data-testid="batch-preview">
        <div className="card-head"><div className="left"><h3 className="card-title">Preview</h3><span className="helper" style={{ fontSize: 12 }}>first {r.preview.length} of {s.n_rows.toLocaleString()} scored rows · prediction and per-class probabilities appended</span></div></div>
        <div className="table-wrap" style={{ maxHeight: 340, overflow: 'auto' }}>
          <table className="table compact">
            <thead><tr>{r.columns.map((c) => <th key={c} className={c === 'prediction' || c.startsWith('p_') ? 'hl' : ''}>{c}</th>)}</tr></thead>
            <tbody>{r.preview.slice(0, 25).map((row, i) => <tr key={i}>{r.columns.map((c) => { const x = row[c]; return <td key={c} className={`mono ${c === 'prediction' || c.startsWith('p_') ? 'hl' : ''}`}>{x == null ? '' : typeof x === 'number' ? (Number.isInteger(x) ? x : x.toFixed(4)) : String(x).slice(0, 40)}</td> })}</tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function PsiBar({ v, band }: { v: number | null; band: string }) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const max = 0.5
  const x = (t: number) => (Math.min(t, max) / max) * (W - 2)
  return (
    <div ref={ref} className="psi-bar">
      <svg width={W} height={16}>
        <rect x={0} y={7} width={x(0.1)} height={2} fill="var(--status-success)" opacity={0.35} />
        <rect x={x(0.1)} y={7} width={x(0.25) - x(0.1)} height={2} fill="var(--status-warning)" opacity={0.35} />
        <rect x={x(0.25)} y={7} width={x(max) - x(0.25)} height={2} fill="var(--status-danger)" opacity={0.35} />
        {[0.1, 0.25].map((t) => <line key={t} x1={x(t)} x2={x(t)} y1={2} y2={14} stroke="var(--border-track)" />)}
        {v != null && <><rect x={0} y={4} width={Math.max(2, x(v))} height={8} rx={2} fill={BAND_COLOR[band]} opacity={0.8} />{v > max && <text x={W - 2} y={12} textAnchor="end" className="tick-text">›</text>}</>}
      </svg>
    </div>
  )
}

/** Short bin labels: a numeric bin (a, b] shows its upper edge b; categories are cut to 11 characters. */
function binLabel(l: string) {
  const m = l.match(/^\(([^,]+), ([^\]]+)\]$/)
  if (m) return `≤${m[2]}`
  if (l.startsWith('≤ ')) return `≤${l.slice(2)}`
  if (l.startsWith('> ')) return `>${l.slice(2)}`
  return l.length > 12 ? `${l.slice(0, 11)}…` : l
}

/** Expected vs actual share per bin, with each bin's PSI term (aᵢ − eᵢ)·ln(aᵢ/eᵢ). */
function BinChart({ f, title, output }: { f: PsiFeature; title: string; output?: boolean }) {
  const bins = f.bins ?? []
  const n = bins.length
  const maxS = Math.max(0.01, ...bins.flatMap((b) => [b.expected, b.actual]))
  return (
    <PlotFrame title={title} right={<span className="mono helper" style={{ fontSize: 12 }}>PSI = {psiFmt(f.psi)}</span>}
      legend={[{ name: 'eᵢ training', color: 'var(--viz-c)', data: [] }, { name: 'aᵢ batch', color: 'var(--viz-d)', data: [] }]}
      chips={<><ParamChip sym="bins" value={String(n)} />{f.ks && <ParamChip sym="KS D" value={f.ks.statistic.toFixed(3)} />}</>}
      caption={<span>{output ? `${f.output}; reference: the version’s own predictions on the holdout. ` : ''}Bar pairs are eᵢ and aᵢ; the number under each bin is its term (aᵢ − eᵢ)·ln(aᵢ/eᵢ), and the terms add up to PSI.</span>}>
      <XYPlot height={200} testId={output ? 'bins-output' : 'bins-feature'} ariaLabel={title}
        x={{ min: 0, max: n, ticks: [], title: '' }} y={{ min: 0, max: maxS * 1.15, ticks: niceTicks(0, maxS * 1.15, 4), title: 'share', fmt: (v) => `${(v * 100).toFixed(0)}%` }}
        series={[]}
        bars={bins.flatMap((b, i) => [{ x0: i + 0.12, x1: i + 0.5, y: b.expected, color: 'var(--viz-c)', opacity: 0.75 }, { x0: i + 0.5, x1: i + 0.88, y: b.actual, color: 'var(--viz-d)', opacity: 0.8 }])}
      >{(sx, sy) => <g>{bins.map((b, i) => <g key={b.label}>
        <text x={sx(i + 0.5)} y={sy(0) + 12} textAnchor="middle" className="tick-text" style={{ fontSize: 9 }}>{binLabel(b.label)}</text>
        <text x={sx(i + 0.5)} y={sy(0) + 23} textAnchor="middle" className="tick-text" style={{ fontSize: 9, fill: b.term > 0.02 ? 'var(--status-danger)' : 'var(--text-muted)' }}>{b.term.toFixed(3)}</text>
      </g>)}</g>}</XYPlot>
    </PlotFrame>
  )
}
