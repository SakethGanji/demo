/**
 * The active-filter row that sits between the dataset header and the grid.
 *
 * This is the most-looked-at line on the cockpit: it is the answer to "what am
 * I actually looking at, and how much of the sheet is it?". The prototype puts
 * it directly above the grid for that reason, and pairs it with the matched
 * count so the narrowing is never invisible.
 *
 * Two governance facts are rendered here rather than discovered as errors:
 *
 *  - **A masked column cannot be filtered or sorted** (`400
 *    sensitive-column-not-filterable`), because a steerable COUNT is a binary
 *    search over the hidden value. Masked columns are therefore absent from the
 *    add-filter list and the reason is stated, not implied by an empty menu.
 *  - **The acting role is shown beside the count**, because the same query
 *    returns different values to different seats and a number with no seat
 *    attached invites the wrong comparison.
 */

import { Plus, X } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Identifier } from '@/shared/components/instrument/Typography';
import { compact } from '@/shared/lib/format';

export interface QueryToken {
  column: string;
  op: string;
  value: string;
}

interface QueryTokenRowProps {
  tokens: QueryToken[];
  onRemove: (index: number) => void;
  onAdd: () => void;
  /** Rows the current filter matched. Null when nothing is filtering. */
  matched: number | null;
  total: number | null;
  maskedColumns: string[];
  /** The acting seat's role per the service, or null while it resolves. */
  role: string | null;
}

export function QueryTokenRow({
  tokens,
  onRemove,
  onAdd,
  matched,
  total,
  maskedColumns,
  role,
}: QueryTokenRowProps) {
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 px-3 pb-2"
      data-testid="query-token-row"
    >
      {tokens.map((t, i) => (
        <span
          key={`${t.column}-${i}`}
          data-testid="query-token"
          className="flex items-center gap-1.5 rounded bg-secondary py-0.5 pr-1 pl-2 shadow-[var(--hi)]"
        >
          <Identifier className="text-micro text-foreground">{t.column}</Identifier>
          <span className="text-micro text-muted-foreground">{t.op}</span>
          <Identifier className="text-micro text-foreground">{t.value}</Identifier>
          <button
            onClick={() => onRemove(i)}
            aria-label={`Remove filter on ${t.column}`}
            className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-2.5" />
          </button>
        </span>
      ))}

      <button
        onClick={onAdd}
        data-testid="query-add-filter"
        className="flex items-center gap-1 rounded px-1.5 py-0.5 text-micro text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      >
        <Plus className="size-2.5" />
        add filter
      </button>

      <span className="ml-auto flex items-center gap-3 text-footnote text-muted-foreground">
        <span className="flex items-center gap-1">
          role <span className="text-foreground">{role ?? 'resolving…'}</span>
          {maskedColumns.length > 0 && (
            <span
              title={`Masked, and therefore not filterable or sortable: ${maskedColumns.join(', ')}`}
            >
              · {maskedColumns.length} masked
            </span>
          )}
        </span>
        {matched != null && total != null && (
          <span className="tabular-nums">
            matched <span className="font-medium text-foreground">{matched.toLocaleString()}</span>
            <span className={cn(matched === total && 'opacity-60')}> / {compact(total)}</span>
          </span>
        )}
      </span>
    </div>
  );
}
