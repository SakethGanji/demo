import { lazy, Suspense, useEffect, useRef, useMemo } from 'react'
import { createRoute } from '@tanstack/react-router'
import { ReactFlowProvider, useViewport } from 'reactflow'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import type { ImperativePanelHandle } from 'react-resizable-panels'
import { Loader2, X, Blocks, Sparkles, CheckCircle2, XCircle, Clock } from 'lucide-react'
import { rootRoute } from './__root'
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import { useEditorLayoutStore, type RightPanelTab } from '@/features/workflow-editor/stores/editorLayoutStore'
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform'
import { backends } from '@/shared/lib/config'
import { cn } from '@/shared/lib/utils'
import { useNodeTypes } from '@/features/workflow-editor/hooks/useNodeTypes'
import type { NodeTypeMetadata } from '@/features/workflow-editor/lib/createNodeData'
import NodeCreatorPanel from '@/features/workflow-editor/components/node-creator/NodeCreatorPanel'

// Lazy load heavy components
const WorkflowCanvas = lazy(() => import('@/features/workflow-editor/components/canvas/WorkflowCanvas'))
const NodeDetailsModal = lazy(() => import('@/features/workflow-editor/components/ndv/NodeDetailsModal'))
const WorkflowNavbar = lazy(() => import('@/features/workflow-editor/components/workflow-navbar/WorkflowNavbar'))
const BottomPanel = lazy(() => import('@/features/workflow-editor/components/bottom-panel/BottomPanel'))
const AIChatSidePanel = lazy(() => import('@/features/workflow-editor/components/ai-chat/AIChatSidePanel'))

// --- Inlined from RightPanel.tsx ---
const rightPanelTabs: { id: RightPanelTab; label: string; icon: typeof Blocks }[] = [
  { id: 'nodes', label: 'Nodes', icon: Blocks },
  { id: 'ai', label: 'AI', icon: Sparkles },
]

function RightPanel() {
  const activeTab = useEditorLayoutStore((s) => s.rightPanelTab)
  const setTab = useEditorLayoutStore((s) => s.setRightPanelTab)
  const closeRightPanel = useEditorLayoutStore((s) => s.closeRightPanel)

  return (
    <div className="editor-chrome h-full flex flex-col bg-card">
      <div className="flex items-center h-9 px-2 border-b border-border shrink-0">
        <div className="flex gap-0.5">
          {rightPanelTabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setTab(tab.id)}
              className={cn(
                'relative inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-none transition-colors',
                activeTab === tab.id
                  ? 'text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
              )}
            >
              <tab.icon size={11} />
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          onClick={closeRightPanel}
          className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent rounded"
          title="Close panel"
        >
          <X size={12} />
        </button>
      </div>
      <div className="flex-1 overflow-hidden min-h-0">
        <Suspense fallback={<div className="flex-1" />}>
          {activeTab === 'nodes' && <NodeCreatorPanel />}
          {activeTab === 'ai' && <AIChatSidePanel />}
        </Suspense>
      </div>
    </div>
  )
}

// --- Inlined from StatusBar.tsx ---
function StatusBar() {
  const nodes = useWorkflowStore((s) => s.nodes)
  const edges = useWorkflowStore((s) => s.edges)
  const executionData = useWorkflowStore((s) => s.executionData)
  const { zoom } = useViewport()

  const nodeCount = nodes.filter((n) => n.type === 'workflowNode').length
  const edgeCount = edges.length

  const execEntries = Object.values(executionData)
  const isRunning = execEntries.some((d) => d.status === 'running')
  const hasErrors = execEntries.some((d) => d.status === 'error')
  const hasLogs = execEntries.length > 0
  const allSuccess = hasLogs && !isRunning && !hasErrors
  const totalDuration = execEntries.reduce(
    (sum, d) => sum + ((d.startTime && d.endTime ? d.endTime - d.startTime : 0)),
    0
  )

  return (
    <div className="editor-chrome flex items-center h-6 px-3 bg-card border-t border-border text-[11px] text-muted-foreground shrink-0 select-none gap-3">
      <span>{nodeCount} nodes</span>
      <span className="text-border">·</span>
      <span>{edgeCount} edges</span>
      <div className="flex-1" />
      {isRunning && (
        <span className="flex items-center gap-1 text-[var(--warning)]">
          <Loader2 size={11} className="animate-spin" />
          Running
        </span>
      )}
      {!isRunning && hasErrors && (
        <span className="flex items-center gap-1 text-destructive">
          <XCircle size={11} />
          Failed
        </span>
      )}
      {allSuccess && (
        <span className="flex items-center gap-1 text-[var(--success)]">
          <CheckCircle2 size={11} />
          <Clock size={10} />
          {totalDuration}ms
        </span>
      )}
      <div className="flex-1" />
      <span>{Math.round(zoom * 100)}%</span>
    </div>
  )
}

function EditorLoadingFallback() {
  return (
    <div className="h-full w-full flex items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Loading editor...</p>
      </div>
    </div>
  )
}

export const editorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'editor',
  validateSearch: (search: Record<string, unknown>): { workflowId?: string } => ({
    workflowId: search.workflowId as string | undefined,
  }),
  component: EditorPage,
})

function EditorPage() {
  const { workflowId } = editorRoute.useSearch()
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow)
  const resetWorkflow = useWorkflowStore((s) => s.resetWorkflow)
  const currentWorkflowId = useWorkflowStore((s) => s.workflowId)

  // Sync node type metadata from React Query cache into the workflow store
  const { data: nodeTypes } = useNodeTypes()
  useEffect(() => {
    if (!nodeTypes) return
    const map = new Map<string, NodeTypeMetadata>(
      nodeTypes.map((nt) => [nt.type, {
        type: nt.type,
        displayName: nt.displayName,
        icon: nt.icon,
        group: nt.group,
        inputCount: nt.inputCount,
        outputCount: typeof nt.outputCount === 'number' ? nt.outputCount : undefined,
        inputs: nt.inputs,
        outputs: nt.outputs,
        outputStrategy: nt.outputStrategy,
        subnodeSlots: nt.subnodeSlots,
        isSubnode: nt.isSubnode,
        subnodeType: nt.subnodeType,
      }])
    )
    useWorkflowStore.getState().setNodeTypesMap(map)
  }, [nodeTypes])

  useEffect(() => {
    if (!workflowId) {
      if (currentWorkflowId) {
        resetWorkflow()
      }
      return
    }

    if (workflowId === currentWorkflowId) {
      return
    }

    async function fetchWorkflow() {
      try {
        const res = await fetch(`${backends.workflow}/api/workflows/${workflowId}`)
        if (!res.ok) throw new Error('Failed to fetch workflow')
        const data = await res.json()
        const transformed = fromBackendWorkflow(data)
        loadWorkflow(transformed)
      } catch (error) {
        console.error('Failed to load workflow:', error)
      }
    }

    fetchWorkflow()
  }, [workflowId, currentWorkflowId, loadWorkflow, resetWorkflow])

  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen)
  const setRightPanelSize = useEditorLayoutStore((s) => s.setRightPanelSize)
  const bottomPanelOpen = useEditorLayoutStore((s) => s.bottomPanelOpen)
  const setBottomPanelSize = useEditorLayoutStore((s) => s.setBottomPanelSize)
  const bottomPanelMaximized = useEditorLayoutStore((s) => s.bottomPanelMaximized)

  const canvasPanelRef = useRef<ImperativePanelHandle>(null)
  const prevCanvasSizeRef = useRef(70)

  useEffect(() => {
    const panel = canvasPanelRef.current
    if (!panel || !bottomPanelOpen) return

    if (bottomPanelMaximized) {
      const currentSize = panel.getSize()
      if (currentSize > 0) {
        prevCanvasSizeRef.current = currentSize
      }
      panel.collapse()
    } else {
      panel.resize(prevCanvasSizeRef.current || 70)
    }
  }, [bottomPanelMaximized, bottomPanelOpen])

  return (
    <ReactFlowProvider>
      <Suspense fallback={<EditorLoadingFallback />}>
        <div className="h-full w-full flex flex-col bg-background">
          {/* Integrated toolbar */}
          <WorkflowNavbar />

          {/* Main content area */}
          <PanelGroup direction="horizontal" className="flex-1 min-h-0">
            {/* Center — canvas + bottom panel */}
            <Panel defaultSize={rightPanelOpen ? 80 : 100} minSize={50}>
              <PanelGroup direction="vertical">
                {/* Canvas */}
                <Panel
                  ref={canvasPanelRef}
                  defaultSize={bottomPanelOpen ? 70 : 100}
                  minSize={20}
                  collapsible
                  collapsedSize={0}
                >
                  <div className="h-full w-full relative">
                    <WorkflowCanvas />
                  </div>
                </Panel>

                {/* Bottom panel */}
                {bottomPanelOpen && (
                  <>
                    <PanelResizeHandle className="h-px bg-border hover:bg-primary/50 transition-colors data-[resize-handle-active]:bg-primary/50" />
                    <Panel
                      defaultSize={30}
                      minSize={15}
                      onResize={setBottomPanelSize}
                    >
                      <BottomPanel />
                    </Panel>
                  </>
                )}
              </PanelGroup>
            </Panel>

            {/* Right panel — node browser + AI chat */}
            {rightPanelOpen && (
              <>
                <PanelResizeHandle className="w-px bg-border hover:bg-primary/50 transition-colors data-[resize-handle-active]:bg-primary/50" />
                <Panel
                  defaultSize={20}
                  minSize={15}
                  maxSize={35}
                  onResize={setRightPanelSize}
                >
                  <RightPanel />
                </Panel>
              </>
            )}
          </PanelGroup>

          {/* Status bar */}
          <StatusBar />

          {/* Overlays */}
          <NodeDetailsModal />
        </div>
      </Suspense>
    </ReactFlowProvider>
  )
}
