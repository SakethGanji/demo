/**
 * "This run" card on the right of the Page view: outcome, times, steps run x of y, the path choices it made.
 * Live (no run) says so and points at the run picker.
 */
import { Chip } from '@/shared/glass/Chip'
import type { PageModel } from '../lib/pageModel'
import { formatMs, outcomeOf, type RunPath } from '../lib/runPath'
import { useGlassStore } from '../state/glassStore'
import { saveRunAsTest, useRetryRun } from '../run/useRunData'

const time = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—')

export function RunCard({ model, path, runId, triggerName }: { model: PageModel; path: RunPath | null; runId?: string | null; triggerName?: string }) {
  const select = useGlassStore((s) => s.select)
  const retry = useRetryRun()
  if (!path) {
    return (
      <aside className="gp-rcard glass" data-testid="glass-run-card">
        <div className="gp-rcard-title">This run</div>
        <p className="gp-faint gp-small">No run picked: the page shows the live draft. Pick a test record below to see the path it took.</p>
      </aside>
    )
  }
  const o = outcomeOf(path)
  const total = model.order.length
  const ran = path.ran.filter((n) => model.nodes[n]).length
  const choices: Array<{ node: string; took: string }> = []
  for (const s of model.sections)
    for (const n of s.nodes) {
      const r = path.byNode[n]
      if (!r || r.status === 'skip' || r.status === 'pending') continue
      const t = model.nodes[n].type
      if (t === 'If' || t === 'Switch' || t === 'Approval' || r.decision) {
        const port = r.decision ?? r.ports[0]
        if (port) choices.push({ node: n, took: port })
      }
    }
  return (
    <aside className="gp-rcard glass" data-testid="glass-run-card" data-outcome={o.tone}>
      <div className="gp-rcard-head">
        <span className="gp-rcard-title">This run</span>
        <Chip size="sm" tone={o.tone === 'bad' ? 'prod' : o.tone === 'ok' ? 'ok' : o.tone === 'warn' ? 'warn' : 'neutral'} data-testid="glass-run-outcome">
          {o.label}
        </Chip>
      </div>
      <dl className="gp-rcard-dl">
        <dt>Received</dt>
        <dd>{time(path.startedAt)}</dd>
        <dt>Finished</dt>
        <dd>{path.finished ? time(path.endedAt) : 'still going'}</dd>
        <dt>Took</dt>
        <dd>{formatMs(path.tookMs) || '—'}</dd>
        <dt>Steps run</dt>
        <dd data-testid="glass-run-steps">
          {ran} of {total}
        </dd>
      </dl>
      {choices.length > 0 && (
        <div className="gp-rcard-choices">
          {choices.map((c) => (
            <button key={c.node} type="button" className="gp-rcard-choice" onClick={() => select(c.node)}>
              <span className="truncate">{model.nodes[c.node].label || c.node}</span>
              <span className="gp-mono">→ {c.took}</span>
            </button>
          ))}
        </div>
      )}
      {path.errors.map((e) => (
        <button key={e.node_name} type="button" className="gp-rcard-err" onClick={() => select(e.node_name)}>
          <b>{e.node_name}</b> {e.error.slice(0, 160)}
        </button>
      ))}
      {runId && (
        <div className="gp-actions">
          {o.tone === 'bad' && (
            <button type="button" className="gl-btn" data-size="sm" onClick={() => void retry(runId)} data-testid="glass-run-retry" title="Run again from the step that failed">
              ↻ Retry
            </button>
          )}
          <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => void saveRunAsTest(runId, triggerName)} data-testid="glass-run-save-test">
            Save as test
          </button>
        </div>
      )}
      <p className="gp-faint gp-small">Paths it didn't take are folded on the page.</p>
    </aside>
  )
}
