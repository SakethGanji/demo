import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { VelaAnswer } from '@/shared/api/types'
import { Icon } from '@/shared/primitives/Icon'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { useApi } from '@/shared/api/mode'
import type { VelaFinding } from '@/shared/api/types'
import { LiveVela } from './LiveVela'
import { DECIDER, DeciderTrail } from './components/DeciderTrail'
import { AgentChat } from './agent/AgentChat'
import { ApiOffline } from '@/shared/components/Status'

/** Stays mounted while closed so applied patches and your questions survive reopening it. */
export function VelaSidecar({ open, onClose, preferredRun, appliedKey, onApplyLive }: {
  open: boolean; onClose: () => void
  /** The run Vela reads (when the URL names none), the applied patch, and how to apply one. */
  preferredRun: string | null; appliedKey: string | null; onApplyLive: (runId: string, f: VelaFinding) => void
}) {
  const { mode } = useApi()
  const [q, setQ] = useState('')
  const [liveRun, setLiveRun] = useState<string | null>(null)
  const [liveQs, setLiveQs] = useState<{ q: string; ctx: string; a?: VelaAnswer; err?: string; t0: number }[]>([])
  const [route, setRoute] = useState<string[] | null>(null)
  const [tab, setTab] = useState<'ask' | 'agent'>('ask')
  const [agentSid, setAgentSid] = useState<string | null>(null)
  const [, setTick] = useState(0)
  const pending = liveQs.some((x) => !x.a && !x.err)
  useEffect(() => { if (open && mode === 'live') api.llm.status().then((s) => setRoute(s.route)).catch(() => setRoute(null)) }, [open, mode])
  useEffect(() => { if (!pending) return; const t = setInterval(() => setTick((n) => n + 1), 100); return () => clearInterval(t) }, [pending])
  const apCtx = () => (location.hash.startsWith('#/vela') ? new URLSearchParams(location.hash.split('?')[1] ?? '').get('ap') : null)
  const askLive = () => {
    const question = q.trim()
    if (!question) return
    const ap = apCtx()
    const idx = liveQs.length
    setLiveQs((x) => [...x, { q: question, ctx: ap ? `autopilot ${ap}` : liveRun ? `run ${liveRun}` : 'no run', t0: performance.now() }])
    setQ('')
    api.vela.ask({ question, run_id: ap ? null : liveRun, autopilot_id: ap })
      .then((a) => setLiveQs((x) => x.map((y, i) => (i === idx ? { ...y, a } : y))))
      .catch((e) => setLiveQs((x) => x.map((y, i) => (i === idx ? { ...y, err: (e as Error).message } : y))))
  }

  return (
    <aside className="sidecar" aria-label="Vela AI Assistant" hidden={!open}>
      <div className="sidecar-head">
        <VelaCrest size={20} />
        <b className="card-title">Vela</b>
        {mode === 'live' && <div className="seg" style={{ marginLeft: 6 }}>
          <button className={tab === 'ask' ? 'on' : ''} onClick={() => setTab('ask')}>Ask</button>
          <button className={tab === 'agent' ? 'on' : ''} onClick={() => setTab('agent')} data-testid="sidecar-agent-tab">Agent</button>
        </div>}
        <span className="spacer" />
        <button className="icon-btn" aria-label="Close Vela" onClick={onClose}><Icon.close width={16} height={16} /></button>
      </div>
      <div className="sidecar-body">
        {mode !== 'live' ? <ApiOffline inline /> : tab === 'agent' ? <AgentChat compact sid={agentSid} setSid={setAgentSid} /> : (
          <>
          <LiveVela open={open} preferredRun={preferredRun} appliedKey={appliedKey} onApply={onApplyLive} onRun={setLiveRun} />
          {liveQs.map((x, i) => (
            <div key={i} className="stack" style={{ gap: 8 }} data-testid="vela-qa">
              <div className="chip" style={{ alignSelf: 'flex-end', fontFamily: 'var(--font-sans)', height: 'auto', padding: '4px 8px', whiteSpace: 'normal' }}>{x.q}</div>
              {x.a || x.err ? (
                <div className="patch">
                  <div className="ph"><VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>Answer</b><span className="spacer" /><span className="micro-label">{x.ctx}</span></div>
                  <p data-testid="vela-answer">{x.err ?? x.a!.answer ?? x.a!.message}</p>
                  <div className="vela-source" data-testid="vela-answer-source">{x.a ? <>
                    decided by: <b data-testid="vela-decider">{x.a.cached ? `${DECIDER[x.a.source] ?? x.a.source} (cached)` : x.a.source === 'computed' ? 'no model (computed)' : DECIDER[x.a.source] ?? x.a.source}</b>
                    {x.a.model ? <span className="mono"> · {x.a.model}</span> : null}
                    <span> · latency <b className="mono" data-testid="vela-latency">{x.a.seconds < 0.01 ? `${(x.a.seconds * 1000).toFixed(2)} ms` : `${x.a.seconds.toFixed(2)} s`}</b></span>
                    <span style={{ flexBasis: '100%' }} />
                    <span className="muted">route:</span> <DeciderTrail attempts={x.a.attempts} source={x.a.source} seconds={x.a.seconds} cached={x.a.cached} originalSeconds={x.a.original_seconds} />
                    <span style={{ flexBasis: '100%' }} />
                    <span className="muted">facts: {x.a.facts.join(', ')}{x.a.facts_sha256 ? <> · key = (h(prompt) <span className="mono">{x.a.prompt_sha256?.slice(0, 8)}</span>, h(facts) <span className="mono">{x.a.facts_sha256.slice(0, 8)}</span>)</> : null}</span>
                  </> : 'request failed'}</div>
                  {x.a?.guard ? (
                    <div className={`vela-guard ${x.a.guard.ok ? '' : 'warn'}`} data-testid="vela-guard">
                      {x.a.guard.ok
                        ? <>Number guard: all {x.a.guard.checked} number{x.a.guard.checked === 1 ? '' : 's'} in the answer appear in the computed facts.</>
                        : <>Number guard: <b>{x.a.guard.unsupported.join(', ')}</b> {x.a.guard.unsupported.length === 1 ? 'is' : 'are'} not in the computed facts. Check before you rely on {x.a.guard.unsupported.length === 1 ? 'it' : 'them'}.</>}
                    </div>
                  ) : null}
                </div>
              ) : <div className="thinking" data-testid="vela-pending"><ResonanceWave width={84} height={18} fast /> Asking {route?.length ? DECIDER[route[0]] : 'the language model'} about {x.ctx} · <span className="mono">{((performance.now() - x.t0) / 1000).toFixed(1)} s</span></div>}
            </div>
          ))}
          </>
        )}
      </div>
      <div className="sidecar-input" hidden={mode !== 'live' || tab === 'agent'}>
        <div className="box">
          <input placeholder={apCtx() ? 'Ask Vela about this autopilot…' : 'Ask Vela about this run…'} value={q} data-testid="vela-ask"
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && askLive()} />
          <button className="icon-btn" aria-label="Send" onClick={askLive} style={{ color: 'var(--accent-primary)' }}><Icon.send width={16} height={16} /></button>
        </div>
      </div>
    </aside>
  )
}
