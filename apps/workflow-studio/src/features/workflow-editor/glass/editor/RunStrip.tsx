/**
 * The run strip (mockup `.strip`): drawer handle · ▶ Run (with the test payload) / ■ Stop · the run on the
 * canvas as a timeline of steps (click one to inspect it) · total time. The drawer opens above it.
 */
import { ChevronUp, Loader2, Play, Square } from 'lucide-react'
import { StatusMark } from '@/shared/glass/StatusMark'
import { cx } from '@/shared/glass/cx'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useGlassStore } from '../state/glassStore'
import { BottomDrawer, goToStep, useRunSteps } from './BottomDrawer'
import { runWithPayload, useRunControl } from './runControl'
import { execToStatus } from './status'
import { fmtMs } from './ui'

export function RunStrip() {
  const drawerOpen = useGlassStore((s) => s.drawerOpen)
  const openDrawer = useGlassStore((s) => s.openDrawer)
  const closeDrawer = useGlassStore((s) => s.closeDrawer)
  const selected = useGlassStore((s) => s.selectedNode)
  const executing = useRunControl((s) => s.executing)
  const cancel = useRunControl((s) => s.cancel)
  const viewing = useWorkflowStore((s) => s.viewingExecution)
  const steps = useRunSteps()
  const max = Math.max(1, ...steps.map((s) => s.ms ?? 0))
  const first = steps.find((s) => s.d.startTime)?.d.startTime
  const last = Math.max(0, ...steps.map((s) => s.d.endTime ?? 0))
  const total = first && last > first ? last - first : steps.reduce((a, s) => a + (s.ms ?? 0), 0)
  const failed = steps.some((s) => s.d.status === 'error')

  return (
    <div className="ge-fl ge-strip" data-testid="ge-runstrip">
      <BottomDrawer />
      <div className="ge-srow">
        <button
          type="button"
          className="dh"
          aria-expanded={drawerOpen}
          aria-label={drawerOpen ? 'Close the drawer' : 'Open runs, logs, problems and tests'}
          onClick={() => (drawerOpen ? closeDrawer() : openDrawer())}
          data-testid="ge-drawer-toggle"
        >
          <ChevronUp />
        </button>
        {executing ? (
          <button type="button" className="ge-btn stop" onClick={() => cancel?.()} data-testid="ge-strip-stop">
            <Square fill="currentColor" /> Stop
          </button>
        ) : (
          <button type="button" className="ge-btn primary play" onClick={() => runWithPayload()} title="Run with the test payload (⌘↵)" data-testid="ge-strip-run">
            <Play /> Run
          </button>
        )}
        {steps.length ? (
          <div className="ge-tl" role="list" aria-label="Steps in this run" data-testid="ge-timeline">
            {steps.map((s) => (
              <button
                key={s.id}
                type="button"
                role="listitem"
                className={cx('ge-ts', selected === s.name && 'cur')}
                onClick={() => goToStep(s.name)}
                title={s.d.output?.error ?? s.label}
                data-testid="ge-timeline-step"
                data-name={s.name}
                data-status={s.d.status}
              >
                <StatusMark status={execToStatus(s.d.status)} size="sm" />
                <b>{s.label}</b>
                <small>
                  <i className={cx(s.d.status === 'error' && 'e', s.d.status === 'waiting' && 'w')} style={{ width: Math.max(4, Math.round(((s.ms ?? 0) / max) * 36)) }} />
                  {s.ms != null ? fmtMs(s.ms) : s.d.status === 'running' ? '…' : ''}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <span className="ge-tlempty" data-testid="ge-timeline-empty">
            {executing ? 'Starting…' : 'No run on the canvas yet · Run tries the workflow with the test payload'}
          </span>
        )}
        {steps.length > 0 && (
          <div className="ge-tot" data-testid="ge-run-total">
            <b>{executing ? <Loader2 className="animate-spin" width={14} /> : fmtMs(total)}</b>
            {executing ? 'running' : failed ? 'failed' : viewing ? 'saved run' : 'this run'}
          </div>
        )}
      </div>
    </div>
  )
}

export default RunStrip
