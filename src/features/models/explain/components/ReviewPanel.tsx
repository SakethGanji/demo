import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { JournalEntry, Review, ReviewVerdict, Run } from '@/shared/api/types'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const dg = (m: string) => (/cost/.test(m) ? 1 : 4)
const VERDICT: Record<ReviewVerdict, { label: string; pill: string; title: string }> = {
  kept: { label: 'kept', pill: 'success', title: 'better beyond noise: the new best node' },
  'kept-honest': { label: 'kept · honest', pill: 'warning', title: 'a leak removed: the score falls and that is the honest score' },
  equivalent: { label: 'equivalent', pill: 'info', title: 'a simpler option within noise' },
  info: { label: 'sensitivity', pill: 'neutral', title: 'what the cost ratio changes; not a model change' },
  rejected: { label: 'rejected', pill: 'neutral', title: 'within noise, worse, or nothing changed' },
  request: { label: 'request', pill: 'success', title: 'a post-deployment change better beyond noise: request it in Registry' },
}
const KIND_PILL: Record<string, string> = { drop: 'danger', adopt: 'success', serve: 'info', confirm_cost: 'warning', request: 'success', join: 'neutral', collect: 'neutral', redefine_label: 'neutral', fix_capture: 'neutral' }

function spec(e: JournalEntry): string {
  const s = e.experiment.spec as Record<string, unknown>
  const k = e.experiment.kind
  if (k === 'ablate') return `without ${(s.columns as string[]).join(', ')}`
  if (k === 'cost_ratio') return `${Number(s.c_fn) / Number(s.c_fp || 1)}:1`
  if (k === 'serve_rung') return `rung ${String(s.rung)}`
  if (k === 'segment' || k === 'monotone') return k === 'segment' ? `by ${String(s.column)}` : Object.keys((s.constraints as object) ?? {}).join(', ')
  if (k === 'apply_data_fix') return `${(s.transforms as unknown[]).length} fix(es)`
  if (k === 'add_features') return s.candidates ? `${(s.candidates as unknown[]).length} candidate(s)` : 'Feature Lab heuristics'
  return ''
}

/** D4 · Review: Claude reads the run and proposes hypotheses; each is proved or rejected by a typed experiment on the
 *  same holdout (the journal), then an improvement plan. Every number comes from GET /runs/{id}/review/{rid}. */
export function ReviewPanel({ run, go }: { run: Run; go: (r: string) => void }) {
  const [rv, setRv] = useState<Review | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [mx, setMx] = useState(5)
  const [mm, setMm] = useState(30)
  const [offline, setOffline] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const live = rv && (rv.status === 'queued' || rv.status === 'running')

  useEffect(() => {
    let alive = true
    api.runs.reviews(run.id).then(async (l) => { if (alive && l.reviews[0]) setRv(await api.runs.review(run.id, l.reviews[0].id)) }).catch(() => {})
    if (/[?&]review=1/.test(window.location.hash)) setTimeout(() => ref.current?.scrollIntoView({ block: 'start' }), 300)
    return () => { alive = false }
  }, [run.id])
  useEffect(() => {
    if (!live || !rv) return
    const t = setInterval(() => { api.runs.review(run.id, rv.id).then(setRv).catch(() => {}) }, 4000)
    return () => clearInterval(t)
  }, [live, rv?.id, run.id])

  const start = async () => {
    setBusy(true); setErr('')
    try { const r = await api.runs.startReview(run.id, { max_experiments: mx, max_minutes: mm, offline }); setRv(await api.runs.review(run.id, r.id)) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const p = rv?.plan
  const refused = rv?.hypotheses.filter((h) => !h.valid) ?? []
  return (
    <div className="card" ref={ref} data-testid="review-panel" data-status={rv?.status ?? 'none'}>
      <div className="card-head"><div className="left"><h3 className="card-title">Review</h3>
        <span className="helper" style={{ fontSize: 12 }}>Claude proposes hypotheses; each is proved or rejected by a typed experiment on the same holdout. The numbers come from code.</span></div>
        <button className="btn sm primary" onClick={start} disabled={busy || !!live} data-testid="review-run">{live ? 'Reviewing…' : busy ? 'Starting…' : rv ? 'Run Review Again' : 'Run Review'}</button></div>
      <div className="card-body stack" style={{ gap: 10 }}>
        {err && <p className="helper" role="alert">✕ {err}</p>}
        {!rv && <p className="helper" style={{ margin: 0, fontSize: 12.5 }}>Four roles read the run (the data, the task and decision point, the errors, then hypotheses). Each hypothesis maps to one typed experiment (ablate, a Data Doctor fix, Feature Lab, clean labels, budget, preset, cost ratio, a simpler rung, a segment, monotone); the journal keeps what beat noise, and the plan says what to do next.</p>}
        {rv && <p className="helper" style={{ margin: 0, fontSize: 12 }} data-testid="review-status">
          {live && <ResonanceWave />} <b>{rv.status}</b> · {rv.mode === 'llm' ? `${rv.decider ?? 'Claude'} (${rv.model ?? '—'})` : 'offline: the deterministic Doctor proposed'}
          {rv.mode_note ? ` · ${rv.mode_note}` : ''} · caps {rv.options.max_experiments} trained experiments, {rv.options.max_minutes} min
          {rv.progress ? ` · ${rv.progress}` : ''}{rv.stop_reason ? ` · stopped: ${rv.stop_reason}` : ''}{rv.error ? ` · ✕ ${rv.error}` : ''}</p>}
        {rv && rv.journal.length > 0 && <div style={{ overflowX: 'auto' }}><table className="table compact" data-testid="review-journal">
          <thead><tr><th className="r">#</th><th>hypothesis</th><th>experiment</th><th className="r">Δ on the same holdout</th><th>verdict</th></tr></thead>
          <tbody>{rv.journal.map((e) => {
            const r = e.result
            const v = e.verdict ? VERDICT[e.verdict] : null
            return <tr key={e.step} data-testid={`journal-${e.step}`} data-verdict={e.verdict ?? e.status}>
              <td className="r mono">{e.step}</td>
              <td style={{ maxWidth: 360, whiteSpace: 'normal' }}><b>{e.hypothesis.title}</b>{e.hypothesis.why && <div className="muted" style={{ fontSize: 11 }}>{e.hypothesis.why}</div>}</td>
              <td style={{ whiteSpace: 'normal', maxWidth: 220 }}><span className="mono">{e.experiment.kind}</span> <span className="muted" style={{ fontSize: 11 }}>{spec(e)}</span>
                {e.run_id && <div><button className="btn sm ghost" onClick={() => go(`models?run=${e.run_id}`)}>{e.run_id} →</button></div>}
                {e.reused && <div className="muted" style={{ fontSize: 11 }}>reused (same experiment already ran)</div>}</td>
              <td className="r mono" style={{ whiteSpace: 'nowrap' }}>{r ? <><b>{sgn(r.delta, dg(r.metric))}</b> <span className="muted">{r.metric}</span>
                <div className="muted" style={{ fontSize: 11 }}>[{sgn(r.ci[0], dg(r.metric))}, {sgn(r.ci[1], dg(r.metric))}]{r.parent != null && r.child != null ? ` · ${r.parent.toFixed(dg(r.metric))} → ${r.child.toFixed(dg(r.metric))}` : ''}</div></>
                : <span className="muted">{e.status === 'succeeded' ? '—' : e.status}</span>}</td>
              <td style={{ whiteSpace: 'normal', maxWidth: 260 }}>{v ? <span className={`pill ${v.pill}`} title={v.title}>{v.label}</span> : <span className="muted">{e.status}</span>}
                {e.reason && <div className="muted" style={{ fontSize: 11 }}>{e.reason}</div>}</td></tr>
          })}</tbody></table></div>}
        {refused.length > 0 && <details className="an-adv"><summary className="micro-label">{refused.length} hypothesis(es) refused by the whitelist before running</summary>
          <ul className="helper" style={{ fontSize: 11.5, margin: '6px 0 0', paddingLeft: 18 }}>{refused.map((h) => <li key={h.id}><b>{h.title}</b>: {h.invalid_reason}</li>)}</ul></details>}
        {p && <div className="stack" style={{ gap: 8 }} data-testid="review-plan">
          <span className="micro-label">improvement plan</span>
          <p style={{ margin: 0, fontSize: 13.5 }} data-testid="review-summary">{p.summary} <span className="pill neutral" title={p.summary_source === 'llm' ? 'worded by Claude; every figure checked against the journal' : 'computed from the journal'}>{p.summary_source === 'llm' ? 'Claude · numbers checked' : 'computed'}</span></p>
          <table className="table compact" data-testid="review-next"><thead><tr><th>do next</th><th>why (measured)</th></tr></thead>
            <tbody>{p.next.map((a, i) => <tr key={i} data-testid={`plan-${a.kind}`}>
              <td style={{ minWidth: 220, whiteSpace: 'normal' }}><span className={`pill ${KIND_PILL[a.kind] ?? 'neutral'}`}>{a.kind.replace('_', ' ')}</span> <b>{a.title}</b>
                {a.run_id && <div><button className="btn sm ghost" onClick={() => go(`models?run=${a.run_id}`)}>Open {a.run_id} →</button></div>}
                {a.serve_model && <div><button className="btn sm ghost" onClick={() => go(`registry?register=${p.best_run ?? run.id}&serve=${encodeURIComponent(a.serve_model!)}`)}>Register it →</button></div>}
                {a.version_id && <div><button className="btn sm ghost" onClick={() => go(`registry?version=${a.version_id}&tab=Monitoring`)}>Open Changes →</button></div>}</td>
              <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{a.why}<div className="muted" style={{ fontSize: 11 }}>{a.source}</div></td></tr>)}</tbody></table>
          {p.domain_gaps.length > 0 && <div data-testid="review-domain"><span className="micro-label">suggestions to investigate (Claude; not measured)</span>
            <ul className="helper" style={{ fontSize: 12, margin: '4px 0 0', paddingLeft: 18 }}>{p.domain_gaps.map((g) => <li key={g.title}><b>{g.title}</b>: {g.why}</li>)}</ul></div>}
          {rv?.guard && rv.guard.blocked.length > 0 && <p className="helper" style={{ fontSize: 11.5, margin: 0 }} data-testid="review-guard">Number guard: {rv.guard.blocked.length} of {rv.guard.checked} texts blocked ({rv.guard.blocked.map((b) => b.numbers.join(', ')).join('; ')} not in the journal); the computed text stands.</p>}
        </div>}
        <details className="an-adv"><summary className="micro-label">Advanced · caps, offline, roles and rules</summary>
          <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 8 }}>
            <label className="stack" style={{ gap: 4 }}><span className="micro-label">trained experiments</span><NumberInput value={mx} min={1} max={8} step={1} onChange={setMx} label="Max experiments" /></label>
            <label className="stack" style={{ gap: 4 }}><span className="micro-label">minutes</span><NumberInput value={mm} min={5} max={240} step={5} onChange={setMm} label="Max minutes" /></label>
            <label className="row" style={{ gap: 6, alignSelf: 'end' }}><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} /> <span className="micro-label">offline (the deterministic Doctor proposes)</span></label>
          </div>
          {rv && rv.roles.length > 0 && <table className="table compact" style={{ marginTop: 8 }}><thead><tr><th>role</th><th>status</th><th>decider</th><th className="r">seconds</th><th>prompt sha256</th></tr></thead>
            <tbody>{rv.roles.map((r, i) => <tr key={i}><td>{r.role}</td><td>{r.status}</td><td>{r.decider ?? '—'}{r.model ? ` · ${r.model}` : ''}</td><td className="r mono">{r.seconds != null ? r.seconds.toFixed(1) : '—'}</td><td className="mono muted">{r.prompt_sha256}</td></tr>)}</tbody></table>}
          <ul className="helper" style={{ fontSize: 11.5, margin: '6px 0 0', paddingLeft: 18 }}>{Object.entries(rv?.rules ?? {}).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}
            {rv?.audit_id != null && <li><b>audit</b>: entry #{rv.audit_id} (inputs {rv.inputs_sha256}, each prompt's hash, the model and decider)</li>}</ul>
        </details>
      </div>
    </div>
  )
}
