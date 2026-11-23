import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { AgentItem, AgentSession, AgentSessionSummary, AgentStatus, AgentStep } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { DECIDER } from '../components/DeciderTrail'

const EXAMPLES = [
  'List my datasets and train a model on the newest one, then run the Model Doctor and register the best version.',
  'Which features matter most in my latest run, and how sure are we?',
  'Score the latest batch and tell me if anything drifted.',
]
const WORKING: AgentSession['status'][] = ['thinking', 'calling', 'awaiting_confirmation']
const USER_KEY = 'ensemble.agent.user'

/** The few scalar fields of a tool result worth a glance (ids, status, the metric), for the card's one line. */
function glance(r: Record<string, unknown> | undefined): string {
  if (!r) return ''
  const keys = ['run_id', 'version_id', 'batch_id', 'dataset_id', 'status', 'metric', 'holdout_score', 'best_model', 'decision', 'p_adverse_class',
    'threshold', 'delta', 'stage', 'approval', 'headline', 'n', 'rows', 'patch', 'autopilot_id']
  const out: string[] = []
  for (const k of keys) {
    const v = r[k]
    if (v == null || typeof v === 'object') continue
    out.push(k === 'headline' ? String(v).slice(0, 110) : `${k.replace(/_/g, ' ')} ${String(v)}`)
    if (out.length >= 4) break
  }
  const d = r.drift as { max_psi?: number; drifted?: string[] } | undefined
  if (d) out.push(`max PSI ${d.max_psi}${d.drifted?.length ? ` · drifted: ${d.drifted.join(', ')}` : ''}`)
  const p = r.primary as { title?: string } | undefined
  if (p?.title) out.push(`primary: ${p.title}`)
  const o = r.options as unknown[] | undefined
  if (Array.isArray(o)) out.push(`${o.length} option${o.length === 1 ? '' : 's'}`)
  const v = r.versions as unknown[] | undefined
  if (Array.isArray(v)) out.push(`${v.length} version${v.length === 1 ? '' : 's'}`)
  const ds = r.datasets as unknown[] | undefined
  if (Array.isArray(ds)) out.push(`${ds.length} datasets`)
  return out.join(' · ')
}

const argLine = (a: Record<string, unknown>) => Object.entries(a).map(([k, v]) => `${k}=${typeof v === 'object' ? (JSON.stringify(v).length > 40 ? '{…}' : JSON.stringify(v)) : JSON.stringify(v)}`).join(', ')

function StepCard({ s, onConfirm, compact }: { s: AgentStep; onConfirm: (ok: boolean) => void; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const icon = { done: '✓', error: '✕', invalid: '✕', declined: '–', stopped: '■', running: '', awaiting: '?' }[s.status]
  return (
    <div className={`ag-step ${s.status} ${s.kind}`} data-testid="agent-step" data-tool={s.tool} data-status={s.status}>
      <div className="ag-step-head">
        <span className="ag-ic">{s.status === 'running' ? <ResonanceWave width={22} height={12} fast /> : icon}</span>
        <span className="ag-n">{s.n}</span>
        <b className="mono">{s.tool}</b>
        {s.kind === 'write' && <span className="pill warning ag-kind">changes state</span>}
        <span className="spacer" />
        {s.status === 'running' && s.progress ? <span className="mono ag-sec">{s.progress}</span> : s.seconds != null ? <span className="mono ag-sec">{s.seconds.toFixed(1)} s</span> : null}
        {s.link && s.status === 'done' && <a className="ag-link" href={s.link}>Open →</a>}
      </div>
      {s.why && !compact && <div className="ag-why">{s.why}</div>}
      {s.status === 'awaiting' ? (
        <div className="ag-confirm" data-testid="agent-confirm">
          <div className="micro-label">Vela wants to make this call. It changes state, so it waits for you.</div>
          <code className="ag-call">{s.http}</code>
          <pre className="ag-args">{JSON.stringify(s.args, null, 1)}</pre>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary sm" disabled={busy} data-testid="agent-run-it" onClick={() => { setBusy(true); onConfirm(true) }}>Run It</button>
            <button className="btn sm ghost" disabled={busy} data-testid="agent-skip" onClick={() => { setBusy(true); onConfirm(false) }}>Skip</button>
          </div>
        </div>
      ) : (
        <details className="ag-detail">
          <summary><span className="mono ag-argline">{argLine(s.args)}</span>{s.status === 'done' && <span className="ag-glance">{glance(s.result)}</span>}</summary>
          {s.http && <code className="ag-call">{s.http}</code>}
          <pre className="ag-args">{JSON.stringify({ args: s.args, result: s.result ?? s.error }, null, 1)}</pre>
        </details>
      )}
      {(s.status === 'error' || s.status === 'invalid') && <div className="ag-err">{s.error}</div>}
      {s.status === 'declined' && <div className="ag-why">Skipped by you; nothing was changed.</div>}
    </div>
  )
}

function Item({ it, onConfirm, compact }: { it: AgentItem; onConfirm: (id: string, ok: boolean) => void; compact?: boolean }) {
  if (it.type === 'user') return <div className="ag-user" data-testid="agent-user">{it.text}</div>
  if (it.type === 'step') return <StepCard s={it} compact={compact} onConfirm={(ok) => onConfirm(it.id, ok)} />
  if (it.type === 'answer') return (
    <div className="patch ag-answer" data-testid="agent-answer">
      <div className="ph"><VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>Answer</b><span className="spacer" />
        {it.decider?.source && <span className="micro-label">{DECIDER[it.decider.source] ?? it.decider.decider}{it.decider.seconds != null ? ` · ${it.decider.seconds.toFixed(1)} s` : ''}</span>}</div>
      <p style={{ whiteSpace: 'pre-wrap' }}>{it.text}</p>
      <div className={`vela-guard ${it.guard.ok ? '' : 'warn'}`} data-testid="agent-guard" data-ok={it.guard.ok} title={it.guard.rule}>
        {it.guard.ok
          ? <>Number guard: all {it.guard.numbers} number{it.guard.numbers === 1 ? '' : 's'} in the answer appear in the tool results{it.guard.rewritten ? ' (after one rewrite)' : ''}.</>
          : <>Number guard: <b>{it.guard.unsupported.join(', ')}</b> {it.guard.unsupported.length === 1 ? 'is' : 'are'} in no tool result. Check before you rely on {it.guard.unsupported.length === 1 ? 'it' : 'them'}.</>}
      </div>
    </div>
  )
  if (it.type === 'offer') return (
    <div className="card card-body stack ag-offer" style={{ gap: 8 }} data-testid="agent-offer">
      <span>{it.text}</span>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{it.links.map((l) => <a key={l.href} className="btn sm" href={l.href}>{l.label}</a>)}</div>
    </div>
  )
  return <div className={`ag-note ${it.error ? 'err' : ''}`}>{it.text}</div>
}

/** The Vela agent's conversation: ask in plain language; it plans, calls the workbench's own API one tool at a time,
 *  stops for your OK before anything that changes state, and answers with numbers from the tool results only.
 *  Used by the Vela page and (compact) by the sidecar. `sid`/`setSid` keep the open conversation (the page puts it in the URL). */
export function AgentChat({ sid, setSid, compact }: { sid: string | null; setSid: (id: string | null) => void; compact?: boolean }) {
  const [s, setS] = useState<AgentSession | null>(null)
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [recent, setRecent] = useState<AgentSessionSummary[]>([])
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  const [user, setUser] = useState(() => localStorage.getItem(USER_KEY) || 'analyst')
  const end = useRef<HTMLDivElement>(null)
  const working = !!s && WORKING.includes(s.status)

  useEffect(() => { api.agent.status().then(setStatus).catch(() => setStatus(null)); api.agent.list().then(setRecent).catch(() => {}) }, [sid])
  useEffect(() => {
    if (!sid) { setS(null); return }
    let alive = true
    let rev = -1
    let t: ReturnType<typeof setTimeout>
    const poll = () => api.agent.get(sid, rev >= 0 ? rev : undefined).then((x) => {
      if (!alive) return
      if (!x.unchanged) { setS(x); rev = x.rev }
      setErr('')
      t = setTimeout(poll, x.unchanged || WORKING.includes(x.status) ? 700 : 4000)
    }).catch((e) => { if (alive) { setErr((e as Error).message); t = setTimeout(poll, 3000) } })
    poll()
    return () => { alive = false; clearTimeout(t) }
  }, [sid])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [s?.rev])

  const send = (text = q) => {
    const m = text.trim()
    if (!m) return
    localStorage.setItem(USER_KEY, user)
    setQ(''); setErr('')
    const p = s && sid ? api.agent.say(sid, m) : api.agent.start(m, user)
    p.then((x) => { setS(x); setSid(x.id) }).catch((e) => setErr((e as Error).message))
  }
  const confirm = (id: string, ok: boolean) => { if (sid) api.agent.confirm(sid, id, ok).catch((e) => setErr((e as Error).message)) }
  const steps = s?.timeline.filter((i) => i.type === 'step').length ?? 0
  const planner = status ? (status.default === 'none' ? 'no model available' : DECIDER[status.default] ?? status.default) : '…'

  return (
    <div className={`ag ${compact ? 'compact' : ''}`} data-testid="agent-chat" data-status={s?.status ?? 'new'}>
      <div className="ag-scroll">
        <div className="ag-inner">
          {!s ? (
            <div className="stack" style={{ gap: 14 }}>
              <div className="ag-intro">
                <VelaCrest size={compact ? 22 : 30} />
                <div>
                  <b className="card-title">Tell Vela what you need. It does the work through the workbench's own API.</b>
                  <div className="helper">Reads run freely. Anything that trains, registers, scores or saves waits for your one click. Approval and Production stay human.</div>
                </div>
              </div>
              <div className="stack" style={{ gap: 6 }}>
                {EXAMPLES.map((e) => <button key={e} className="ag-example" onClick={() => send(e)} data-testid="agent-example">{e}</button>)}
              </div>
              {!compact && recent.length > 0 && <div className="stack" style={{ gap: 4 }}>
                <span className="micro-label">Recent</span>
                {recent.slice(0, 5).map((r) => <button key={r.id} className="ag-recent" onClick={() => setSid(r.id)}>
                  <span className="mono">{r.id}</span> {r.title}<span className="muted"> · {r.steps} calls · {r.status}</span></button>)}
              </div>}
            </div>
          ) : s.timeline.map((it, i) => <Item key={i} it={it} compact={compact} onConfirm={confirm} />)}
          {s && working && s.status === 'thinking' && <div className="thinking" data-testid="agent-thinking"><ResonanceWave width={84} height={18} fast /> {DECIDER[s.decider] ?? s.decider} is choosing the next step…</div>}
          {err && <div className="ag-note err">{err}</div>}
          <div ref={end} />
        </div>
      </div>
      <div className="ag-foot">
        <div className="ag-inner">
          <div className="ag-box">
            <textarea rows={compact ? 2 : 2} value={q} placeholder={s ? 'Ask a follow-up…' : 'e.g. Train a default model on my loans table with an out-of-time split…'} data-testid="agent-input"
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!working) send() } }} />
            {working
              ? <button className="btn sm" data-testid="agent-stop" onClick={() => sid && api.agent.stop(sid).catch(() => {})}>■ Stop</button>
              : <button className="btn primary sm" data-testid="agent-send" disabled={!q.trim()} onClick={() => send()}>Send</button>}
          </div>
          <div className="ag-meta">
            <span>planner <b>{s ? DECIDER[s.decider] ?? s.decider : planner}</b></span>
            {s && <span>{steps} / {s.budget.max_steps} calls · {Math.round(s.budget.seconds)} / {Math.round(s.budget.max_seconds)} s</span>}
            {status && <span>{status.tools} tools · {status.write_tools.length} need your OK</span>}
            <span className="spacer" />
            {!compact && <label>acting for <input className="ag-user-in" value={user} onChange={(e) => setUser(e.target.value)} disabled={!!s} /></label>}
            {s && <button className="btn sm ghost" onClick={() => setSid(null)} data-testid="agent-new">New Conversation</button>}
          </div>
        </div>
      </div>
    </div>
  )
}
