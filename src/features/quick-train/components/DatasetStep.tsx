import { useState } from 'react'
import { DropZone } from '@/shared/components/DropZone'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { SAMPLES, profileCsv, type Dataset } from '../datasets'

/**
 * Step 1 in mock mode: drop a CSV (profiled in the browser, never uploaded) or pick a built-in sample.
 * Live mode picks and uploads data in Build (G0 Smart Intake), which opens New Model on its Target step.
 */
export function DatasetStep({ ds, onPick }: { ds: Dataset; onPick: (d: Dataset) => void }) {
  const [err, setErr] = useState('')
  const K = ds.classes.length
  const pick = (d: Dataset) => { setErr(''); onPick(d) }
  const onFile = async (f: File) => { try { pick(await profileCsv(f)) } catch (e) { setErr((e as Error).message) } }
  const maxShare = Math.max(...ds.classes.map((x) => x.share), 0.0001)
  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.5fr)', alignItems: 'start' }}>
        <div className="stack">
          <DropZone title="Drop a CSV here" helper="or click to browse. It is profiled in your browser and never uploaded." onFile={onFile} onError={setErr} />
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
          <div className="card" data-testid="dataset-list">
            <div className="card-head"><h3 className="card-title">Sample Datasets</h3><span className="micro-label">Built-in · mock</span></div>
            {SAMPLES.map((s) => (
              <button key={s.name} className={`belt-row ${ds.name === s.name ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => pick(s)}>
                <span className="belt-text"><b className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{s.name}</b><span className="helper" style={{ fontSize: 12 }}>{s.rows.toLocaleString()} rows × {s.cols.length} cols · {s.note}</span></span>
                <span className="pin-state">{ds.name === s.name ? '● Selected' : '○ Use'}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="card" data-testid="dataset-profile" data-source="mock">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Profile</h3><span className="mono" data-testid="profile-name" style={{ fontSize: 12.5 }}>{ds.name}</span></div>
            <div className="row"><span data-testid="profile-rows"><ParamChip sym="n" value={ds.rows.toLocaleString()} /></span><span data-testid="profile-k"><ParamChip sym="K" value={String(K)} /></span></div>
          </div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Column</th><th>Type</th><th>Example</th><th>Role</th></tr></thead>
            <tbody>
              {ds.cols.map((c) => (
                <tr key={c.name} className={c.name === ds.target ? 'sel' : ''}>
                  <td className="table-cell">{c.name}</td><td><span className="chip" style={{ fontSize: 11 }}>{c.type === 'text' ? 'str' : c.type === 'number' ? 'num' : c.type === 'id' ? 'id' : 'cat'}</span></td>
                  <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.sample}</td>
                  <td>{c.name === ds.target ? <span className="pill success">Target y</span> : <span className="muted">{c.type === 'id' ? 'Ignored' : 'Feature'}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
            <div className="micro-label" style={{ marginBottom: 8 }}>Classes of <span className="mono" style={{ textTransform: 'none' }}>{ds.target || '—'}</span></div>
            {ds.classes.map((c) => (
              <div key={c.name} className="dist-row">
                <span>{c.name}</span>
                <svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(c.share / maxShare) * 100}%`} y1="5" y2="5" stroke={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={c.share < 0.1 ? '4 3' : undefined} /><circle cx={`${(c.share / maxShare) * 100}%`} cy="5" r="3" fill={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} /></svg>
                <span className="table-cell">{(c.share * 100).toFixed(1)}%</span>
              </div>
            ))}
            <p className="helper" data-testid="profile-source" style={{ fontSize: 12, marginTop: 8 }}>{ds.note}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
