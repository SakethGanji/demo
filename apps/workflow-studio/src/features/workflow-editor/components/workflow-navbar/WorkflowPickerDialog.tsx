import { useState, useMemo } from 'react';
import { Search, X, Workflow, Loader2, Link, Copy } from 'lucide-react';
import { useWorkflows } from '@/features/workflows/hooks/useWorkflows';
import type { WorkflowDefinition } from '@/features/workflows/hooks/useWorkflows';

interface WorkflowPickerDialogProps {
  open: boolean;
  onClose: () => void;
  onEmbed: (workflowId: string, workflowName: string) => void;
  onCopy: (workflowName: string, definition: WorkflowDefinition) => void;
  currentWorkflowId?: string;
}

export default function WorkflowPickerDialog({
  open,
  onClose,
  onEmbed,
  onCopy,
  currentWorkflowId,
}: WorkflowPickerDialogProps) {
  const [search, setSearch] = useState('');
  const { data: workflows, isLoading, isError } = useWorkflows();

  const filtered = useMemo(() => {
    if (!workflows) return [];
    const list = workflows.filter((wf) => wf.id !== currentWorkflowId);
    if (!search) return list;
    const lower = search.toLowerCase();
    return list.filter((wf) => wf.name.toLowerCase().includes(lower));
  }, [workflows, currentWorkflowId, search]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-background/60"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 w-[420px] max-h-[480px] flex flex-col rounded-lg border border-border bg-card shadow-xl overflow-hidden">
        {/* Header */}
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">Add Subworkflow</h2>
              <p className="text-xs text-muted-foreground mt-0.5">Embed as reference or copy nodes inline</p>
            </div>
            <button
              onClick={onClose}
              className="rounded-lg p-1.5 hover:bg-accent transition-colors"
            >
              <X size={16} className="text-muted-foreground" />
            </button>
          </div>

          {/* Search */}
          <div className="relative">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <input
              type="text"
              placeholder="Search workflows..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-input bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring transition-all"
              autoFocus
            />
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Loader2 size={24} className="animate-spin text-muted-foreground mb-2" />
              <p className="text-sm text-muted-foreground">Loading workflows...</p>
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center py-12">
              <p className="text-sm text-destructive">Failed to load workflows</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <p className="text-sm text-muted-foreground">
                {search ? `No workflows matching "${search}"` : 'No other workflows found'}
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {filtered.map((wf) => (
                <div
                  key={wf.id}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-accent transition-colors group"
                >
                  <div className="h-8 w-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Workflow size={16} className="text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">
                      {wf.name}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {wf.nodeCount} node{wf.nodeCount !== 1 ? 's' : ''}
                      {!wf.active && (
                        <span className="ml-1.5 text-[var(--warning)]">(Inactive)</span>
                      )}
                    </div>
                  </div>
                  {/* Action buttons — visible on hover */}
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => { onEmbed(wf.id, wf.name); onClose(); }}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                      title="Embed as live reference"
                    >
                      <Link size={11} />
                      Embed
                    </button>
                    <button
                      onClick={() => { onCopy(wf.name, wf.definition); onClose(); }}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-secondary text-secondary-foreground hover:bg-accent transition-colors"
                      title="Copy nodes into this workflow"
                    >
                      <Copy size={11} />
                      Copy
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
