/**
 * The glass Editor view (BUILD-PLAN §1.5, mockup "Editor"): the shared canvas with glass nodes and edges, and
 * the editing chrome floating over it: tools bar, draft bar (version · saved · changes · problems · tests ·
 * undo/redo · publish · versions), inspector, run strip + bottom drawer, node catalog and the node sheet.
 *
 * It ports the legacy editor's FEATURES, never its chrome: the old navbar / bottom panel / right panel / node
 * modal are not rendered. Their stores still drive things (the canvas, ⌘K and shortcuts write to them), so
 * small bridges below route those writes to the glass panels.
 */
import { useEffect, useRef } from 'react'
import { useReactFlow } from '@xyflow/react'
import { useQueryClient } from '@tanstack/react-query'
import { Hand, LayoutGrid, Maximize, Minus, MousePointer2, Plus, StickyNote } from 'lucide-react'
import WorkflowCanvas from '../../components/canvas/WorkflowCanvas'
import EditorCommandPalette from '../../components/command-palette/EditorCommandPalette'
import ViewingRunBanner from '../../components/workflow-navbar/ViewingRunBanner'
import DriftBanner from '../../components/tests/DriftBanner'
import { EmptyCanvasHint } from '@/features/ai-builder/components/EmptyCanvasHint'
import { useExecutionStream } from '../../hooks/useExecutionStream'
import { useBackToLive } from '../../hooks/useViewingRun'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'
import { getEditorAction } from '../../stores/editorActionsStore'
import { useGlassStore } from '../state/glassStore'
import { useTestStore } from '../../stores/testStore'
import { cx } from '@/shared/glass/cx'
import GlassNode from './GlassNode'
import GlassEdge from './GlassEdge'
import { NodeSheet } from './NodeSheet'
import { DraftBar } from './DraftBar'
import { Inspector } from './Inspector'
import { RunStrip } from './RunStrip'
import { selectOnCanvas } from './BottomDrawer'
import { NodeCatalog } from './NodeCatalog'
import { useRunControl } from './runControl'
import { isTriggerData } from './status'
import './glass-editor.css'

const NODE_TYPES = { workflowNode: GlassNode }
const EDGE_TYPES = { workflowEdge: GlassEdge }

/** One stream for every run path in the Editor (strip, payload, ⌘↵, ⌘K), so ■ Stop cancels whichever started. */
function useBindRun() {
  const { executeWorkflow, isExecuting, cancelExecution } = useExecutionStream()
  const backToLive = useBackToLive()
  const bind = useRunControl((s) => s.bind)
  const exec = useRef(executeWorkflow)
  exec.current = executeWorkflow
  const back = useRef(backToLive)
  back.current = backToLive
  const awaitingRun = useRef(false)
  useEffect(() => {
    bind({
      start: (input) => {
        // a live run replaces a saved run on the canvas; once the engine names it, it is the run every view shows
        if (useWorkflowStore.getState().viewingExecution) back.current()
        useGlassStore.getState().setRunId(null)
        awaitingRun.current = true
        exec.current(input)
      },
      cancel: cancelExecution,
      executing: isExecuting,
    })
  }, [bind, cancelExecution, isExecuting])
  useEffect(() => () => useRunControl.setState({ start: null, cancel: null, executing: false }), [])
  useEffect(
    () =>
      useTestStore.subscribe((s, prev) => {
        if (!awaitingRun.current || !s.lastExecutionId || s.lastExecutionId === prev.lastExecutionId || s.lastExecutionId === 'error') return
        awaitingRun.current = false
        useGlassStore.getState().setRunId(s.lastExecutionId)
      }),
    [],
  )
  // a finished run joins the drawer's run list
  const qc = useQueryClient()
  const was = useRef(false)
  useEffect(() => {
    if (was.current && !isExecuting) void qc.invalidateQueries({ queryKey: ['glass', 'runs'] })
    was.current = isExecuting
  }, [isExecuting, qc])
}

/** Route the legacy layout stores' open-requests (from the canvas, ⌘K and shortcuts) to the glass panels. */
function useBridges() {
  useEffect(() => {
    // the legacy panels are not rendered here; start with them closed so their flags mean "a request"
    useEditorLayoutStore.setState({ bottomPanelOpen: false })
    const lay = useEditorLayoutStore.getState()
    if (lay.rightPanelOpen && !(lay.rightPanelTab === 'nodes' && lay.sourceNodeId)) useEditorLayoutStore.setState({ rightPanelOpen: false })

    const offLayout = useEditorLayoutStore.subscribe((s, prev) => {
      if (s.bottomPanelOpen && (!prev.bottomPanelOpen || s.bottomPanelTab !== prev.bottomPanelTab)) {
        const g = useGlassStore.getState()
        const t = s.bottomPanelTab
        if (t === 'input') {
          // "test payload": the trigger's inspector holds it
          const trig = useWorkflowStore.getState().nodes.find((n) => n.type === 'workflowNode' && isTriggerData(n.data as never))
          if (trig) g.select((trig.data as { name: string }).name)
          g.setInspector('expanded')
        } else g.openDrawer(t === 'logs' ? 'logs' : t === 'problems' ? 'problems' : 'tests')
        useEditorLayoutStore.setState({ bottomPanelOpen: false })
      }
      // AI / interface / builder tabs belong to other surfaces; the glass editor shows only the catalog
      if (s.rightPanelOpen && s.rightPanelTab !== 'nodes') useEditorLayoutStore.setState({ rightPanelOpen: false })
    })

    // a step selected in another view (Page / Definition) is the selected step here too
    const picked = useGlassStore.getState().selectedNode
    if (picked) {
      const n = useWorkflowStore.getState().nodes.find((x) => x.type === 'workflowNode' && (x.data as { name: string }).name === picked)
      if (n) {
        selectOnCanvas(n.id)
        if (useGlassStore.getState().inspector === 'closed') useGlassStore.getState().setInspector('compact')
      }
    }

    // canvas selection → glass selection (by name) + the compact inspector
    const offSel = useWorkflowStore.subscribe((s, prev) => {
      if (s.selectedNodeId === prev.selectedNodeId) return
      const g = useGlassStore.getState()
      if (!s.selectedNodeId) {
        if (g.inspector === 'compact') g.setInspector('closed')
        return
      }
      const n = s.nodes.find((x) => x.id === s.selectedNodeId)
      if (!n || n.type !== 'workflowNode') return
      g.select((n.data as { name: string }).name)
      if (g.inspector === 'closed') g.setInspector('compact')
    })
    return () => {
      offLayout()
      offSel()
    }
  }, [])
}

function ToolsBar() {
  const mode = useEditorLayoutStore((s) => s.canvasMode)
  const setMode = useEditorLayoutStore((s) => s.setCanvasMode)
  const openCreator = useEditorLayoutStore((s) => s.openCreatorPanel)
  const addSticky = useWorkflowStore((s) => s.addStickyNote)
  const rf = useReactFlow()
  // One step per click, applied at once: an animated step would read a half-finished zoom on a quick second click
  // (so steps came out uneven) and leave the scale still moving after the click.
  const zoom = (d: number) => void rf.zoomTo(Math.min(2, Math.max(0.2, rf.getZoom() + d)))
  return (
    <div className="ge-fl ge-tools" role="toolbar" aria-label="Canvas tools" data-testid="ge-tools">
      <button type="button" className={cx('ge-tb', mode === 'pointer' && 'on')} aria-pressed={mode === 'pointer'} title="Select (V)" aria-label="Select" onClick={() => setMode('pointer')} data-testid="ge-tool-pointer">
        <MousePointer2 />
      </button>
      <button type="button" className={cx('ge-tb', mode === 'hand' && 'on')} aria-pressed={mode === 'hand'} title="Pan (H)" aria-label="Pan" onClick={() => setMode('hand')} data-testid="ge-tool-hand">
        <Hand />
      </button>
      <i className="vs" aria-hidden />
      <button type="button" className="ge-tb add" title="Add a step (N)" onClick={() => openCreator('regular')} data-testid="ge-tool-add">
        <span className="pl" aria-hidden>
          <Plus />
        </span>
        Add
      </button>
      <button
        type="button"
        className="ge-tb"
        title="Sticky note"
        aria-label="Add a sticky note"
        onClick={() => {
          const el = document.querySelector('.ge-canvas')?.getBoundingClientRect()
          const p = rf.screenToFlowPosition({ x: (el?.left ?? 0) + (el?.width ?? 800) / 2, y: (el?.top ?? 0) + (el?.height ?? 600) / 3 })
          addSticky(p)
        }}
        data-testid="ge-tool-note"
      >
        <StickyNote />
      </button>
      <button type="button" className="ge-tb" title="Tidy up (⇧T)" aria-label="Tidy up" onClick={() => getEditorAction('tidy-up')?.(null)} data-testid="ge-tool-tidy">
        <LayoutGrid />
      </button>
      <i className="vs" aria-hidden />
      <button type="button" className="ge-tb" title="Zoom out" aria-label="Zoom out" onClick={() => zoom(-0.2)}>
        <Minus />
      </button>
      <button type="button" className="ge-tb" title="Fit (⇧1)" aria-label="Zoom to fit" onClick={() => void rf.fitView({ padding: 0.2, duration: 200 })} data-testid="ge-tool-fit">
        <Maximize />
      </button>
      <button type="button" className="ge-tb" title="Zoom in" aria-label="Zoom in" onClick={() => zoom(0.2)}>
        <Plus />
      </button>
    </div>
  )
}

export function EditorView() {
  useBindRun()
  useBridges()
  const drawer = useGlassStore((s) => s.drawerOpen)
  const insp = useGlassStore((s) => s.inspector)
  const selected = useGlassStore((s) => s.selectedNode)
  const sheet = false
  return (
    <div
      className="ge ge-root"
      data-testid="glass-edit"
      data-drawer={drawer ? 'open' : 'closed'}
      data-insp={insp !== 'closed' && selected ? 'open' : 'closed'}
      data-sheet={sheet ? 'open' : 'closed'}
    >
      <div className="ge-canvas">
        <WorkflowCanvas nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES} />
        <EmptyCanvasHint />
      </div>
      <ToolsBar />
      <DraftBar />
      <div className="ge-banners">
        <ViewingRunBanner />
        <DriftBanner />
      </div>
      <NodeCatalog />
      <Inspector />
      <RunStrip />
      <NodeSheet />
      <EditorCommandPalette />
    </div>
  )
}

export default EditorView
