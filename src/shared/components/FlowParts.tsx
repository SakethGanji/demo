import { useState } from 'react'
import { flows } from '@/shared/api/flows'
import type { LiveState } from '@/shared/hooks/useRunEvents'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

/** Pieces shared by the forecast, anomaly and import pages. */
export const FAMILY_COLOR: Record<string, string> = { baseline: 'var(--text-muted)', statistical: 'var(--viz-b)', tabular: 'var(--viz-a)', deep: 'var(--viz-c)', foundation: 'var(--viz-e)', ensemble: 'var(--viz-d)', iforest: 'var(--viz-a)', lof: 'var(--viz-c)', other: 'var(--text-body)' }
const f3 = (v: number | null | undefined, d = 3) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d))

export function LiveModels({ live, metric }: { live: LiveState; metric: string }) {
  const done = live.models.filter((m) => m.val != null)
  const vals = done.map((m) => m.val!)
  const lo = Math.min(...vals, 0), hi = Math.max(...vals, 1e-9)
  return (
    <div className="card" data-testid="fc-live">
      <div className="card-head"><div className="left"><h3 className="card-title">Live · models as they fit</h3><span className="helper" style={{ fontSize: 12 }}>{metric} · {live.stage}</span></div><ResonanceWave /></div>
      <div className="card-body stack" style={{ gap: 6 }}>
        {live.models.length === 0 && <p className="helper row"><ResonanceWave /> Preparing data and models…</p>}
        {live.models.map((m) => (
          <div key={m.model} className="fc-live-row">
            <span className="dot" style={{ background: FAMILY_COLOR[m.family ?? 'other'] }} />
            <span className="mono name">{m.model}</span><span className="muted fam">{m.family}</span>
            <span className="bar">{m.status === 'running' ? <ResonanceWave /> : m.val != null ? <i style={{ width: `${(100 * (m.val - lo)) / (hi - lo || 1)}%`, background: FAMILY_COLOR[m.family ?? 'other'] }} /> : null}</span>
            <span className="mono r">{m.status === 'failed' ? 'failed' : m.val != null ? f3(m.val) : '…'}</span>
            <span className="mono r muted">{m.fit != null ? `${m.fit.toFixed(1)} s` : ''}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function Register({ runId, go, name }: { runId: string; go: (r: string) => void; name: string }) {
  const [open, setOpen] = useState(false)
  const [who, setWho] = useState(() => localStorage.getItem('ensemble.actor') ?? '')
  const [model, setModel] = useState(name.replace(/[^A-Za-z0-9_.-]+/g, '-'))
  const [state, setState] = useState<string>('')
  const submit = async () => {
    localStorage.setItem('ensemble.actor', who)
    setState('registering…')
    try { const v = await flows.register(runId, who, model); setState(''); go(`registry?version=${v.id}`) } catch (e) { setState(`✕ ${(e as Error).message}`) }
  }
  if (!open) return <button className="btn primary" onClick={() => setOpen(true)} data-testid="flow-register">Register…</button>
  return (
    <div className="row flow-register">
      <input className="input" placeholder="your name (audit log)" value={who} onChange={(e) => setWho(e.target.value)} style={{ width: 170 }} />
      <input className="input mono" value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 230 }} aria-label="Registered model name" />
      <button className="btn primary" disabled={!who.trim()} onClick={submit} data-testid="flow-register-go">Register</button>
      <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      {state && <span className="helper" style={{ fontSize: 12 }}>{state}</span>}
    </div>
  )
}
