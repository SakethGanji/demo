import { usePoc } from '@/shared/hooks/usePoc'
import type { Params } from '@/features/training-gym/params'
import { useEffect, useState } from 'react'
import { useApi } from '@/shared/api/mode'

/** Live when the API answers, mock (the in-browser simulation) when it doesn't. Glyph plus word, never colour alone. */
function ApiMode() {
  const { mode, health, recheck } = useApi()
  const label = mode === 'live' ? `● live · API ${health?.version ?? ''}` : mode === 'mock' ? '▲ mock mode · API offline' : '○ connecting'
  const title = mode === 'live' ? 'Connected to the ENSEMBLE API: datasets and runs are real.'
    : mode === 'mock' ? 'The API is not reachable, so the app runs on its built-in simulation. Click to check again.' : 'Checking the API…'
  return <button type="button" className={`api-mode ${mode}`} data-testid="api-mode" data-mode={mode} title={title} onClick={recheck}>{label}</button>
}

/** ENSEMBLE_POC_MODE=1: say so on every page, so nobody mistakes a single-person test setup for the real rules. */
function PocMode() {
  const on = usePoc()
  return on ? <><span className="sb-poc" data-testid="sb-poc" title="ENSEMBLE_POC_MODE=1 on the API: four eyes off, everyone counts as admin and owner, every permission check passes. Masking, integrity checks, legal holds and the audit log stay on.">▲ POC mode · one-person approvals</span><span className="sb-sep" /></> : null
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

/** Live mode: the API's real state (GET /api/status): the active run and its progress, the queue, the machine. */
function LiveStatus() {
  const { status } = useApi()
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  const a = status?.active
  const elapsed = a?.started_at ? Math.max(0, (now - new Date(a.started_at).getTime()) / 1000) : null
  return <>
    <span data-testid="sb-engine">AutoGluon {status?.engine.version ?? '…'}</span><span className="sb-sep" />
    {a ? (
      <a className="live sb-run" href={`#/quick-train?run=${a.id}`} data-testid="sb-active" title={a.name}>
        <i className="live-dot" style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
        {a.id} {a.stage === 'model_started' || a.stage === 'model_finished' || a.stage === 'fit_started' ? 'training' : a.stage.replace(/_/g, ' ')} · {a.models_finished} / {a.models_started} models{a.best_val != null ? ` · best ${a.best_val.toFixed(4)}` : ''}{elapsed != null ? ` · ${clock(elapsed)}${a.time_limit ? ` / ${clock(a.time_limit)}` : ''}` : ''}
      </a>
    ) : <span className="muted-sb" data-testid="sb-active">no run training</span>}
    <span data-testid="sb-queued">{status?.queued ?? 0} queued</span>
    {status?.registry && <><span className="sb-sep" /><a className="sb-reg" href="#/registry" data-testid="sb-registry">{status.registry.stages.Production} in production · {status.registry.approvals.pending} pending review</a></>}
    {status?.pool && status.pool.active_jobs > 0 && <span data-testid="sb-pool">· {status.pool.active_jobs} worker job{status.pool.active_jobs > 1 ? 's' : ''}</span>}
    <span className="spacer" />
    <span data-testid="sb-runs">{status?.runs ?? '…'} runs</span><span className="sb-sep" />
    <span data-testid="sb-cpu" className="sb-wide">{status?.cpu_count ?? '…'} CPUs{status?.memory_gb ? ` · ${status.memory_gb} GB` : ''}</span><span className="sb-sep sb-wide" />
    <span className="sb-wide">Python {status?.python ?? '…'}</span><span className="sb-sep sb-wide" /><span data-testid="sb-version">ENSEMBLE {status?.version ?? ''}</span>
  </>
}

export function StatusBar({ params }: { params: Params }) {
  const { mode } = useApi()
  if (mode === 'live') return <footer className="statusbar"><ApiMode /><span className="sb-sep" /><LiveStatus /></footer>
  return (
    <footer className="statusbar">
      <ApiMode /><span className="sb-sep" /><PocMode />
      <span className="ok">● gym-default</span><span>6 / 6 nodes</span><span className="sb-sep" /><span className="muted-sb">simulated:</span>
      <span className="live"><i className="live-dot" style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />run_8a10 training · fold 3 of 5</span>
      <span>1 queued</span><span className="sb-sep" />
      <span>C=<b>{Number.isInteger(params.C) ? params.C.toFixed(1) : params.C}</b> · class_weight=<b>{params.classWeight === 'None' ? 'None' : "'balanced'"}</b></span>
      <span className="spacer" />
      <span>Draft saved 12s ago</span><span className="sb-sep" /><span>us-east-1</span><span className="sb-sep" /><span>v3.0.0-rc1</span><span className="sb-sep" /><span>ENSEMBLE 2.6.1</span>
    </footer>
  )
}
