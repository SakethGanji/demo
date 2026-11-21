import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Latency, ModelVersion, Stage } from '@/shared/api/types'
import type { RetireRequest } from '@/shared/api/client'
import { useWidth } from '@/shared/hooks/useWidth'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { APPROVAL_STATUS, STAGES, STAGE_STATUS, bytes, ms, nameKey, short, us } from '../lib'

const STEP_TEXT: Record<string, string> = {
  clone: 'predictor.clone(work)', refit_full: 'refit_full(model=…)', distill: 'distill(): single student models', infer_limit: 'set_model_best under τ', deploy_copy: 'clone_for_deployment + pruned pipeline',
  measure: 'size · latency · holdout CIs', hash: 'SHA-256 of model and data', mlflow: 'MLflow model version',
}

export function BuildSteps({ v }: { v: ModelVersion }) {
  const steps = v.progress?.steps ?? []
  const names = ['clone', 'refit_full', ...(steps.some((x) => x.name === 'distill') ? ['distill'] : []), 'infer_limit', 'deploy_copy', 'measure', 'hash', 'mlflow']
  return (
    <div className="card" data-testid="build-steps">
      <div className="card-head"><div className="left"><h3 className="card-title">Build</h3><span className="helper" style={{ fontSize: 12 }}>in a worker process · the API stays responsive</span></div>
        <StatusPill status={v.status === 'ready' ? 'success' : v.status === 'failed' ? 'danger' : 'info'}>{v.status}</StatusPill></div>
      <div className="card-body build-steps">
        {names.map((n) => {
          const s = steps.find((x) => x.name === n)
          const st = s?.status ?? (v.status === 'building' ? 'waiting' : '—')
          return (
            <div key={n} className={`bstep ${st}`} data-testid={`step-${n}`} data-status={st}>
              <i className="bstep-node" />
              <span className="mono bstep-name">{n}</span>
              <span className="helper bstep-what">{STEP_TEXT[n]}</span>
              <span className="bstep-info mono">
                {st === 'running' ? <ResonanceWave width={46} height={12} /> : null}
                {s?.seconds != null ? `${(s.seconds as number).toFixed(1)} s` : ''}
                {s && typeof s.reason === 'string' ? ` ${s.reason}` : ''}
                {s && typeof s.chosen === 'string' ? ` → ${s.chosen}` : ''}
                {s && typeof s.best === 'string' && n === 'refit_full' ? ` → ${s.best}` : ''}
                {s && typeof s.error === 'string' ? ` ${s.error}` : ''}
              </span>
            </div>
          )
        })}
        {v.error && <p role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {v.error}</p>}
      </div>
    </div>
  )
}

/** Repeated batch timings as beads on a string: p50 and p95 marked, source above, version below. */
function LatencyStrings({ src, ver }: { src: Latency; ver: Latency }) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const all = [...src.us_per_row, ...ver.us_per_row]
  const hi = Math.max(...all) * 1.08, lo = 0
  const L = 120, R = 70
  const sx = (v: number) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R)
  const row = (lat: Latency, y: number, name: string, color: string) => (
    <g>
      <text x={L - 10} y={y + 4} textAnchor="end" className="fi-name">{name}</text>
      <line x1={L} x2={sx(hi)} y1={y} y2={y} stroke="var(--border-track)" strokeWidth={1} />
      <circle cx={L} cy={y} r={3} fill="var(--text-muted)" />
      {lat.us_per_row.map((t, i) => <circle key={i} cx={sx(t)} cy={y} r={4.5} fill={color} opacity={0.75} />)}
      <line x1={sx(lat.us_per_row_p50)} x2={sx(lat.us_per_row_p50)} y1={y - 11} y2={y + 11} stroke={color} strokeWidth={2} />
      <line x1={sx(lat.us_per_row_p95)} x2={sx(lat.us_per_row_p95)} y1={y - 8} y2={y + 8} stroke={color} strokeDasharray="2 2" />
      <text x={W - R + 6} y={y + 4} className="tick-text" style={{ fill: 'var(--text-primary)' }}>{us(lat.us_per_row_p50)} µs</text>
    </g>
  )
  return (
    <div ref={ref} data-testid="latency-strings">
      <svg width={W} height={96}>
        {[0, hi / 2, hi].map((v) => <g key={v}><line x1={sx(v)} x2={sx(v)} y1={8} y2={74} stroke="var(--plot-grid)" /><text className="tick-text" x={sx(v)} y={90} textAnchor="middle">{us(v)}</text></g>)}
        <text className="axis-title" x={W - 4} y={90} textAnchor="end">µs / row</text>
        {row(src, 26, 'source run', 'var(--viz-c)')}
        {row(ver, 56, 'this version', 'var(--viz-a)')}
      </svg>
    </div>
  )
}

export function Tradeoff({ v }: { v: ModelVersion }) {
  const r = v.result!
  const src = r.source, ver = r.version
  const metric = ver.eval_metric
  const ci = ver.holdout_ci.metrics, sci = src.holdout_ci?.metrics ?? {}
  const mkey = ci[metric] ? metric : Object.keys(ci)[0]
  const speed = src.latency.us_per_row_p50 / ver.latency.us_per_row_p50
  const rows: [string, string, string, string, string?][] = [
    ['served model', src.best_model, ver.best_model, ''],
    ['models on disk', String(src.n_models), String(ver.n_models), `${ver.n_models - src.n_models >= 0 ? '+' : '−'}${Math.abs(ver.n_models - src.n_models)}`],
    ['size on disk S', bytes(src.size_bytes), bytes(ver.size_bytes), `× ${(ver.size_bytes / src.size_bytes).toFixed(3)}`, 'size'],
    ['batch t̂ p50 (µs/row)', us(src.latency.us_per_row_p50), us(ver.latency.us_per_row_p50), `× ${(1 / speed).toFixed(3)}`, 'p50'],
    ['batch t̂ p95 (µs/row)', us(src.latency.us_per_row_p95), us(ver.latency.us_per_row_p95), `× ${(ver.latency.us_per_row_p95 / src.latency.us_per_row_p95).toFixed(3)}`],
    ['one row p50 (ms)', ms(src.latency.single_ms_p50), ms(ver.latency.single_ms_p50), src.latency.single_ms_p50 && ver.latency.single_ms_p50 ? `× ${(ver.latency.single_ms_p50 / src.latency.single_ms_p50).toFixed(3)}` : ''],
    ['one row p95 (ms)', ms(src.latency.single_ms_p95), ms(ver.latency.single_ms_p95), ''],
    ['throughput (rows/s)', Math.round(src.latency.throughput_rows_per_s ?? 0).toLocaleString(), Math.round(ver.latency.throughput_rows_per_s ?? 0).toLocaleString(), ''],
  ]
  const m = ci[mkey], sm = sci[mkey]
  return (
    <div className="card" data-testid="tradeoff">
      <div className="card-head"><div className="left"><h3 className="card-title">Deployment Trade-off · Measured</h3>
        <span className="helper" style={{ fontSize: 12 }}>same holdout rows, same machine, one after the other</span></div>
        <div className="row">
          <span className="chip"><span className="sym">speed-up</span>=<b data-testid="speedup">{speed.toFixed(2)}×</b></span>
          <span className="chip"><span className="sym">S′/S</span>=<b data-testid="size-ratio">{(ver.size_bytes / src.size_bytes).toFixed(3)}</b></span>
        </div>
      </div>
      <div className="card-body tradeoff-grid">
        <table className="table compact">
          <thead><tr><th /><th className="r">source run</th><th className="r">this version</th><th className="r">ratio</th></tr></thead>
          <tbody>
            {rows.map(([k, a, b, d, id]) => <tr key={k} data-testid={id ? `to-${id}` : undefined}><td className="muted">{k}</td><td className="r mono">{a}</td><td className="r mono"><b>{b}</b></td><td className="r mono muted">{d}</td></tr>)}
            {m && <tr data-testid="to-metric"><td className="muted">holdout {metricInfo(mkey)?.name ?? mkey} · 95% CI</td>
              <td className="r mono">{sm ? `${fmtScore(sm.value)} [${fmtScore(sm.ci_low, 3)}, ${fmtScore(sm.ci_high, 3)}]` : '—'}</td>
              <td className="r mono"><b>{fmtScore(m.value)}</b> [{fmtScore(m.ci_low, 3)}, {fmtScore(m.ci_high, 3)}]</td>
              <td className="r mono muted">{sm ? `Δ ${m.value - sm.value >= 0 ? '+' : '−'}${fmtScore(Math.abs(m.value - sm.value), 4)}` : ''}</td></tr>}
          </tbody>
        </table>
        <div>
          <LatencyStrings src={src.latency} ver={ver.latency} />
          <p className="helper" style={{ fontSize: 12, marginTop: 4 }}>
            Beads: t̂ = T / n per repeat, where T is the wall time of one <span className="mono">predict_proba</span> call on n = {ver.latency.batch_rows.toLocaleString()} holdout rows ({ver.latency.batch_repeats} repeats after a warm-up, models persisted in memory). Solid tick p50, dashed p95. One-row latency: {ver.latency.single_calls} single-row calls. Speed-up = t̂₅₀(source) / t̂₅₀(version).
          </p>
        </div>
      </div>
      {r.infer_limit && r.infer_limit.candidates && (() => {
        const il = r.infer_limit!
        const one = il.batch_size === 1
        return (
          <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }} data-testid="infer-limit">
            <p className="helper" style={{ fontSize: 12.5, marginBottom: 8 }}>
              {il.serve_fastest
                ? <><b className="mono">serve fastest</b>: m* = argmin<sub>m</sub> τ₉₅(m) = <b className="mono">{il.chosen}</b>, where τ₉₅(m) is m’s measured one-row p95 as served (pipeline pruned to what m reads, m persisted).</>
                : <><b className="mono">infer_limit</b> λ = {one ? `${ms((il.infer_limit_us ?? 0) / 1e3)} ms per one-row call` : `${us(il.infer_limit_us)} µs/row`} ({il.infer_limit_s} s, <span className="mono">infer_limit_batch_size = {il.batch_size}</span>): m* = argmax<sub>m : t(m) ≤ λ</sub> s_val(m) = <b className="mono">{il.chosen}</b>{il.met ? '' : ' — no model met λ, so the fastest was kept'}. t(m) = {one ? 'τ₉₅(m), the one-row p95 as served' : 'measured batch time per row'}.</>}
            </p>
            <table className="table compact">
              <thead><tr><th className="nocase">model m</th><th className="r nocase">s_val(m)</th><th className="r nocase">{one ? 'τ₉₅(m) ms' : 't̂(m) µs/row'}</th><th className="r">models needed</th><th className="nocase">{il.serve_fastest ? 'pipeline pruned' : 'within λ'}</th></tr></thead>
              <tbody>{il.candidates!.map((c) => (
                <tr key={c.model} className={c.model === il.chosen ? 'sel' : ''}><td className="mono">{c.model}</td><td className="r mono">{fmtScore(c.score_val_natural)}</td>
                  <td className="r mono">{one ? ms(c.single_ms_p95) : us(c.time_used_us)}</td><td className="r mono">{c.n_models}</td>
                  <td>{il.serve_fastest ? (c.pipeline_pruned ? 'yes' : 'no') : c.within ? <StatusPill status="success">yes</StatusPill> : <StatusPill status="neutral">no</StatusPill>}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )
      })()}
      {r.distill && (
        <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }} data-testid="distill">
          <p className="helper" style={{ fontSize: 12.5 }}>
            <b className="mono">distill</b>: students fit to the teacher <span className="mono">{r.distill.teacher}</span>’s predicted probabilities p̂<sub>T</sub>(y | x) on the training rows plus augmented rows ({r.distill.method}); {r.distill.seconds.toFixed(0)} s. Holdout {metric}: teacher {fmtScore(r.distill.teacher_score_test)}{Object.entries(r.distill.scores).map(([m, sc]) => <span key={m}> · <span className="mono">{m}</span> {fmtScore(sc.score_test)}</span>)}.
            {r.distill.served ? <> Served: <b className="mono">{r.distill.served}</b>.</> : null}
          </p>
        </div>
      )}
    </div>
  )
}

export function ReviewCard({ v, actor, onChanged }: { v: ModelVersion; actor: string; onChanged: (v: ModelVersion) => void }) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [override, setOverride] = useState('')
  const act = async (f: () => Promise<{ version: ModelVersion }>) => {
    setBusy(true); setErr('')
    try { const r = await f(); onChanged(r.version); setComment('') } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const me = nameKey(actor)
  const author = v.options?.change_by
  const self = !!me && (me === nameKey(v.created_by) || (!!author && me === nameKey(author)))
  const ready = v.status === 'ready'
  return (
    <div className="card" data-testid="review-card">
      <div className="card-head"><div className="left"><h3 className="card-title">Review &amp; Stage</h3></div>
        <div className="row"><StatusPill status={STAGE_STATUS[v.stage]}>{v.stage}</StatusPill><StatusPill status={APPROVAL_STATUS[v.approval]}>{v.approval}</StatusPill>{v.retired_at && <StatusPill status="neutral">retired</StatusPill>}</div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="stage-rail" data-testid="stage-rail">
          {STAGES.map((s, i) => (
            <div key={s} className={`stage-stop ${v.stage === s ? 'on' : ''}`}>
              {i > 0 && <i className="stage-wire" />}
              <button className="stage-node" disabled={!ready || busy || v.stage === s || !actor.trim() || (s === 'Production' && v.approval !== 'approved') || (v.approval === 'rejected' && (s === 'Staging' || s === 'Production'))}
                data-testid={`to-${s}`} title={s === 'Production' && v.approval !== 'approved' ? 'Production needs an approved review' : `Move to ${s}`}
                onClick={() => act(() => api.registry.transition(v.id, s as Stage, actor, comment, s === 'Production' ? override : ''))}>{s}</button>
            </div>
          ))}
        </div>
        <textarea className="kw-input" rows={2} placeholder="comment for the audit log (a rejection needs one)" data-testid="review-comment" value={comment} onChange={(e) => setComment(e.target.value)} />
        {v.approval !== 'pending' && <p className="review-done" data-testid="review-done">{v.approval === 'approved' ? '● approved' : '✕ rejected'} — one review per version; register the run again for a new review.</p>}
        {v.approval === 'pending' && <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button className="btn primary" data-testid="approve" disabled={!ready || busy || v.approval !== 'pending' || !actor.trim() || self} onClick={() => act(() => api.registry.review(v.id, 'approve', actor, comment))}>Approve as {actor || '…'}</button>
          <button className="btn" data-testid="reject" disabled={!ready || busy || v.approval !== 'pending' || !actor.trim() || self || !comment.trim()} onClick={() => act(() => api.registry.review(v.id, 'reject', actor, comment))}>Reject</button>
          {busy && <ResonanceWave width={50} height={12} />}
        </div>}
        <p className="helper" style={{ fontSize: 12 }}>
          None → Staging → Production → Archived. Production needs an approved review; promoting archives the model’s current Production version; rejecting archives a live version.
          <br />Names, not authenticated identities: four eyes compares names on letters and digits only (case, spaces, punctuation and accents ignored; lookalike letters folded). Whoever registered a version does not move it to Production themselves, and Production is blocked while a critical Doctor finding is open unless overridden with a reason (audited).
          {self && v.approval === 'pending' ? <><br /><b style={{ color: 'var(--status-warning)' }}>▲ Four eyes: {v.created_by} registered this version{author ? ` and ${author} designed its change experiment` : ''}, so someone else reviews it.</b></> : null}
        </p>
        <RetireBox v={v} actor={actor} comment={comment} onChanged={onChanged} />
        {err && <p role="alert" className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        {(/critical finding is open/.test(err) || override) && <label className="row" style={{ gap: 6 }}><span className="micro-label">override reason</span>
          <input className="input" style={{ flex: 1 }} placeholder="why Production goes ahead with the finding open (≥ 10 characters, audited)" value={override} onChange={(e) => setOverride(e.target.value)} data-testid="production-override" /></label>}
      </div>
    </div>
  )
}

/** DL2: end of life for a registered version, four eyes: one person asks (the comment above is the reason), another
 *  approves (or an admin at once). Refused while it is champion or in Production. Once retired its training data can be
 *  archived, and deleted after the retention period. */
function RetireBox({ v, actor, comment, onChanged }: { v: ModelVersion; actor: string; comment: string; onChanged: (v: ModelVersion) => void }) {
  const [reqs, setReqs] = useState<RetireRequest[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const load = () => api.registry.retireState(v.id).then((r) => setReqs(r.requests)).catch(() => setReqs([]))
  useEffect(() => { load() }, [v.id, v.retired_at]) // eslint-disable-line react-hooks/exhaustive-deps
  const pending = reqs.find((r) => r.status === 'pending')
  const run = async (p: Promise<RetireRequest & { version?: ModelVersion }>, ok: string) => {
    setMsg(null)
    try { const r = await p; setMsg({ ok: true, text: ok }); await load(); onChanged(r.version ?? await api.registry.version(v.id)) } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
  }
  if (v.approval === 'rejected') return null
  return (
    <div className="retire-box" data-testid="retire-box">
      {v.retired_at ? <span className="helper" data-testid="retired-line">⏹ Retired {new Date(v.retired_at).toLocaleDateString()} by {v.retired_by}: it stays Archived; its training data can be archived now and deleted after the retention period.</span>
        : pending ? <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <span className="helper">Retirement asked by <b>{pending.requested_by}</b>: “{pending.reason}”</span>
          {nameKey(actor) === nameKey(pending.requested_by)
            ? <button className="btn sm ghost" onClick={() => run(api.registry.decideRetire(pending.id, false, actor), 'Retirement request withdrawn.')} data-testid="retire-withdraw">Withdraw my request</button>
            : <>
              <button className="btn sm" disabled={!actor.trim()} onClick={() => run(api.registry.decideRetire(pending.id, true, actor, comment), 'Retired.')} data-testid="retire-approve">Approve retirement as {actor || '…'}</button>
              <button className="btn sm ghost" disabled={!actor.trim()} onClick={() => run(api.registry.decideRetire(pending.id, false, actor, comment), 'Retirement rejected.')}>Reject</button></>}
        </div>
          : <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <button className="btn sm" disabled={!actor.trim() || comment.trim().length < 5} onClick={() => run(api.registry.retire(v.id, actor, comment), 'Retirement asked: someone else (or an admin) approves.')} data-testid="retire-ask"
              title={comment.trim().length < 5 ? 'Write the reason in the comment box first' : 'Four eyes: someone else approves'}>Retire…</button>
            <span className="helper" style={{ fontSize: 12 }}>End of life, four eyes; the comment is the reason. Not while it is champion or in Production.</span></div>}
      {msg && <p className="helper" role={msg.ok ? 'status' : 'alert'} style={{ color: msg.ok ? 'var(--status-success)' : 'var(--status-danger)', margin: '4px 0 0' }} data-testid="retire-msg">{msg.ok ? '✓' : '✕'} {msg.text}</p>}
    </div>
  )
}

export function Provenance({ v }: { v: ModelVersion }) {
  const r = v.result!
  const ml = r.mlflow
  const rows: [string, string, string?][] = [
    ['model SHA-256', r.hashes.model_sha256, 'every file of the version’s predictor folder'],
    ['data SHA-256', r.hashes.data_sha256, r.hashes.data_file],
    ['split SHA-256', r.hashes.splits_sha256, 'the fixed holdout and folds'],
    ['predictor', r.path, 'TabularPredictor.load(path)'],
    ['MLflow', ml ? `models:/${ml.name}/${ml.version}` : 'not registered', ml ? `run ${ml.run_id} · experiment ${ml.experiment_id}` : undefined],
  ]
  return (
    <div className="card" data-testid="provenance">
      <div className="card-head"><div className="left"><h3 className="card-title">Provenance</h3></div></div>
      <div className="card-body">
        <dl className="prov">{rows.map(([k, val, note]) => <div key={k}><dt className="micro-label">{k}</dt><dd className="mono" data-testid={`prov-${k.split(' ')[0]}`} title={val}>{val}</dd>{note && <small className="helper">{note}</small>}</div>)}</dl>
        <p className="helper" style={{ fontSize: 12 }}>Hash h = SHA-256(path₁ ‖ bytes₁ ‖ … ‖ pathₙ ‖ bytesₙ) over the folder’s files in sorted order; the audit log records h at every action, so a changed model is visible. Short form: <span className="mono">{short(v.model_hash)}</span>.</p>
      </div>
    </div>
  )
}
