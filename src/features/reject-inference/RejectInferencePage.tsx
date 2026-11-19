import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type RiCompare, type RiDetect, type RiMethod, type RiResult } from '@/shared/api/flows'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ApiOnly, ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { fix, pct } from '@/shared/lib/format'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { CiBar } from '@/features/segmented/SegmentedPage'
import '@/features/expected-loss/expected-loss.css'
import '@/features/segmented/segmented.css'

const ORDER: RiMethod[] = ['approved_only', 'reweighting', 'parcelling', 'fuzzy']
const NAME: Record<RiMethod, string> = { approved_only: 'Approved only', reweighting: 'Reweighting', parcelling: 'Parcelling', fuzzy: 'Fuzzy augmentation' }
const COLOR: Record<RiMethod, string> = { approved_only: 'var(--text-tertiary, #8a94a6)', reweighting: 'var(--viz-a)', parcelling: 'var(--viz-b)', fuzzy: 'var(--viz-c)' }
const FITS = ['approved_only', 'acceptance', 'reweighting', 'parcelling', 'fuzzy']
const sgn = (v: number | null | undefined, d = 4) => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`)

/**
 * G7 · Reject inference, a choice inside Build. Without ?run: the setup, pre-filled from the G0 roles (the outcome, the
 * approve/decline decision, what to leave out, the split), one primary action. With ?run: approved-only training against
 * reweighting, parcelling and fuzzy augmentation, on a through-the-door holdout, with paired CIs and the assumption.
 */
export function RejectInferencePage({ go }: { go: (r: string) => void }) {
  const runId = useHashParam('run')
  return (
    <ApiOnly mock={(
      <div className="page"><Head />
        <div className="card card-body"><p className="helper">▲ mock mode: reject inference trains AutoGluon on the API, which is offline. Start it with <span className="mono">make dev</span>.</p></div></div>
    )}>
      {runId ? <RunView key={runId} runId={runId} /> : <Setup go={go} />}
    </ApiOnly>
  )
}

function Head({ right }: { right?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div><h1 className="page-title">Reject Inference</h1><p className="helper sub">Outcomes exist only for the applicants you approved. Add the declined back three standard ways and see which model holds up on everyone who applies.</p></div>
      {right}
    </div>
  )
}

function Formula({ k }: { k: number }) {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14, lineHeight: 1.7 }} data-testid="ri-formula">
        reweighting: w = 1 / P̂(A = 1 | x) &nbsp;·&nbsp; parcelling: P(y = 1 | declined, band b) = min(0.95, k · bad rate<sub>approved, b</sub>) &nbsp;·&nbsp; fuzzy: q = min(0.95, k · f<sub>0</sub>(x)), &nbsp;k = {k}
      </div>
    </div>
  )
}

function Assumption({ k, decision }: { k: number; decision: string | null }) {
  return (
    <p className="helper" style={{ fontSize: 12.5 }} data-testid="ri-assumption">
      <b>The assumption.</b> Nothing in the data says how a declined applicant would have done. The decision{decision ? <> (<span className="mono">{decision}</span>)</> : null} may have used
      what the table does not hold (an interview, a phone call, policy rules), so declined applicants can be riskier than approved ones who look the same.
      Parcelling and fuzzy augmentation assume they are <b>k = {k}×</b> as likely to default as an approved applicant the approved-only model scores the same (k = 1: the decline told us nothing
      beyond x). Reweighting assumes k = 1: the decision depended only on x, so it can only re-balance the approved rows. k cannot be learned from approved outcomes; 2 is a common rule of thumb.
    </p>
  )
}

// ------------------------------------------------------------------------------------------------------------ setup

function Setup({ go }: { go: (r: string) => void }) {
  const dsId = useHashParam('dataset') ?? 'credit-rejects'
  const det = useFetch(() => flows.ri.detect(dsId), [dsId])
  const ds = useFetch(() => api.datasets.get(dsId), [dsId])
  const fromBuild = (): Partial<RiDetect> => {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
    return { ...(q.get('label') ? { label: q.get('label') } : {}), ...(q.get('decision') ? { decision_column: q.get('decision') } : {}) }
  }
  const [pick, setPick] = useState<Partial<RiDetect>>(fromBuild)
  const [k, setK] = useState(2)
  const [limit, setLimit] = useState(20)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => setPick(fromBuild()), [dsId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (det.err || ds.err) return <div className="page"><Head /><ErrorCard err={det.err || ds.err} /></div>
  if (!det.data || !ds.data) return <div className="page"><Head /><Loading>Reading the column roles…</Loading></div>
  const d = { ...det.data, ...pick }
  const two = ds.data.profile.columns.filter((c) => c.unique === 2)
  const start = async () => {
    setBusy(true); setErr('')
    try {
      const r = await flows.ri.create({ dataset_id: dsId, label: d.label!, ...(d.decision_column ? { decision_column: d.decision_column } : {}), k,
        drop_columns: d.drop_columns.filter((c) => c !== d.label && c !== d.decision_column), time_limit: limit })
      go(`reject-inference?run=${r.id}`)
    } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  const why = (key: 'label' | 'decision_column') => (pick[key] && pick[key] !== det.data![key] ? 'set by you' : (det.data!.evidence[d[key] ?? ''] ?? []).join(', '))
  return (
    <div className="page" data-testid="ri-setup">
      <Head />
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">What we use</h3><span className="helper" style={{ fontSize: 12 }}>{ds.data.name} · {ds.data.rows.toLocaleString()} applicants{d.approval_rate != null ? ` · ${pct(d.approval_rate)} approved` : ''} · detected from the column roles; change any of them</span></div>
          <button className="btn primary" disabled={!d.label || busy} onClick={start} data-testid="ri-start">{busy ? 'Starting…' : 'Compare 4 ways to train →'}</button></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <Formula k={k} />
          <div className="table-wrap"><table className="table compact" data-testid="ri-detected">
            <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
            <tbody>
              <tr><td>outcome y</td>
                <td><select className="input mono" value={d.label ?? ''} onChange={(e) => setPick({ ...pick, label: e.target.value })} data-testid="ri-label">
                  {!d.label && <option value="">—</option>}{two.map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{d.label ? why('label') : 'nothing detected: pick the yes/no outcome'}</td></tr>
              <tr><td>decision A</td>
                <td><select className="input mono" value={d.decision_column ?? ''} onChange={(e) => setPick({ ...pick, decision_column: e.target.value || null })} data-testid="ri-decision">
                  <option value="">(no column: no outcome = declined)</option>{two.filter((c) => c.name !== d.label).map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{d.decision_column ? <>{why('decision_column')}{det.data.approved_value && !pick.decision_column ? <> · approved = <span className="mono">{det.data.approved_value}</span></> : null}</> : 'declined = rows with no outcome'}</td></tr>
              <tr><td>left out</td><td className="mono" colSpan={2} style={{ whiteSpace: 'normal' }}>{d.drop_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· IDs, dates, leaks, protected · {d.split_why}</span></td></tr>
              <tr><td>check</td><td colSpan={2} className={d.through_the_door ? 'el-ok' : 'helper'} style={{ fontSize: 12.5 }}>{d.through_the_door
                ? '● Through the door: this sample keeps the outcomes the declined applicants would have had, aside, so every method is checked on everyone in the holdout.'
                : 'Approved holdout only: nobody knows how the declined would have done, so the methods can be compared only where outcomes exist, which is the biased view.'}</td></tr>
            </tbody>
          </table></div>
          <Assumption k={k} decision={d.decision_column} />
          <details className="an-adv">
            <summary className="micro-label">Advanced · k = {k} · {limit} s per model</summary>
            <div className="row" style={{ gap: 8, padding: 'var(--space-8) 0', flexWrap: 'wrap' }}>
              <label className="helper">k</label>
              <input className="input mono" type="number" min={1} max={5} step={0.5} value={k} onChange={(e) => setK(Math.min(5, Math.max(1, Number(e.target.value) || 2)))} style={{ width: 70 }} data-testid="ri-k" />
              <label className="helper">time per model (s)</label>
              <input className="input mono" type="number" min={10} max={3600} value={limit} onChange={(e) => setLimit(Number(e.target.value) || 20)} style={{ width: 90 }} />
              <span className="helper" style={{ fontSize: 12 }}>Five AutoGluon fits (medium_quality, log loss): approved-only, the decision model P̂(A | x), and the three methods.</span>
            </div>
          </details>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------------------------------------- run page

function RunView({ runId }: { runId: string }) {
  const live = useRunEvents(runId)
  const r = useFetch(() => flows.ri.get(runId), [runId, live.done], { pollMs: 4000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <div className="page"><Head /><ErrorCard err={r.err} /></div>
  if (!r.data) return <div className="page"><Head /><Loading>Loading the run…</Loading></div>
  const run = r.data
  const on = new Set(live.events.filter((e) => e.type === 'ri_model_started').map((e) => e.model as string))
  const done = new Set(live.events.filter((e) => e.type === 'ri_model_finished').map((e) => e.model as string))
  return (
    <div className="page" data-testid="ri-run" data-status={run.status}>
      <Head />
      <p className="helper mono" style={{ fontSize: 12, marginTop: -8 }}>{run.name} · {runId} · y = {run.config.label} · A = {run.config.found.decision_column ?? 'has an outcome'} · k = {run.config.k}</p>
      {run.status === 'failed' && <ErrorCard err={run.error ?? 'The run failed.'} />}
      {(run.status === 'queued' || run.status === 'running') && (
        <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
          <div className="row"><ResonanceWave /> <span>Five fits, {run.config.time_limit ?? '—'} s each, then the through-the-door check · {live.stage}</span></div>
          <div className="el-steps seg-steps">{FITS.map((m) => (
            <div key={m} className={`el-step ${done.has(m) ? 'done' : on.has(m) ? 'on' : ''}`}><b>{m === 'acceptance' ? 'decision model P̂(A | x)' : NAME[m as RiMethod]}</b><span className="mono">{done.has(m) ? '✓' : on.has(m) ? '…' : ''}</span></div>
          ))}</div>
        </div></div>
      )}
      {run.status === 'succeeded' && run.reject_inference && <RiResults r={run.reject_inference} decision={run.config.found.decision_column} />}
    </div>
  )
}

function Calibration({ r }: { r: RiResult }) {
  const t = r.ttd!
  const top = Math.max(...ORDER.flatMap((m) => (t.methods[m].deciles ?? []).flatMap((g) => [g.mean_p, g.bad_rate]))) * 1.05
  const max = Math.min(1, Math.ceil(top * 20) / 20)
  const show: RiMethod[] = ['approved_only', t.best === 'approved_only' ? 'fuzzy' : t.best]
  const series: XYSeries[] = show.map((m) => ({ name: NAME[m], color: COLOR[m], dots: true, width: m === 'approved_only' ? 1.5 : 2.2,
    points: (t.methods[m].deciles ?? []).map((g) => [g.mean_p, g.bad_rate] as [number, number]) }))
  return (
    <PlotFrame title="Calibration on Everyone Who Applied" caption={<>each dot is a tenth of the through-the-door holdout, by p̂: its mean p̂ against its observed bad rate (hidden outcomes for the declined); on the diagonal is calibrated</>}
      legend={show.map((m) => ({ name: NAME[m], color: COLOR[m], data: [] }))}>
      <XYPlot testId="ri-calibration" height={260} series={series} diagonal={{ label: 'calibrated' }}
        x={{ min: 0, max, ticks: niceTicks(0, max, 5), title: 'mean p̂', fmt: (v) => v.toFixed(2) }}
        y={{ min: 0, max, ticks: niceTicks(0, max, 5), title: 'observed bad rate', fmt: (v) => v.toFixed(2) }} />
    </PlotFrame>
  )
}

/** The comparison of the four ways to train, on the TTD holdout when it exists, else on the approved holdout. */
export function RiResults({ r, decision }: { r: RiResult; decision: string | null }) {
  const t = r.ttd
  const view: Record<RiMethod, RiCompare> = t ? t.methods : r.approved_holdout
  const base = view.approved_only
  const best = t ? t.best : 'approved_only'
  const b = view[best]
  const span = Math.max(1e-6, ...ORDER.slice(1).map((m) => view[m].delta_auc).filter(Boolean).map((d) => Math.max(Math.abs(d!.ci[0]), Math.abs(d!.ci[1])))) * 1.1
  const vcls = (v?: string) => (v === 'better' ? 'el-ok' : v === 'worse' ? 'el-bad' : 'helper')
  const dl = (d?: { delta: number; ci: [number, number] }) => (d ? <>{sgn(d.delta, 3)} <span className="helper">[{sgn(d.ci[0], 3)}, {sgn(d.ci[1], 3)}]</span></> : '—')
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="ri-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">{t ? 'Through the Door: Every Applicant in the Holdout' : 'Approved Holdout Only'}</h3>
          <span className="helper" style={{ fontSize: 12 }}>{t ? `${t.n.toLocaleString()} applicants (${r.data.holdout_declined.toLocaleString()} declined, scored with their hidden outcomes) none of the models saw` : `${base.n.toLocaleString()} approved applicants: no outcomes exist for the declined`}</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="ri-hero">
          <HeroCell label="approved only · AUC" sub={`log loss ${fix(base.log_loss, 4)}`}>{fix(base.auc ?? null, 4)}</HeroCell>
          {best !== 'approved_only' && <HeroCell label={`${NAME[best]} · AUC`} sub={b.delta_auc ? `Δ ${sgn(b.delta_auc.delta)} [${sgn(b.delta_auc.ci[0])}, ${sgn(b.delta_auc.ci[1])}]` : ''} subClass={vcls(b.verdict)}>{fix(b.auc ?? null, 4)}</HeroCell>}
          {best !== 'approved_only' && <HeroCell label="Δ log loss" sub={`95% CI [${sgn(b.delta_log_loss.ci[0])}, ${sgn(b.delta_log_loss.ci[1])}] · ${b.verdict}`} subClass={vcls(b.verdict)}>{sgn(b.delta_log_loss.delta)}</HeroCell>}
          <HeroCell label="bad rate vs mean p̂" sub={t ? `approved only ${fix(base.mean_p, 3)} · ${NAME[best].toLowerCase()} ${fix(b.mean_p, 3)}` : `approved only ${fix(base.mean_p, 3)}`}>{pct(base.bad_rate)}</HeroCell>
          {t && <HeroCell label="better than approved only" sub={t.better.length ? t.better.map((m) => NAME[m]).join(', ') : 'none (no CI excludes 0)'} subClass={t.better.length ? 'el-ok' : undefined}>{`${t.better.length} of 3`}</HeroCell>}
          <HeroCell label="assumption k" sub="declined vs approved, same score">{`${r.k}×`}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula k={r.k} />
          <p className="helper" style={{ fontSize: 12.5 }}>
            A = 1 for an approved applicant; y = 1 for {String(r.positive_class)} in {r.label}, known only when A = 1. f<sub>0</sub> is AutoGluon trained on the {r.data.train_approved.toLocaleString()} approved
            training applicants; P̂(A = 1 | x) an AutoGluon model of the decision on all {(r.data.train_approved + r.data.train_declined).toLocaleString()}, so w is large for approved applicants who look declined
            (clipped at {r.detail.reweighting.w_max}, scaled to mean 1). A band b is a tenth of training applicants by f<sub>0</sub>; q is the weight of a declined applicant's "bad" copy (1 − q its "good" copy).
            ΔAUC has DeLong's paired 95% CI; Δ log loss (ranking and calibration together; lower is better) a paired bootstrap CI. Better means a CI excludes 0 in its favour and none against it.
          </p>
          <Assumption k={r.k} decision={decision} />
        </div>
      </div>
      <div className="card" data-testid="ri-table">
        <div className="card-head"><div className="left"><h3 className="card-title">Four Ways to Train</h3><span className="helper" style={{ fontSize: 12 }}>each against approved only, on the same {t ? 'through-the-door' : 'approved'} holdout rows</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>method</th><th className="r">AUC</th><th className="r">ΔAUC [95% CI]</th><th style={{ width: '7%' }} title="ΔAUC and its CI on an axis centred at 0" /><th className="r">log loss</th><th className="r">Δ log loss [95% CI]</th>
            <th className="r" title="mean p̂ against the observed bad rate">p̂ / bad</th>{t && <th className="r" title="the declined holdout applicants: mean p̂ against their hidden bad rate">declined p̂ / bad</th>}{t && <th className="r" title={`approve the ${pct(t.old_policy.share)} with the lowest p̂ (the old approval rate): their bad rate; the old policy's was ${pct(t.old_policy.bad_rate ?? 0)}`}>bad @ {pct(t.old_policy.share)}</th>}<th>verdict</th></tr></thead>
          <tbody>
            {ORDER.map((m) => { const c = view[m]; return (
              <tr key={m} data-testid="ri-row" className={m === best && m !== 'approved_only' ? 'seg-total' : undefined}>
                <td><b>{NAME[m]}</b></td><td className="r mono">{fix(c.auc ?? null, 4)}</td>
                <td className="r mono">{m === 'approved_only' ? '—' : dl(c.delta_auc)}</td><td>{m === 'approved_only' ? null : <CiBar d={c.delta_auc} span={span} better="up" />}</td>
                <td className="r mono">{fix(c.log_loss, 4)}</td><td className="r mono">{m === 'approved_only' ? '—' : dl(c.delta_log_loss)}</td>
                <td className="r mono">{fix(c.mean_p, 3)}<span className="helper"> / {fix(c.bad_rate, 3)}</span></td>
                {t && <td className="r mono">{c.declined ? <>{fix(c.declined.mean_p, 3)}<span className="helper"> / {fix(c.declined.bad_rate, 3)}</span></> : '—'}</td>}
                {t && <td className="r mono">{c.approve_at_old_rate ? pct(c.approve_at_old_rate.bad_rate) : '—'}</td>}
                <td className={vcls(c.verdict)}>{m === 'approved_only' ? 'baseline' : (c.verdict ?? '—')}</td>
              </tr>) })}
          </tbody>
        </table></div>
        <div className="card-body"><details className="an-adv" data-testid="ri-more">
          <summary className="micro-label">How each method was built{t ? ' · the approved-only view' : ''}</summary>
          <div className="stack" style={{ gap: 'var(--space-8)', paddingTop: 'var(--space-8)' }}>
            <p className="helper" style={{ fontSize: 12.5 }}>
              <b>Reweighting:</b> decision model {r.detail.reweighting.acceptance_model.best_model}; w from {fix(r.detail.reweighting.weights.min, 2)} to {fix(r.detail.reweighting.weights.max, 2)} (median {fix(r.detail.reweighting.weights.median, 2)}, {r.detail.reweighting.weights.clipped} clipped), effective n {Math.round(r.detail.reweighting.effective_n).toLocaleString()} of {r.data.train_approved.toLocaleString()}.{' '}
              <b>Parcelling:</b> {r.data.train_declined.toLocaleString()} declined applicants drawn bad at {pct(r.detail.parcelling.assigned_bad_rate)} overall.{' '}
              <b>Fuzzy:</b> mean q = {fix(r.detail.fuzzy.mean_q, 3)}.{' '}
              Every fit: AutoGluon {r.presets ?? 'medium_quality'}, {r.time_limit ?? '—'} s, log loss ({ORDER.map((m) => `${NAME[m]} ${r.fits[m].best_model}`).join(' · ')}).
            </p>
            <div className="table-wrap"><table className="table compact">
              <thead><tr><th>parcel band (by f₀)</th><th className="r">approved</th><th className="r">approved bad rate</th><th className="r">declined</th><th className="r">assigned bad rate</th></tr></thead>
              <tbody>{r.detail.parcelling.bands.map((x) => <tr key={x.band}><td className="mono">{x.band}</td><td className="r mono">{x.approved.toLocaleString()}</td><td className="r mono">{x.approved_bad_rate == null ? '—' : pct(x.approved_bad_rate)}</td><td className="r mono">{x.declined.toLocaleString()}</td><td className="r mono">{pct(x.assigned_bad_rate)}</td></tr>)}</tbody>
            </table></div>
            {t && <><p className="helper" style={{ fontSize: 12.5 }}>Without the hidden outcomes, only the approved holdout applicants could be scored. That view is the one reject inference exists to correct:</p>
              <div className="table-wrap"><table className="table compact">
                <thead><tr><th>method</th><th className="r">AUC, approved only</th><th className="r">log loss</th><th className="r">mean p̂ vs bad rate</th></tr></thead>
                <tbody>{ORDER.map((m) => { const c = r.approved_holdout[m]; return <tr key={m}><td>{NAME[m]}</td><td className="r mono">{fix(c.auc ?? null, 4)}</td><td className="r mono">{fix(c.log_loss, 4)}</td><td className="r mono">{fix(c.mean_p, 3)} vs {fix(c.bad_rate, 3)}</td></tr> })}</tbody>
              </table></div></>}
          </div>
        </details></div>
      </div>
      {t && <Calibration r={r} />}
    </div>
  )
}
