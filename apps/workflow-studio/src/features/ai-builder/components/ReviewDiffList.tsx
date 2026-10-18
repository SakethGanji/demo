import { Minus, PenLine, Plus } from 'lucide-react';
import type { DiffMark } from '@/features/workflow-editor/stores/historyStore';
import { cn } from '@/shared/lib/utils';
import { diffItems, fieldLabel } from '../proposalMarks';
import type { ProposalDiff } from '../types';

const KIND: Record<DiffMark, { word: string; icon: typeof Plus; tone: string }> = {
  added: { word: 'Added', icon: Plus, tone: 'bg-success-soft text-success-text' },
  changed: { word: 'Changed', icon: PenLine, tone: 'bg-warning-soft text-warning-text' },
  removed: { word: 'Removed', icon: Minus, tone: 'bg-destructive-soft text-destructive' },
};

/** What the change does to the workflow, one row per step. Clicking a row brings that step into view on the canvas. */
export function ReviewDiffList({ diff, selected, onSelect }: {
  diff: ProposalDiff | null;
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const items = diffItems(diff);
  const extras: string[] = [];
  if (diff) {
    const link = (n: number) => `${n} ${n === 1 ? 'link' : 'links'} between steps`;
    if (diff.connections.added.length) extras.push(`${link(diff.connections.added.length)} added`);
    if (diff.connections.removed.length) extras.push(`${link(diff.connections.removed.length)} removed`);
    if (diff.tests.added.length) extras.push(`${diff.tests.added.length} ${diff.tests.added.length === 1 ? 'test' : 'tests'} added`);
    if (diff.interface.changed) extras.push('The form or chat screen changed');
  }
  return (
    <div className="flex flex-col gap-1.5" data-testid="ai-review-diff-list">
      {items.length === 0 && <div className="text-[12.5px] text-muted-foreground">No steps were added, changed or removed.</div>}
      <ul className="flex flex-col">
        {items.map((it) => {
          const k = KIND[it.kind];
          const Icon = k.icon;
          return (
            <li key={`${it.kind}-${it.name}`}>
              <button
                type="button"
                onClick={() => onSelect(it.name)}
                data-testid={`ai-review-diff-${it.name}`}
                data-kind={it.kind}
                aria-pressed={selected === it.name}
                className={cn(
                  'flex w-full items-start gap-2 rounded-ctl px-1.5 py-1.5 text-left text-[13px] outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus',
                  selected === it.name && 'bg-(--hover-fill)',
                )}
              >
                <span className={cn('mt-px inline-flex h-5 shrink-0 items-center gap-1 rounded-chip px-1.5 text-[11.5px] font-medium', k.tone)}>
                  <Icon className="size-3" /> {k.word}
                </span>
                <span className="min-w-0">
                  <span className="font-medium">{it.name}</span>
                  {it.kind === 'changed' && it.fields.length > 0 && (
                    <span className="block text-[12px] leading-4 text-muted-foreground">
                      {it.fields.map(fieldLabel).join(', ')}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {extras.length > 0 && <ul className="pl-1.5 text-[12px] leading-5 text-muted-foreground">{extras.map((e) => <li key={e}>{e}</li>)}</ul>}
    </div>
  );
}
