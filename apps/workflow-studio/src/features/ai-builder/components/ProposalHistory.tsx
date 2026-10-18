import { useQuery } from '@tanstack/react-query';
import { History } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { EmptyState } from '@/shared/components/ui/empty-state';
import { aiBuilderApi } from '../api';
import type { ProposalStatus, ProposalSummary } from '../types';

const STATUS: Record<ProposalStatus, { word: string; variant: 'success' | 'warning' | 'neutral' | 'ai' | 'error' }> = {
  clarify: { word: 'Waiting for answers', variant: 'warning' },
  spec_draft: { word: 'Plan being written', variant: 'ai' },
  spec_approved: { word: 'Plan approved', variant: 'ai' },
  building: { word: 'Building', variant: 'ai' },
  ready: { word: 'Ready to review', variant: 'warning' },
  failed: { word: 'Did not work', variant: 'error' },
  kept: { word: 'Kept', variant: 'success' },
  undone: { word: 'Undone', variant: 'neutral' },
  discarded: { word: 'Discarded', variant: 'neutral' },
  answered: { word: 'Answered', variant: 'neutral' },
};

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

/** Past AI changes of this project (or of one workflow): who asked, when, and how it ended. This is the audit list. */
export function ProposalHistory({ projectId, workflowId, onOpen }: {
  projectId: string;
  workflowId?: string | null;
  onOpen: (p: ProposalSummary) => void;
}) {
  const q = useQuery({
    queryKey: ['ai-proposals', projectId, workflowId ?? null],
    queryFn: () => aiBuilderApi.list({ projectId, workflowId: workflowId ?? undefined, limit: 20 }),
    enabled: !!projectId,
  });
  const items = (q.data?.items ?? []).filter((p) => p.kind === 'build' || p.kind === 'modify');
  return (
    <div className="flex flex-col gap-1" data-testid="ai-history-panel">
      {q.isLoading && <div className="text-[12.5px] text-muted-foreground">Loading…</div>}
      {!q.isLoading && items.length === 0 && (
        <EmptyState icon={<History />} title="Nothing yet" className="py-6">Changes the AI makes will be listed here, with who asked for them.</EmptyState>
      )}
      <ul className="flex flex-col">
        {items.map((p) => {
          const s = STATUS[p.status] ?? STATUS.discarded;
          return (
            <li key={p.id}>
              <button
                type="button" onClick={() => onOpen(p)} data-testid={`ai-history-row-${p.id}`} data-status={p.status}
                className="flex w-full items-center gap-2 rounded-ctl px-1.5 py-1.5 text-left text-[13px] outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{p.kind === 'modify' ? 'Change to a workflow' : 'New workflow'}</span>
                  <span className="block truncate text-[12px] text-muted-foreground">{p.actor} · {when(p.created_at)}</span>
                </span>
                <Badge variant={s.variant}>{s.word}</Badge>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
