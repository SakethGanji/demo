/**
 * `/p/$project/workflows/$workflowId`: the existing canvas editor inside a project (§2.1, §6.6).
 */
import { useEffect } from 'react'
import { useParams } from '@tanstack/react-router'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useEditorProjectStore } from '@/features/workflow-editor/stores/editorProjectStore'
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore'
import { WorkflowEditorPage } from '@/features/workflow-editor/WorkflowEditorPage'

export function ProjectWorkflowEditor() {
  const { project } = useProjectCtx()
  const { workflowId } = useParams({ strict: false }) as { workflowId: string }
  const setProject = useEditorProjectStore((s) => s.setProject)
  const ready = useEditorProjectStore((s) => s.projectId === project.id)
  useEffect(() => {
    setProject(project.id, project.slug, project.environments.map((e) => e.name))
  }, [project, setProject])
  useEffect(() => {
    if (workflowId === 'new') useWorkflowStore.getState().resetWorkflow()
  }, [workflowId])
  if (!ready) return null
  return <WorkflowEditorPage workflowId={workflowId === 'new' ? undefined : workflowId} />
}
