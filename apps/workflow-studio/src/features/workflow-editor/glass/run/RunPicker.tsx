/**
 * "Showing [Run #id ▾]": picks the run every view shows. Search by id / status / mode, pages through older runs,
 * "Live · no run" goes back to the draft. The selected run offers Retry (failed runs) and Save as test.
 */
import { Popover } from '@base-ui/react/popover'
import { useMemo, useState } from 'react'
import { StatusMark } from '@/shared/glass/StatusMark'
import { cx, type StepStatus } from '@/shared/glass/cx'
import type { RunListItem } from '@/shared/api/types'
import { formatMs } from '../lib/runPath'
import { saveRunAsTest, useRetryRun, useRunDetail, useSetRun, useWorkflowRuns } from './useRunData'

export function runStatus(s: string): StepStatus {
  const v = s.toLowerCase()
  if (v === 'success' || v === 'completed') return 'ok'
  if (v === 'failed' || v === 'error' || v === 'crashed') return 'fail'
  if (v === 'waiting') return 'need'
  if (v === 'running' || v === 'queued' || v === 'new') return 'running'
  return 'skip'
}

/** The tail of the id: run ids are time-ordered, so their heads are the same for runs made close together. */
export const shortId = (id: string) => `#${id.replace(/^exec_/, '').replace(/-/g, '').slice(-6)}`

export function when(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const today = new Date()
  const same = d.toDateString() === today.toDateString()
  return same ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function RunPicker({ workflowId, runId, triggerName, compact = false, pinned = [] }: { workflowId?: string; runId: string | null; triggerName?: string; compact?: boolean; /** Runs already shown as pills (compact): the trigger then reads "All runs". */ pinned?: string[] }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const runs = useWorkflowRuns(workflowId, true)
  const detail = useRunDetail(runId)
  const setRun = useSetRun()
  const retry = useRetryRun()
  const all = useMemo(() => (runs.data?.pages ?? []).flatMap((p) => p.runs), [runs.data])
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return all
    return all.filter((r) => [r.id, r.status, r.mode, r.trigger?.type ?? '', when(r.start_time)].some((x) => String(x).toLowerCase().includes(s)))
  }, [all, q])
  const current = all.find((r) => r.id === runId)
  const status = current?.status ?? detail.data?.status
  const failed = status ? runStatus(status) === 'fail' : false

  const pick = (r: RunListItem | null) => {
    setRun(r ? r.id : null)
    setOpen(false)
  }

  return (
    <div className={cx('gp-runbar', compact && 'is-compact')} data-testid="glass-runbar">
      {!compact && <span className="gp-runbar-label">Showing</span>}
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger className={cx(compact ? 'gp-rec gp-run-trigger' : 'gl-chip gp-run-trigger')} data-testid="glass-run-picker">
          {compact && runId && pinned.includes(runId) ? (
            <>All runs</>
          ) : runId ? (
            <>
              {status && <StatusMark status={runStatus(status)} size="sm" />}
              Run {shortId(runId)}
              {current && <span className="gp-faint">· {when(current.start_time)}</span>}
            </>
          ) : (
            <>
              <span className="gp-live-dot" aria-hidden />
              Live · no run
            </>
          )}
          <span aria-hidden>▾</span>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner sideOffset={8} align={compact ? 'center' : 'start'} side={compact ? 'top' : 'bottom'} className="z-[70]">
            <Popover.Popup className="gl-overlay gp-runpop" data-testid="glass-run-menu">
              <input className="gp-runpop-search" placeholder="Search runs: id, status, trigger…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search runs" autoFocus />
              <div className="gp-runpop-list" role="listbox" aria-label="Runs">
                <button type="button" role="option" aria-selected={!runId} className="gp-runpop-row" onClick={() => pick(null)} data-testid="glass-run-live">
                  <span className="gp-live-dot" aria-hidden />
                  <span>Live · no run</span>
                  <span className="gp-faint gp-ml">the draft, nothing ran</span>
                </button>
                {shown.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    role="option"
                    aria-selected={r.id === runId}
                    className={cx('gp-runpop-row', r.id === runId && 'is-on')}
                    onClick={() => pick(r)}
                    data-testid="glass-run-option"
                    data-run-id={r.id}
                    data-status={r.status}
                  >
                    <StatusMark status={runStatus(r.status)} size="sm" />
                    <span className="gp-mono">{shortId(r.id)}</span>
                    <span className="gp-faint">{when(r.start_time)}</span>
                    <span className="gp-faint">{r.trigger?.type ?? r.mode}</span>
                    <span className="gp-faint gp-ml">{r.error_count ? `${r.error_count} error${r.error_count > 1 ? 's' : ''}` : formatMs(r.duration_ms)}</span>
                  </button>
                ))}
                {runs.isLoading && <div className="gp-runpop-note">Loading runs…</div>}
                {!runs.isLoading && all.length === 0 && <div className="gp-runpop-note">No runs yet. Run the workflow to see its path here.</div>}
                {all.length > 0 && shown.length === 0 && <div className="gp-runpop-note">No run matches “{q}”.</div>}
              </div>
              {runs.hasNextPage && (
                <button type="button" className="gl-btn gp-runpop-more" data-variant="ghost" data-size="sm" disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()} data-testid="glass-run-more">
                  {runs.isFetchingNextPage ? 'Loading…' : 'Older runs'}
                </button>
              )}
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
      {!compact && runId && failed && (
        <button type="button" className="gl-btn" data-size="sm" onClick={() => void retry(runId)} data-testid="glass-run-retry" title="Run again from the step that failed">
          ↻ Retry
        </button>
      )}
      {!compact && runId && (
        <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => void saveRunAsTest(runId, triggerName)} data-testid="glass-run-save-test">
          Save as test
        </button>
      )}
      {!compact && <span className="gl-sp" />}
      {!compact && runId && (
        <span className="gp-legend" aria-hidden>
          <i data-k="path" /> this run's path <i data-k="off" /> not taken
        </span>
      )}
    </div>
  )
}
