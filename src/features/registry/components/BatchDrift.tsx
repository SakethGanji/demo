import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import type { BatchJob, BatchJoin, BatchListItem, ModelVersion, PsiFeature, ShiftCheck } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks } from '@/shared/components/XYPlot'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { CopyId } from '@/shared/components/DataKit'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { BAND_COLOR, BAND_STATUS, pct, us, when } from '../lib'

const HL = (c: string) => c === 'prediction' || c === 'risk_group' || /^S_\d/.test(c) || c === 'routed_model' || c === 'decision' || c.startsWith('p_') || c === 'anomaly_score' || c === 'flagged' || c === 'mean' || c === 'percentile'
const psiFmt = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(4))

/** Batch scoring of a CSV with the version (in a worker, with progress), then PSI drift of every feature and of the
 *  model's output against what the model was built on. */
/** What a batch CSV holds and what each row gets back, per kind. */
const BATCH_WHAT: Record<string, string> = {
  tabular: 'a CSV with the model’s feature columns; extra columns are kept and passed through',
  forecast: 'a CSV of new history in the run’s long format (same item id, timestamp, target and covariate columns): every series is forecast H steps past its end, no refit',
  anomaly: 'a CSV with the forest’s feature columns: every row gets s, its percentile among the fitted rows and a flag at s*',
  expected_loss: 'a CSV of loans with the model’s input columns: every row gets PD̂, LGD̂, EAD̂ and EL',
  segmented: 'a CSV with the model’s feature columns and the segment column: each row is scored by its own segment’s model (unknown segments use the global one)',
  survival: 'a CSV with the model’s input columns: every row gets its risk score, risk group, Ŝ(τ) at each horizon and median time',
  multilabel: 'a CSV with the model’s input columns: every row gets its label set, n_labels and p̂ per label',
}

/** P15: the 400s of a join batch, in plain words (the API's message follows with the file, table and columns). */
const BATCH_CODES: Record<string, string> = {
  batch_column_missing: 'A file lacks a column its table had at training.',
  batch_table_missing: 'The model joins more tables than the files sent: one file per table is needed.',
  batch_table_unknown: 'A file does not match any table of the model’s join (files are matched by name: <table>.csv).',
  batch_table_twice: 'Two files were sent for the same table.',
  batch_grain_violated: 'A joined table repeats a key, so a grain row would repeat: the batch does not keep one row per grain row.',
  batch_empty: 'The grain table’s file has no rows.',
  ambiguous_tables: 'The model’s join reads two tables with the same name: the files cannot be told apart by name.',
  join_gone: 'The model’s saved join no longer exists.',
}
const stem = (n: string) => n.replace(/\.[^.]+$/, '').toLowerCase()
const ext = (n: string) => (n.match(/\.[^.]+$/)?.[0] ?? '.csv').toLowerCase()

/** The input tables of a join model (the frozen input record, else the version's hashes); [] for a one-table model. */
export function joinTables(v: ModelVersion): { name: string; role: string }[] {
  const r = v.result
  const rec = r?.inputs
  if (rec?.frame?.kind === 'join_frame' && rec.tables?.length) return rec.tables.map((t) => ({ name: t.name, role: t.role }))
  if (r?.hashes?.join && r.hashes.inputs?.length) return r.hashes.inputs.map((t, i) => ({ name: t.table, role: i === 0 ? 'grain' : `join:${i - 1}` }))
  return []
}

export function BatchDrift({ v, actor, onScored, kind = 'tabular' }: { v: ModelVersion; actor: string; onScored?: () => void; kind?: string }) {
  const tables = joinTables(v)
  const [list, setList] = useState<BatchListItem[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [job, setJob] = useState<BatchJob | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dataDate, setDataDate] = useState('')
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

  const [jfiles, setJfiles] = useState<File[]>([])  // P15: a join model's files (one per table, or one workbook)
  const submit = async () => {
    const send: File | File[] | null = tables.length ? (jfiles.length ? jfiles : null) : file
    if (!send) return
    setBusy(true); setErr('')
    try { const b = await api.registry.scoreBatch(v.id, send, actor, dataDate || undefined); setJob(b); setSel(b.id); refresh() } catch (e) {
      const d = e instanceof ApiError ? e.detail as { code?: string; message?: string; columns?: string[]; table?: string; file?: string } | undefined : undefined
      setErr(d && typeof d === 'object' && d.code ? `${BATCH_CODES[d.code] ?? ''} ${d.message ?? ''} (${d.code})`.trim() : (e as Error).message)
    } finally { setBusy(false) }
  }
  const r = job?.result
  if (tables.length) return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="batch">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Batch Scoring · through the saved join</h3>
          <span className="helper" style={{ fontSize: 12 }}>one file per input table, named after the table ({tables.map((t) => `${t.name}.csv`).join(', ')}), or one workbook with a sheet per table: the model's join is applied to them exactly as at training (frames.materialise), then the frame is scored</span></div></div>
        <JoinFiles tables={tables} files={jfiles} setFiles={setJfiles} />
        <div className="card-body batch-up">
          <label className="row" style={{ gap: 4 }} title="F18: the date of the data in this batch (its last day). Monitoring orders batches by it.">
            <span className="helper">data date</span><input className="input sm mono" style={{ width: 110 }} placeholder="YYYY-MM-DD" value={dataDate} onChange={(e) => setDataDate(e.target.value)} data-testid="batch-date" /></label>
          <button className="btn primary" data-testid="batch-submit" disabled={!jfiles.length || busy || !actor.trim() || v.status !== 'ready'} onClick={submit}>Score {jfiles.length} file{jfiles.length === 1 ? '' : 's'} →</button>
          {!actor.trim() && <span className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ type your name in “acting as” first</span>}
        </div>
        {err && <div className="card-body data-warn" role="alert" data-testid="batch-err" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>}
        {job && (job.status === 'queued' || job.status === 'running') && (
          <div className="card-body batch-prog" data-testid="batch-progress">
            <div className="bp-track"><i style={{ width: `${job.progress * 100}%` }} /><b style={{ left: `${job.progress * 100}%` }} /></div>
            <span className="mono">{(job.progress * 100).toFixed(0)}% · {job.stage}</span><ResonanceWave width={50} height={12} />
          </div>
        )}
        {list.length > 0 && <BatchList list={list} sel={sel} setSel={setSel} kind={kind} />}
        {job?.status === 'failed' && <div className="card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {job.error}</div>}
      </div>
      {job?.status === 'succeeded' && r?.summary.join && <JoinSummary j={r.summary.join} />}
      {job?.status === 'succeeded' && r && <BatchResult job={job} />}
    </div>
  )
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="batch">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">{kind === 'forecast' ? 'Batch Forecast' : 'Batch Scoring'}</h3><span className="helper" style={{ fontSize: 12 }}>{BATCH_WHAT[kind] ?? BATCH_WHAT.tabular}</span></div></div>
        <div className="card-body batch-up">
          <label className={`batch-drop ${file ? 'has' : ''}`} data-testid="batch-drop">
            <input ref={input} type="file" accept=".csv,text/csv" data-testid="batch-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ display: 'none' }} />
            <span className="mono">{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : 'Choose a CSV…'}</span>
            <button className="btn sm" onClick={(e) => { e.preventDefault(); input.current?.click() }}>Browse</button>
          </label>
          <label className="row" style={{ gap: 4 }} title="F18: the date of the data in this file (its last day). Default: the latest date in the run's time column when the file has it, else today. Monitoring orders batches by it.">
            <span className="helper">data date</span><input className="input sm mono" style={{ width: 110 }} placeholder="YYYY-MM-DD" value={dataDate} onChange={(e) => setDataDate(e.target.value)} data-testid="batch-date" /></label>
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
        {list.length > 0 && <BatchList list={list} sel={sel} setSel={setSel} kind={kind} />}
        {job?.status === 'failed' && <div className="card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {job.error}</div>}
      </div>
      {job?.status === 'succeeded' && r && <BatchResult job={job} />}
    </div>
  )
}

function BatchList({ list, sel, setSel, kind }: { list: BatchListItem[]; sel: string | null; setSel: (id: string) => void; kind: string }) {
  return (
    <div className="table-wrap" style={{ borderTop: '1px solid var(--border-default)' }}>
      <table className="table compact" data-testid="batch-list">
        <thead><tr><th>batch</th><th>file</th><th>data date</th><th>by</th><th>when</th><th className="r">rows</th><th className="r">max PSI</th><th className="r nocase">PSI({kind === 'anomaly' ? 's' : kind === 'forecast' ? 'ŷ' : 'p̂'})</th><th>status</th></tr></thead>
        <tbody>{list.map((b) => (
          <tr key={b.id} className={b.id === sel ? 'sel' : ''} onClick={() => setSel(b.id)} style={{ cursor: 'pointer' }}>
            <td className="mono">{b.id}</td><td className="mono">{b.filename}</td><td className="mono">{b.data_date ?? '—'}</td><td>{b.created_by}</td><td className="mono">{when(b.created_at)}</td>
            <td className="r mono">{b.n_rows?.toLocaleString() ?? '—'}</td><td className="r mono">{psiFmt(b.max_psi)}</td><td className="r mono">{psiFmt(b.prediction_psi)}</td>
            <td><StatusPill status={b.status === 'succeeded' ? 'success' : b.status === 'failed' ? 'danger' : 'info'}>{b.status}</StatusPill></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

/** P15: which file goes to which table of the model's join, before anything is sent. A file is matched by its name's
 *  stem (applications.csv → applications); a per-table Browse renames the file to its table. One .xlsx carries every
 *  table as a sheet. */
function JoinFiles({ tables, files, setFiles }: { tables: { name: string; role: string }[]; files: File[]; setFiles: (f: File[]) => void }) {
  const all = useRef<HTMLInputElement>(null)
  const one = useRef<HTMLInputElement>(null)
  const [target, setTarget] = useState('')
  const names = new Set(tables.map((t) => t.name.toLowerCase()))
  const book = files.length === 1 && /\.xlsx?$/i.test(files[0].name) ? files[0] : null
  const of = (t: string) => files.find((f) => stem(f.name) === t.toLowerCase())
  const unknown = book ? [] : files.filter((f) => !names.has(stem(f.name)))
  const put = (t: string, f: File) => setFiles([...files.filter((x) => stem(x.name) !== t.toLowerCase() && !/\.xlsx?$/i.test(x.name)), new File([f], `${t}${ext(f.name)}`, { type: f.type })])
  return (
    <div className="card-body stack" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <input ref={all} type="file" multiple accept=".csv,text/csv,.xlsx,.parquet" data-testid="batch-files" style={{ display: 'none' }} onChange={(e) => { setFiles([...(e.target.files ?? [])]); e.target.value = '' }} />
        <input ref={one} type="file" accept=".csv,text/csv,.parquet" data-testid="batch-file-table" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f && target) put(target, f); e.target.value = '' }} />
        <button className="btn sm" onClick={() => all.current?.click()} data-testid="batch-files-pick">Choose files…</button>
        {files.length > 0 && <button className="btn sm ghost" onClick={() => setFiles([])}>Clear</button>}
        <span className="helper">{book ? `workbook ${book.name}: its sheets are matched to the tables by name` : `${files.length} file${files.length === 1 ? '' : 's'} chosen · one per input table, matched by file name`}</span>
      </div>
      <table className="table compact" data-testid="batch-table-map">
        <thead><tr><th style={{ width: 28 }} /><th>table</th><th>role</th><th>file</th><th /></tr></thead>
        <tbody>{tables.map((t) => { const f = book ?? of(t.name); return (
          <tr key={t.name} data-testid={`batch-map-${t.name}`} className={f ? '' : 'batch-missing'}>
            <td className={f ? 'g-ok' : 'g-warn'} aria-label={f ? 'file chosen' : 'no file yet'}>{f ? '✓' : '○'}</td>
            <td className="mono"><b>{t.name}</b></td><td>{t.role === 'grain' ? <span className="pt-chip" title="one row per scored row">grain</span> : <span className="pt-chip">joined #{t.role.replace('join:', '')}</span>}</td>
            <td className="mono">{f ? <>{book ? `${f.name} › ${t.name}` : f.name} <span className="muted">{(f.size / 1024).toFixed(1)} KB</span></> : <span className="g-warn">no file yet: choose one</span>}</td>
            <td className="r">{!book && <button className="btn sm ghost" onClick={() => { setTarget(t.name); setTimeout(() => one.current?.click(), 0) }} data-testid={`batch-pick-${t.name}`}>Browse…</button>}</td>
          </tr>) })}
          {unknown.map((f) => <tr key={f.name}><td colSpan={2} style={{ color: 'var(--status-danger)' }}>✕ matches no table</td><td className="mono">{f.name}</td><td /></tr>)}
        </tbody>
      </table>
    </div>
  )
}

/** P15: what the saved join did to the batch files: rows per file, cast failures, grain rows = frame rows. */
function JoinSummary({ j }: { j: BatchJoin }) {
  return (
    <div className="card" data-testid="batch-join">
      <div className="card-head"><div className="left"><h3 className="card-title">⋈ Join {j.join_name}</h3>
        <span className="helper" style={{ fontSize: 12 }} title={j.spec_sha256}>{j.spec_frozen ? 'the spec the model was trained with' : <b className="g-warn">▲ the spec changed since training</b>}</span></div>
        <StatusPill status={j.rows_equal_grain ? 'success' : 'danger'}>{j.rows_equal_grain ? 'rows = grain rows' : 'rows ≠ grain rows'}</StatusPill></div>
      <div className="card-body mono helper" data-testid="batch-join-rows">frame rows n_f = {j.frame_rows.toLocaleString()} · grain rows n_g = {j.grain_rows.toLocaleString()} ({j.grain}) · n_f {j.frame_rows === j.grain_rows ? '=' : '≠'} n_g · frame sha {j.frame_sha256.slice(0, 12)}…</div>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>table</th><th>file</th><th className="r">rows</th><th>sha256</th><th>cast failures</th><th>extra columns</th></tr></thead>
        <tbody>{Object.entries(j.tables).map(([t, x]) => (
          <tr key={t}><td className="mono">{t}{t === j.grain ? <span className="pt-chip" style={{ marginLeft: 4 }}>grain</span> : null}</td><td className="mono">{x.file}</td><td className="r mono">{x.rows.toLocaleString()}</td>
            <td><CopyId value={x.sha256} n={8} /></td>
            <td className="mono">{Object.keys(x.cast_failures).length ? <span className="g-warn">{Object.entries(x.cast_failures).map(([c, n]) => `${c} ${n}`).join(', ')}</span> : <span className="g-ok">none</span>}</td>
            <td className="mono muted">{x.extra_columns.join(', ') || '—'}</td></tr>))}</tbody>
      </table></div>
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
        {s.reasons && <div className="eh-cell" data-testid="batch-reasons"><span className="micro-label">reason codes</span><b className="mono">{s.reasons.n_adverse.toLocaleString()} adverse</b><small className="math-s">top {s.reasons.top_k} with s·φⱼ &gt; 0 · {s.reasons.methods.join(', ')} · {s.reasons.rows_per_s?.toFixed(1)} rows/s · most cited {s.reasons.top_codes.slice(0, 3).map((c) => `${c.code} ${(c.share * 100).toFixed(0)}%`).join(', ')}</small></div>}
        {s.reasons?.recourse && !('error' in s.reasons.recourse) && <div className="eh-cell" data-testid="batch-recourse"><span className="micro-label">recourse</span><b className="mono">{s.reasons.recourse.with_valid} / {s.reasons.recourse.rows_searched}</b><small className="math-s">first {s.reasons.recourse.rows_searched} adverse rows with a valid change · column “recourse”</small></div>}
        {s.flagged_share != null && <div className="eh-cell"><span className="micro-label">flagged</span><b className="mono">{pct(s.flagged_share)}</b><small className="math-s">{s.flag_rule ?? <>share with p̂ &gt; t = {s.threshold?.toFixed(3)}</>}{s.expected_share != null ? ` · c = ${pct(s.expected_share)}` : ''}</small></div>}
        {s.seg_segments && <div className="eh-cell" data-testid="batch-routed"><span className="micro-label">routed</span><b className="mono">{(s.n_rows - (s.seg_global_rows ?? 0)).toLocaleString()} / {s.n_rows.toLocaleString()}</b><small className="math-s">rows scored by their segment's model · {s.seg_segments.map((g) => `${g.value} ${g.n}${g.model === 'global' ? ' (global)' : ''}`).join(', ')}{s.reasons_rows ? ` · reason codes for the first ${s.reasons_rows} adverse` : ''}</small></div>}
        {s.surv_groups && <div className="eh-cell" data-testid="batch-surv"><span className="micro-label">risk groups</span><b className="mono">{s.surv_groups.high ?? 0} high</b><small className="math-s">{Object.entries(s.surv_groups).map(([g, n]) => `${g} ${n}`).join(' · ')} · cut at the holdout's quartiles of r̂</small></div>}
        {s.surv_mean_survival && Object.entries(s.surv_mean_survival).slice(0, 1).map(([t, v]) => <div key={t} className="eh-cell"><span className="micro-label">mean Ŝ({t})</span><b className="mono">{pct(v)}</b><small className="math-s">{Object.entries(s.surv_mean_survival!).map(([h, x]) => `Ŝ(${h}) ${pct(x)}`).join(' · ')} {s.surv_unit ? `(${s.surv_unit}s)` : ''}</small></div>)}
        {s.ml_labels && <div className="eh-cell" data-testid="batch-ml"><span className="micro-label">labels per row</span><b className="mono">{fmtScore(s.ml_mean_labels ?? 0, 2)}</b><small className="math-s">{pct(s.ml_multi ?? 0)} with 2+ · {pct(s.ml_empty ?? 0)} none · {s.ml_labels.map((l) => `${l.label} ${pct(l.share)}`).join(' · ')}</small></div>}
        {s.items != null && <div className="eh-cell"><span className="micro-label">series forecast</span><b className="mono">{s.items}</b><small className="math-s">{s.forecast_rows?.toLocaleString()} rows · covariates: {s.known_covariates_source}</small></div>}
        {s.mean_prediction != null && <div className="eh-cell"><span className="micro-label">mean {s.prediction_symbol ?? 'ŷ'}</span><b className="mono">{fmtScore(s.mean_prediction, 2)}</b><small className="math-s">σ = {fmtScore(s.std_prediction, 2)}</small></div>}
        <div className="eh-cell"><span className="micro-label">max PSI (features)</span><b className="mono" data-testid="batch-max-psi" style={{ color: worst ? BAND_COLOR[worst.band] : undefined }}>{psiFmt(d.max_psi)}</b><small className="math-s">{worst ? `${worst.feature} · ${worst.band}` : ''}</small></div>
        <div className="eh-cell"><span className="micro-label">PSI(output)</span><b className="mono" data-testid="batch-pred-psi" style={{ color: BAND_COLOR[p.band] }}>{psiFmt(p.psi)}</b><small className="math-s">{p.ks ? `KS D = ${p.ks.statistic.toFixed(3)}` : p.band}</small></div>
        {s.problem_type !== 'forecast' && s.problem_type !== 'anomaly' && s.problem_type !== 'survival' && s.problem_type !== 'multilabel' && !s.seg_segments && <AdversarialCell bid={job.id} />}
      </div>

      {s.reasons?.recourse && !('error' in s.reasons.recourse) && s.reasons.recourse.rows.length > 0 && (() => {
        const rr = s.reasons.recourse
        return (
          <details className="an-adv card" data-testid="batch-recourse-rows">
            <summary className="micro-label" style={{ padding: 'var(--space-12) var(--space-16)' }}>Recourse · the nearest valid change for the first {rr.rows_searched} adverse rows ({rr.seconds.toFixed(1)} s)</summary>
            <div className="table-wrap"><table className="table compact">
              <thead><tr><th>row</th><th>p̂</th><th>nearest change that flips the decision</th><th>d</th><th>π</th></tr></thead>
              <tbody>{rr.rows.map((r) => { const o = r.options.find((x) => x.valid); return (
                <tr key={r.row}><td className="mono">{r.row + 1}</td><td className="mono">{r.p.toFixed(4)}</td><td>{o ? o.changes.map((c) => `${c.feature} ${String(c.from ?? 'missing')} → ${String(c.to)}`).join(' and ') : <span className="helper">none found</span>}</td><td className="mono">{o?.distance.toFixed(2) ?? '—'}</td><td className="mono">{o?.plausibility?.toFixed(2) ?? '—'}</td></tr>) })}</tbody>
            </table></div>
          </details>
        )
      })()}

      <div className="card" data-testid="psi-table">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Population Stability Index</h3><span className="helper" style={{ fontSize: 12 }}>each {s.problem_type === 'forecast' ? 'column of the new history' : 'feature of the batch'} against {d.reference ?? 'the training rows'}</span></div>
            <div className="row" style={{ gap: 6 }}>
              <StatusPill status="success">{d.counts.stable} stable</StatusPill><StatusPill status="warning">{d.counts.watch} watch</StatusPill><StatusPill status="danger">{d.counts.shift} shift</StatusPill>
              <a className="btn sm primary" href={api.registry.batchUrl(job.id)} download data-testid="batch-download">Download scored CSV</a>
            </div>
          </div>
          <div className="formula psi-formula">
            <div className="eq"><i>PSI</i> = Σ<sub><i>i</i></sub> (<i>a</i><sub><i>i</i></sub> − <i>e</i><sub><i>i</i></sub>) · ln(<i>a</i><sub><i>i</i></sub> / <i>e</i><sub><i>i</i></sub>)</div>
            <dl className="where">
              <dt>eᵢ</dt><dd>share of the n_e = {d.n_expected.toLocaleString()} reference rows in bin i (expected): {d.reference ?? 'the training rows'}</dd>
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
        <BinChart f={p} title="Output distribution" output reference={d.prediction_reference} />
      </div>

      <div className="card" data-testid="batch-preview">
        <div className="card-head"><div className="left"><h3 className="card-title">Preview</h3><span className="helper" style={{ fontSize: 12 }}>first {r.preview.length} {s.problem_type === 'forecast' ? 'forecast rows · mean and every quantile per series and step' : s.problem_type === 'anomaly' ? `of ${s.n_rows.toLocaleString()} scored rows · anomaly_score s, expected_path_length E[h], percentile and flagged appended` : `of ${s.n_rows.toLocaleString()} scored rows · prediction and per-class probabilities appended`}</span></div></div>
        <div className="table-wrap" style={{ maxHeight: 340, overflow: 'auto' }}>
          <table className="table compact">
            <thead><tr>{r.columns.map((c) => <th key={c} className={HL(c) ? 'hl' : ''}>{c}</th>)}</tr></thead>
            <tbody>{r.preview.slice(0, 25).map((row, i) => <tr key={i}>{r.columns.map((c) => { const x = row[c]; return <td key={c} className={`mono ${HL(c) ? 'hl' : ''}`}>{x == null ? '' : typeof x === 'number' ? (Number.isInteger(x) ? x : x.toFixed(4)) : String(x).slice(0, 40)}</td> })}</tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/** F15: the adversarial AUC of this batch against the training rows, next to PSI: one classifier over all the columns
 *  together (PSI looks at one column at a time). Started here; the full check is Explain → Shift. */
function AdversarialCell({ bid }: { bid: string }) {
  const [c, setC] = useState<ShiftCheck | { status: 'none'; run_id?: string } | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    api.registry.batchShift(bid).then((x) => alive && setC(x as ShiftCheck)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [bid, tick])
  const running = c?.status === 'queued' || c?.status === 'running'
  useEffect(() => {
    if (!running) return
    const t = setTimeout(() => setTick((v) => v + 1), 2000)
    return () => clearTimeout(t)
  }, [running, c])
  const start = async () => { setErr(''); try { setC(await api.registry.startBatchShift(bid)) } catch (e) { setErr((e as Error).message) } }
  const done = c && c.status === 'succeeded' && 'result' in c && c.result
  const top = done ? c.result!.features.find((f) => f.significant) : undefined
  return (
    <div className="eh-cell" data-testid="batch-adv">
      <span className="micro-label">adversarial AUC</span>
      {done ? <>
        <b className="mono" data-testid="batch-adv-auc" style={{ color: c.result!.verdict.shift ? (c.result!.auc.value >= 0.7 ? BAND_COLOR.shift : BAND_COLOR.watch) : BAND_COLOR.stable }}>{c.result!.auc.value.toFixed(3)}</b>
        <small className="math-s">[{c.result!.auc.ci[0].toFixed(3)}, {c.result!.auc.ci[1].toFixed(3)}] · {c.result!.verdict.label}{top ? ` · ${top.feature}` : ''} · <a href={`#/models?run=${c.run_id}&tab=Doctor&sub=Shift`} data-testid="batch-adv-link">details</a></small>
      </> : running ? <><b className="mono"><ResonanceWave /></b><small className="math-s">{(c as ShiftCheck).stage ?? 'running'}</small></>
        : <><button className="btn sm" onClick={start} data-testid="batch-adv-run">Run shift check</button><small className="math-s">{err ? `✕ ${err}` : c?.status === 'failed' ? `✕ ${(c as ShiftCheck).error}` : 'all columns at once; ≈ 0.5 = no shift'}</small></>}
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
function BinChart({ f, title, output, reference }: { f: PsiFeature; title: string; output?: boolean; reference?: string }) {
  const bins = f.bins ?? []
  const n = bins.length
  const maxS = Math.max(0.01, ...bins.flatMap((b) => [b.expected, b.actual]))
  return (
    <PlotFrame title={title} right={<span className="mono helper" style={{ fontSize: 12 }}>PSI = {psiFmt(f.psi)}</span>}
      legend={[{ name: 'eᵢ training', color: 'var(--viz-c)', data: [] }, { name: 'aᵢ batch', color: 'var(--viz-d)', data: [] }]}
      chips={<><ParamChip sym="bins" value={String(n)} />{f.ks && <ParamChip sym="KS D" value={f.ks.statistic.toFixed(3)} />}</>}
      caption={<span>{output ? `${f.output}; reference: ${reference ?? 'the version’s own predictions on the holdout'}. ` : ''}Bar pairs are eᵢ and aᵢ; the number under each bin is its term (aᵢ − eᵢ)·ln(aᵢ/eᵢ), and the terms add up to PSI.</span>}>
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
