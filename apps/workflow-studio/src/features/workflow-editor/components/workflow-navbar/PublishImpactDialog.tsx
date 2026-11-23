import { ArrowUpRight, TriangleAlert } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { Badge } from '@/shared/components/ui/badge';
import { useEditorProjectStore } from '../../stores/editorProjectStore';
import type { PublishImpactRow } from '../../lib/subworkflowsApi';

/**
 * Shown after a publish when the new version breaks a published parent that follows `latest`
 * (DESIGN-STAGE8-REUSE §4.3, §12.6, owner decision O7: the publish already happened; this asks the author to acknowledge).
 */
export function PublishImpactDialog({
  impact,
  open,
  onOpenChange,
}: {
  impact: PublishImpactRow[];
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const slug = useEditorProjectStore((s) => s.slug);
  const breaking = impact.filter((r) => r.breaking.length > 0);
  const others = impact.length - breaking.length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-testid="publish-impact-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-warning-text" aria-hidden /> This version breaks {breaking.length === 1 ? 'a workflow' : `${breaking.length} workflows`}
          </DialogTitle>
          <DialogDescription>
            The version is published. These workflows call this one with <span className="font-mono">latest</span> and no longer fit its inputs or output. Fix them, or
            publish the previous version again.
          </DialogDescription>
        </DialogHeader>
        <ul className="max-h-72 space-y-1.5 overflow-y-auto">
          {breaking.map((r) => (
            <li
              key={`${r.parent_workflow_id}:${r.node}`}
              className="rounded-ctl bg-recess px-3 py-2 text-[12.5px]"
              data-testid="publish-impact-row"
              data-parent-id={r.parent_workflow_id}
            >
              <div className="flex items-center gap-2">
                <a
                  href={`/p/${slug}/workflows/${r.parent_workflow_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 font-medium hover:underline"
                >
                  <span className="truncate">{r.parent_name}</span>
                  <ArrowUpRight size={12} className="shrink-0 text-muted-foreground" aria-hidden />
                </a>
                <span className="truncate text-muted-foreground">{r.node}</span>
                <div className="flex-1" />
                {r.in_release && <Badge variant="warning">in a release</Badge>}
              </div>
              {r.breaking.map((f, i) => (
                <div key={i} className="mt-1 text-muted-foreground">
                  <span className="mr-1.5 font-mono text-[11.5px]">{f.code}</span>
                  {f.message}
                </div>
              ))}
            </li>
          ))}
        </ul>
        {others > 0 && (
          <p className="text-[12px] text-muted-foreground">
            {others} other {others === 1 ? 'workflow follows' : 'workflows follow'} this one and {others === 1 ? 'is' : 'are'} not affected.
          </p>
        )}
        <DialogFooter>
          <Button size="sm" onClick={() => onOpenChange(false)} data-testid="publish-impact-confirm">
            I understand
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
