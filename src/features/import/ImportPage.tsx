import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type FlowRun } from '@/shared/api/flows'
import { useApi } from '@/shared/api/mode'
import type { DatasetSummary } from '@/shared/api/types'
import { LiveModels } from '@/shared/components/FlowParts'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fix } from '@/shared/lib/format'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { HeroCell } from '@/shared/components/Status'


/** Import an AutoGluon TabularPredictor someone already trained (a zipped folder or a path on the server): it is
 *  checked, scored on a dataset's holdout like a trained run, and then works everywhere a run does. */
export function ImportPage({ go }: { go: (r: string) => void }) {
  const { mode } = useApi()
  const runId = useHashParam('run')
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dsId, setDsId] = useState('credit-default')
  const [file, setFile] = useState<File | null>(null)
  const [path, setPath] = useState('')
  const [how, setHow] = useState<'zip' | 'path'>('zip')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [past, setPast] = useState<FlowRun[]>([])
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (mode === 'live') { api.datasets.list().then(setDatasets).catch(() => {}); flows.runs('tabular').then((rs) => setPast(rs.filter((r) => r.kind === 'import'))).catch(() => {}) } }, [mode, runId])
  if (mode === 'mock') return <div className="page"><div className="page-head"><h1 className="page-title">Import Predictor</h1></div><div className="card card-body"><p className="helper">▲ mock mode: the API is offline. Start it with <span className="mono">make dev</span> to import a predictor.</p></div></div>
  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const r = await flows.importRun({ dataset_id: dsId, file: how === 'zip' ? file : null, path: how === 'path' ? path : undefined, created_by: localStorage.getItem('ensemble.actor') ?? '' })
      location.hash = `#/import?run=${r.id}`
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="page import" data-testid="import-page">
      <div className="page-head">
        <div><h1 className="page-title">Import Predictor</h1><p className="helper sub">Bring a TabularPredictor trained elsewhere (a notebook, another team). It is not retrained: ENSEMBLE scores it on a dataset’s holdout, then Explain, the registry, the model card, Try It and batch scoring work on it.</p></div>
      </div>
      <div className="grid imp-grid">
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">1 · The predictor</h3></div>
            <div className="seg"><button className={how === 'zip' ? 'on' : ''} onClick={() => setHow('zip')}>Upload .zip</button><button className={how === 'path' ? 'on' : ''} onClick={() => setHow('path')}>Server path</button></div></div>
          <div className="card-body stack" style={{ gap: 10 }}>
            {how === 'zip' ? <>
              <input ref={ref} type="file" accept=".zip" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <div className="imp-drop" onClick={() => ref.current?.click()} data-testid="imp-file">{file ? <><b className="mono">{file.name}</b> · {(file.size / 1e6).toFixed(1)} MB</> : <>Choose the zipped folder <span className="mono">TabularPredictor(path=…)</span> saved to</>}</div>
              <pre className="fc-call mono">{`import shutil\nshutil.make_archive("my_predictor", "zip", root_dir=".", base_dir="AutogluonModels/ag-2026…")`}</pre>
            </> : <label className="col"><span className="micro-label">folder on the API’s machine</span><input className="input mono" placeholder="/models/credit_v3" value={path} onChange={(e) => setPath(e.target.value)} data-testid="imp-path" /></label>}
            <p className="helper" style={{ fontSize: 12 }}>Checked: it loads with <span className="mono">TabularPredictor.load</span> (any AutoGluon 1.x; versions are reported), the folder holds <span className="mono">predictor.pkl</span>, archive paths stay inside the folder.</p>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">2 · Data to score it on</h3></div></div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <select className="select" value={dsId} onChange={(e) => setDsId(e.target.value)} data-testid="imp-dataset">{datasets.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.rows.toLocaleString()} rows</option>)}</select>
            <p className="helper" style={{ fontSize: 12 }}>It must contain the predictor’s label and every feature. The dataset’s fixed 20% holdout is scored. If those rows were in the predictor’s own training data, its holdout scores are optimistic, and the run says so.</p>
            {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }} data-testid="imp-error">✕ {err}</p>}
            <div className="row"><span className="spacer" /><button className="btn primary" disabled={busy || (how === 'zip' ? !file : !path.trim())} onClick={submit} data-testid="imp-go">{busy ? 'Checking…' : 'Import'}</button></div>
          </div>
        </div>
      </div>
      {runId && <ImportRun key={runId} runId={runId} go={go} />}
      {past.length > 0 && <div className="card"><div className="card-head"><div className="left"><h3 className="card-title">Imported runs</h3></div></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>run</th><th>name</th><th>status</th><th>label</th><th className="r">holdout</th><th /></tr></thead>
          <tbody>{past.map((r) => <tr key={r.id}><td className="table-cell">{r.id}</td><td>{r.name}</td><td><StatusPill status={r.status === 'succeeded' ? 'success' : r.status === 'failed' ? 'danger' : 'info'}>{r.status}</StatusPill></td><td>{r.label}</td>
            <td className="r table-cell">{r.eval_metric && r.holdout ? `${r.eval_metric.name} ${fix((r.holdout.metrics as Record<string, number>)[r.eval_metric.name], 4)}` : '—'}</td>
            <td><button className="chip-btn" onClick={() => { location.hash = `#/import?run=${r.id}` }}>Open</button></td></tr>)}</tbody></table></div></div>}
    </div>
  )
}

function ImportRun({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const [run, setRun] = useState<FlowRun | null>(null)
  useEffect(() => { flows.run(runId).then(setRun).catch(() => {}) }, [runId, live.done])
  if (!run) return <div className="card card-body row"><ResonanceWave /> Reading {runId}…</div>
  const im = (run.imported ?? {}) as Record<string, unknown>
  const done = run.status === 'succeeded'
  const metric = run.eval_metric?.name ?? ''
  const hm = (run.holdout?.metrics ?? {}) as Record<string, number>
  return (
    <div className="stack" style={{ gap: 16 }} data-testid="import-run" data-status={run.status}>
      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">{done ? `holdout ${metric}` : live.stage}</span><b className="mono">{done ? fix(hm[metric], 4) : run.status === 'failed' ? 'failed' : <ResonanceWave />}</b>
          <small>{run.name} · {run.id}</small></div>
        <div className="eh-cell"><span className="micro-label">label · problem</span><b className="mono">{run.label}</b><small className="math-s">{String(im.problem_type ?? run.problem_type ?? '')}</small></div>
        <div className="eh-cell"><span className="micro-label">models</span><b className="mono">{String(im.n_models ?? '—')}</b><small className="math-s">best {String(im.model_best ?? run.best_model ?? '')}</small></div>
        <HeroCell label="AutoGluon" sub="saved with" subClass="math-s">{String(im.autogluon_version ?? '—')}</HeroCell>
        <div className="eh-cell"><span className="micro-label">source</span><b className="mono" style={{ fontSize: 13 }}>{String(im.source_kind ?? '')}</b><small className="math-s">{String(im.source ?? '').split('/').slice(-2).join('/')}</small></div>
      </div>
      {!done && run.status !== 'failed' && <LiveModels live={live} metric="validation score as saved" />}
      {run.status === 'failed' && <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {run.error}</div>}
      {done && <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Imported · scored on the holdout</h3><span className="helper" style={{ fontSize: 12 }}>{run.data?.holdout_rows as number} holdout rows</span></div>
          <div className="row"><button className="btn" onClick={() => go(`explain?run=${run.id}`)} data-testid="imp-explain">Explain →</button><button className="btn" onClick={() => go(`quick-train?run=${run.id}`)}>Results →</button><button className="btn primary" onClick={() => go(`registry?register=${run.id}`)} data-testid="imp-register">Register →</button></div></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>#</th><th>model</th><th className="r">val (as saved)</th><th className="r">holdout</th><th className="r">stack level</th><th className="r">predict</th></tr></thead>
          <tbody>{(run.leaderboard ?? []).map((r) => <tr key={r.model} className={r.model === run.best_model ? 'sel' : ''}><td className="table-cell">{r.rank}</td><td className="table-cell">{r.model}</td><td className="r table-cell">{fix(r.score_val_natural, 4)}</td><td className="r table-cell"><b>{fix(r.score_test_natural, 4)}</b></td><td className="r table-cell">{r.stack_level}</td><td className="r table-cell">{r.pred_time_test != null ? `${(Number(r.pred_time_test) * 1000).toFixed(0)} ms` : '—'}</td></tr>)}</tbody></table></div>
        <p className="helper caption-pad" style={{ fontSize: 12 }}>{Object.entries(hm).slice(0, 6).map(([k, v]) => `${k} ${fix(v, 4)}`).join(' · ')}. Not refit: the models are the ones you saved. The holdout may overlap their training rows; if so, these scores are optimistic.</p>
      </div>}
    </div>
  )
}
