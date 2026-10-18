import { useMemo, useState } from 'react';
import { ChevronDown, Search, Workflow } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover';
import { Input } from '@/shared/components/ui/input';
import { Badge } from '@/shared/components/ui/badge';
import { cn } from '@/shared/lib/utils';
import type { SubworkflowListItem } from '../../../lib/subworkflowsApi';

interface Props {
  value: string;
  items: SubworkflowListItem[] | undefined;
  loading: boolean;
  /** the workflow being edited: never offered (a workflow cannot call itself) */
  currentWorkflowId?: string | null;
  onPick: (item: SubworkflowListItem) => void;
}

/** Searchable list of the project's workflows with their callable state; a workflow that cannot be called is disabled and says why. */
export function SubworkflowPicker({ value, items, loading, currentWorkflowId, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');

  const available = useMemo(() => (items ?? []).filter((w) => w.id !== currentWorkflowId), [items, currentWorkflowId]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return available;
    return available.filter((w) => w.name.toLowerCase().includes(needle) || (w.description ?? '').toLowerCase().includes(needle));
  }, [available, q]);
  const selected = available.find((w) => w.id === value);

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(''); }}>
      <PopoverTrigger
        data-testid="subwf-picker"
        className="flex h-8 w-full items-center justify-between gap-1.5 rounded-ctl bg-recess pr-2 pl-2.5 text-left text-[13px] outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
      >
        <span className={cn('flex min-w-0 items-center gap-1.5', !value && 'text-muted-foreground')}>
          <Workflow size={13} className="shrink-0 text-hue-flow" aria-hidden />
          <span className="truncate" data-testid="subwf-picker-value">
            {selected ? selected.name : value ? value : loading ? 'Loading workflows...' : 'Select a workflow...'}
          </span>
        </span>
        <ChevronDown size={14} className="shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent className="w-(--anchor-width) min-w-72 p-1.5" align="start">
        <div className="relative mb-1.5">
          <Search size={13} className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search workflows"
            className="h-7 pl-7"
            data-testid="subwf-picker-search"
          />
        </div>
        <div className="max-h-64 overflow-y-auto" role="listbox" aria-label="Workflows">
          {shown.length === 0 && (
            <p className="px-2 py-3 text-center text-[12px] text-muted-foreground" data-testid="subwf-picker-empty">
              {loading ? 'Loading...' : 'No workflows match.'}
            </p>
          )}
          {shown.map((w) => (
            <button
              key={w.id}
              type="button"
              role="option"
              aria-selected={w.id === value}
              disabled={!w.callable}
              data-testid={`subwf-picker-option-${w.id}`}
              data-callable={w.callable ? 'true' : 'false'}
              onClick={() => {
                onPick(w);
                setOpen(false);
              }}
              className={cn(
                'flex w-full flex-col gap-0.5 rounded-chip px-2 py-1.5 text-left text-[13px] outline-none',
                w.callable ? 'row-hover focus-visible:bg-raise' : 'cursor-not-allowed opacity-60',
                w.id === value && 'bg-raise shadow-raise-sm',
              )}
            >
              <span className="flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate font-medium">{w.name}</span>
                {w.published_version != null ? (
                  <Badge variant="neutral">v{w.published_version}</Badge>
                ) : (
                  <Badge variant="warning">draft only</Badge>
                )}
              </span>
              {w.inputs.length > 0 && (
                <span className="truncate text-[11.5px] text-muted-foreground">
                  takes {w.inputs.map((f) => f.name).join(', ')}
                </span>
              )}
              {!w.callable && w.reasons.length > 0 && (
                <span className="text-[11.5px] text-destructive" data-testid={`subwf-picker-reason-${w.id}`}>
                  {w.reasons.join(' ')}
                </span>
              )}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
