/**
 * The glass workflow editor (BUILD-PLAN §1.5), mounted by WorkflowEditorPage unless `?legacy=1`.
 * One header bar; two views (Page, Canvas) plus the Definition from the ⋯ menu, over the same draft and run:
 *   page       bar · shape rail | the Page document | This run · floating test bar
 *   editor     bar · <EditorView/> (the Canvas)
 *   definition bar · read-only JSON
 *   side       bar · Page ⇆ JSON, hover-linked · test bar
 * The run shown everywhere is `?execution=` (glassStore.runId mirrors it); the Page picks the latest run itself.
 */
import { lazy, memo, Suspense, useEffect, useMemo, useState } from 'react'
import { useWorkflowStore } from '../stores/workflowStore'
import { useEditorLayoutStore } from '../stores/editorLayoutStore'
import { useExecutionParam } from '../hooks/useViewingRun'
import { toBackendWorkflow } from '../lib/workflowTransform'
import type { WorkflowNodeData } from '../types/workflow'
import type { Node } from '@xyflow/react'
import { DocHead, GlassHeader, useVersion } from './GlassHeader'
import { useGlassStore } from './state/glassStore'
import { addDraftStep, setNodeValue } from './state/editActions'
import { buildPageModel, toPageWorkflow, type PageWorkflow } from './lib/pageModel'
import { runPath } from './lib/runPath'
import { PageView } from './page/PageView'
import { Outline } from './page/Outline'
import { RunCard } from './page/RunCard'
import { TestBar } from './page/TestBar'
import { useRunDetail, useSetRun, useWorkflowRuns } from './run/useRunData'
import { DefinitionView, type DefinitionDoc } from './definition/DefinitionView'
import { SideBySide } from './definition/SideBySide'
import { UnsavedGuard } from './editor/UnsavedGuard'
import './page/glass-page.css'

const EditorViewLazy = lazy(() => import('./editor/EditorView').then((m) => ({ default: m.EditorView })))
const EditorPane = memo(function EditorPane() {
  return (
    <Suspense fallback={<div className="gp-loading">Loading the editor…</div>}>
      <EditorViewLazy />
    </Suspense>
  )
})

/** The trigger's "Test payload ▾": the JSON a test run sends (shared with the Editor), or this run's input. */
function TestPayload({ triggerName, runItems }: { triggerName: string; runItems?: unknown[] }) {
  const [open, setOpen] = useState(false)
  const payload = useEditorLayoutStore((s) => s.payloadInput)
  const setPayload = useEditorLayoutStore((s) => s.setPayloadInput)
  const setSource = useGlassStore((s) => s.setPayloadSource)
  const runId = useGlassStore((s) => s.runId)
  return (
    <div className="gp-expand">
      <button type="button" className="gl-disc" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="glass-test-payload">
        Test payload {open ? '▴' : '▾'}
      </button>
      {open && (
        <div className="gp-payload">
          <textarea
            className="gp-mono"
            value={payload}
            spellCheck={false}
            rows={Math.min(12, Math.max(4, payload.split('\n').length))}
            onChange={(e) => {
              setPayload(e.target.value)
              setSource({ kind: 'typed' })
            }}
            aria-label={`Test payload for ${triggerName}`}
            data-testid="glass-test-payload-input"
          />
          {runId && runItems?.length ? (
            <button
              type="button"
              className="gl-btn"
              data-variant="ghost"
              data-size="sm"
              onClick={() => {
                setPayload(JSON.stringify(runItems.length === 1 ? runItems[0] : runItems, null, 2))
                setSource({ kind: 'run', runId })
              }}
            >
              Use this run's input
            </button>
          ) : null}
        </div>
      )}
    </div>
  )
}

function useDraftWorkflow(): { page: PageWorkflow; doc: DefinitionDoc } {
  const nodes = useWorkflowStore((s) => s.nodes)
  const edges = useWorkflowStore((s) => s.edges)
  const name = useWorkflowStore((s) => s.workflowName)
  const id = useWorkflowStore((s) => s.workflowId)
  return useMemo(() => {
    const wf = toBackendWorkflow(nodes as Node<WorkflowNodeData>[], edges, name, id)
    const notes = new Map((nodes as Node<WorkflowNodeData>[]).filter((n) => n.type === 'workflowNode').map((n) => [n.data.name, n.data.notes]))
    const page = toPageWorkflow({
      id,
      name,
      nodes: wf.nodes.map((n) => ({ ...n, notes: notes.get(n.name) || undefined })),
      connections: wf.connections,
    })
    return { page, doc: wf as unknown as DefinitionDoc }
  }, [nodes, edges, name, id])
}

export default function GlassEditorShell({ workflowId }: { workflowId?: string }) {
  const view = useGlassStore((s) => s.view)
  const runId = useGlassStore((s) => s.runId)
  const storeId = useWorkflowStore((s) => s.workflowId)
  const execParam = useExecutionParam() ?? null
  const liveChosen = useGlassStore((s) => s.liveChosen)
  const setRun = useSetRun()
  const runs = useWorkflowRuns(workflowId, view !== 'editor')
  const latestRun = runs.data?.pages[0]?.runs[0]?.id

  useEffect(() => {
    useGlassStore.getState().resetForWorkflow()
  }, [workflowId])
  useEffect(() => {
    if (useGlassStore.getState().runId !== execParam) useGlassStore.getState().setRunId(execParam)
  }, [execParam])

  // the Page follows a real record: with no run picked, show the latest one (until someone picks "Live · no run")
  useEffect(() => {
    if (view === 'editor' || execParam || liveChosen || !latestRun) return
    setRun(latestRun)
  }, [view, execParam, liveChosen, latestRun, setRun])

  const { page, doc } = useDraftWorkflow()
  const model = useMemo(() => buildPageModel(page), [page])
  const detail = useRunDetail(runId)
  const execution = detail.data && (!workflowId || detail.data.workflow_id === workflowId) ? detail.data : null
  const path = useMemo(() => runPath(page.connections, page.nodes.map((n) => n.name), execution), [page, execution])
  const version = useVersion(workflowId)

  const loading = !!workflowId && workflowId !== 'new' && storeId !== workflowId
  if (loading) return <div className="glass-page gp-shell gp-loading" data-testid="glass-editor-loading">Loading workflow…</div>

  const triggerItems = model.trigger ? path?.byNode[model.trigger]?.items : undefined
  const pageEl = (
    <PageView
      workflow={page}
      model={model}
      execution={execution}
      onParamChange={setNodeValue}
      onAddStep={(d, after) => void addDraftStep(d, after)}
      onOpenCanvas={(n) => {
        useGlassStore.getState().select(n)
        useGlassStore.getState().setView('editor')
      }}
      triggerExtra={model.trigger ? <TestPayload triggerName={model.trigger} runItems={triggerItems} /> : undefined}
    />
  )
  const testbar = <TestBar workflowId={workflowId} runId={runId} triggerName={model.trigger} />
  const header = (
    <div className="gp-top">
      <GlassHeader model={model} runId={runId} compact={view === 'editor'} />
    </div>
  )

  if (view === 'editor') {
    return (
      <div className="glass-page gp-shell" data-view="editor" data-testid="glass-editor">
        <UnsavedGuard />
        {header}
        <div className="gp-editor-body">
          <EditorPane />
        </div>
      </div>
    )
  }

  return (
    <div className="glass-page gp-shell" data-view={view} data-testid="glass-editor">
      <UnsavedGuard />
      {view === 'page' ? (
        <div className="gp-wrap">
          {header}
          <div className="gp-cols">
            <Outline model={model} path={path} />
            <main className="gp-main gp-doc glass">
              <DocHead model={model} path={path} />
              {pageEl}
            </main>
            <RunCard model={model} path={path} runId={execution?.id ?? null} triggerName={model.trigger} />
          </div>
          {testbar}
        </div>
      ) : view === 'definition' ? (
        <div className="gp-wrap">
          {header}
          <div className="gp-cols gp-one">
            <main className="gp-main gp-doc glass">
              <DefinitionView doc={doc} version={version.data} />
            </main>
          </div>
        </div>
      ) : (
        <div className="gp-wrap gp-two">
          {header}
          <SideBySide page={pageEl} doc={doc} version={version.data} />
          {testbar}
        </div>
      )}
    </div>
  )
}
