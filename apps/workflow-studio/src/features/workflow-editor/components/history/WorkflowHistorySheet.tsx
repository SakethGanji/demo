import { useEffect, useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet';
import { Button } from '@/shared/components/ui/button';
import { Well, Callout } from '@/shared/components/ui/surface';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { workflowsApi } from '@/shared/lib/api';
import { cn } from '@/shared/lib/utils';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useHistoryStore } from '../../stores/historyStore';
import { fromBackendWorkflow } from '../../lib/workflowTransform';
import { buildVersionDiffView } from '../../lib/versionDiffView';

const MAX_VERSIONS = 50;

export default function WorkflowHistorySheet() {
  const open = useHistoryStore((s) => s.open);
  const setOpen = useHistoryStore((s) => s.setOpen);
  const selected = useHistoryStore((s) => s.selectedVersion);
  const selectVersion = useHistoryStore((s) => s.selectVersion);
  const setDiff = useHistoryStore((s) => s.setDiff);
  const marks = useHistoryStore((s) => s.marks);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const nodes = useWorkflowStore((s) => s.nodes);
  const edges = useWorkflowStore((s) => s.edges);
  const isDirty = useWorkflowStore((s) => s._isDirty);

  const list = useQuery({
    queryKey: ['workflow-versions', workflowId],
    queryFn: () => workflowsApi.listVersions(workflowId!),
    enabled: open && !!workflowId,
  });
  const versions = useMemo(
    () => [...(list.data ?? [])].sort((a, b) => b.version_number - a.version_number).slice(0, MAX_VERSIONS),
    [list.data],
  );

  // Node counts need each version's definition.
  const details = useQueries({
    queries: versions.map((v) => ({
      queryKey: ['workflow-version', workflowId, v.id],
      queryFn: () => workflowsApi.getVersion(workflowId!, v.id),
      enabled: open && !!workflowId,
      staleTime: 60_000,
    })),
  });
  const detailById = new Map(versions.map((v, i) => [v.version_number, details[i]?.data]));
  const selectedDetail = selected != null ? detailById.get(selected) : undefined;

  // Diff the chosen version against the canvas; recompute while the sheet stays open and nodes change.
  useEffect(() => {
    if (!open || !selectedDetail) return;
    const { marks: m, ghostNodes } = buildVersionDiffView(selectedDetail.definition, nodes, edges);
    setDiff(m, ghostNodes);
  }, [open, selectedDetail, nodes, edges, setDiff]);

  const restore = async () => {
    const version = versions.find((v) => v.version_number === selected);
    if (!workflowId || !version) return;
    try {
      const wf = await workflowsApi.restoreVersion(workflowId, version.id);
      useWorkflowStore.getState().loadWorkflow(fromBackendWorkflow(wf));
      toast.success(`Restored version ${version.version_number} as the draft`);
      setOpen(false);
    } catch (e) {
      toast.error('Could not restore this version', { description: e instanceof Error ? e.message : String(e) });
    }
  };

  const counts = { added: 0, removed: 0, changed: 0 };
  for (const m of Object.values(marks)) counts[m] += 1;

  return (
    <Sheet open={open} onOpenChange={setOpen} modal={false}>
      <SheetContent
        side="right"
        showCloseButton
        overlayClassName="hidden"
        className="gap-0 overflow-hidden rounded-xl p-0 data-[side=right]:inset-y-2 data-[side=right]:right-2 data-[side=right]:h-auto data-[side=right]:w-[340px] data-[side=right]:max-w-[calc(100vw-16px)] data-[side=right]:sm:max-w-[340px]"
        data-testid="history-sheet"
      >
        <div className="hairline-b px-5 pt-4 pb-3 pr-12">
          <SheetTitle className="text-[14px]">Version history</SheetTitle>
          <SheetDescription className="mt-1 text-[12px] text-muted-foreground">
            Pick a version to compare it with the canvas.
          </SheetDescription>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
          {!workflowId ? (
            <p className="text-[13px] text-muted-foreground" data-testid="history-empty">Save the workflow to start its history.</p>
          ) : list.isPending ? (
            <div className="flex flex-col gap-2" data-testid="history-loading">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.isError ? (
            <p className="text-[13px] text-destructive" data-testid="history-error">Could not load versions.</p>
          ) : versions.length === 0 ? (
            <p className="text-[13px] text-muted-foreground" data-testid="history-empty">No versions yet. Publish to create one.</p>
          ) : (
            <Well className="flex flex-col gap-0.5 p-1.5">
              {versions.map((v) => {
                const nodeCount = detailById.get(v.version_number)?.definition.nodes.length;
                const active = v.version_number === selected;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => selectVersion(active ? null : v.version_number)}
                    aria-pressed={active}
                    data-testid={`history-version-${v.version_number}`}
                    className={cn(
                      'flex w-full flex-col gap-0.5 rounded-chip px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-(--hover-fill)',
                      active && 'bg-raise shadow-raise-sm hover:bg-raise',
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-medium">Version {v.version_number}</span>
                      <span className="text-[12px] text-muted-foreground tabular-nums">
                        {nodeCount == null ? '…' : `${nodeCount} node${nodeCount === 1 ? '' : 's'}`}
                      </span>
                    </span>
                    <span className="truncate text-[12px] text-muted-foreground">
                      {new Date(v.created_at).toLocaleString()}
                      {v.message ? ` · ${v.message}` : ''}
                    </span>
                  </button>
                );
              })}
            </Well>
          )}

          {selected != null && selectedDetail && (
            <div className="flex flex-col gap-2" data-testid="history-diff-summary">
              <p className="text-[12.5px] text-muted-foreground">
                On the canvas: {counts.added} added (green), {counts.changed} changed (amber), {counts.removed} removed (faded).
              </p>
              {isDirty && <Callout tone="warn">Restoring replaces your unsaved changes.</Callout>}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 shadow-[inset_0_1px_0_var(--hairline)]">
          <Button size="sm" disabled={selected == null} onClick={restore} data-testid="history-restore">
            <RotateCcw /> Restore this version
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
