/**
 * `/p/$project/workflows/$workflowId`: the existing canvas editor inside a project (§2.1, §6.6).
 * `?ai=1` and `?proposal=<id>` open the Build tab: the AI builds and is reviewed on this same page (PLAN-MOTION-UI §2).
 */
import { useEffect, useRef } from 'react'
import { useParams, useSearch } from '@tanstack/react-router'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useAiProposalStore } from '@/features/ai-builder/aiProposalStore'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import { useEditorLayoutStore } from '@/features/workflow-editor/stores/editorLayoutStore'
import { useEditorProjectStore } from '@/features/workflow-editor/stores/editorProjectStore'
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import { WorkflowEditorPage } from '@/features/workflow-editor/WorkflowEditorPage'

export function ProjectWorkflowEditor() {
  const { project } = useProjectCtx()
  const { workflowId } = useParams({ strict: false }) as { workflowId: string }
  const { ai, proposal } = useSearch({ strict: false }) as { ai?: 1; proposal?: string }
  const setProject = useEditorProjectStore((s) => s.setProject)
  const ready = useEditorProjectStore((s) => s.projectId === project.id)
  useEffect(() => {
    setProject(project.id, project.slug, project.environments.map((e) => e.name), project.name)
  }, [project, setProject])
  useEffect(() => {
    if (workflowId === 'new') useWorkflowStore.getState().resetWorkflow()
  }, [workflowId])

  // the Build tab is where the AI lives: a link that asks for it opens it
  useEffect(() => {
    if (ai || proposal) useEditorLayoutStore.getState().ensureRightPanelOpen('builder')
  }, [ai, proposal])

  // going to another workflow while a stage is up: the stage belonged to the one we left (Keep on a new workflow
  // navigates with its own `?proposal=`, so that move is not a departure)
  const lastWorkflow = useRef(workflowId)
  useEffect(() => {
    if (lastWorkflow.current === workflowId) return
    lastWorkflow.current = workflowId
    const stage = useStageStore.getState()
    if (stage.active && stage.proposalId !== proposal) {
      stage.end()
      useAiProposalStore.getState().reset()
    }
  }, [workflowId, proposal])
  useEffect(() => () => {
    useStageStore.getState().end()
    useAiProposalStore.getState().reset()
  }, [])

  if (!ready) return null
  return <WorkflowEditorPage workflowId={workflowId === 'new' ? undefined : workflowId} />
}
