/**
 * The drawer above the run strip (mockup `.dpane`): Runs (this workflow's runs: view on the canvas, retry the
 * failed step, save as a test) · Logs (each step of the run on the canvas, expandable to its output) ·
 * Problems (the live check; click to go to the step) · Tests (the workflow's tests, run all, scorecard).
 */
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, FlaskConical, Loader2, RotateCcw, X } from 'lucide-react'
import { StatusMark } from '@/shared/glass/StatusMark'
import { cx } from '@/shared/glass/cx'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useCheckStore } from '../../stores/checkStore'
import { useTestStore } from '../../stores/testStore'
import { getEditorAction } from '../../stores/editorActionsStore'
import type { WorkflowNodeData } from '../../types/workflow'
import { useGlassStore } from '../state/glassStore'
import { useRetryRun, useSetRun, useWorkflowRuns, saveRunAsTest } from '../run/useRunData'
import { shortId } from '../run/RunPicker'
import { unwrapItems } from './GlassEdge'
import { execToStatus, isTriggerData, plural, runToStatus } from './status'
import { fmtMs, timeAgo } from './ui'

const TestsPanel = lazy(() => import('../../components/tests/TestsPanel'))

type Tab = 'runs' | 'logs' | 'problems' | 'tests'

/** Select one node on the canvas (React Flow's flag and the store's id), without marking the draft edited. */
export function selectOnCanvas(id: string) {
  const ws = useWorkflowStore.getState()
  ws.setNodes(ws.nodes.map((x) => (!!x.selected === (x.id === id) ? x : { ...x, selected: x.id === id })))
  ws.setSelectedNode(id)
}

export function goToStep(name: string) {
  const ws = useWorkflowStore.getState()
  const n = ws.nodes.find((x) => x.type === 'workflowNode' && (x.data as WorkflowNodeData).name === name)
  if (!n) return
  selectOnCanvas(n.id)
  const g = useGlassStore.getState()
  g.select(name)
  if (g.inspector === 'closed') g.setInspector('compact')
  getEditorAction('focus-node')?.(n.id)
}

function triggerName(): string | undefined {
  const n = useWorkflowStore.getState().nodes.find((x) => x.type === 'workflowNode' && isTriggerData(x.data as never))
  return n ? (n.data as WorkflowNodeData).name : undefined
}

function DrawerRuns() {
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const runs = useWorkflowRuns(workflowId)
  const runId = useGlassStore((s) => s.runId)
  const setRun = useSetRun()
  const retry = useRetryRun()
  const list = useMemo(() => runs.data?.pages.flatMap((p) => p.runs) ?? [], [runs.data])
  // a run still going: look again shortly
  const going = list.some((r) => runToStatus(r.status) === 'running')
  const refetch = runs.refetch
  useEffect(() => {
    if (!going) return
    const t = setTimeout(() => void refetch(), 1500)
    return () => clearTimeout(t)
  }, [going, list, refetch])
  if (!workflowId || workflowId === 'new') return <p className="ge-empty">Save the workflow to keep its runs.</p>
  if (runs.isPending) return <p className="ge-empty"><Loader2 className="animate-spin" /> Loading runs…</p>
  if (runs.isError) return <p className="ge-empty">Couldn't load the runs.</p>
  if (!list.length) return <p className="ge-empty" data-testid="drawer-runs-empty">No runs yet. Press Run to try the workflow.</p>
  return (
    <div className="ge-list" data-testid="drawer-runs">
      {list.map((r) => {
        const st = runToStatus(r.status)
        return (
          <div key={r.id} className={cx('ge-rrow', runId === r.id && 'cur')} data-testid="drawer-run-row" data-status={r.status} data-id={r.id}>
            <StatusMark status={st} size="sm" />
            <button type="button" className="rm" onClick={() => setRun(runId === r.id ? null : r.id)} title="Show this run on the canvas" data-testid="drawer-run-view">
              <b>
                <code>#{shortId(r.id)}</code>
                {r.status}
                {r.error_count ? ` · ${plural(r.error_count, 'error')}` : ''}
              </b>
              <small>
                {r.trigger?.type || r.mode || 'manual'}
                {r.environment ? ` · ${r.environment}` : ''}
              </small>
            </button>
            <span className="rt">{timeAgo(r.start_time)}</span>
            <span className="rt">{r.duration_ms != null ? fmtMs(r.duration_ms) : '…'}</span>
            <span className="ra">
              {st === 'fail' && (
                <button type="button" className="ge-btn ghost sm" onClick={() => void retry(r.id)} data-testid="drawer-run-retry">
                  <RotateCcw /> Retry
                </button>
              )}
              <button type="button" className="ge-btn ghost sm" onClick={() => void saveRunAsTest(r.id, triggerName())} data-testid="drawer-run-save-test">
                <FlaskConical /> Save as test
              </button>
            </span>
          </div>
        )
      })}
      {runs.hasNextPage && (
        <button type="button" className="ge-btn ghost sm" onClick={() => void runs.fetchNextPage()} disabled={runs.isFetchingNextPage}>
          {runs.isFetchingNextPage ? 'Loading…' : 'Older runs'}
        </button>
      )}
    </div>
  )
}

export function useRunSteps() {
  const executionData = useWorkflowStore((s) => s.executionData)
  const nodes = useWorkflowStore((s) => s.nodes)
  return useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n]))
    return Object.entries(executionData)
      .filter(([, d]) => d && d.status !== 'idle')
      .map(([id, d]) => {
        const n = byId.get(id)
        const data = n?.data as WorkflowNodeData | undefined
        const ms = d.metrics?.executionTimeMs ?? (d.startTime && d.endTime ? d.endTime - d.startTime : null)
        return { id, name: data?.name ?? id, label: data?.label ?? id, d, ms, start: d.startTime ?? Number.MAX_SAFE_INTEGER }
      })
      .sort((a, b) => a.start - b.start)
  }, [executionData, nodes])
}

function DrawerLogs() {
  const steps = useRunSteps()
  const [open, setOpen] = useState<string | null>(null)
  if (!steps.length) return <p className="ge-empty" data-testid="drawer-logs-empty">Run the workflow to see each step's log.</p>
  return (
    <div className="ge-list" data-testid="drawer-logs">
      {steps.map((s) => {
        const out = unwrapItems(s.d.output?.items)
        const inn = unwrapItems(s.d.input?.items)
        const isOpen = open === s.id
        return (
          <div key={s.id} className={cx('ge-lrow', isOpen && 'open')} data-testid="log-row" data-name={s.name} data-status={s.d.status}>
            <button type="button" className="ge-lh" onClick={() => setOpen(isOpen ? null : s.id)} aria-expanded={isOpen}>
              <StatusMark status={execToStatus(s.d.status)} size="sm" />
              <b>{s.label}</b>
              <small>{s.d.output?.error ?? `${inn.length} in → ${plural(out.length, 'item')} out`}</small>
              <span className="lt">{s.ms != null ? fmtMs(s.ms) : ''}</span>
              <span
                role="link"
                tabIndex={0}
                className="lt"
                onClick={(e) => (e.stopPropagation(), goToStep(s.name))}
                onKeyDown={(e) => e.key === 'Enter' && (e.stopPropagation(), goToStep(s.name))}
                title="Go to the step"
              >
                open ▸
              </span>
              {isOpen ? <ChevronDown width={14} /> : <ChevronRight width={14} />}
            </button>
            {isOpen && (
              <div className="ge-lb">
                {s.d.output?.error && <div className="ge-rout err" role="alert">{s.d.output.error}</div>}
                <pre className="ge-codep" data-testid="log-output">{JSON.stringify(out.slice(0, 20), null, 2)}</pre>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function DrawerProblems() {
  const findings = useCheckStore((s) => s.findings)
  const list = useMemo(() => {
    const rank = { error: 0, warning: 1, info: 2 } as const
    return [...findings].filter((f) => f.severity !== 'info').sort((a, b) => rank[a.severity] - rank[b.severity])
  }, [findings])
  if (!list.length) return <p className="ge-empty" data-testid="problems-empty">No problems found.</p>
  return (
    <div className="ge-list" data-testid="problems-panel">
      {list.map((f, i) => (
        <button
          key={`${f.code}-${f.node}-${f.param}-${i}`}
          type="button"
          className={cx('ge-prow', f.severity === 'error' && 'err')}
          disabled={!f.node}
          onClick={() => f.node && goToStep(f.node)}
          data-testid="problem-row"
          data-code={f.code}
          data-severity={f.severity}
        >
          <span className="ge-pi2">{f.severity === 'error' ? '!' : '?'}</span>
          <b>{f.message}</b>
          <small>
            {f.node ?? 'Workflow'}
            {f.param ? ` · ${f.param}` : ''} · <code>{f.code}</code>
          </small>
          {f.node && <span className="pk">go ▸</span>}
        </button>
      ))}
    </div>
  )
}

export function BottomDrawer() {
  const open = useGlassStore((s) => s.drawerOpen)
  const tab = useGlassStore((s) => s.drawerTab) as Tab
  const setTab = useGlassStore((s) => s.setDrawerTab)
  const close = useGlassStore((s) => s.closeDrawer)
  const counts = useCheckStore((s) => s.counts)
  const testCount = useWorkflowStore((s) => s.tests.length)
  const failing = useTestStore((s) => (s.report ? s.report.summary.failed + s.report.summary.errored > 0 : false))
  const passing = useTestStore((s) => (s.report ? s.report.summary.failed + s.report.summary.errored === 0 && s.report.summary.total > 0 : false))
  const steps = useWorkflowStore((s) => Object.values(s.executionData).filter((d) => d && d.status !== 'idle').length)
  if (!open) return null
  const problems = counts.error + counts.warning
  const tabs: { id: Tab; label: string; n?: number; tone?: string }[] = [
    { id: 'runs', label: 'Runs' },
    { id: 'logs', label: 'Logs', n: steps || undefined },
    { id: 'problems', label: 'Problems', n: problems || undefined, tone: counts.error ? 'err' : 'warn' },
    { id: 'tests', label: 'Tests', n: testCount || undefined, tone: failing ? 'err' : passing ? 'ok' : undefined },
  ]
  return (
    <div className="ge-dpane" data-testid="ge-drawer" data-tab={tab}>
      <div className="ge-dtabs">
        <div className="ge-dtl" role="tablist" aria-label="Drawer">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} data-testid={`drawer-tab-${t.id}`}>
              {t.label}
              {t.n != null && <span className={cx('n', t.tone)} data-testid={t.id === 'problems' ? 'problems-count' : t.id === 'tests' ? 'tests-count' : undefined}>{t.n}</span>}
            </button>
          ))}
        </div>
        <div className="ge-dtools">
          <button type="button" className="ge-ib" aria-label="Close the drawer" onClick={close} data-testid="drawer-close">
            <X />
          </button>
        </div>
      </div>
      <div className={cx('ge-dbody', tab === 'tests' && 'flush')}>
        {tab === 'runs' && <DrawerRuns />}
        {tab === 'logs' && <DrawerLogs />}
        {tab === 'problems' && <DrawerProblems />}
        {tab === 'tests' && (
          <div className="ge-leaf" style={{ height: '100%', overflow: 'auto' }}>
            {/* an auto-height box: the panel grows to its content and the drawer scrolls it, nothing is cut */}
            <div>
              <Suspense fallback={<p className="ge-empty">Loading tests…</p>}>
                <TestsPanel />
              </Suspense>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default BottomDrawer
