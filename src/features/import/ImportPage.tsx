import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type FlowRun } from '@/shared/api/flows'
import { HeroCell } from '@/shared/components/Status'
import type { DatasetSummary } from '@/shared/api/types'
import { LiveModels } from '@/shared/components/FlowParts'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { fix } from '@/shared/lib/format'


/** Import an AutoGluon TabularPredictor someone already trained (a zipped folder or a path on the server): it is
 *  checked, scored on a dataset's holdout like a trained run, and then works everywhere a run does. */
export function ImportPage() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dsId, setDsId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [path, setPath] = useState('')
  const [how, setHow] = useState<'zip' | 'path'>('zip')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { api.datasets.list().then((l) => { setDatasets(l); setDsId((id) => id || l[0]?.id || '') }).catch(() => {}) }, [])
  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const r = await flows.importRun({ dataset_id: dsId, file: how === 'zip' ? file : null, path: how === 'path' ? path : undefined, created_by: localStorage.getItem('ensemble.actor') ?? '' })
      location.hash = `#/models?run=${r.id}`
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
            <div className="row"><span className="spacer" /><button className="btn primary" disabled={busy || !dsId || (how === 'zip' ? !file : !path.trim())} onClick={submit} data-testid="imp-go">{busy ? 'Checking…' : 'Import'}</button></div>
          </div>
        </div>
      </div>
      <p className="helper">Earlier imports are in <a href="#/models?type=import">Models →</a></p>
    </div>
  )
}

/** The import's facts (what was loaded, from where, with which AutoGluon) above the run page, and its progress while it is checked. */
export function ImportRun({ runId }: { runId: string }) {
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
    </div>
  )
}
