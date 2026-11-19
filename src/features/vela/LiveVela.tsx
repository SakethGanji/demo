import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run, VelaFinding, VelaResponse, VelaWording } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { MathText } from '@/shared/components/MathText'
import { DoctorPanel } from '@/features/explain/components/DoctorPanel'

const SOURCE: Record<string, string> = { 'anthropic-sdk': 'Anthropic SDK', 'claude-cli': 'Claude CLI', 'anthropic-api': 'Anthropic API', computed: 'computed text' }
const SEV: Record<string, 'danger' | 'warning' | 'info'> = { critical: 'danger', warning: 'warning', info: 'info' }

const show = (x: unknown) => (x == null ? '—' : typeof x === 'string' ? `'${x}'` : JSON.stringify(x).length > 60 ? `${JSON.stringify(x).slice(0, 57)}…` : JSON.stringify(x))

/** Which run Vela reads: the run in the URL (Explain, New Model), the Gym's source run, else the latest finished run. */
function useContextRun(preferred: string | null) {
  const [hashRun, setHashRun] = useState<string | null>(null)
  useEffect(() => {
    const read = () => setHashRun(new URLSearchParams(location.hash.split('?')[1] ?? '').get('run'))
    read()
    addEventListener('hashchange', read)
    const t = setInterval(read, 1500)
    return () => { removeEventListener('hashchange', read); clearInterval(t) }
  }, [])
  return hashRun ?? preferred
}

export function LiveVela({ open, preferredRun, appliedKey, onApply, onRun }: {
  open: boolean; preferredRun: string | null; appliedKey: string | null; onApply: (runId: string, f: VelaFinding) => void
  /** Tells the sidecar which run is in view, for the question box. */
  onRun?: (runId: string | null) => void
}) {
  const ctx = useContextRun(preferredRun)
  const [runs, setRuns] = useState<Run[]>([])
  const [picked, setPicked] = useState<string | null>(null)
  const [res, setRes] = useState<VelaResponse | null>(null)
  const [err, setErr] = useState('')
  const [wording, setWording] = useState<VelaWording | null>(null)
  const [wordingBusy, setWordingBusy] = useState(false)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  // The run list refreshes while open, so a run that finishes after Vela opened (the one in the URL) appears.
  useEffect(() => {
    if (!open) return
    const load = () => api.runs.list().then((r) => setRuns((old) => {
      const next = r.filter((x) => x.status === 'succeeded')
      return next.map((x) => x.id).join() === old.map((x) => x.id).join() ? old : next
    })).catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [open, ctx])
  const runId = picked ?? (ctx && runs.some((r) => r.id === ctx) ? ctx : runs[0]?.id ?? null)
  useEffect(() => { onRun?.(runId) }, [runId])

  useEffect(() => {
    if (!open || !runId) return
    let alive = true
    setRes(null); setErr(''); setWording(null)
    api.vela.findings(runId).then((r) => {
      if (!alive) return
      setRes(r)
      setWording(r.wording)
      if (!r.wording && r.findings.length) {
        setWordingBusy(true)
        api.vela.word(runId).then((w) => alive && setWording(w)).catch(() => {}).finally(() => alive && setWordingBusy(false))
      }
    }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [open, runId])

  const text = (f: VelaFinding) => (wording && wording.key === res?.key ? wording.texts[f.id] ?? f.computed_text : f.computed_text)

  return (
    <>
      {runs.length > 0 && <div className="vela-ctx" data-testid="vela-context">
        <span className="micro-label">Run</span>
        <select className="select sm" value={runId ?? ''} onChange={(e) => setPicked(e.target.value)} aria-label="Run Vela reads" data-testid="vela-run">
          {runs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.name}</option>)}
        </select>
      </div>}
      <div className="vela-source" data-testid="vela-source">
        {wordingBusy ? <><ResonanceWave width={40} height={12} fast /> wording with the language model…</>
          : wording ? <>wording: <b>{SOURCE[wording.source]}</b>{wording.model ? <span className="mono"> · {wording.model}</span> : null}{wording.source !== 'computed' ? <span className="mono"> · {wording.cached ? 'cached' : `${wording.seconds.toFixed(2)} s`}</span> : null}{wording.source === 'computed' && wording.attempts.length ? <span className="muted"> ({wording.attempts.map((a) => `${SOURCE[a.source] ?? a.source}: ${a.error ?? 'ok'}`).join('; ')})</span> : null}{wording.disabled ? <span className="muted"> (LLM off)</span> : null}</>
            : res ? <>wording: <b>computed text</b></> : null}
        {(wordingBusy || wording || res) && <span className="muted"> · </span>}<span className="muted">numbers always computed from the run</span>
      </div>
      {!runs.length && !err && <p className="helper" style={{ fontSize: 12 }}>No finished runs yet: train one in New Model or the Gym, and Vela reads it.</p>}
      {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      {runId && !res && !err && <div className="thinking"><ResonanceWave width={84} height={18} fast /> Computing findings from {runId}</div>}
      {res && runs.find((r) => r.id === res.run_id) && (!runs.find((r) => r.id === res.run_id)!.kind || runs.find((r) => r.id === res.run_id)!.kind === 'tabular') &&
        <DoctorPanel run={runs.find((r) => r.id === res.run_id)!} compact links={{ go: (r) => { location.hash = `#/${r}` }, onApplyPatch: (rid, p) => onApply(rid, { id: p.id, kind: 'doctor', severity: 'warning', title: p.title, text: '', computed_text: '', numbers: {}, formula: '', patch: p } as unknown as VelaFinding) }} />}
      {res && res.findings.length === 0 && <div className="patch" data-testid="vela-none"><div className="ph"><VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>Nothing to fix</b></div><p>No check fired on {res.run_id}: no leakage suspect, no overfit gap beyond 2 SE, the budget was not exhausted, no weak class, segment, imbalance, drift or degenerate column.</p></div>}
      {res?.findings.filter((f) => !dismissed.has(f.id)).map((f) => {
        const applied = appliedKey === `${res.run_id}:${f.patch.id}`
        return (
          <div key={f.id} className={`patch live ${applied ? 'applied' : ''}`} data-testid={`vela-finding-${f.kind}`}>
            <div className="ph">
              <VelaCrest size={16} />
              <b className="card-title" style={{ fontSize: 13 }}>{f.title}</b>
              <span className="spacer" />
              {applied ? <StatusPill status="success">Applied</StatusPill> : <span className={`pill ${SEV[f.severity]}`}>{f.severity}</span>}
            </div>
            <p data-testid="vela-text">{text(f)}</p>
            {f.formula && <div className="vela-formula math"><MathText text={f.formula} /></div>}
            <div className="patch-title micro-label">Patch · {f.patch.title}</div>
            {f.patch.changes.map((c) => (
              <div key={c.path} className="diffline" title={c.label}><span className="mono k">{c.path}</span> <span className="old">{show(c.from)}</span> → <span className="new">{show(c.to)}</span></div>
            ))}
            {!applied && (
              <div className="row">
                <button className="btn sm primary" onClick={() => onApply(res.run_id, f)} data-testid={`vela-apply-${f.kind}`}>Apply Patch</button>
                <button className="btn sm ghost" onClick={() => setDismissed((s) => new Set(s).add(f.id))}>Dismiss</button>
              </div>
            )}
          </div>
        )
      })}
      {res && res.errors.length > 0 && <p className="helper" style={{ fontSize: 11 }}>Some checks could not run: {res.errors.join('; ')}</p>}
    </>
  )
}
