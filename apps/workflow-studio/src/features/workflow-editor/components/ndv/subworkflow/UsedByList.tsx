import { ArrowUpRight } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Spinner } from '@/shared/components/ui/spinner';
import { useEditorProjectStore } from '../../../stores/editorProjectStore';
import { useUsedBy, type UsedByRow } from '../../../hooks/useSubworkflows';

const WHERE_LABEL: Record<UsedByRow['where'], string> = {
  draft: 'draft',
  published: 'published',
  release: 'release',
};

/** The parents that call a workflow: one row per call node. Rows link to the parent in a new tab. */
export function UsedByList({ workflowId, enabled = true }: { workflowId: string | null | undefined; enabled?: boolean }) {
  const slug = useEditorProjectStore((s) => s.slug);
  const { data, isLoading, error } = useUsedBy(workflowId ?? undefined, enabled);

  if (!workflowId) return <p className="text-[12px] text-muted-foreground">Save this workflow to see what calls it.</p>;
  if (isLoading) return <div className="flex justify-center py-3"><Spinner className="size-4 text-muted-foreground" /></div>;
  if (error) return <p className="text-[12px] text-destructive">Could not load the callers.</p>;
  if (!data || data.length === 0) {
    return <p className="text-[12px] text-muted-foreground" data-testid="used-by-empty">No workflow calls this one yet.</p>;
  }
  return (
    <ul className="space-y-1" data-testid="used-by-list">
      {data.map((r) => (
        <li key={`${r.parent_workflow_id}:${r.node}:${r.where}`}>
          <a
            href={`/p/${slug}/workflows/${r.parent_workflow_id}`}
            target="_blank"
            rel="noopener noreferrer"
            data-testid={`used-by-row-${r.parent_workflow_id}`}
            className="row-hover flex items-center gap-2 rounded-ctl px-2 py-1.5 text-[13px]"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{r.parent_name}</span>
              <span className="block truncate text-[11.5px] text-muted-foreground">
                {r.node} · {r.version_spec}
              </span>
            </span>
            <Badge variant={r.where === 'draft' ? 'warning' : 'neutral'}>{WHERE_LABEL[r.where] ?? r.where}</Badge>
            <ArrowUpRight size={13} className="shrink-0 text-muted-foreground" aria-hidden />
          </a>
        </li>
      ))}
    </ul>
  );
}
