import { useNavigate, useSearch } from '@tanstack/react-router';
import { useEditorProjectStore } from '@/features/workflow-editor/stores/editorProjectStore';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { useSaveWorkflow } from '@/features/workflow-editor/hooks/useWorkflowApi';
import { flushPendingEdits } from '@/features/workflow-editor/lib/pendingEdits';
import { BuilderPanel } from './BuilderPanel';

/**
 * The editor's "Build" tab: ask the AI to build a new workflow (an empty canvas) or change the open one. Anything unsaved
 * on the canvas is saved first, so the AI starts from what you see. The plan, the live build and the review all happen
 * here and on the canvas next to it; the proposal on screen lives in the URL as `?proposal=<id>`.
 */
export default function EditorBuilderTab() {
  const projectId = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const { proposal } = useSearch({ strict: false }) as { proposal?: string };
  const navigate = useNavigate();
  const { saveWorkflow } = useSaveWorkflow();

  const beforeRequest = async (): Promise<boolean> => {
    flushPendingEdits();
    if (!useWorkflowStore.getState().isDirty()) return true;
    try {
      await saveWorkflow();
      return true;
    } catch {
      return false; // saveWorkflow already told the person
    }
  };

  const openProposal = (id: string) => void navigate({
    to: '/p/$project/workflows/$workflowId',
    params: { project: slug, workflowId: useWorkflowStore.getState().workflowId ?? 'new' },
    search: (prev: Record<string, unknown>) => ({ ...prev, ai: undefined, proposal: id === 'new' ? undefined : id }),
    replace: id !== 'new' || !!proposal,
  });

  return (
    <div className="flex h-full flex-col" data-testid="ai-editor-tab">
      <BuilderPanel
        projectId={projectId}
        projectSlug={slug}
        workflowId={workflowId ?? null}
        resolveWorkflowId={() => useWorkflowStore.getState().workflowId ?? null}
        proposalId={proposal ?? null}
        beforeRequest={beforeRequest}
        onOpenProposal={openProposal}
      />
    </div>
  );
}
