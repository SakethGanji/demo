import { useRef, useState } from 'react'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { SAMPLES, profileCsv, type Dataset } from '../datasets'

/** Step 1: drop a CSV or pick a sample, then read its profile. */
export function DatasetStep({ ds, onPick }: { ds: Dataset; onPick: (d: Dataset) => void }) {
  const [err, setErr] = useState('')
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const K = ds.classes.length
  const pick = (d: Dataset) => { setErr(''); onPick(d) }
  const onFile = async (f?: File) => {
    if (!f) return
    if (!/\.(csv|tsv|txt)$/i.test(f.name)) { setErr(`${f.name} is not a CSV. This demo profiles CSV files in your browser; pick a sample for Parquet.`); return }
    try { pick(await profileCsv(f)) } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.5fr)', alignItems: 'start' }}>
        <div className="stack">
          <div className={`drop ${over ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0]) }} onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && input.current?.click()}>
            <svg width="120" height="34" viewBox="0 0 120 34" aria-hidden><path d="M2 17C20 -8 40 42 60 17S100 -8 118 17" stroke="var(--accent-secondary)" strokeWidth="1.75" fill="none" /><path d="M2 17C20 4 40 30 60 17S100 4 118 17" stroke="var(--accent-primary)" strokeWidth="1.75" fill="none" opacity=".6" /><circle cx="2" cy="17" r="3" fill="var(--accent-primary)" /><circle cx="118" cy="17" r="3" fill="var(--accent-primary)" /></svg>
            <b className="card-title">Drop a CSV here</b>
            <span className="helper">or click to browse. It is profiled in your browser and never uploaded.</span>
            <input ref={input} type="file" accept=".csv,.tsv,.txt" hidden onChange={(e) => onFile(e.target.files?.[0])} />
          </div>
          {err && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
          <div className="card">
            <div className="card-head"><h3 className="card-title">Sample Datasets</h3><span className="micro-label">Workspace</span></div>
            {SAMPLES.map((s) => (
              <button key={s.name} className={`belt-row ${ds.name === s.name ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => pick(s)}>
                <span className="belt-text"><b className="mono" style={{ fontSize: 12.5, fontWeight: 500 }}>{s.name}</b><span className="helper" style={{ fontSize: 12 }}>{s.rows.toLocaleString()} rows × {s.cols.length} cols · {s.note}</span></span>
                <span className="pin-state">{ds.name === s.name ? '● Selected' : '○ Use'}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Profile</h3><span className="mono" style={{ fontSize: 12.5 }}>{ds.name}</span></div>
            <div className="row"><ParamChip sym="n" value={ds.rows.toLocaleString()} /><ParamChip sym="K" value={String(K)} /></div>
          </div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Column</th><th>Type</th><th>Example</th><th>Role</th></tr></thead>
            <tbody>
              {ds.cols.map((c) => (
                <tr key={c.name} className={c.name === ds.target ? 'sel' : ''}>
                  <td className="table-cell">{c.name}</td><td><span className="chip" style={{ fontSize: 11 }}>{c.type === 'text' ? 'str' : c.type === 'number' ? 'num' : c.type === 'id' ? 'id' : 'cat'}</span></td>
                  <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.sample}</td>
                  <td>{c.name === ds.target ? <StatusPill status="success">Target y</StatusPill> : c.type === 'id' ? <span className="muted">Ignored</span>
                    : <span className="muted">Feature</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
            <div className="micro-label" style={{ marginBottom: 8 }}>Classes of <span className="mono" style={{ textTransform: 'none' }}>{ds.target}</span></div>
            {ds.classes.map((c) => (
              <div key={c.name} className="dist-row">
                <span>{c.name}</span>
                <svg width="100%" height="10" aria-hidden><line x1="0" x2={`${(c.share / Math.max(...ds.classes.map((x) => x.share))) * 100}%`} y1="5" y2="5" stroke={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={c.share < 0.1 ? '4 3' : undefined} /><circle cx={`${(c.share / Math.max(...ds.classes.map((x) => x.share))) * 100}%`} cy="5" r="3" fill={c.share < 0.1 ? 'var(--status-warning)' : 'var(--accent-primary)'} /></svg>
                <span className="table-cell">{(c.share * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
