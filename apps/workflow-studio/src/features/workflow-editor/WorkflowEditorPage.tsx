import { lazy, Suspense, useEffect } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { Loader2, X, Blocks, Sparkles, LayoutTemplate, WandSparkles } from 'lucide-react'
import { toast } from 'sonner'
import { EditorTabList, EditorTab } from '@/shared/components/ui/editor-tabs'
import { EmptyCanvasHint } from '@/features/ai-builder/components/EmptyCanvasHint'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import { useEditorLayoutStore, type RightPanelTab } from '@/features/workflow-editor/stores/editorLayoutStore'
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform'
import { workflowsApi, executionsApi } from '@/shared/lib/api'
import { useExecutionParam } from '@/features/workflow-editor/hooks/useViewingRun'
import EditorCommandPalette from '@/features/workflow-editor/components/command-palette/EditorCommandPalette'
import DriftBanner from '@/features/workflow-editor/components/tests/DriftBanner'
import ViewingRunBanner from '@/features/workflow-editor/components/workflow-navbar/ViewingRunBanner'
import WorkflowHistorySheet from '@/features/workflow-editor/components/history/WorkflowHistorySheet'
import { useNodeActions } from '@/features/workflow-editor/hooks/useNodeActions'
import { registerEditorAction } from '@/features/workflow-editor/stores/editorActionsStore'
import { useNodeTypes } from '@/features/workflow-editor/hooks/useNodeTypes'
import { useLiveCheck } from '@/features/workflow-editor/hooks/useLiveCheck'
import { useLiveTests } from '@/features/workflow-editor/hooks/useLiveTests'
import type { NodeTypeMetadata } from '@/features/workflow-editor/lib/createNodeData'
// Lazy load heavy components
const NodeCreatorPanel = lazy(() => import('@/features/workflow-editor/components/node-creator/NodeCreatorPanel'))
const WorkflowCanvas = lazy(() => import('@/features/workflow-editor/components/canvas/WorkflowCanvas'))
const NodeDetailsModal = lazy(() => import('@/features/workflow-editor/components/ndv/NodeDetailsModal'))
const WorkflowNavbar = lazy(() => import('@/features/workflow-editor/components/workflow-navbar/WorkflowNavbar'))
const BottomPanel = lazy(() => import('@/features/workflow-editor/components/bottom-panel/BottomPanel'))
const AIChatPanel = lazy(() => import('@/features/workflow-editor/components/ai-chat/AIChatPanel'))
const InterfacePanel = lazy(() => import('@/features/workflow-editor/components/interface/InterfacePanel'))
const NodeAIDialog = lazy(() => import('@/features/ai-builder/components/NodeAIDialog'))
const EditorBuilderTab = lazy(() => import('@/features/ai-builder/components/EditorBuilderTab'))

// --- Floating panel shared styles ---
const floatingPanel = 'glass-overlay overflow-hidden'
const panelTransition = 'transition-all duration-300 ease-out'

// --- Inlined from RightPanel.tsx ---
const rightPanelTabs: { id: RightPanelTab; label: string; icon: typeof Blocks }[] = [
  { id: 'nodes', label: 'Nodes', icon: Blocks },
  { id: 'ai', label: 'AI', icon: Sparkles },
  { id: 'interface', label: 'Interface', icon: LayoutTemplate },
  { id: 'builder', label: 'Build', icon: WandSparkles },
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
          className="mr-1 flex size-6 items-center justify-center rounded-ctl text-muted-foreground transition-colors hover:bg-(--hover-fill) hover:text-foreground"
          title="Close panel"
          aria-label="Close panel"
          data-testid="editor-right-close"
        >
          <X size={12} />
        </button>
      </EditorTabList>
      <div className="flex-1 overflow-hidden min-h-0">
        <Suspense fallback={<div className="flex-1" />}>
          {activeTab === 'nodes' && <NodeCreatorPanel />}
          {activeTab === 'ai' && <AIChatPanel />}
          {activeTab === 'interface' && <InterfacePanel />}
          {activeTab === 'builder' && <EditorBuilderTab />}
        </Suspense>
      </div>
    </div>
  )
}

function EditorLoadingFallback() {
  return (
    <div className="h-full w-full flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Loading editor...</p>
      </div>
    </div>
  )
}

/**
 * Today's canvas editor, unchanged inside (UI-DESIGN §2.1). Mounted at
 * `/p/$project/workflows/$workflowId` (`new` = a fresh workflow).
 */
export function WorkflowEditorPage({ workflowId }: { workflowId?: string }) {
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

  // Live mistake check of the draft (debounced; stores into checkStore)
  useLiveCheck()
  useLiveTests()

  // ⌘K palette handlers for the node actions (the context menu and the node panel call the same ones).
  const nodeActions = useNodeActions()
  useEffect(() => {
    const offs = [
      registerEditorAction('test-step', (id) => { if (id) void nodeActions.testStep(id, 'last_run') }),
      registerEditorAction('run-to', (id) => { if (id) void nodeActions.runTo(id) }),
      registerEditorAction('run-from', (id) => { if (id) void nodeActions.runFrom(id) }),
      registerEditorAction('toggle-disable', (id) => { if (id) nodeActions.toggleDisable(id) }),
    ]
    return () => offs.forEach((off) => off())
  }, [nodeActions])

  useEffect(() => {
    if (!workflowId) {
      // a stage that is about to hand over to a freshly kept workflow loads it before the URL catches up
      if (currentWorkflowId && !useStageStore.getState().active) {
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
        const data = await workflowsApi.get(workflowId!)
        const transformed = fromBackendWorkflow(data)
        loadWorkflow(transformed)
      } catch (error) {
        toast.error('Could not load workflow', { description: error instanceof Error ? error.message : String(error) })
      }
    }

    fetchWorkflow()
  }, [workflowId, currentWorkflowId, loadWorkflow, resetWorkflow, closeBottomPanel])

  // `?execution=<id>`: show that saved run's data on the canvas, as a live run would.
  const executionId = useExecutionParam()
  const workflowLoaded = !!workflowId && workflowId === currentWorkflowId
  useEffect(() => {
    const store = useWorkflowStore.getState()
    if (!executionId) {
      if (store.viewingExecution) store.exitViewingExecution()
      return
    }
    if (!workflowLoaded || store.viewingExecution?.id === executionId) return
    let cancelled = false
    executionsApi
      .get(executionId)
      .then((detail) => {
        if (cancelled) return
        if (detail.workflow_id !== workflowId) {
          toast.error('That run belongs to a different workflow')
          return
        }
        useWorkflowStore.getState().loadExecutionData(detail)
      })
      .catch((error) => {
        if (!cancelled) toast.error('Could not load the run', { description: error instanceof Error ? error.message : String(error) })
      })
    return () => {
      cancelled = true
    }
  }, [executionId, workflowLoaded, workflowId])

  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen)
  const bottomPanelOpen = useEditorLayoutStore((s) => s.bottomPanelOpen)
  const bottomPanelMaximized = useEditorLayoutStore((s) => s.bottomPanelMaximized)

  return (
    <ReactFlowProvider>
      <Suspense fallback={<EditorLoadingFallback />}>
        <div className="h-full w-full relative overflow-clip">
          {/* Layer 0: the canvas, a recessed well under the navbar */}
          <div className="well absolute right-3 bottom-3 left-3 top-[68px] overflow-hidden rounded-panel">
            <WorkflowCanvas />
            <EmptyCanvasHint />
          </div>

          {/* Layer 2: Floating navbar */}
          <div className="absolute top-3 left-3 right-3 z-20">
            <WorkflowNavbar />
          </div>

          {/* Viewing a saved run */}
          <div className="absolute top-[76px] left-1/2 z-20 flex -translate-x-1/2 flex-col items-center gap-2">
            <ViewingRunBanner />
            <DriftBanner />
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
          <NodeAIDialog />
          <WorkflowHistorySheet />
          <EditorCommandPalette />
        </div>
      </Suspense>
    </ReactFlowProvider>
  )
}
