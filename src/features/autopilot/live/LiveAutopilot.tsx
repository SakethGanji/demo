import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ApCheck, ApStep, Autopilot } from '@/shared/api/types'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { DataChanges } from '../components/DataChanges'
import { IterationString } from '../components/IterationString'
import { Gauge } from '../components/RequirementGauge'
import type { Step } from '../run'
import { LiveBrief } from './LiveBrief'
import { LiveInbox } from './LiveInbox'
import { LiveDecisionLog, RequirementMath, StepsTable } from './panels'

export const LAYER = (layer?: string, source?: string | null, model?: string | null) =>
  layer === 'layer-2' ? `layer 2 · ${source === 'anthropic-api' ? 'Anthropic API' : 'Claude CLI'}${model ? ` · ${model}` : ''}` : 'layer 1 · fixed strategy'

const SHORT: Record<string, string> = {
  profile: 'Profile', baseline: 'Baseline', audit: 'Audit', apply: 'Apply', retrain: 'Retrain', rebalance: 'Rebalance',
  calibrate_threshold: 'Threshold', escalate_preset: 'Escalate', add_families: 'Families', reduce_latency: 'Refit',
  synthesize: 'Synthetic', final: 'Verdict',
}

function readAp(): string | null {
  return new URLSearchParams(location.hash.split('?')[1] ?? '').get('ap')
}

export const fmtMetric = (metric: string, v: number | null | undefined) =>
  v == null ? '—' : metric.includes('latency') ? `${v.toFixed(1)} ms` : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(4)

/** The live steps as the prototype's string: kept runs are nodes, dropped runs hang off as branches, the inbox is a diamond. */
function toString(a: Autopilot): { steps: Step[]; total: number } {
  const out: Step[] = []
  const ib = a.inbox
  for (const s of a.steps) {
    if ((s.status === 'running' && s.kind !== 'final') || s.status === 'interrupted') continue
    const base = { id: s.id, short: s.kind === 'final' ? (a.verdict?.met ? 'Met' : 'Not met') : SHORT[s.kind] ?? s.kind, title: s.title, finding: '', ms: 0 }
    if (s.status === 'dropped') out.push({ ...base, kind: 'branch', tried: { f1: s.score ?? 0, rec: 0, lat: 0 } })
    else out.push({ ...base, kind: 'step', after: { f1: s.run_id ? s.score ?? 0 : s.kind === 'final' ? a.verdict?.score ?? 0 : 0, rec: 0, lat: 0 } })
    if (s.kind === 'audit' && ib && ib.n_items > 0) {
      const answered = !!(ib.submitted || ib.applied)
      out.push({ id: `${s.id}-ask`, kind: 'ask', short: 'Inbox', title: 'Ground-Truth Inbox', finding: '', ms: 0,
        answer: answered ? 'answered' : undefined, auto: a.brief.autonomy === 'auto' })
    }
  }
  const running = a.status === 'running' || a.status === 'queued'
  return { steps: out, total: out.length + (running ? 2 : 0) }
}

function gaugeRange(c: ApCheck): [number, number] {
  const obs = c.observed ?? c.value
  if (c.op === '<=') return [0, Math.max(c.value * 1.6, obs * 1.15)]
  const lo = Math.max(0, Math.min(obs, c.value) - 0.06)
  return [Math.floor(lo * 100) / 100, Math.min(1, Math.max(c.value, obs) + 0.03)]
}

export function LiveAutopilot({ go }: { go: (r: string) => void }) {
  const [apId, setApId] = useState<string | null>(readAp())
  const [a, setA] = useState<Autopilot | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const f = () => setApId(readAp())
    addEventListener('hashchange', f)
    return () => removeEventListener('hashchange', f)
  }, [])

  useEffect(() => {
    if (!apId) { setA(null); return }
    let alive = true
    let t: ReturnType<typeof setTimeout>
    const poll = () => api.autopilot.get(apId).then((x) => {
      if (!alive) return
      setA(x); setErr('')
      if (!['finished', 'failed', 'cancelled'].includes(x.status)) t = setTimeout(poll, 1500)
    }).catch((e) => { if (alive) { setErr((e as Error).message); t = setTimeout(poll, 4000) } })
    poll()
    return () => { alive = false; clearTimeout(t) }
  }, [apId])

  const open = (id: string | null) => { location.hash = id ? `#/autopilot?ap=${id}` : '#/autopilot'; setApId(id) }

  return (
    <div className="wizard">
      <div className="stepper-bar" style={{ paddingBottom: 16 }}>
        <div className="stepper-title" style={{ marginBottom: 0 }}>
          <VelaCrest size={26} />
          <h1 className="page-title">Vela Autopilot</h1>
          <span className="helper">Real runs, chained. It audits the labels with cleanlab and only stops to ask you what is true.</span>
          <span className="spacer" />
          {a && <button className="btn sm ghost" onClick={() => open(null)} data-testid="ap-new">New Brief</button>}
          <Segmented options={['Manual', 'Autopilot']} value="Autopilot" onChange={(v) => v === 'Manual' && go('gym')} />
        </div>
      </div>
      <div className="wizard-scroll">
        {!apId ? <LiveBrief onStarted={(id) => open(id)} onOpen={open} />
          : !a ? <div className="page"><p className="helper">{err || 'Loading the autopilot…'}</p></div>
          : <RunView a={a} go={go} refresh={() => api.autopilot.get(a.id).then(setA)} />}
      </div>
    </div>
  )
}

function RunView({ a, go, refresh }: { a: Autopilot; go: (r: string) => void; refresh: () => void }) {
  const { steps, total } = useMemo(() => toString(a), [a])
  const awaiting = a.status === 'awaiting_inbox'
  const finished = a.status === 'finished'
  const running = a.status === 'running' || a.status === 'queued'
  const cur = a.steps.find((s) => s.id === a.current)
  const scored = a.steps.filter((s) => s.run_id && s.score != null)
  const kept = scored.filter((s) => s.status === 'done')
  const best: ApStep | undefined = a.verdict?.best_step ? a.steps.find((s) => s.id === a.verdict!.best_step) : kept[kept.length - 1]
  const checks = best?.checks ?? a.brief.requirements.map((r) => ({ ...r, observed: null, ok: false, gap: null, name: r.metric, sym: r.metric }))
  const versions = a.dataset_versions
  const diff = versions.reduce((d, v) => ({ removed: d.removed + v.dropped, relabelled: d.relabelled + v.relabelled, synthetic: d.synthetic + v.synthetic }), { removed: 0, relabelled: 0, synthetic: 0 })
  const lastDecision = a.decisions[a.decisions.length - 1]
  const eyebrow = finished ? (a.verdict?.met ? 'DONE · REQUIREMENTS MET' : 'STOPPED · REQUIREMENTS NOT MET') : awaiting ? 'WAITING ON YOU'
    : a.status === 'failed' ? 'FAILED' : a.status === 'cancelled' ? 'CANCELLED' : 'VELA IS WORKING'
  const title = finished ? (best ? `${best.run_id} · ${best.title.toLowerCase()}` : 'No model') : awaiting ? `${a.inbox?.n_items ?? 0} rows need your answer` : cur ? `${cur.title}${cur.run_id ? ` · ${cur.run_id}` : ''}` : a.error ?? '…'

  return (
    <div className="page" data-testid="ap-run" data-status={a.status}>
      <section className="stage">
        <div className="stage-head">
          <div>
            <span className="hero-eyebrow" style={{ paddingLeft: 0, color: awaiting ? '#FBBF24' : finished && !a.verdict?.met ? '#FCA5A5' : undefined }} data-testid="ap-eyebrow">{eyebrow}</span>
            <h2 className="hero-title" data-testid="ap-title">{title}</h2>
          </div>
          <div className="stage-read">
            <span><small>id</small><b className="mono" style={{ fontSize: 13 }}>{a.id}</b></span>
            <span><small>runs</small><b>{scored.length}</b></span>
            <span><small>budget</small><b>{Math.round(a.budget.spent)} / {Math.round(a.budget.total)} s</b></span>
            <span><small>dataset</small><b>v{1 + versions.length}</b></span>
            <span title="Which layer decided the last step"><small>decided by</small><b style={{ fontSize: 12.5 }} data-testid="ap-layer">{lastDecision ? LAYER(lastDecision.layer, lastDecision.source, lastDecision.model) : a.brief.decider === 'layer1' ? 'layer 1 only' : 'Claude → fixed'}</b></span>
          </div>
        </div>
        <IterationString steps={steps} shown={steps.length} working={running} total={total} />
        <div className="gauges" style={{ gridTemplateColumns: `repeat(${Math.min(4, checks.length)}, 1fr)` }}>
          {checks.map((c) => {
            const [min, max] = gaugeRange(c)
            return <Gauge key={c.metric} label={c.name} sym={c.sym} value={c.observed ?? 0} target={c.value} min={min} max={max} lower={c.op === '<='}
              fmt={(v) => fmtMetric(c.metric, v)} />
          })}
        </div>
        <div className="stage-foot">
          <div className="stage-prog"><i style={{ width: `${Math.min(100, (a.budget.spent / a.budget.total) * 100)}%` }} /></div>
          {running || awaiting ? <button className="btn sm" onClick={() => api.autopilot.cancel(a.id).then(refresh)}>Stop</button> : null}
          <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-on-shell-muted)' }}>Σ tᵢ = {Math.round(a.budget.spent)} s ≤ B = {Math.round(a.budget.total)} s</span>
        </div>
      </section>

      {a.status === 'failed' && <div className="card" style={{ borderColor: 'var(--status-danger)' }}><div className="card-body"><b>Autopilot failed.</b> <span className="mono">{a.error}</span></div></div>}

      {a.inbox && a.inbox.n_items > 0 && <LiveInbox a={a} onChange={refresh} />}

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <LiveDecisionLog a={a} />
        <div className="stack">
          <RequirementMath a={a} best={best} />
          <DataChanges diff={diff} version={1 + versions.length} rows={a.facts?.rows ?? 1} />
          {versions.length > 0 && <div className="card"><div className="card-body stack" style={{ gap: 6 }}>
            {versions.map((v) => <div key={v.id} className="helper" style={{ fontSize: 12 }}>
              <b className="mono">{v.id}</b> · {v.name} · {v.rows.toLocaleString()} rows · parent <span className="mono">{v.parent}</span>
              {v.synthetic ? <> · <span className="pill neutral">synthetic {v.synthetic} {v.synthetic_class} · {v.synthetic_source}</span></> : null}
            </div>)}
          </div></div>}
          {finished && a.verdict && (
            <div className="card navy" data-testid="ap-result">
              <div className="card-head"><h3 className="card-title">Result</h3>{a.verdict.met ? <StatusPill status="success">Met</StatusPill> : <StatusPill status="warning">Not Met</StatusPill>}</div>
              <div className="minis">
                {a.verdict.checks.map((c) => <div key={c.metric} className="mini"><span className="micro-label">{c.name}</span><span className="v">{fmtMetric(c.metric, c.observed)}</span><span className="u">{c.op} {c.value} {c.ok ? '✓' : '✗'}</span></div>)}
              </div>
              <div className="card-body row" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap' }}>
                {a.verdict.best_run_id && <button className="btn primary" onClick={() => go(`experiments?compare=${[a.steps.find((s) => s.kind === 'baseline')?.run_id, a.verdict!.best_run_id].filter(Boolean).join(',')}`)}>Compare With Baseline</button>}
                {a.verdict.best_run_id && <button className="btn" onClick={() => go(`explain?run=${a.verdict!.best_run_id}`)}>Explain</button>}
                {a.verdict.best_run_id && <button className="btn" onClick={() => go(`gym?from=${a.verdict!.best_run_id}`)}>Open In Gym →</button>}
              </div>
            </div>
          )}
        </div>
      </div>
      <StepsTable a={a} />
    </div>
  )
}
