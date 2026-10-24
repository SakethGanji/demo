/**
 * `/p/$project/notebooks`: the Manager. Every notebook with its status, cells (and how many run without AI), the
 * last run's attribution, what it was kept as and the kept check's pass/fail strip. "New notebook" opens the gallery.
 */
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { CheckStrip, Chip, StatusMark, type StepStatus } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useDeleteNotebook, useNotebooks } from './hooks'
import { fmtTime, plural } from './lib/cells'
import { TARGET_LABEL } from './keep/KeepDrawer'
import { TemplateGallery } from './TemplateGallery'
import type { NotebookListItem } from './types'
import './notebook.css'

const STATUS: Record<NotebookListItem['status'], { text: string; mark: StepStatus }> = {
  idle: { text: 'Idle', mark: 'pending' },
  running: { text: 'Running', mark: 'running' },
  needs_you: { text: 'Needs you', mark: 'need' },
  failed: { text: 'Failed', mark: 'fail' },
  done: { text: 'Done', mark: 'ok' },
}

export function NotebookManagerPage() {
  const { pid, slug } = useProjectCtx()
  const list = useNotebooks(pid)
  const del = useDeleteNotebook(pid)
  const [gallery, setGallery] = useState(false)
  const [confirm, setConfirm] = useState<string | null>(null)
  const items = list.data ?? []

  return (
    <div className="nb-scroll glass-page" data-testid="notebook-manager">
      <div className="nb-wrap nb-mgr">
        <header className="nb-row">
          <div>
            <h1 className="nb-title">Notebooks</h1>
            <p className="nb-faint">Work through a task step by step: read, check, change, ask a person. Keep it as a workflow when it works.</p>
          </div>
          <span className="nb-sp" />
          <button type="button" className="gl-btn" data-variant="primary" onClick={() => setGallery(!gallery)} data-testid="nb-new">
            + New notebook
          </button>
        </header>

        {gallery && <TemplateGallery onClose={() => setGallery(false)} />}

        {list.isError && <p className="nb-err">{errorMessage(list.error)}</p>}
        {list.isSuccess && items.length === 0 && !gallery && (
          <div className="nb-card nb-faint" data-testid="nb-list-empty">No notebooks yet. Start one from a template or a blank page.</div>
        )}

        {items.length > 0 && (
          <ul className="nb-list" data-testid="nb-list">
            {items.map((n) => {
              const s = STATUS[n.status]
              const lr = n.last_run
              return (
                <li key={n.id} className="nb-item" data-testid="nb-item" data-status={n.status}>
                  <StatusMark size="sm" status={s.mark} />
                  <div className="min-w-0 flex-1">
                    <Link to="/p/$project/notebooks/$notebookId" params={{ project: slug, notebookId: n.id }} className="nb-item-title">
                      {n.title}
                    </Link>
                    <div className="nb-faint nb-item-meta">
                      <span>{s.text}</span>
                      <span>· {plural(n.cell_count, 'cell')}</span>
                      {n.cell_count > 0 && <span>· {n.no_ai_count}/{n.no_ai_count + n.needs_ai_count} no AI</span>}
                      {lr ? (
                        <span>
                          · run {lr.n} {fmtTime(lr.at)}
                          {lr.triggered_by ? ` by ${lr.triggered_by}` : ''}
                          {lr.run_as ? ` as ${lr.run_as}` : ''}
                          {lr.via_agent ? ` via ${lr.via_agent}` : ''}
                        </span>
                      ) : (
                        <span>· not run yet</span>
                      )}
                      {n.writes_env && <span>· writes · {n.writes_env}</span>}
                    </div>
                  </div>
                  {n.needs_you && <Chip tone="warn" size="sm">needs you</Chip>}
                  {n.kept.map((k) => (
                    <Chip key={k.workflow_id + k.at} size="sm">kept as {TARGET_LABEL[k.target].toLowerCase()}</Chip>
                  ))}
                  {n.check && n.check.last.length > 0 && (
                    <CheckStrip summary results={n.check.last.map((r) => (r === 'pass' || r === 'fail' || r === 'skip' ? r : 'none'))} data-testid="nb-item-checks" />
                  )}
                  {confirm === n.id ? (
                    <span className="nb-row">
                      <button type="button" className="gl-btn" data-size="sm" onClick={() => setConfirm(null)}>Cancel</button>
                      <button
                        type="button"
                        className="gl-btn"
                        data-size="sm"
                        data-variant="primary"
                        data-testid="nb-item-delete-confirm"
                        onClick={() => del.mutate(n.id, { onSettled: () => setConfirm(null) })}
                      >
                        Delete
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="gl-icon-btn"
                      aria-label={`Delete ${n.title}`}
                      disabled={n.status === 'running'}
                      onClick={() => setConfirm(n.id)}
                      data-testid="nb-item-delete"
                    >
                      ×
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
        {del.isError && <p className="nb-err">{errorMessage(del.error)}</p>}
        <p className="nb-faint">Updated {items[0] ? fmtTime(items[0].updated_at) : '—'}</p>
      </div>
    </div>
  )
}
