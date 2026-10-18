import { AlertTriangle, ArrowUpRight, CheckCircle2, ClipboardList, GitCompare, Loader2 } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Callout } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { ChatCardShell } from './ChatCardShell';
import type { AIChatCard } from '../../types/workflow';

type SpecCard = Extract<AIChatCard, { kind: 'spec' }>;
type ProposalCard = Extract<AIChatCard, { kind: 'proposal' }>;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** A plan the chat wrote for a new workflow. Approving locks its tests and builds it; nothing is saved until the result is kept. */
export function SpecChatCard({ card, onApprove, onOpen }: { card: SpecCard; onApprove: () => void; onOpen: () => void }) {
  const working = card.phase === 'approving' || card.phase === 'building';
  return (
    <ChatCardShell data-testid="ai-chat-spec-card" data-phase={card.phase}>
      <div className="flex items-center gap-1.5 font-medium text-foreground"><ClipboardList className="size-3.5 text-muted-foreground" /> {card.title}</div>
      {card.summary && <p className="mt-1 text-muted-foreground">{card.summary}</p>}
      <p className="mt-1 text-muted-foreground">
        {plural(card.rules, 'rule', 'rules')} · {plural(card.steps, 'step', 'steps')} · {plural(card.tests, 'test', 'tests')}
      </p>
      {card.errors > 0 && (
        <Callout tone="warn" icon={<AlertTriangle />} className="mt-1.5" data-testid="ai-chat-spec-issues">
          The plan has {plural(card.errors, 'problem', 'problems')} to fix first. Open it to fix {card.errors === 1 ? 'it' : 'them'}.
        </Callout>
      )}
      {card.error && <Callout tone="error" icon={<AlertTriangle />} className="mt-1.5" role="alert" data-testid="ai-chat-spec-error">{card.error}</Callout>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={onApprove} disabled={working || card.errors > 0} data-testid="ai-chat-spec-approve">
          {working ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
          {card.phase === 'approving' ? 'Approving…' : card.phase === 'building' ? 'Building and testing…' : 'Approve and build'}
        </Button>
        <Button size="sm" variant="outline" onClick={onOpen} disabled={working} data-testid="ai-chat-spec-open">Open the plan</Button>
      </div>
    </ChatCardShell>
  );
}

/** A change the chat built on a copy. The review (what changed, the tests, Keep) is on the AI build page. */
export function ProposalChatCard({ card, onOpen }: { card: ProposalCard; onOpen: () => void }) {
  const ready = card.status === 'ready';
  const { added, changed, removed } = card.counts;
  return (
    <ChatCardShell data-testid="ai-chat-proposal-card" data-status={card.status} data-passed={card.passed ? 'true' : 'false'}>
      <div className={cn('flex items-center gap-1.5 font-medium', ready && !card.passed ? 'text-warning-text' : 'text-foreground')}>
        <GitCompare className="size-3.5 text-muted-foreground" />
        {ready ? (card.passed ? 'A change is ready to review' : 'A change is ready, but some tests fail') : 'The change could not be built'}
      </div>
      {ready && (
        <p className="mt-1 text-muted-foreground">
          {plural(added, 'step', 'steps')} added · {changed} changed · {removed} removed. Nothing is saved until you keep it.
        </p>
      )}
      <div className="mt-2">
        <Button size="sm" variant={ready && card.passed ? 'default' : 'secondary'} onClick={onOpen} data-testid="ai-chat-open-review">
          Review the change <ArrowUpRight />
        </Button>
      </div>
    </ChatCardShell>
  );
}
