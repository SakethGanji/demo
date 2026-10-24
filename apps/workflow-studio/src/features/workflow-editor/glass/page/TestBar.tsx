/**
 * The floating test bar under the Page: which record (run) the page follows, the last few as pills plus the full
 * run picker, and "Show paths not taken".
 */
import { useMemo } from 'react'
import { RunPicker, runStatus, shortId, when } from '../run/RunPicker'
import { useSetRun, useWorkflowRuns } from '../run/useRunData'
import { useGlassStore } from '../state/glassStore'

export function TestBar({ workflowId, runId, triggerName }: { workflowId?: string; runId: string | null; triggerName?: string }) {
  const runs = useWorkflowRuns(workflowId, true)
  const setRun = useSetRun()
  const showUntaken = useGlassStore((s) => s.showUntaken)
  const setShowUntaken = useGlassStore((s) => s.setShowUntaken)
  const latest = useMemo(() => (runs.data?.pages ?? []).flatMap((p) => p.runs).slice(0, 3), [runs.data])
  return (
    <div className="gp-testbar glass" role="group" aria-label="Test with" data-testid="glass-testbar">
      <span className="gp-tb-l">Test with</span>
      {latest.map((r) => (
        <button key={r.id} type="button" className="gp-rec" aria-pressed={r.id === runId} onClick={() => setRun(r.id === runId ? null : r.id)} data-testid="glass-rec" data-run-id={r.id} title={`Run ${shortId(r.id)} · ${when(r.start_time)}`}>
          <span className="gp-rs" data-status={runStatus(r.status)} aria-hidden />
          <span className="gp-mono">{shortId(r.id)}</span>
          {when(r.start_time)}
        </button>
      ))}
      <RunPicker workflowId={workflowId} runId={runId} triggerName={triggerName} compact pinned={latest.map((r) => r.id)} />
      <span className="gp-sep" aria-hidden />
      <button type="button" className="gp-switch" aria-pressed={showUntaken} onClick={() => setShowUntaken(!showUntaken)} data-testid="glass-show-untaken">
        <span className="gp-tr" aria-hidden />
        Show paths not taken
      </button>
    </div>
  )
}
