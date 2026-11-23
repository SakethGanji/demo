import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ModelVersion, MonitoringPoint, MonitoringState, RetrainProposal } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { BAND_COLOR, pct, versionLabel } from '../lib'
import { fix } from '@/shared/lib/format'
import { HeroCell } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'

const sgn = (v: number | null | undefined, d = 4) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`)
const COLORS = ['var(--viz-a)', 'var(--viz-b)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-e)']
const KIND_STATUS = { covariate: 'warning', score: 'warning', performance: 'danger', feed: 'danger' } as const

/** F18 · Monitoring over time for one version: every scored batch in data order, PSI per feature, the score's
 *  distribution, F15's adversarial AUC, performance once labels arrive (with the lag), alerts, the challenger's shadow
 *  comparison and the retrain proposal. Every number comes from GET /registry/versions/{id}/monitoring. */
export function MonitoringPanel({ v, actor, versions, onSelect, onChanged }: { v: ModelVersion; actor: string; versions: ModelVersion[]; onSelect: (id: string) => void; onChanged?: () => void }) {
  const { data: m, err, reload: refetch } = useFetch(() => api.registry.monitoring(v.id), [v.id], { pollMs: 1500, while: (d) => (d?.running.length ?? 0) > 0 })
  const reload = () => { refetch(); onChanged?.() }
  if (err) return <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>
  if (!m) return <div className="card card-body row"><ResonanceWave /> Reading the monitoring state…</div>
  const pts = m.points
  const a = m.alerts
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="monitoring">
      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">monitoring</span><b className="mono">{m.label.split(' ').pop()}</b><small>{m.model} · {m.stage} · {a.n_points} batches in data order · {a.n_labelled} labelled</small></div>
        <div className="eh-cell" data-testid="mon-first-cov"><span className="micro-label">first drift alert</span><b className="mono" style={{ color: a.first_covariate ? BAND_COLOR.shift : BAND_COLOR.stable }}>{a.first_covariate?.period ?? 'none'}</b><small className="math-s">{a.first_covariate ? 'seen when scored' : 'within thresholds'}</small></div>
        <div className="eh-cell" data-testid="mon-first-perf"><span className="micro-label">first performance alert</span><b className="mono" style={{ color: a.first_performance ? BAND_COLOR.shift : BAND_COLOR.stable }}>{a.first_performance?.period ?? 'none'}</b><small className="math-s">{a.first_performance ? `visible ${a.first_performance.visible_on}` : a.n_labelled ? 'none on labelled batches' : 'no labels yet'}</small></div>
        <div className="eh-cell"><span className="micro-label">labels up to</span><b className="mono">{a.latest_labelled?.period ?? '—'}</b><small className="math-s">{a.latest_labelled ? `lag ${a.latest_labelled.label_lag_days} days · ` : ''}{a.unlabelled_since ? `unlabelled from ${a.unlabelled_since}` : 'every batch labelled'}</small></div>
        <div className="eh-cell"><span className="micro-label">active alerts</span><b className="mono" style={{ color: a.active.length ? BAND_COLOR.shift : BAND_COLOR.stable }}>{a.active.length}</b><small className="math-s">{[...new Set(a.active.map((x) => x.kind))].join(', ') || 'none'}</small></div>
        {m.challenger && <div className="eh-cell"><span className="micro-label">challenger</span><b className="mono">{m.challenger.label.split(' ').pop()}</b><small className="math-s">{m.challenger.label} · {m.challenger.stage} · {m.challenger.batches} batches in the shadow</small></div>}
      </div>

      {pts.length === 0 ? <div className="card card-body helper">No scored batch yet. Score a file in Batch &amp; Drift (with its data date); each batch becomes one point here.</div> : <>
        <AlertsCard m={m} />
        <div className="grid psi-grid">
          <PsiOverTime m={m} />
          <ScoreOverTime m={m} />
        </div>
        <div className="grid psi-grid">
          <PerfOverTime m={m} />
          <CalibrationOverTime m={m} />
        </div>
        <BatchTable m={m} actor={actor} onLabelled={reload} />
      </>}
      <ChallengerCard m={m} v={v} actor={actor} versions={versions} onSelect={onSelect} onSaved={reload} />
      <RetrainCard m={m} v={v} actor={actor} />
      <ThresholdsCard m={m} actor={actor} onSaved={reload} />
    </div>
  )
}

function AlertsCard({ m }: { m: MonitoringState }) {
  const a = m.alerts
  const rows: [string, string, typeof a.first_covariate, string][] = [
    ['Covariate drift', 'PSI per feature, F15 adversarial AUC', a.first_covariate, 'when the batch is scored'],
    ['Score drift', 'PSI of p̂ against the holdout predictions', a.first_score, 'when the batch is scored'],
    ['Performance', 'AUC vs baseline, observed vs predicted rate', a.first_performance, 'when the batch’s labels arrive'],
  ]
  return (
    <div className="card" data-testid="mon-alerts">
      <div className="card-head"><div className="left"><h3 className="card-title">Which Signal Caught What</h3><span className="helper" style={{ fontSize: 12 }}>the first batch (in data order) on which each kind of signal crossed its threshold, and when that became visible</span></div></div>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>signal</th><th>reads</th><th>first batch</th><th>visible</th><th>evidence</th></tr></thead>
        <tbody>{rows.map(([n, reads, x, when]) => (
          <tr key={n}><td><b>{n}</b></td><td className="muted">{reads}</td><td className="mono">{x?.period ?? '—'}</td><td className="mono">{x ? `${x.visible_on ?? ''}` : '—'} <span className="helper" style={{ fontSize: 11 }}>{x ? when : ''}</span></td>
            <td>{x ? <span className="mono" style={{ fontSize: 12 }}>{x.text}</span> : <span className="helper">no alert</span>}</td></tr>
        ))}</tbody>
      </table></div>
      {a.active.length > 0 && <div className="card-body stack" style={{ gap: 6 }} data-testid="mon-active">
        <span className="micro-label">active now</span>
        {a.active.map((x, i) => <div key={i} className="row" style={{ gap: 8 }}><StatusPill status={KIND_STATUS[x.kind]}>{x.kind}</StatusPill><span className="mono" style={{ fontSize: 12 }}>{x.period}: {x.text}</span><span className="helper" style={{ fontSize: 11 }}>rule {x.rule}</span></div>)}
      </div>}
      <div className="caption" style={{ padding: '8px 16px' }}><span className="helper" style={{ fontSize: 12 }}>
        Covariate checks see p(x) the moment a batch is scored. A change in p(y | x) leaves the inputs alone and shows only in performance, once labels arrive; the newest batches have no labels, so their performance is unknown, not fine.
      </span></div>
    </div>
  )
}

const xAxis = (pts: MonitoringPoint[]) => ({ min: -0.5, max: Math.max(pts.length - 0.5, 0.5), ticks: pts.map((_, i) => i), title: 'data month', fmt: (x: number) => pts[Math.round(x)]?.period?.slice(2) ?? '' })

function PsiOverTime({ m }: { m: MonitoringState }) {
  const pts = m.points
  const th = m.config.thresholds
  const top = useMemo(() => {
    const mx: Record<string, number> = {}
    pts.forEach((p) => p.features.forEach((f) => { if (f.psi != null) mx[f.feature] = Math.max(mx[f.feature] ?? 0, f.psi) }))
    return Object.entries(mx).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([k]) => k)
  }, [pts])
  const maxY = Math.max(0.3, ...pts.flatMap((p) => p.features.map((f) => f.psi ?? 0)), ...pts.map((p) => p.prediction_psi ?? 0))
  const series: XYSeries[] = [
    ...top.map((f, i) => ({ name: f, color: COLORS[i], dots: true, points: pts.map((p, k) => [k, p.features.find((x) => x.feature === f)?.psi ?? 0] as [number, number]) })),
    { name: 'PSI(p̂)', color: 'var(--text-muted)', dash: '4 3', dots: true, points: pts.map((p, k) => [k, p.prediction_psi ?? 0] as [number, number]) },
  ]
  return (
    <PlotFrame title="PSI Per Feature Over Time" legend={series.map((s) => ({ name: s.name, color: s.color, dash: s.dash, data: [] }))}
      chips={<><ParamChip sym="θ_PSI" value={String(th.feature_psi)} /><ParamChip sym="watch" value={String(th.feature_psi_watch)} /></>}
      caption={<span>PSI = Σᵢ (aᵢ − eᵢ)·ln(aᵢ/eᵢ) of each batch against the training rows (eᵢ: training share in bin i, aᵢ: batch share). The {top.length} features that moved most, and the score. An alert fires when any feature's PSI &gt; θ_PSI = {th.feature_psi}.</span>}>
      <XYPlot height={230} testId="mon-psi" x={xAxis(pts)} y={{ min: 0, max: maxY * 1.1, ticks: niceTicks(0, maxY * 1.1, 4), title: 'PSI', fmt: (v) => v.toFixed(2) }}
        refs={[{ y: th.feature_psi, label: `θ = ${th.feature_psi}`, color: 'var(--status-danger)', dash: '4 3' }, { y: th.feature_psi_watch, label: 'watch', color: 'var(--status-warning)', dash: '2 3' }]} series={series} />
    </PlotFrame>
  )
}

function ScoreOverTime({ m }: { m: MonitoringState }) {
  const pts = m.points.filter((p) => p.score_quantiles)
  if (!pts.length) return null
  const ref = pts[0].mean_p_reference
  const q = (k: 'q10' | 'q25' | 'q50' | 'q75' | 'q90') => pts.map((p, i) => [i, p.score_quantiles![k]] as [number, number])
  const maxY = Math.max(...pts.map((p) => p.score_quantiles!.q90), 0.2)
  const series: XYSeries[] = [
    { name: 'mean p̂', color: 'var(--viz-a)', dots: true, width: 2, points: pts.map((p, i) => [i, p.mean_p ?? 0] as [number, number]) },
    { name: 'q₂₅ · q₇₅', color: 'var(--viz-b)', points: q('q25') }, { name: '', color: 'var(--viz-b)', points: q('q75') },
    { name: 'q₁₀ · q₉₀', color: 'var(--viz-b)', dash: '3 3', points: q('q10') }, { name: '', color: 'var(--viz-b)', dash: '3 3', points: q('q90') },
    { name: 'flagged share', color: 'var(--viz-d)', dash: '5 2', points: pts.map((p, i) => [i, p.flagged_share ?? 0] as [number, number]) },
  ]
  const ch = m.points.filter((p) => p.shadow && !p.shadow.error)
  if (ch.length) series.push({ name: 'challenger mean p̂', color: 'var(--viz-e)', dots: true, points: m.points.map((p, i) => [i, p.shadow?.mean_p_challenger ?? NaN] as [number, number]).filter((x) => Number.isFinite(x[1])) })
  return (
    <PlotFrame title="Score Distribution Over Time" legend={series.filter((s) => s.name).map((s) => ({ name: s.name, color: s.color, dash: s.dash, data: [] }))}
      chips={ref != null ? <ParamChip sym="p̄ holdout" value={ref.toFixed(3)} /> : undefined}
      caption={<span>p̂ = P(default | x) of every row of each batch: the mean, the quartiles and the 10th / 90th percentiles, and the share decided adverse at the served threshold. The dashed line is the mean p̂ on the version's holdout.</span>}>
      <XYPlot height={230} testId="mon-score" x={xAxis(pts)} y={{ min: 0, max: maxY * 1.1, ticks: niceTicks(0, maxY * 1.1, 4), title: 'p̂', fmt: (v) => v.toFixed(2) }}
        refs={ref != null ? [{ y: ref, label: 'holdout p̄', dash: '4 3' }] : []} series={series} />
    </PlotFrame>
  )
}

function PerfOverTime({ m }: { m: MonitoringState }) {
  const pts = m.points
  const b = m.baseline
  const th = m.config.thresholds
  const lab = pts.map((p, i) => [i, p] as const).filter(([, p]) => p.performance?.auc != null)
  const lo = Math.min(b?.auc_ci?.[0] ?? 1, ...lab.map(([, p]) => p.performance!.auc_ci![0])) - 0.03
  const hi = Math.max(b?.auc_ci?.[1] ?? 0, ...lab.map(([, p]) => p.performance!.auc_ci![1])) + 0.02
  return (
    <PlotFrame title="Performance With The Label Lag" legend={[{ name: 'AUC ± 1.96·SE (DeLong)', color: 'var(--viz-a)', data: [] }, { name: 'challenger AUC (same rows)', color: 'var(--viz-e)', data: [] }]}
      chips={<>{b?.auc != null && <ParamChip sym="AUC₀" value={b.auc.toFixed(3)} />}<ParamChip sym="δ" value={String(th.auc_drop)} /></>}
      caption={<span>Each point is plotted at its <b>data</b> month, but exists only from its <b>label</b> date (lag = label date − data date, shown on hover). AUC₀ is the version's holdout AUC. Alert: the upper 95 % bound below AUC₀ − δ. Batches without labels are marked ○ on the axis: their performance is not known yet.</span>}>
      <XYPlot height={230} testId="mon-perf" x={xAxis(pts)} y={{ min: Math.max(0, lo), max: Math.min(1, hi), ticks: niceTicks(Math.max(0, lo), Math.min(1, hi), 4), title: 'AUC', fmt: (v) => v.toFixed(2) }}
        refs={b?.auc != null ? [{ y: b.auc, label: 'AUC₀', dash: '4 3' }, { y: b.auc - th.auc_drop, label: 'AUC₀ − δ', color: 'var(--status-danger)', dash: '2 3' }] : []}
        series={[
          { name: 'AUC', color: 'var(--viz-a)', dots: true, points: lab.map(([i, p]) => [i, p.performance!.auc!] as [number, number]), tip: (k) => { const p = lab[k][1]; return `[${fix(p.performance!.auc_ci![0])}, ${fix(p.performance!.auc_ci![1])}] · n ${p.performance!.n} · labels ${p.labels_date} (lag ${p.label_lag_days} d)` } },
          { name: 'challenger', color: 'var(--viz-e)', dash: '4 2', dots: true, points: lab.filter(([, p]) => p.paired?.auc).map(([i, p]) => [i, p.paired!.auc!.auc_challenger] as [number, number]) },
        ]}>
        {(sx, sy) => <g>
          {lab.map(([i, p]) => <line key={i} x1={sx(i)} x2={sx(i)} y1={sy(p.performance!.auc_ci![0])} y2={sy(p.performance!.auc_ci![1])} stroke="var(--viz-a)" strokeWidth={1.5} opacity={0.6} />)}
          {pts.map((p, i) => !p.performance && <circle key={`u${i}`} cx={sx(i)} cy={sy(Math.max(0, lo)) - 6} r={3.5} fill="none" stroke="var(--text-muted)" />)}
        </g>}
      </XYPlot>
    </PlotFrame>
  )
}

function CalibrationOverTime({ m }: { m: MonitoringState }) {
  const pts = m.points
  const lab = pts.map((p, i) => [i, p] as const).filter(([, p]) => p.performance)
  const maxY = Math.max(0.2, ...lab.map(([, p]) => p.performance!.observed_ci[1]), ...pts.map((p) => p.mean_p ?? 0)) * 1.1
  return (
    <PlotFrame title="Observed vs Predicted Default Rate" legend={[{ name: 'observed ȳ (Wilson 95 %)', color: 'var(--viz-d)', data: [] }, { name: 'predicted p̄', color: 'var(--viz-a)', data: [] }]}
      chips={<ParamChip sym="θ_z" value={String(m.config.thresholds.calibration_z)} />}
      caption={<span>Calibration in the large: z = (Σy − Σp̂) / √Σp̂(1 − p̂). Covariate drift moves both lines together (the model still prices the new x); a concept change opens a gap only the labels show. Alert: |z| &gt; θ_z.</span>}>
      <XYPlot height={230} testId="mon-calib" x={xAxis(pts)} y={{ min: 0, max: maxY, ticks: niceTicks(0, maxY, 4), title: 'rate', fmt: (v) => v.toFixed(2) }}
        series={[
          { name: 'predicted p̄', color: 'var(--viz-a)', dots: true, points: pts.map((p, i) => [i, p.mean_p ?? 0] as [number, number]) },
          { name: 'observed ȳ', color: 'var(--viz-d)', dots: true, points: lab.map(([i, p]) => [i, p.performance!.observed_rate] as [number, number]), tip: (k) => `z = ${sgn(lab[k][1].performance!.calibration_z, 1)} · labels ${lab[k][1].labels_date}` },
        ]}>
        {(sx, sy) => <g>{lab.map(([i, p]) => <line key={i} x1={sx(i)} x2={sx(i)} y1={sy(p.performance!.observed_ci[0])} y2={sy(p.performance!.observed_ci[1])} stroke="var(--viz-d)" strokeWidth={1.5} opacity={0.6} />)}</g>}
      </XYPlot>
    </PlotFrame>
  )
}

function BatchTable({ m, actor, onLabelled }: { m: MonitoringState; actor: string; onLabelled: () => void }) {
  const [bid, setBid] = useState<string>('')
  const [file, setFile] = useState<File | null>(null)
  const [idc, setIdc] = useState(m.config.id_column ?? '')
  const [ld, setLd] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const upload = async () => {
    if (!file || !bid) return
    setBusy(true); setMsg('')
    try { const r = await api.registry.uploadLabels(bid, file, actor, idc || undefined, undefined, ld || undefined); setMsg(`✓ ${(r.labels as { n_joined: number }).n_joined} rows labelled`); onLabelled() } catch (e) { setMsg(`✕ ${(e as Error).message}`) } finally { setBusy(false) }
  }
  return (
    <div className="card" data-testid="mon-table">
      <div className="card-head"><div className="left"><h3 className="card-title">Batches In Data Order</h3><span className="helper" style={{ fontSize: 12 }}>one row per scored batch; performance once its labels are attached</span></div></div>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>data month</th><th className="r">rows</th><th className="r">max PSI</th><th>feature</th><th className="r nocase">PSI(p̂)</th><th className="r nocase">mean p̂</th><th className="r nocase">AUC_adv</th><th>labels</th><th className="r">lag</th><th className="r">AUC</th><th className="r nocase">ȳ · p̄ (z)</th><th>alerts</th></tr></thead>
        <tbody>{m.points.map((p) => {
          const worst = p.features.find((f) => f.psi != null)
          const pf = p.performance
          return (
            <tr key={p.batch_id} data-testid={`mon-row-${p.period}`}>
              <td className="mono" title={p.batch_id}>{p.period}</td><td className="r mono">{p.n_rows?.toLocaleString()}</td>
              <td className="r mono" style={{ color: worst ? BAND_COLOR[worst.band] : undefined }}>{fix(p.max_psi)}</td><td className="mono">{worst?.feature}</td>
              <td className="r mono">{fix(p.prediction_psi)}</td><td className="r mono">{fix(p.mean_p)}</td>
              <td className="r mono">{p.adversarial?.auc != null ? `${fix(p.adversarial.auc)} ${p.adversarial.verdict ?? ''}` : p.adversarial?.status === 'running' ? '…' : '—'}</td>
              <td className="mono">{p.labels_date ?? <span className="helper">awaiting</span>}</td><td className="r mono">{p.label_lag_days != null ? `${p.label_lag_days} d` : '—'}</td>
              <td className="r mono" title={pf?.auc_ci ? `95 % [${fix(pf.auc_ci[0])}, ${fix(pf.auc_ci[1])}]` : ''}>{fix(pf?.auc)}</td>
              <td className="r mono">{pf ? `${fix(pf.observed_rate)} · ${fix(pf.mean_p)} (${sgn(pf.calibration_z, 1)})` : '—'}</td>
              <td>{p.alerts.map((x, i) => <StatusPill key={i} status={KIND_STATUS[x.kind]}>{x.signal.replace('_', ' ')}</StatusPill>)}</td>
            </tr>
          )
        })}</tbody>
      </table></div>
      <div className="card-body row" style={{ gap: 8, flexWrap: 'wrap', borderTop: '1px solid var(--border-default)' }}>
        <span className="micro-label">attach labels</span>
        <select className="select sm" value={bid} onChange={(e) => setBid(e.target.value)} data-testid="mon-label-batch"><option value="">batch…</option>{m.points.map((p) => <option key={p.batch_id} value={p.batch_id}>{p.period} · {p.batch_id}{p.performance ? ' (labelled)' : ''}</option>)}</select>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <label className="row" style={{ gap: 4 }}><span className="helper">id column</span><input className="input sm mono" style={{ width: 140 }} value={idc} onChange={(e) => setIdc(e.target.value)} /></label>
        <label className="row" style={{ gap: 4 }}><span className="helper">label date</span><input className="input sm mono" style={{ width: 110 }} placeholder="YYYY-MM-DD" value={ld} onChange={(e) => setLd(e.target.value)} /></label>
        <button className="btn sm" disabled={!file || !bid || busy || !actor.trim()} onClick={upload}>Attach</button>
        {msg && <span className="helper" style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}

function ChallengerCard({ m, v, actor, versions, onSelect, onSaved }: { m: MonitoringState; v: ModelVersion; actor: string; versions: ModelVersion[]; onSelect: (id: string) => void; onSaved: () => void }) {
  const ch = m.challenger
  const staging = versions.filter((x) => x.model_name === v.model_name && x.stage === 'Staging' && x.status === 'ready' && x.id !== v.id)
  const [pick, setPick] = useState('')
  const [err, setErr] = useState('')
  const save = async (id: string) => { setErr(''); try { await api.registry.putMonitoring(v.model_name, { actor, challenger_version_id: id }); onSaved() } catch (e) { setErr((e as Error).message) } }
  const pool = ch?.pooled
  const shadowed = m.points.filter((p) => p.shadow && !p.shadow.error && p.shadow.version_id === ch?.version_id)
  return (
    <div className="card" data-testid="mon-challenger">
      <div className="card-head"><div className="left"><h3 className="card-title">Champion · Challenger</h3><span className="helper" style={{ fontSize: 12 }}>a Staging version shadow-scores every batch of the Production version: same rows, its own features and threshold; its output is never served</span></div>
        <div className="row" style={{ gap: 6 }}>
          <select className="select sm" value={pick} onChange={(e) => setPick(e.target.value)}><option value="">{staging.length ? 'a Staging version…' : 'no Staging version of this model'}</option>{staging.map((x) => <option key={x.id} value={x.id}>{versionLabel(x)} · {x.summary.best_model}</option>)}</select>
          <button className="btn sm" disabled={!pick || !actor.trim()} onClick={() => save(pick)}>Set Challenger</button>
          {ch && <button className="btn sm" disabled={!actor.trim()} onClick={() => save('')}>Clear</button>}
        </div>
      </div>
      {err && <div className="card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>}
      {!ch ? <div className="card-body helper">No challenger. {v.stage !== 'Production' ? 'Shadow scoring runs on the Production version’s batches.' : 'Move another version of this model to Staging, then set it here; batches scored from then on are shadow-scored.'}</div> : <>
        <div className="card-body">
          <div className="card navy explain-hero">
            <div className="eh-cell"><span className="micro-label">challenger</span><b className="mono">{ch.label.split(' ').pop()}</b><small>{ch.label} · {ch.stage} · review {ch.approval} · {ch.batches} batches shadowed</small></div>
            <HeroCell label="same decision" sub="mean over batches · 1[p̂₁ ≥ t₁] = 1[p̂₂ ≥ t₂]" subClass="math-s">{pct(ch.agreement_mean)}</HeroCell>
            <div className="eh-cell"><span className="micro-label">score PSI</span><b className="mono">{fix(ch.psi_mean)}</b><small className="math-s">p̂₂ on p̂₁'s deciles · Spearman ρ {fix(ch.spearman_mean)}</small></div>
            {pool?.auc && <div className="eh-cell" data-testid="mon-paired"><span className="micro-label">ΔAUC (paired)</span><b className="mono" style={{ color: pool.auc.ci[0] > 0 ? BAND_COLOR.stable : pool.auc.ci[1] < 0 ? BAND_COLOR.shift : undefined }}>{sgn(pool.auc.delta)}</b><small className="math-s">[{sgn(pool.auc.ci[0])}, {sgn(pool.auc.ci[1])}] · p {fix(pool.auc.p_value)} · n {pool.n.toLocaleString()}</small></div>}
            {pool && <div className="eh-cell"><span className="micro-label">Δ log loss (paired)</span><b className="mono">{sgn(pool.log_loss.delta)}</b><small className="math-s">[{sgn(pool.log_loss.ci[0])}, {sgn(pool.log_loss.ci[1])}] · bootstrap B = {pool.log_loss.B}</small></div>}
          </div>
          <div className="formula" style={{ marginTop: 8 }}>
            <div className="eq">Δ = AUC₂ − AUC₁,  Var(Δ) = Var(A₁) + Var(A₂) − 2 Cov(A₁, A₂)</div>
            <dl className="where">
              <dt>AUC₁</dt><dd>the champion ({m.label}) on the labelled rows{pool?.auc ? `: ${fix(pool.auc.auc_champion, 4)}` : ''}</dd>
              <dt>AUC₂</dt><dd>the challenger ({ch.label}) on the same rows{pool?.auc ? `: ${fix(pool.auc.auc_challenger, 4)}` : ''}</dd>
              <dt>Cov</dt><dd>DeLong's structural components of both scores on the same positives and negatives{pool?.auc?.corr != null ? ` (correlation ${fix(pool.auc.corr)})` : ''}; paired, so the interval is narrower than two separate ones</dd>
            </dl>
          </div>
          {!pool && <p className="helper" style={{ fontSize: 12 }}>The paired comparison needs labels: it appears once a shadow-scored batch is labelled.</p>}
        </div>
        {shadowed.length > 0 && <div className="table-wrap"><table className="table compact">
          <thead><tr><th>data month</th><th className="r">same decision</th><th className="r nocase">κ</th><th className="r">score PSI</th><th className="r nocase">ρ</th><th className="r">flagged 1 · 2</th><th className="r nocase">ΔAUC [95 %]</th></tr></thead>
          <tbody>{shadowed.map((p) => <tr key={p.batch_id}><td className="mono">{p.period}</td><td className="r mono">{pct(p.shadow!.agreement)}</td><td className="r mono">{fix(p.shadow!.kappa)}</td><td className="r mono">{fix(p.shadow!.psi)}</td><td className="r mono">{fix(p.shadow!.spearman)}</td>
            <td className="r mono">{pct(p.shadow!.flagged_champion)} · {pct(p.shadow!.flagged_challenger)}</td>
            <td className="r mono">{p.paired?.auc ? `${sgn(p.paired.auc.delta, 3)} [${sgn(p.paired.auc.ci[0], 3)}, ${sgn(p.paired.auc.ci[1], 3)}]` : <span className="helper">awaiting labels</span>}</td></tr>)}</tbody>
        </table></div>}
        <div className="card-body row" style={{ gap: 8 }}>
          <span className="helper" style={{ fontSize: 12 }}>{ch.decision}</span>
          <button className="btn sm" onClick={() => onSelect(ch.version_id)}>Open {ch.label} to review →</button>
        </div>
      </>}
    </div>
  )
}

function RetrainCard({ m, v, actor }: { m: MonitoringState; v: ModelVersion; actor: string }) {
  const [p, setP] = useState<RetrainProposal | null>(null)
  const [months, setMonths] = useState<number>(m.config.retrain_window_months)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [out, setOut] = useState<{ run: string; ds: string } | null>(null)
  useEffect(() => { api.registry.retrainProposal(v.id, months).then(setP).catch((e) => setErr((e as Error).message)) }, [v.id, months, m.alerts.n_labelled])
  const launch = async () => {
    setBusy(true); setErr('')
    try { const r = await api.registry.launchRetrain(v.id, actor, months); setOut({ run: r.run.id, ds: r.dataset.id }) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const body = (p?.body ?? {}) as { presets?: unknown; time_limit?: number; fit_kwargs?: Record<string, unknown>; split_spec?: Record<string, unknown> | null; eval_metric?: string }
  return (
    <div className="card" data-testid="mon-retrain">
      <div className="card-head"><div className="left"><h3 className="card-title">Retrain Proposal</h3><span className="helper" style={{ fontSize: 12 }}>the champion's configuration, trained on the most recent labelled window; it launches only when you click</span></div>
        <div className="row" style={{ gap: 6 }}>
          {m.retrain.suggested && <StatusPill status="warning">suggested by the alerts</StatusPill>}
          <label className="row" style={{ gap: 4 }}><span className="helper">window</span><select className="select sm" style={{ minWidth: 110 }} value={months} onChange={(e) => setMonths(Number(e.target.value))}>{[3, 6, 9, 12].map((k) => <option key={k} value={k}>{k} months</option>)}</select></label>
          <button className="btn sm primary" disabled={!p?.ready || busy || !actor.trim() || !!out} onClick={launch} data-testid="mon-retrain-launch">{busy ? 'Launching…' : 'Launch Retrain Run'}</button>
        </div>
      </div>
      {err && <div className="card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>}
      {p && <div className="card-body stack" style={{ gap: 8 }}>
        {(m.retrain.feed_fixes ?? []).length > 0 && <div className="split-warn" role="note" data-testid="mon-feed-fix">{(m.retrain.feed_fixes ?? []).map((x) => <div key={x.feature}><b>Fix the feed first:</b> {x.text}. A unit change, not drift: not a reason to retrain.</div>)}</div>}
        {p.why.length > 0 && <div className="stack" style={{ gap: 4 }}><span className="micro-label">why</span>{p.why.map((w) => <span key={w} className="mono" style={{ fontSize: 12 }}>· {w}</span>)}</div>}
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <ParamChip sym="window" value={p.window.start ? `${p.window.start} … ${p.window.end}` : '—'} />
          <ParamChip sym="n" value={`${p.rows.toLocaleString()} labelled rows`} />
          <ParamChip sym="batches" value={String(p.batches.length)} />
          {body.presets != null && <ParamChip sym="presets" value={String(body.presets)} />}
          {body.time_limit != null && <ParamChip sym="τ" value={`${body.time_limit} s`} />}
          {body.eval_metric && <ParamChip sym="metric" value={body.eval_metric} />}
          {body.fit_kwargs?.hyperparameters != null && <ParamChip sym="models" value={Object.keys(body.fit_kwargs.hyperparameters as object).join(' + ')} />}
        </div>
        <span className="helper" style={{ fontSize: 12 }}>Split: {p.split_note}. Lineage: the new dataset records the batches and label files it came from; the run's parent is {p.champion_run_id}. {p.note}</span>
        {p.blocked && <span className="helper" style={{ color: 'var(--status-warning)' }}>▲ {p.blocked}</span>}
        {out && <span className="helper" data-testid="mon-retrain-out">✓ Started <a href={`#/quick-train?run=${out.run}`}>{out.run}</a> on dataset {out.ds}. Register and review it like any run; promotion stays a human decision.</span>}
      </div>}
    </div>
  )
}

function ThresholdsCard({ m, actor, onSaved }: { m: MonitoringState; actor: string; onSaved: () => void }) {
  const [th, setTh] = useState<Record<string, number>>({ ...m.config.thresholds })
  const [msg, setMsg] = useState('')
  const save = async () => { setMsg(''); try { await api.registry.putMonitoring(m.model, { actor, thresholds: th }); setMsg('✓ saved (audited)'); onSaved() } catch (e) { setMsg(`✕ ${(e as Error).message}`) } }
  const label: Record<string, string> = { feature_psi: 'θ_PSI (feature)', feature_psi_watch: 'watch band', prediction_psi: 'θ_score (PSI of p̂)', adversarial_auc: 'θ_adv (AUC lower bound)', auc_drop: 'δ (AUC drop)', calibration_z: 'θ_z (calibration)', min_labelled: 'min labelled rows' }
  return (
    <details className="card reg-adv" data-testid="mon-thresholds">
      <summary className="card-head micro-label" style={{ cursor: 'pointer' }}>Advanced · alert thresholds for {m.model}{m.config.updated_by ? ` (last set by ${m.config.updated_by}, ${m.config.updated_at})` : ''}</summary>
      <div className="card-body stack" style={{ gap: 8 }}>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
          {Object.keys(label).map((k) => <label key={k} className="stack" style={{ gap: 2 }}><span className="helper" style={{ fontSize: 12 }}>{label[k]}</span>
            <input className="input sm mono" type="number" step="0.01" value={th[k]} onChange={(e) => setTh({ ...th, [k]: Number(e.target.value) })} />
            {m.rules[k] && <small className="helper mono" style={{ fontSize: 11 }}>{m.rules[k]}</small>}</label>)}
        </div>
        <div className="row" style={{ gap: 8 }}><button className="btn sm" disabled={!actor.trim()} onClick={save}>Save Thresholds</button>{msg && <span className="helper">{msg}</span>}</div>
      </div>
    </details>
  )
}
