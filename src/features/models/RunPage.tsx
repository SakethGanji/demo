import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { useApi } from '@/shared/api/mode'
import type { DoctorAction, Run } from '@/shared/api/types'
import { KindRun, type KindSpec } from '@/shared/components/KindFlow'
import { ApiOnly, ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam, useTabParam } from '@/shared/hooks/useHashParam'
import { kindOf, problemLine, type RunKind } from '@/shared/lib/runKinds'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { NotesStrip } from './components/NotesStrip'
import { Tabs } from '@/shared/primitives/Tabs'
import { AnomalyRun } from '@/features/anomaly/AnomalyPage'
import { EL_SPEC } from '@/features/expected-loss/ExpectedLossPage'
import { ForecastRun } from '@/features/forecast/ForecastPage'
import { ImportRun } from '@/features/import/ImportPage'
import { ML_SPEC } from '@/features/multilabel/MultilabelPage'
import { initialChoices } from '@/features/quick-train/QuickTrainPage'
import { SAMPLES } from '@/features/quick-train/datasets'
import { simulate } from '@/features/quick-train/race/mockRun'
import { ResultsStep } from '@/features/quick-train/results/ResultsStep'
import { inferTask } from '@/features/quick-train/wizard'
import { RI_SPEC } from '@/features/reject-inference/RejectInferencePage'
import { SEG_SPEC } from '@/features/segmented/SegmentedPage'
import { SURV_SPEC } from '@/features/survival/SurvivalPage'
import { AnnotatorsPanel } from './explain/components/AnnotatorsPanel'
import { DoctorPanel } from './explain/components/DoctorPanel'
import { HoldoutErrorsPanel } from './explain/components/HoldoutErrorsPanel'
import { LabellingQueuePanel } from './explain/components/LabellingQueuePanel'
import { LearningCurvePanel } from './explain/components/LearningCurvePanel'
import { ShiftPanel } from './explain/components/ShiftPanel'
import { SlicesPanel } from './explain/components/SlicesPanel'
import { ExplainTab } from './explain/ExplainTab'
import { DOCTOR_SUBS, TABS, tabOfSub, type DoctorSub, type ExplainSub, type Tab } from './tabs'
import { MockConfusion } from './ModelsPage'

export type ApplyPatch = (runId: string, patch: NonNullable<DoctorAction['patch']>) => void
const SPECS: Partial<Record<RunKind, KindSpec>> = { expected_loss: EL_SPEC, segmented: SEG_SPEC, survival: SURV_SPEC, reject_inference: RI_SPEC, multilabel: ML_SPEC }

/** Models → one run: Results, Explain and Doctor for a tabular fit; the kind's own page for the others. */
export function RunPage({ runId, go, onApplyPatch }: { runId: string; go: (r: string) => void; onApplyPatch?: ApplyPatch }) {
  const { mode } = useApi()
  return mode === 'mock' ? <MockRun runId={runId} go={go} /> : <LiveRun runId={runId} go={go} onApplyPatch={onApplyPatch} />
}

function LiveRun({ runId, go, onApplyPatch }: { runId: string; go: (r: string) => void; onApplyPatch?: ApplyPatch }) {
  const r = useFetch(() => api.runs.get(runId), [runId], { pollMs: 3000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <ErrorCard err={r.err} />
  if (!r.data) return <Loading>Loading run {runId}…</Loading>
  const run = r.data
  const k = kindOf(run)
  const spec = SPECS[k]
  if (spec) return <KindRun spec={spec} runId={runId} go={go} />
  if (k === 'forecast') return <ForecastRun runId={runId} go={go} />
  if (k === 'anomaly') return <AnomalyRun runId={runId} go={go} />
  return <TabularRun run={run} go={go} onApplyPatch={onApplyPatch} reload={r.reload} />
}

const writeHash = (runId: string, tab: Tab, sub?: string, extra: Record<string, string> = {}) =>
  history.replaceState(null, '', `#/models?run=${runId}&tab=${tab}${sub ? `&sub=${encodeURIComponent(sub)}` : ''}${Object.entries(extra).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('')}`)

function TabularRun({ run, go, onApplyPatch, reload }: { run: Run; go: (r: string) => void; onApplyPatch?: ApplyPatch; reload: () => void }) {
  const [tab, setTab] = useTabParam<Tab>(TABS, 'Results')
  const subParam = useHashParam('sub')
  const [sub, setSub] = useState<string>(() => tabOfSub(subParam)?.sub ?? '')
  useEffect(() => { const to = tabOfSub(subParam); if (to) setSub(to.sub) }, [subParam]) // a redirect or a link while open
  const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
  const [extra, setExtra] = useState<Record<string, string>>(() => Object.fromEntries(['slice', 'group'].flatMap((k) => (q.get(k) ? [[k, q.get(k)!]] : []))))
  const done = run.status === 'succeeded'
  const tabular = kindOf(run) === 'tabular'
  const tabs: Tab[] = done ? (tabular ? [...TABS] : ['Results', 'Explain']) : ['Results']
  const cur = tabs.includes(tab) ? tab : 'Results'
  const pickTab = (t: Tab) => { setTab(t); setSub(''); setExtra({}); writeHash(run.id, t) }
  /** Open a view by name from anywhere on the page (the Doctor's actions, Weak Slices' links, Errors → SHAP). */
  const open = (name: string, more: Record<string, string> = {}) => {
    const to = tabOfSub(name)
    if (!to) return
    setTab(to.tab); setSub(to.sub); setExtra(more); writeHash(run.id, to.tab, to.sub, more)
  }
  return (
    <div className="page run-page" data-testid="run-page" data-run={run.id} data-status={run.status}>
      <div className="page-head">
        <div><h1 className="page-title">{run.name}</h1><p className="helper sub"><span className="mono">{run.id}</span> · {problemLine(run)} · {run.dataset_id}{run.label ? <> · y = <span className="mono">{run.label}</span></> : null}</p></div>
        <StatusPill status={done ? 'success' : run.status === 'failed' ? 'danger' : 'info'}>{run.status}</StatusPill>
      </div>
      {kindOf(run) === 'import' && <ImportRun runId={run.id} />}
      <NotesStrip kind="run" id={run.id} />
      {tabs.length > 1 && <Tabs tabs={tabs} value={cur} onChange={pickTab} testIds={{ Explain: 'results-explain', Doctor: 'run-doctor' }} />}
      {!done && (run.status === 'failed' || run.status === 'cancelled'
        ? <ErrorCard inline err={`This run ${run.status}${run.error ? `: ${run.error}` : '.'}`} />
        : kindOf(run) !== 'import' && <div className="card card-body row"><ResonanceWave /> Training · {run.status}. <span className="spacer" /><button className="btn primary" onClick={() => go(`quick-train?run=${run.id}`)}>Watch it train →</button></div>)}
      {done && cur === 'Results' && <ResultsStep runId={run.id} mockRun={null} ds={null} live go={go} onAgain={() => go('build')} embedded />}
      {done && cur === 'Explain' && <ExplainTab run={run} sub={(sub || 'Performance') as ExplainSub} extra={extra} pick={open} onRunChanged={reload} />}
      {done && cur === 'Doctor' && <DoctorTab run={run} sub={(sub || 'Model Doctor') as DoctorSub} extra={extra} open={open} go={go} onApplyPatch={onApplyPatch} />}
    </div>
  )
}

/** The run page's Doctor tab: what limits the model and what to do next, and how good the labels are. */
function DoctorTab({ run, sub, extra, open, go, onApplyPatch }: { run: Run; sub: DoctorSub; extra: Record<string, string>; open: (name: string, more?: Record<string, string>) => void; go: (r: string) => void; onApplyPatch?: ApplyPatch }) {
  const labels = run.problem_type === 'binary' || run.problem_type === 'multiclass'
  const subs = DOCTOR_SUBS.filter((s) => s !== 'Labels' || labels)
  const cur = subs.includes(sub) ? sub : 'Model Doctor'
  return (
    <div className="stack explain" style={{ gap: 'var(--space-16)' }} data-testid="doctor-tab">
      <Tabs tabs={subs} value={cur} onChange={(t) => open(t)} />
      {cur === 'Model Doctor' && <DoctorPanel run={run} links={{ go, onApplyPatch, openTab: open }} />}
      {cur === 'Holdout Errors' && <HoldoutErrorsPanel run={run} onRow={(row) => open('Features & SHAP', { row: String(row) })} />}
      {cur === 'Weak Slices' && <SlicesPanel run={run} links={{
        onRow: (row) => open('Features & SHAP', { row: String(row) }),
        onLabel: (s) => open('Labels', { slice: s.id }),
        onFairness: (c) => open('Fairness', { group: c }),
      }} />}
      {cur === 'Shift' && <ShiftPanel run={run} go={go} onApplyPatch={onApplyPatch} />}
      {cur === 'Learning Curve' && <LearningCurvePanel run={run} />}
      {cur === 'Labels' && <><LabellingQueuePanel key={extra.slice ?? 'all'} run={run} sliceId={extra.slice} /><AnnotatorsPanel run={run} /></>}
    </div>
  )
}

/** Mock mode: a simulated run with the same three tabs (Explain shows the scripted confusion matrix; the Doctor reads real runs only). */
function MockRun({ runId, go }: { runId: string; go: (r: string) => void }) {
  const [tab, setTab] = useTabParam<Tab>(TABS, 'Results')
  const [sim] = useState(() => {
    const ds = SAMPLES[0]
    const ch = initialChoices(ds)
    return simulate(ds, ch, inferTask(ds, ch.label) ?? 'multiclass', runId).run
  })
  return (
    <div className="page run-page" data-testid="run-page" data-run={runId}>
      <div className="page-head"><div><h1 className="page-title">{sim.name} <span className="pill warning">SIMULATED</span></h1><p className="helper sub"><span className="mono">{runId}</span> · mock mode: the API is offline, so this run is scripted</p></div></div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} testIds={{ Explain: 'results-explain', Doctor: 'run-doctor' }} />
      {tab === 'Results' && <ResultsStep runId={runId} mockRun={sim} ds={SAMPLES[0]} live={false} go={go} onAgain={() => go('build')} embedded />}
      {tab === 'Explain' && <MockConfusion />}
      {tab === 'Doctor' && <ApiOnly mock={<div className="card card-body"><p className="helper">▲ mock mode: the Model Doctor reads real runs; start the API with <span className="mono">make dev</span>.</p></div>}>{null}</ApiOnly>}
    </div>
  )
}
