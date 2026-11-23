/**
 * The glass editor's header bar (one glass bar for every view): logo, `Workflows /` crumb, the title (click to rename),
 * status and version, then Save · Run ▾ · Schedule · env · Share, the Page | Canvas switch and ⋯ (View definition,
 * side by side, download, import, publish…). `DocHead` is the head of the Page document itself.
 */
import { Menu } from '@base-ui/react/menu'
import { Popover } from '@base-ui/react/popover'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Chip } from '@/shared/glass/Chip'
import { SplitButton } from '@/shared/glass/SplitButton'
import { ViewSwitch } from '@/shared/glass/ViewSwitch'
import { cx } from '@/shared/glass/cx'
import { projectsApi } from '@/shared/api/endpoints'
import { workflowsApi } from '@/shared/lib/api'
import { useWorkflowStore } from '../stores/workflowStore'
import { useEditorProjectStore } from '../stores/editorProjectStore'
import { getEditorAction, registerEditorAction } from '../stores/editorActionsStore'
import { useSaveWorkflow, usePublishWorkflow, useImportWorkflow } from '../hooks/useWorkflowApi'
import { useUsedBy, usedByChipLabel } from '../hooks/useSubworkflows'
import { UsedByDialog } from '../components/ndv/subworkflow/UsedByDialog'
import { PublishImpactDialog } from '../components/workflow-navbar/PublishImpactDialog'
import { EnvProfileSelector } from '../components/env-profiles/EnvProfileSelector'
import { hasBreakingImpact, parsePublishImpact, type PublishImpactRow } from '../lib/subworkflowsApi'
import { toBackendWorkflow } from '../lib/workflowTransform'
import { useRunControl, runWithPayload, runWithoutPayload } from './editor/runControl'
import { describeCron, sentenceFor, sentenceText } from './lib/pageSentences'
import { useGlassStore, type GlassView } from './state/glassStore'
import { cronNode, nodeByName, setSchedule } from './state/editActions'
import { downloadJson } from './definition/DefinitionView'
import { saveRunAsTest, useWorkflowRuns } from './run/useRunData'
import { shortId } from './run/RunPicker'
import type { PageModel } from './lib/pageModel'
import type { RunPath } from './lib/runPath'
import { endChip } from './lib/pageRun'

const PRESETS: Array<{ label: string; expr: string | null }> = [
  { label: 'Webhook only · no schedule', expr: null },
  { label: 'Weekdays at 07:00', expr: '0 7 * * 1-5' },
  { label: 'Every hour', expr: '0 * * * *' },
  { label: 'Weekdays at 15:30', expr: '30 15 * * 1-5' },
]

/** Run from the header: the run lives in the Editor (its stream, its drawer), so switch there, then start. */
export function runInEditor(mode: 'payload' | 'none' | 'to' | 'from' = 'payload') {
  const g = useGlassStore.getState()
  const go = () => {
    if (mode === 'payload') runWithPayload()
    else if (mode === 'none') runWithoutPayload()
    else {
      const id = g.selectedNode ? nodeByName(g.selectedNode)?.id : undefined
      if (!id) return void toast.message('Select a step first')
      getEditorAction(mode === 'to' ? 'run-to' : 'run-from')?.(id)
    }
  }
  if (g.view === 'editor' && useRunControl.getState().start) return go()
  g.setView('editor')
  const t0 = Date.now()
  const off = useRunControl.subscribe((s) => {
    if (s.start) {
      off()
      setTimeout(go, 0)
    } else if (Date.now() - t0 > 8000) off()
  })
}

export function useVersion(workflowId?: string) {
  return useQuery({
    queryKey: ['glass', 'versions', workflowId],
    queryFn: () => workflowsApi.listVersions(workflowId!),
    enabled: !!workflowId && workflowId !== 'new',
    staleTime: 10_000,
    select: (vs) => (vs.length ? Math.max(...vs.map((v) => v.version_number)) : null),
  })
}

function ScheduleButton() {
  const nodes = useWorkflowStore((s) => s.nodes)
  const cron = useMemo(() => cronNode(), [nodes]) // eslint-disable-line react-hooks/exhaustive-deps
  const current = cron ? String(cron.data.parameters?.cronExpression ?? '') : null
  const [custom, setCustom] = useState(current ?? '')
  const [open, setOpen] = useState(false)
  const apply = (expr: string | null) => {
    setSchedule(expr)
    setOpen(false)
    toast.success(expr ? `Schedule set: ${describeCron(expr) ?? expr}` : 'Schedule removed', { description: 'Save to keep it; publish to make it live.' })
  }
  return (
    <Popover.Root open={open} onOpenChange={(o) => (setOpen(o), o && setCustom(current ?? ''))}>
      <Popover.Trigger className="gl-btn" data-size="sm" data-testid="glass-schedule">
        ⏱ {current ? describeCron(current) ?? current : 'Schedule'}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="end" className="z-[70]">
          <Popover.Popup className="gl-overlay gl-menu gp-sched" data-testid="glass-schedule-menu">
            {PRESETS.map((p) => (
              <button key={p.label} type="button" className="gl-menu-item" data-on={(p.expr ?? null) === (current || null) ? '' : undefined} onClick={() => apply(p.expr)}>
                <span>{p.label}</span>
                {p.expr && <span className="gl-hint gp-mono">{p.expr}</span>}
              </button>
            ))}
            <form
              className="gp-sched-custom"
              onSubmit={(e) => {
                e.preventDefault()
                if (custom.trim().split(/\s+/).length !== 5) return void toast.error('A cron needs 5 fields: minute hour day month weekday')
                apply(custom.trim())
              }}
            >
              <input className="gp-mono" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="m h dom mon dow" aria-label="Cron expression" />
              <button type="submit" className="gl-btn" data-variant="primary" data-size="sm">
                Set
              </button>
            </form>
            {custom && describeCron(custom) && <div className="gp-faint gp-small px-2 pb-1">{describeCron(custom)}</div>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}

export function GlassHeader({ model, compact, runId }: { model: PageModel; compact?: boolean; runId: string | null }) {
  const navigate = useNavigate()
  const projectSlug = useEditorProjectStore((s) => s.slug)
  const workflowName = useWorkflowStore((s) => s.workflowName)
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const isActive = useWorkflowStore((s) => s.isActive)
  const dirty = useWorkflowStore((s) => s._isDirty)
  const setWorkflowName = useWorkflowStore((s) => s.setWorkflowName)
  const executing = useRunControl((s) => s.executing)
  const view = useGlassStore((s) => s.view)
  const setView = useGlassStore((s) => s.setView)
  const { saveWorkflow, isSaving } = useSaveWorkflow()
  const { publish, unpublish, isPublishing } = usePublishWorkflow()
  const { importWorkflow } = useImportWorkflow()
  const version = useVersion(workflowId)
  const usedBy = useUsedBy(workflowId && workflowId !== 'new' ? workflowId : undefined)
  const usedByLabel = usedByChipLabel(usedBy.data)
  const [usedByOpen, setUsedByOpen] = useState(false)
  const [impact, setImpact] = useState<PublishImpactRow[]>([])
  const [impactOpen, setImpactOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draftName, setDraftName] = useState(workflowName)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) nameRef.current?.select()
  }, [editing])

  // ⌘↵ and the ⌘K "Run workflow" go through the same path as the Run button
  useEffect(() => {
    const off = registerEditorAction('run', () => runInEditor('payload'))
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey) || useRunControl.getState().executing) return
      e.preventDefault()
      runInEditor('payload')
    }
    window.addEventListener('keydown', onKey)
    return () => {
      off()
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const submitName = () => {
    const v = draftName.trim()
    if (v && v !== workflowName) setWorkflowName(v)
    setEditing(false)
  }

  const publishAndReview = async () => {
    const res = await publish()
    const rows = parsePublishImpact(res)
    if (hasBreakingImpact(rows)) {
      setImpact(rows)
      setImpactOpen(true)
    }
  }

  const backendDoc = () => {
    const st = useWorkflowStore.getState()
    return toBackendWorkflow(st.nodes as never, st.edges, st.workflowName, st.workflowId)
  }

  const duplicate = async () => {
    try {
      const doc = backendDoc()
      const res = await projectsApi.createWorkflow(useEditorProjectStore.getState().projectId, { ...doc, id: undefined, name: `${doc.name} (copy)` })
      toast.success(`Duplicated as “${doc.name} (copy)”`)
      navigate({ to: '/p/$project/workflows/$workflowId', params: { project: projectSlug, workflowId: res.id } } as never)
    } catch (e) {
      toast.error('Could not duplicate', { description: e instanceof Error ? e.message : String(e) })
    }
  }

  const remove = async () => {
    if (!workflowId) return
    try {
      await workflowsApi.delete(workflowId)
      toast.success(`Deleted “${workflowName}”`)
      navigate({ to: '/p/$project', params: { project: projectSlug } } as never)
    } catch (e) {
      toast.error('Could not delete', { description: e instanceof Error ? e.message : String(e) })
    }
  }

  const onImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const r = new FileReader()
    r.onload = () => importWorkflow(String(r.result))
    r.readAsText(f)
    e.target.value = ''
  }

  const runItems = [
    { id: 'payload', label: 'With the current payload', hint: '⌘↵', onSelect: () => runInEditor('payload') },
    {
      id: 'typed',
      label: 'With a typed payload…',
      onSelect: () => {
        const g = useGlassStore.getState()
        g.setPayloadSource({ kind: 'typed' })
        if (model.trigger) g.select(model.trigger)
        g.setInspector('expanded')
        g.setView('editor')
      },
    },
    { id: 'none', label: 'Without a payload', onSelect: () => runInEditor('none') },
    { id: 'to', label: 'Run to the selected step', onSelect: () => runInEditor('to') },
    { id: 'from', label: 'Run from the selected step', onSelect: () => runInEditor('from') },
    ...(runId ? [{ id: 'save', label: `Save run ${shortId(runId)} as a test`, onSelect: () => void saveRunAsTest(runId, model.trigger) }] : []),
  ]

  const views: Array<{ value: GlassView; label: string }> = [
    { value: 'page', label: 'Page' },
    { value: 'editor', label: 'Canvas' },
  ]

  const title = editing ? (
    <input
      ref={nameRef}
      className="gp-title-input"
      value={draftName}
      onChange={(e) => setDraftName(e.target.value)}
      onBlur={submitName}
      onKeyDown={(e) => {
        if (e.key === 'Enter') submitName()
        if (e.key === 'Escape') setEditing(false)
      }}
      aria-label="Workflow name"
      data-testid="glass-title-input"
    />
  ) : (
    <button type="button" className="gp-title" title="Click to rename" onClick={() => (setDraftName(workflowName), setEditing(true))} data-testid="glass-title">
      {workflowName}
    </button>
  )

  return (
    <header className={cx('gp-hbar glass', compact && 'is-compact')} data-testid="glass-header" data-view={view}>
      <div className="gp-hl">
        <span className="gp-logo" aria-hidden />
        <Link to="/p/$project" params={{ project: projectSlug }} className="gp-crumb" data-testid="glass-back">
          Workflows /
        </Link>
        {title}
        <Chip size="sm" tone={isActive ? 'ok' : 'neutral'} dot={isActive ? 'live' : true} data-testid="glass-status">
          {isActive ? 'Live' : 'Draft'}
        </Chip>
        {version.data != null && (
          <Chip size="sm" mono data-testid="glass-version">
            v{version.data}
          </Chip>
        )}
        {dirty && <span className="gp-faint gp-small gp-unsaved">· unsaved</span>}
      </div>
      <div className="gp-hr">
        {dirty && (
          <button type="button" className="gl-btn" data-size="sm" onClick={() => saveWorkflow()} disabled={isSaving} data-testid="glass-save">
            {isSaving ? 'Saving…' : 'Save'}
          </button>
        )}
        <SplitButton
          label={executing ? 'Running…' : 'Run'}
          icon={<span aria-hidden>▶</span>}
          size="sm"
          disabled={executing}
          onClick={() => runInEditor('payload')}
          items={runItems}
          menuLabel="Run options"
          data-testid="glass-run"
        />
        <ScheduleButton />
        <span className="gp-env">
          <EnvProfileSelector />
        </span>
        <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => toast.message('Share · coming')} data-testid="glass-share">
          Share
        </button>
        <span className="gp-viewsw" data-testid="glass-view-switch">
          <ViewSwitch size="sm" value={view === 'editor' ? 'editor' : 'page'} onChange={(v) => setView(v)} options={views} aria-label="View" />
        </span>
        <Menu.Root>
          <Menu.Trigger className="gl-btn gp-icon-btn" data-size="sm" aria-label="More: view definition, download, import" data-testid="glass-more">
            ⋯
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner sideOffset={6} align="end" className="z-[70]">
              <Menu.Popup className="gl-overlay gl-menu" data-testid="glass-more-menu">
                <Menu.Item className="gl-menu-item" data-on={view === 'definition' ? '' : undefined} onClick={() => setView('definition')} data-testid="glass-more-definition">
                  View definition<span className="gl-hint">JSON</span>
                </Menu.Item>
                <Menu.Item className="gl-menu-item" data-on={view === 'side' ? '' : undefined} onClick={() => setView(view === 'side' ? 'page' : 'side')} data-testid="glass-view-side">
                  Definition side by side<span className="gl-hint">⇆</span>
                </Menu.Item>
                <Menu.Item className="gl-menu-item" onClick={() => downloadJson(backendDoc() as never)} data-testid="glass-more-download">
                  Download definition
                </Menu.Item>
                <Menu.Item className="gl-menu-item" onClick={() => fileRef.current?.click()} data-testid="glass-more-import">
                  Import from JSON…
                </Menu.Item>
                <Menu.Separator className="gl-menu-sep" />
                <Menu.Item className="gl-menu-item" onClick={() => void duplicate()} disabled={!workflowId}>
                  Duplicate
                </Menu.Item>
                <Menu.Item className="gl-menu-item" onClick={() => setUsedByOpen(true)} disabled={!workflowId}>
                  Used by…<span className="gl-hint">{usedByLabel ?? 'none'}</span>
                </Menu.Item>
                {isActive ? (
                  <Menu.Item className="gl-menu-item" onClick={() => void unpublish()} disabled={isPublishing || !workflowId} data-testid="glass-unpublish">
                    Unpublish
                  </Menu.Item>
                ) : (
                  <Menu.Item className="gl-menu-item" onClick={() => void publishAndReview()} disabled={isPublishing || !workflowId} data-testid="glass-publish">
                    Publish<span className="gl-hint">new version, triggers on</span>
                  </Menu.Item>
                )}
                <Menu.Item className="gl-menu-item gp-danger" onClick={() => setConfirmDelete(true)} disabled={!workflowId} data-testid="glass-delete">
                  Delete workflow
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={onImport} />
      <PublishImpactDialog impact={impact} open={impactOpen} onOpenChange={setImpactOpen} />
      <UsedByDialog open={usedByOpen} onOpenChange={setUsedByOpen} workflowId={workflowId ?? null} />
      {confirmDelete && (
        <div className="gp-confirm-wrap" role="dialog" aria-modal="true" aria-label="Delete workflow" onClick={() => setConfirmDelete(false)}>
          <div className="gl-overlay gp-confirm" onClick={(e) => e.stopPropagation()}>
            <p>
              Delete <b>{workflowName}</b>? Its runs stay in the run log; the workflow and its versions go.
            </p>
            <div className="gp-actions">
              <button type="button" className="gl-btn" data-size="sm" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
              <button type="button" className="gl-btn gp-danger-btn" data-variant="primary" data-size="sm" onClick={() => void remove()} data-testid="glass-delete-confirm">
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  )
}

/** The document's head inside the Page: the name, what starts it · runs this week · used by, and where this run ended. */
export function DocHead({ model, path }: { model: PageModel; path: RunPath | null }) {
  const workflowName = useWorkflowStore((s) => s.workflowName)
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const runs = useWorkflowRuns(workflowId)
  const usedBy = useUsedBy(workflowId && workflowId !== 'new' ? workflowId : undefined)
  const usedByLabel = usedByChipLabel(usedBy.data)
  const [usedByOpen, setUsedByOpen] = useState(false)
  const trigger = model.trigger ? model.nodes[model.trigger] : undefined
  const triggerSentence = trigger ? sentenceText(sentenceFor(trigger)).replace(/\.$/, '') : 'No trigger yet'
  const weekAgo = Date.now() - 7 * 86400_000
  const firstPage = runs.data?.pages[0]
  const weekRuns = firstPage ? firstPage.runs.filter((r) => Date.parse(r.start_time) >= weekAgo).length : null
  const weekLabel = weekRuns === null ? null : `${weekRuns}${firstPage?.nextCursor && weekRuns === firstPage.runs.length ? '+' : ''} run${weekRuns === 1 ? '' : 's'} this week`
  const end = endChip(path, model)
  return (
    <div className="gp-dochead">
      <div className="gp-grow">
        <h2>{workflowName}</h2>
        <div className="gp-sub" data-testid="glass-byline">
          <span>{triggerSentence}</span>
          {weekLabel && <span> · {weekLabel}</span>}
          {usedByLabel && (
            <>
              {' · '}
              <button type="button" className="gp-ref" onClick={() => setUsedByOpen(true)} data-testid="glass-used-by">
                {usedByLabel.toLowerCase()}
              </button>
            </>
          )}
        </div>
        <div className="gp-gen">Written from the workflow's steps — no AI.</div>
      </div>
      {end && (
        <Chip size="sm" tone={end.tone === 'bad' ? 'prod' : end.tone} dot data-testid="glass-end-chip">
          {end.text}
        </Chip>
      )}
      <UsedByDialog open={usedByOpen} onOpenChange={setUsedByOpen} workflowId={workflowId ?? null} />
    </div>
  )
}
