import { lazy, Suspense, useEffect } from 'react'
import { createRoute } from '@tanstack/react-router'
import { ReactFlowProvider } from '@xyflow/react'
import { Loader2, X, Blocks, Sparkles } from 'lucide-react'
import { EditorTabList, EditorTab } from '@/shared/components/ui/editor-tabs'
import { rootRoute } from './__root'
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import { useEditorLayoutStore, type RightPanelTab } from '@/features/workflow-editor/stores/editorLayoutStore'
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform'
import { backends } from '@/shared/lib/config'
import { useNodeTypes } from '@/features/workflow-editor/hooks/useNodeTypes'
import type { NodeTypeMetadata } from '@/features/workflow-editor/lib/createNodeData'
// Lazy load heavy components
const NodeCreatorPanel = lazy(() => import('@/features/workflow-editor/components/node-creator/NodeCreatorPanel'))
const WorkflowCanvas = lazy(() => import('@/features/workflow-editor/components/canvas/WorkflowCanvas'))
const NodeDetailsModal = lazy(() => import('@/features/workflow-editor/components/ndv/NodeDetailsModal'))
const WorkflowNavbar = lazy(() => import('@/features/workflow-editor/components/workflow-navbar/WorkflowNavbar'))
const BottomPanel = lazy(() => import('@/features/workflow-editor/components/bottom-panel/BottomPanel'))
const AIChatPanel = lazy(() => import('@/features/workflow-editor/components/ai-chat/AIChatPanel'))

// --- Floating panel shared styles ---
const floatingPanel = 'bg-[var(--surface)]/80 backdrop-blur-xl rounded-xl shadow-lg border border-border/30 overflow-hidden'
const panelTransition = 'transition-all duration-300 ease-out'

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
    <div className="h-full flex flex-col">
      <EditorTabList>
        {rightPanelTabs.map((tab) => (
          <EditorTab
            key={tab.id}
            active={activeTab === tab.id}
            icon={tab.icon}
            onClick={() => setTab(tab.id)}
          >
            {tab.label}
          </EditorTab>
        ))}
        <div className="flex-1" />
        <button
          onClick={closeRightPanel}
          className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent rounded"
          title="Close panel"
        >
          <X size={12} />
        </button>
      </EditorTabList>
      <div className="flex-1 overflow-hidden min-h-0">
        <Suspense fallback={<div className="flex-1" />}>
          {activeTab === 'nodes' && <NodeCreatorPanel />}
          {activeTab === 'ai' && <AIChatPanel />}
        </Suspense>
      </div>
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
      }])
    )
    useWorkflowStore.getState().setNodeTypesMap(map)
  }, [nodeTypes])

  const closeBottomPanel = useEditorLayoutStore((s) => s.closeBottomPanel)

  useEffect(() => {
    if (!workflowId) {
      if (currentWorkflowId) {
        resetWorkflow()
        closeBottomPanel()
      }
      return
    }

    if (workflowId === currentWorkflowId) {
      return
    }

    // Switching to a different workflow — clear stale execution panel
    closeBottomPanel()

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
  }, [workflowId, currentWorkflowId, loadWorkflow, resetWorkflow, closeBottomPanel])

  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen)
  const bottomPanelOpen = useEditorLayoutStore((s) => s.bottomPanelOpen)
  const bottomPanelMaximized = useEditorLayoutStore((s) => s.bottomPanelMaximized)

  return (
    <ReactFlowProvider>
      <Suspense fallback={<EditorLoadingFallback />}>
        <div className="h-full w-full relative bg-background">
          {/* Layer 0: Full-viewport canvas */}
          <WorkflowCanvas />

          {/* Layer 2: Floating navbar */}
          <div className="absolute top-3 left-3 right-3 z-20">
            <WorkflowNavbar />
          </div>

          {/* Layer 1: Floating right panel */}
          <div
            className={[
              'absolute right-3 bottom-3 z-10',
              floatingPanel,
              panelTransition,
              rightPanelOpen ? 'translate-x-0 opacity-100' : 'translate-x-[calc(100%+12px)] opacity-0 pointer-events-none',
            ].join(' ')}
            style={{ width: 'min(384px, 50vw - 24px)', top: 'calc(0.75rem + 44px + 0.75rem)' }}
          >
            <RightPanel />
          </div>

          {/* Layer 1: Floating bottom panel */}
          <div
            className={[
              'absolute left-3 bottom-3 z-10',
              floatingPanel,
              panelTransition,
              bottomPanelMaximized ? 'h-[calc(100%-80px)]' : 'h-[min(280px,50vh)]',
              bottomPanelOpen ? 'translate-y-0 opacity-100' : 'translate-y-[calc(100%+12px)] opacity-0 pointer-events-none',
            ].join(' ')}
            style={{ right: rightPanelOpen ? 'calc(min(384px, 50vw - 24px) + 24px)' : 12 }}
          >
            <BottomPanel />
          </div>

          {/* Overlays */}
          <NodeDetailsModal />
        </div>
      </Suspense>
    </ReactFlowProvider>
  )
}
