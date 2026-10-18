/**
 * Badge under an ExecuteWorkflow node (DESIGN-STAGE8-REUSE 12.3): "Refund eligibility · v3", "· latest" or "· draft".
 * Clicking it opens the sub-workflow in a new tab (n8n behaviour), so the node itself still opens its details.
 */
import { Workflow } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { useEditorProjectStore } from '../../../stores/editorProjectStore';
import { useSubworkflowList } from '../../../hooks/useSubworkflows';
import { badgeText, readCallParams } from '../../ndv/subworkflow/subworkflowParams';

export function SubworkflowBadge({ parameters }: { parameters?: Record<string, unknown> }) {
  const projectId = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const p = readCallParams(parameters);
  const list = useSubworkflowList(p.workflowId ? projectId : undefined);
  if (!p.workflowId) return null;
  const name = list.data?.find((w) => w.id === p.workflowId)?.name;
  const missing = !!list.data && !name;
  return (
    <div className="nodrag mt-1 flex justify-center">
      <a
        href={`/p/${slug}/workflows/${p.workflowId}`}
        target="_blank"
        rel="noopener noreferrer"
        title="Open the sub-workflow in a new tab"
        data-testid="node-subwf-link"
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        <Badge
          variant={missing ? 'error' : p.version === 'draft' ? 'warning' : 'neutral'}
          className="h-4 max-w-44 gap-1 px-1.5 text-[10.5px]"
          data-testid="node-subwf-badge"
          data-version={String(p.version)}
        >
          <Workflow aria-hidden />
          <span className="truncate">{missing ? 'Workflow not found' : badgeText(name, p.version)}</span>
        </Badge>
      </a>
    </div>
  );
}
