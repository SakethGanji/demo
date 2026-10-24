/**
 * The template gallery: Blank plus the engine's templates (GET /notebook-templates). A template with query or change
 * cells asks which connection to point them at. Creating navigates to the new notebook.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { errorMessage } from '@/shared/api/http'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useConnections } from '@/features/connections/useConnections'
import { isNotBuilt } from './api'
import { useCreateNotebook, useTemplates } from './hooks'
import { KIND_LABEL } from './lib/cells'
import { myName } from './lib/identity'
import { TARGET_LABEL } from './keep/KeepDrawer'

export function TemplateGallery({ onClose }: { onClose: () => void }) {
  const { pid, slug, viewEnv } = useProjectCtx()
  const navigate = useNavigate()
  const tpls = useTemplates()
  const conns = useConnections(pid)
  const create = useCreateNotebook(pid)
  const [pick, setPick] = useState<string>('')
  const [title, setTitle] = useState('')
  const [conn, setConn] = useState('')
  const list = (tpls.data ?? []).filter((t) => t.slug !== 'blank')
  const tpl = list.find((t) => t.slug === pick) ?? null
  const needsConn = !!tpl?.cells.some((c) => c.kind === 'query' || c.kind === 'change')
  const writes = !!tpl?.cells.some((c) => c.kind === 'change')
  // a template that changes rows needs a writable connection (the engine refuses a read-only one)
  const connections = (conns.data ?? []).filter((c) => !writes || !c.readonly)

  const go = () => {
    create.mutate(
      {
        title: title.trim() || tpl?.title || 'Untitled notebook',
        template: tpl?.slug ?? null,
        connection: needsConn ? conn || null : null,
        environment: viewEnv || null,
        created_by: myName(),
      },
      { onSuccess: (nb) => void navigate({ to: '/p/$project/notebooks/$notebookId', params: { project: slug, notebookId: nb.id } }) },
    )
  }

  return (
    <section className="nb-card flex flex-col gap-3" data-testid="nb-gallery" aria-label="New notebook">
      <div className="nb-gallery">
        <button type="button" className="nb-tpl" aria-pressed={pick === ''} onClick={() => setPick('')} data-testid="nb-tpl" data-slug="">
          <h4>Blank</h4>
          <p>An empty page. Add blocks with + or /.</p>
        </button>
        {list.map((t) => (
          <button key={t.slug} type="button" className="nb-tpl" aria-pressed={pick === t.slug} onClick={() => setPick(t.slug)} data-testid="nb-tpl" data-slug={t.slug}>
            <h4>{t.title}</h4>
            <p>{t.description}</p>
            <p className="kind-label">
              {t.cells.map((c) => KIND_LABEL[c.kind]).join(' → ')}
              {t.keeps_as ? ` · keeps as ${TARGET_LABEL[t.keeps_as].toLowerCase()}` : ''}
            </p>
          </button>
        ))}
      </div>
      {tpls.isError && (
        <p className="nb-faint" data-testid="nb-tpl-unavailable">
          {isNotBuilt(tpls.error) ? 'Templates are not available on this engine yet; start from a blank notebook.' : errorMessage(tpls.error)}
        </p>
      )}
      <div className="nb-row">
        <label className="nb-field">
          Title
          <input className="nb-input" value={title} placeholder={tpl?.title ?? 'Untitled notebook'} onChange={(e) => setTitle(e.target.value)} aria-label="Notebook title" />
        </label>
        {needsConn && (
          <label className="nb-field">
            Connection
            <select className="nb-select" value={conn} onChange={(e) => setConn(e.target.value)} aria-label="Connection" data-testid="nb-tpl-conn">
              <option value="">{writes ? 'Choose later (writable only)' : 'Choose later'}</option>
              {connections.map((c) => (
                <option key={c.id} value={c.key}>{c.name || c.key}{c.readonly ? ' · read-only' : ' · writable'}</option>
              ))}
            </select>
          </label>
        )}
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-variant="ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="gl-btn" data-variant="primary" disabled={create.isPending} onClick={go} data-testid="nb-create">
          Create notebook
        </button>
      </div>
      {create.isError && <p className="nb-err" data-testid="nb-create-error">{errorMessage(create.error)}</p>}
    </section>
  )
}
