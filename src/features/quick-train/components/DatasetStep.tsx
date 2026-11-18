import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import { useApi } from '@/shared/api/mode'
import type { DatasetSummary } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { SAMPLES, fromServer, profileCsv, type Dataset } from '../datasets'

const isCsv = (f: File) => /\.(csv|tsv|txt)$/i.test(f.name)

/**
 * Step 1: drop a CSV or pick a sample, then read its profile.
 * Live: the list and profiles come from the API and uploads go to it. Mock: built-in samples and an in-browser profiler.
 */
export function DatasetStep({ ds, onPick }: { ds: Dataset; onPick: (d: Dataset) => void }) {
  const { mode, recheck } = useApi()
  const live = mode === 'live'
  const [err, setErr] = useState('')
  const [over, setOver] = useState(false)
  const [server, setServer] = useState<DatasetSummary[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const K = ds.classes.length
  const pick = (d: Dataset) => { setErr(''); onPick(d) }

  const offline = (e: unknown) => {
    if (e instanceof ApiError && e.unreachable) { recheck(); return true }
    return false
  }

  const pickServer = async (id: string) => {
    setBusy(id)
    try { pick(fromServer(await api.datasets.get(id))) } catch (e) {
      setErr(offline(e) ? 'The API went offline; the built-in samples are shown instead.' : (e as Error).message)
    } finally { setBusy(null) }
  }

  // In live mode, list the server's datasets and start on its first sample (unless a server dataset is already chosen).
  useEffect(() => {
    if (!live) { setServer(null); return }
    let alive = true
    api.datasets.list().then((list) => {
      if (!alive) return
      setServer(list)
      // #/quick-train?dataset=<id> (Home's first-run cards) starts on that dataset.
      const want = new URLSearchParams(location.hash.split('?')[1] ?? '').get('dataset')
      if (!ds.id && list.length) pickServer(want && list.some((x) => x.id === want) ? want : list[0].id)
    }).catch((e) => { if (alive && !offline(e)) setErr((e as Error).message) })
    return () => { alive = false }
  }, [live])

  const onFile = async (f?: File) => {
    if (!f) return
    if (!isCsv(f)) { setErr(`${f.name} is not a CSV. Upload a .csv or .tsv file with a header row${live ? '' : ', or pick a sample'}.`); return }
    if (live) {
      setBusy('upload')
      try {
        const d = await api.datasets.upload(f)
        setServer((s) => [...(s ?? []).filter((x) => x.id !== d.id), d])
        pick(fromServer(d))
        return
      } catch (e) {
        if (!offline(e)) { setErr((e as Error).message); return }
        // the API dropped out mid-upload: profile it here instead, as mock mode would
      } finally { setBusy(null) }
    }
    try { pick(await profileCsv(f)) } catch (e) { setErr((e as Error).message) }
  }

  const p = ds.profile
  const maxShare = Math.max(...ds.classes.map((x) => x.share), 0.0001)
  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.5fr)', alignItems: 'start' }}>
        <div className="stack">
          <div className={`drop ${over ? 'over' : ''}`} data-testid="dataset-drop" onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0]) }} onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && input.current?.click()}>
            <svg width="120" height="34" viewBox="0 0 120 34" aria-hidden><path d="M2 17C20 -8 40 42 60 17S100 -8 118 17" stroke="var(--accent-secondary)" strokeWidth="1.75" fill="none" /><path d="M2 17C20 4 40 30 60 17S100 4 118 17" stroke="var(--accent-primary)" strokeWidth="1.75" fill="none" opacity=".6" /><circle cx="2" cy="17" r="3" fill="var(--accent-primary)" /><circle cx="118" cy="17" r="3" fill="var(--accent-primary)" /></svg>
            <b className="card-title">{busy === 'upload' ? 'Uploading and profiling…' : 'Drop a CSV here'}</b>
            <span className="helper">{live ? 'or click to browse. It is uploaded to the local API and profiled there.' : 'or click to browse. It is profiled in your browser and never uploaded.'}</span>
            <input ref={input} type="file" accept=".csv,.tsv,.txt" hidden data-testid="dataset-file" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
          </div>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
          <div className="card" data-testid="dataset-list">
            <div className="card-head"><h3 className="card-title">{live ? 'Datasets' : 'Sample Datasets'}</h3><span className="micro-label">{live ? 'Local API' : 'Built-in · mock'}</span></div>
            {live && server
              ? server.map((s) => (
                <button key={s.id} data-testid={`dataset-${s.id}`} className={`belt-row ${ds.id === s.id ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => pickServer(s.id)} disabled={busy !== null}>
                  <span className="belt-text"><b className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{s.filename}</b><span className="helper" style={{ fontSize: 12 }}>{s.rows.toLocaleString()} rows × {s.n_cols} cols · {s.note}</span></span>
                  <span className="pin-state">{busy === s.id ? '◌ Profiling' : ds.id === s.id ? '● Selected' : '○ Use'}</span>
                </button>
              ))
              : SAMPLES.map((s) => (
                <button key={s.name} className={`belt-row ${ds.name === s.name ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => pick(s)}>
                  <span className="belt-text"><b className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{s.name}</b><span className="helper" style={{ fontSize: 12 }}>{s.rows.toLocaleString()} rows × {s.cols.length} cols · {s.note}</span></span>
                  <span className="pin-state">{ds.name === s.name ? '● Selected' : '○ Use'}</span>
                </button>
              ))}
            {live && !server && <div className="card-body helper">Loading datasets…</div>}
          </div>
        </div>
        <div className="card" data-testid="dataset-profile" data-source={ds.id ? 'api' : 'mock'}>
          <div className="card-head">
            <div className="left"><h3 className="card-title">Profile</h3><span className="mono" data-testid="profile-name" style={{ fontSize: 12.5 }}>{ds.name}</span>
              {ds.sample && <StatusPill status="neutral">Generated sample</StatusPill>}</div>
            <div className="row"><span data-testid="profile-rows"><ParamChip sym="n" value={ds.rows.toLocaleString()} /></span><span data-testid="profile-k"><ParamChip sym="K" value={String(K)} /></span></div>
          </div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Column</th><th>Type</th><th>Example</th>{p && <th>Missing</th>}<th>Role</th></tr></thead>
            <tbody>
              {ds.cols.map((c) => (
                <tr key={c.name} className={c.name === ds.target ? 'sel' : ''}>
                  <td className="table-cell">{c.name}</td><td><span className="chip" style={{ fontSize: 11 }}>{c.type === 'text' ? 'str' : c.type === 'number' ? 'num' : c.type === 'id' ? 'id' : 'cat'}</span></td>
                  <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.sample}</td>
                  {p && <td className={c.missing ? 'table-cell' : 'muted'}>{c.missing ? `${c.missing.toLocaleString()} · ${((c.missing / ds.rows) * 100).toFixed(1)}%` : '—'}</td>}
                  <td>{c.name === ds.target ? <StatusPill status="success">Target y</StatusPill> : c.type === 'id' ? <span className="muted">Ignored</span>
                    : <span className="muted">Feature</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          {p && (
            <div className="card-body row" data-testid="profile-quality" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap', gap: 8 }}>
              <ParamChip sym="dup" value={p.duplicates.rows.toLocaleString()} />
              <ParamChip sym="missing" value={p.missing.cells.toLocaleString()} />
              <ParamChip sym="holdout" value={`${p.splits.holdout_rows.toLocaleString()} · ${Math.round(p.splits.holdout_share * 100)}%`} />
              <ParamChip sym="folds" value={`${p.splits.n_folds} × ${p.splits.stratified ? 'stratified' : 'random'}`} />
              <ParamChip sym="seed" value={String(p.splits.seed)} />
            </div>
          )}
          {p && p.warnings.length > 0 && (
            <div className="card-body stack" data-testid="profile-warnings" style={{ borderTop: '1px solid var(--border-default)', gap: 4 }}>
              {p.warnings.map((w) => <span key={w} className="helper" style={{ fontSize: 12 }}><span style={{ color: 'var(--status-warning)' }}>▲</span> {w}</span>)}
            </div>
          )}
          <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
            <div className="micro-label" style={{ marginBottom: 8 }}>{p?.task === 'regression' ? 'Numeric label' : 'Classes of'} <span className="mono" style={{ textTransform: 'none' }}>{ds.target || '—'}</span></div>
            {p?.task === 'regression' && p.target_stats && (
              <div className="row" data-testid="profile-target-stats" style={{ flexWrap: 'wrap', gap: 6 }}>
                <ParamChip sym="ȳ" value={p.target_stats.mean.toLocaleString(undefined, { maximumFractionDigits: 1 })} />
                <ParamChip sym="σ_y" value={p.target_stats.std.toLocaleString(undefined, { maximumFractionDigits: 1 })} />
                <ParamChip sym="y ∈" value={`[${p.target_stats.min.toLocaleString(undefined, { maximumFractionDigits: 1 })}, ${p.target_stats.max.toLocaleString(undefined, { maximumFractionDigits: 1 })}]`} />
              </div>
            )}
            {ds.classes.map((c) => (
              <div key={c.name} className="dist-row">
                <span>{c.name}</span>
                <svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(c.share / maxShare) * 100}%`} y1="5" y2="5" stroke={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={c.share < 0.1 ? '4 3' : undefined} /><circle cx={`${(c.share / maxShare) * 100}%`} cy="5" r="3" fill={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} /></svg>
                <span className="table-cell">{(c.share * 100).toFixed(1)}%</span>
              </div>
            ))}
            <p className="helper" data-testid="profile-source" style={{ fontSize: 12, marginTop: 8 }}>{ds.id ? `Profiled by the API · all ${ds.rows.toLocaleString()} rows` : ds.note}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
