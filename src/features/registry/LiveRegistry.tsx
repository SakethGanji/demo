import { useCallback, useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { AuditEntry, DatasetDetail, DatasetSummary, ModelVersion, RegisteredModel, RegistryCounts, Run } from '@/shared/api/types'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { Tabs } from '@/shared/primitives/Tabs'
import { AuditTable } from './components/AuditTable'
import { BatchDrift } from './components/BatchDrift'
import { MonitoringPanel } from './components/MonitoringPanel'
import { CreditReport } from './components/CreditReport'
import { Lineage } from './components/Lineage'
import { ModelCardView } from './components/ModelCardView'
import { RegisterPanel } from './components/RegisterPanel'
import { KindResults, RESULTS, TryItFor } from '@/shared/components/KindResults'
import { kindOf, type RunKind } from '@/shared/lib/runKinds'
import { useTabParam } from '@/shared/hooks/useHashParam'
import { ReasonCodes } from './components/ReasonCodes'
import { BuildSteps, Provenance, ReviewCard, Tradeoff } from './components/VersionOverview'
import { ServingCard } from './components/ServingCard'
import { APPROVAL_STATUS, STAGE_STATUS, bytes, runParamReg, serveParamReg, distillParamReg, short, us, useActor, versionParam, when } from './lib'
import { ErrorCard, HeroCell, Loading } from '@/shared/components/Status'

type Tab = 'Overview' | 'Results' | 'Model Card' | 'Try It' | 'Reason Codes' | 'Batch & Drift' | 'Monitoring' | 'Credit Report' | 'Fair Lending'
const TABS: Tab[] = ['Overview', 'Results', 'Model Card', 'Try It', 'Reason Codes', 'Batch & Drift', 'Monitoring', 'Credit Report', 'Fair Lending']
/** G11: the four per-kind result tabs became one Results tab; Audit is the card below, filtered to the version. */
const TAB_ALIASES: Record<string, Tab> = { 'Expected Loss': 'Results', Segments: 'Results', Survival: 'Results', Labels: 'Results', batch: 'Batch & Drift', drift: 'Batch & Drift', Audit: 'Overview' }
const binaryTabular = (v: ModelVersion) => flowOf(v) === 'tabular' && v.result?.version?.problem_type === 'binary'
/** Which tabs a version has. */
const APPLIES: Record<Tab, (v: ModelVersion) => boolean> = {
  Overview: () => true, 'Model Card': () => true, 'Try It': () => true, 'Batch & Drift': () => true,
  Results: (v) => !!RESULTS[flowOf(v) as RunKind],
  'Reason Codes': (v) => rowFlow(v) && v.result?.version?.problem_type === 'binary',
  Monitoring: (v) => flowOf(v) === 'tabular',
  'Credit Report': binaryTabular, 'Fair Lending': binaryTabular,
}

/** The registry on the API's real data: versions built from runs, their stages and reviews, the audit log, lineage,
 *  and each version's card, Try It and batch scoring with drift. */
export function LiveRegistry({ go }: { go: (r: string) => void }) {
  const [actor, setActor] = useActor()
  const [models, setModels] = useState<RegisteredModel[]>([])
  const [versions, setVersions] = useState<ModelVersion[]>([])
  const [counts, setCounts] = useState<RegistryCounts | null>(null)
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [loaded, setLoaded] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<string | null>(versionParam)
  const [tab, setTab] = useTabParam<Tab>(TABS, 'Overview', TAB_ALIASES)
  const [registering, setRegistering] = useState<string | null>(runParamReg)
  const [detail, setDetail] = useState<ModelVersion | null>(null)
  const [ds, setDs] = useState<DatasetDetail | null>(null)
  const [tick, setTick] = useState(0)
  const bump = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    const f = () => { setSel(versionParam()); const r = runParamReg(); if (r) setRegistering(r) }
    addEventListener('hashchange', f); return () => removeEventListener('hashchange', f)
  }, [])
  useEffect(() => {
    let alive = true
    Promise.all([api.registry.get(), api.registry.audit({ limit: 60 }), api.runs.list(), api.datasets.list()]).then(([reg, au, rs, dss]) => {
      if (!alive) return
      setModels(reg.models); setVersions(reg.versions); setCounts(reg.counts); setAudit(au); setRuns(rs); setDatasets(dss); setLoaded(true); setErr('')
    }).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [tick])
  // keep polling while anything builds
  useEffect(() => {
    if (!versions.some((v) => v.status === 'building')) return
    const t = setTimeout(bump, 1200)
    return () => clearTimeout(t)
  }, [versions, bump])
  useEffect(() => {
    if (!sel) { setDetail(null); return }
    let alive = true
    api.registry.version(sel).then((v) => alive && setDetail(v)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [sel, tick])
  // G5: the list holds tabular runs; a routed (segmented) version's run is fetched by id for Try It's form.
  const [otherRun, setOtherRun] = useState<Run | null>(null)
  const listed = detail ? runs.find((r) => r.id === detail.run_id) ?? null : null
  useEffect(() => {
    if (!detail || listed || !loaded) return
    let alive = true
    api.runs.get(detail.run_id).then((r) => alive && setOtherRun(r)).catch(() => {})
    return () => { alive = false }
  }, [detail?.run_id, listed, loaded]) // eslint-disable-line react-hooks/exhaustive-deps
  const run = listed ?? (otherRun && detail && otherRun.id === detail.run_id ? otherRun : null)
  useEffect(() => {
    if (!run) return
    let alive = true
    api.datasets.get(run.dataset_id).then((d) => alive && setDs(d)).catch(() => {})
    return () => { alive = false }
  }, [run?.dataset_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const select = (id: string | null, t: Tab = tab) => {
    setSel(id); setTab(t)
    history.replaceState(null, '', id ? `#/registry?version=${id}&tab=${encodeURIComponent(t)}` : '#/registry')
  }
  const okRuns = runs.filter((r) => r.status === 'succeeded' && kindOf(r) !== 'reject_inference')

  if (err && !loaded) return <ErrorCard err={err} />
  if (!loaded || !counts) return <Loading>Reading the registry: registered models, versions, stages, the audit log and lineage…</Loading>
  const prod = versions.filter((v) => v.stage === 'Production')
  const shownAudit = detail ? audit.filter((a) => a.version_id === detail.id) : audit

  return (
    <div className="page registry" data-testid="registry">
      <div className="page-head">
        <div>
          <h1 className="page-title">Registry</h1>
          <p className="helper sub">{counts.models} registered model{counts.models === 1 ? '' : 's'} · {counts.versions} version{counts.versions === 1 ? '' : 's'} · {counts.stages.Production} in Production · MLflow registry mirrored on a local store</p>
        </div>
        <div className="row" style={{ flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8 }}>
          <label className="actor"><span className="micro-label">acting as</span><input className="input" data-testid="actor" placeholder="your name" value={actor} onChange={(e) => setActor(e.target.value)} /></label>
          <button className="btn" onClick={() => go('models')}>Models</button>
          <button className={`btn ${detail ? '' : 'primary'}`} data-testid="open-register" disabled={!okRuns.length} onClick={() => setRegistering(okRuns[0]?.id ?? null)}>Register…</button>
        </div>
      </div>

      {registering != null && okRuns.length > 0 && (
        <RegisterPanel runs={okRuns} initialRun={registering} initialServe={serveParamReg()} initialDistill={distillParamReg()} actor={actor} onCancel={() => { setRegistering(null); if (runParamReg()) history.replaceState(null, '', '#/registry') }}
          onRegistered={(v) => { setRegistering(null); bump(); select(v.id, 'Overview') }} />
      )}

      <div className="card navy explain-hero" data-testid="registry-kpis">
        <div className="eh-cell lead"><span className="micro-label">in production</span><b className="mono" data-testid="kpi-production">{counts.stages.Production}</b>
          <small title={prod.map((v) => `${v.model_name} v${v.version}`).join('\n')}>{prod.length ? `${prod.slice(0, 2).map((v) => `${v.model_name} v${v.version}`).join(' · ')}${prod.length > 2 ? ` · +${prod.length - 2} more` : ''}` : 'nothing live yet'}</small></div>
        <div className="eh-cell"><span className="micro-label">staging</span><b className="mono" data-testid="kpi-staging">{counts.stages.Staging}</b><small className="math-s">awaiting promotion</small></div>
        <div className="eh-cell"><span className="micro-label">pending review</span><b className="mono" data-testid="kpi-pending" style={{ color: counts.approvals.pending ? 'var(--accent-secondary-shell)' : undefined }}>{counts.approvals.pending}</b><small className="math-s">four eyes: reviewer ≠ registrant</small></div>
        <div className="eh-cell"><span className="micro-label">approved · rejected</span><b className="mono">{counts.approvals.approved} · {counts.approvals.rejected}</b><small className="math-s">of {counts.versions} versions</small></div>
        <HeroCell label="building" sub="in worker processes" subClass="math-s">{counts.building}</HeroCell>
        <HeroCell label="batches scored" sub="with PSI drift" subClass="math-s">{counts.batches}</HeroCell>
      </div>

      {versions.length === 0 ? (
        <div className="card card-body" data-testid="registry-empty">
          <p className="helper">No versions yet. Register a finished run: it is cloned into its own predictor folder (optionally refit, cut to a latency budget and stripped for deployment), measured, hashed and logged to MLflow, then reviewed here.</p>
          {!okRuns.length && <button className="btn" onClick={() => go('build')}>Train a model first →</button>}
        </div>
      ) : <>
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">Lineage</h3><span className="helper" style={{ fontSize: 12 }}>dataset → run → version → registered model</span></div>
            <div className="row">
              <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="#0D9488" strokeWidth="2" /></svg>Production</span>
              <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--accent-primary)" strokeWidth="2" /></svg>Approved</span>
              <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="4 3" /></svg>Pending / Staging</span>
              <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-danger)" strokeWidth="2" strokeDasharray="2 4" /></svg>Rejected</span>
            </div>
          </div>
          <Lineage versions={versions} models={models} runs={runs} datasets={datasets} selected={sel} />
        </div>

        <div className="card table-wrap">
          <table className="table" data-testid="versions">
            <thead><tr><th>Version</th><th>Stage</th><th>Review</th><th>Source run</th><th>Served model</th><th className="r">Holdout</th><th className="r">Size</th><th className="r nocase">t̂₅₀ µs/row</th><th>Registered by</th><th className="r">When</th><th>h</th></tr></thead>
            <tbody>
              {versions.map((v) => {
                const m = v.summary.eval_metric ?? ''
                return (
                  <tr key={v.id} className={v.id === sel ? 'sel' : ''} onClick={() => select(v.id)} style={{ cursor: 'pointer' }} data-testid={`version-${v.id}`} data-stage={v.stage} data-approval={v.approval}>
                    <td className="table-cell"><b>{v.model_name}</b> v{v.version}</td>
                    <td>{v.status === 'ready' ? <StatusPill status={STAGE_STATUS[v.stage]}>{v.stage}</StatusPill> : <StatusPill status={v.status === 'failed' ? 'danger' : 'info'}>{v.status}</StatusPill>}</td>
                    <td><StatusPill status={APPROVAL_STATUS[v.approval]}>{v.approval}</StatusPill></td>
                    <td className="table-cell">{v.run_id}</td>
                    <td className="table-cell">{v.summary.best_model ?? '—'}</td>
                    <td className="r table-cell">{v.summary.holdout && m ? `${metricInfo(m)?.name ?? m} ${fmtScore(v.summary.holdout[m], 4)}` : '—'}</td>
                    <td className="r table-cell">{bytes(v.summary.size_bytes)}</td>
                    <td className="r table-cell">{us(v.summary.us_per_row_p50)}</td>
                    <td className="muted">{v.created_by}</td>
                    <td className="r table-cell">{when(v.created_at)}</td>
                    <td className="table-cell muted">{short(v.model_hash, 8)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </>}

      {detail && (
        <div className="stack version-detail" style={{ gap: 'var(--space-16)' }} data-testid="version-detail" data-version={detail.id} data-status={detail.status}>
          <div className="vd-head">
            <h2 className="vd-title">{detail.model_name} <span className="mono">v{detail.version}</span></h2>
            <StatusPill status={STAGE_STATUS[detail.stage]}>{detail.stage}</StatusPill><StatusPill status={APPROVAL_STATUS[detail.approval]}>{detail.approval}</StatusPill>
            <span className="helper mono" style={{ fontSize: 12 }}>from {detail.run_id} · registered by {detail.created_by}{detail.options.description ? ` · “${detail.options.description}”` : ''}</span>
            <span className="spacer" />
            <button className="btn sm" onClick={() => go(openRun(detail))} data-testid="vd-open-flow">{flowOf(detail) === 'tabular' ? 'Explain run →' : `Open ${flowOf(detail).replace('_', ' ')} run →`}</button>
          </div>
          <Tabs tabs={TABS.filter((t) => APPLIES[t](detail))} value={APPLIES[tab](detail) ? tab : 'Overview'} onChange={(t) => select(detail.id, t as Tab)} />
          {tab === 'Overview' && (detail.status !== 'ready'
            ? <BuildSteps v={detail} />
            : <>
              <div className="grid vd-top"><ReviewCard v={detail} actor={actor} onChanged={() => bump()} /><Provenance v={detail} /></div>
              {flowOf(detail) === 'tabular' ? <Tradeoff v={detail} /> : <FlowMeasured v={detail} />}
              {flowOf(detail) === 'tabular' && detail.result?.serving && <ServingCard key={detail.id} v={detail} sampleRow={
                ds && detail.result.version.features.length ? Object.fromEntries(detail.result.version.features.map((f) => [f, ds.profile.columns.find((c) => c.name === f)?.example ?? null])) : null} />}
              <BuildSteps v={detail} />
            </>)}
          {detail.status === 'ready' && tab === 'Results' && APPLIES.Results(detail) && <KindResults key={detail.id} kind={flowOf(detail) as RunKind} runId={detail.run_id} />}
          {detail.status === 'ready' && tab === 'Try It' && <TryItFor v={detail} kind={flowOf(detail)} run={run} ds={ds} open={() => go(openRun(detail))} />}
          {detail.status === 'ready' && tab === 'Model Card' && <ModelCardView v={detail} actor={actor} onEdited={bump} />}
          {detail.status === 'ready' && tab === 'Reason Codes' && rowFlow(detail) && detail.result?.version?.problem_type === 'binary' && <ReasonCodes key={detail.id} v={detail} actor={actor} onEdited={bump} />}
          {detail.status === 'ready' && tab === 'Monitoring' && flowOf(detail) === 'tabular' && <MonitoringPanel key={detail.id} v={detail} actor={actor} versions={versions} onSelect={(id) => select(id, 'Overview')} onChanged={bump} />}
          {detail.status === 'ready' && tab === 'Credit Report' && flowOf(detail) === 'tabular' && detail.result?.version?.problem_type === 'binary' && <CreditReport key={detail.id} v={detail} actor={actor} />}
          {detail.status === 'ready' && tab === 'Fair Lending' && flowOf(detail) === 'tabular' && detail.result?.version?.problem_type === 'binary' && (
            <div className="card card-body" data-testid="fl-link"><p className="helper">Fair lending (BISG proxy groups, adverse impact ratios, the four-fifths rule) is read from the version's run: it lives in Models → Explain → Fairness, next to the other fairness checks.{' '}
              <button className="btn sm" onClick={() => go(`models?run=${detail.run_id}&tab=Explain&sub=Fairness`)}>Open fair lending →</button></p></div>)}
          {detail.status === 'ready' && tab === 'Batch & Drift' && <BatchDrift v={detail} actor={actor} onScored={bump} kind={flowOf(detail)} />}
          {detail.status !== 'ready' && tab !== 'Overview' && <div className="card card-body row"><ResonanceWave /> This version is {detail.status}; it is usable once ready.</div>}
        </div>
      )}

      <div className="card" data-testid="audit-log">
        <div className="card-head"><div className="left"><h3 className="card-title">Audit Log</h3><span className="helper" style={{ fontSize: 12 }}>{detail ? <>{detail.model_name} v{detail.version} only · <button className="chip-btn" onClick={() => select(null)}>show all</button></> : 'append-only · who, what, when, and the model’s SHA-256 at that moment'}</span></div>
          <span className="mono helper" style={{ fontSize: 12 }}>{shownAudit.length} entr{shownAudit.length === 1 ? 'y' : 'ies'}</span></div>
        <AuditTable entries={shownAudit} onVersion={(id) => select(id)} testId={detail ? 'version-audit' : undefined} />
      </div>
    </div>
  )
}

const flowOf = (v: ModelVersion) => ((v.result as unknown as { kind?: string } | null)?.kind ?? 'tabular')
/** The page a version's run opens on (tabular: Explain). */
const openRun = (v: ModelVersion) => `models?run=${v.run_id}${flowOf(v) === 'tabular' ? '&tab=Explain' : ''}`
/** Versions that score one row with a TabularPredictor (G5: a routed version uses its row's segment model). */
const rowFlow = (v: ModelVersion) => flowOf(v) === 'tabular' || flowOf(v) === 'segmented'

/** Forecast and anomaly versions: what the build measured (no refit or infer_limit; those are TabularPredictor's). */
function FlowMeasured({ v }: { v: ModelVersion }) {
  const r = v.result as unknown as { kind: string; version: { best_model: string; size_bytes: number; latency: Record<string, number>; holdout: Record<string, number> | null; eval_metric: string } }
  const lat = r.version.latency ?? {}
  return (
    <div className="card" data-testid="flow-measured">
      <div className="card-head"><div className="left"><h3 className="card-title">Measured</h3><span className="helper" style={{ fontSize: 12 }}>{r.kind} · {r.version.best_model} · {(r.version.size_bytes / 1e6).toFixed(1)} MB on disk</span></div></div>
      <div className="card-body row" style={{ flexWrap: 'wrap', gap: 24 }}>
        {Object.entries(lat).filter(([, x]) => typeof x === 'number').map(([k, x]) => <div key={k}><span className="micro-label">{k.replace(/_/g, ' ')}</span><div className="mono">{Number(x).toPrecision(4)}</div></div>)}
        {Object.entries(r.version.holdout ?? {}).filter(([, x]) => typeof x === 'number').slice(0, 8).map(([k, x]) => <div key={k}><span className="micro-label">{k}</span><div className="mono">{Number(x).toFixed(4)}</div></div>)}
      </div>
    </div>
  )
}
