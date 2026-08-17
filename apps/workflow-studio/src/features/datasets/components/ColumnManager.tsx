/**
 * SHAPE-R1 — the rail as a COLUMN MANAGER.
 *
 * The reference cockpit's rail is a flat, unsearchable list with a type chip
 * per column. At 241 columns that is a 6,000px scroll with no filter, no
 * grouping and no pin — and nothing anywhere on the screen tells you the file
 * is 96% sensitivities.
 *
 * Above 30 columns this replaces it: a filter, the pinned identity column
 * (R2), the type exceptions (R3), and the rest folded by longest common prefix
 * with a coverage bar instead of a repeated type badge.
 *
 * The coverage bar is the R3 substitution in miniature: when one dtype covers
 * ≥95% of columns, a `float64` badge printed 241 times is texture, not
 * information. What actually varies between those columns is how complete they
 * are, so that is what the bar shows.
 */

import { useMemo, useState } from 'react';
import { ChevronRight, Columns3, Pin, Search } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Footnote } from '@/shared/components/instrument/Typography';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage } from '@/shared/components/instrument/coverage';
import {
  addressColumns,
  detectIdentity,
  dominantType,
  groupByPrefix,
  isWideTable,
  middleTruncate,
  type ShapeColumn,
} from '@/shared/components/instrument/shape';
import { DtypeChip } from './lenses/primitives';
import { controlClass } from './fieldStyles';

interface ColumnManagerProps {
  columns: ShapeColumn[];
  /** Rows in the sheet — the denominator for every coverage bar. */
  rowCount: number | null;
  /** Column currently focused in the grid, if any. */
  selected: string | null;
  onSelect: (name: string) => void;
}

/** One column row: name, ordinal when ambiguous, and its completeness. */
function ColumnRow({
  column,
  label,
  ordinal,
  ambiguous,
  rowCount,
  showType,
  active,
  pinned,
  onSelect,
}: {
  column: ShapeColumn;
  label: string;
  ordinal: number;
  ambiguous: boolean;
  rowCount: number | null;
  showType: boolean;
  active: boolean;
  pinned?: boolean;
  onSelect: (name: string) => void;
}) {
  // R9: with no rows there is no denominator, so no bar is drawn at all —
  // MagnitudeBar renders "no rows" rather than dividing by zero.
  const cov = coverage(column.nonNullCount ?? 0, rowCount ?? 0);

  return (
    <button
      onClick={() => onSelect(column.name)}
      data-testid="column-manager-row"
      title={ambiguous ? `${column.name} — column ${ordinal}` : column.name}
      className={cn(
        'w-full rounded px-2 py-1 text-left transition-colors',
        active ? 'bg-accent' : 'hover:bg-muted/60',
      )}
    >
      <span className="flex items-center gap-1.5">
        {pinned && <Pin className="size-2.5 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate font-mono text-micro text-foreground">
          {middleTruncate(label, 26)}
        </span>
        {showType && <DtypeChip dtype={column.dtype} />}
      </span>
      {rowCount != null && rowCount > 0 && (
        <MagnitudeBar of={cov} className="mt-1 h-1" />
      )}
    </button>
  );
}

export function ColumnManager({ columns, rowCount, selected, onSelect }: ColumnManagerProps) {
  const [filter, setFilter] = useState('');
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  const wide = isWideTable(columns.length);
  const identity = useMemo(() => detectIdentity(columns), [columns]);
  const dominant = useMemo(() => dominantType(columns), [columns]);

  // R10: ordinal + name. Computed over the FULL column list, before filtering,
  // so an ordinal always means position in the sheet — not position in whatever
  // subset happens to be on screen.
  const addressed = useMemo(() => addressColumns(columns.map((c) => c.name)), [columns]);
  const labelFor = useMemo(() => {
    const m = new Map<number, { label: string; ordinal: number; ambiguous: boolean }>();
    addressed.forEach((a, i) => m.set(i, a));
    return m;
  }, [addressed]);

  const q = filter.trim().toLowerCase();
  const visible = useMemo(
    () =>
      columns
        .map((c, i) => ({ column: c, index: i }))
        .filter(({ column }) => !q || column.name.toLowerCase().includes(q)),
    [columns, q],
  );

  // Only fold into prefix groups when there is enough to fold and the user is
  // not already narrowing by hand — a filtered list is short enough to read.
  const groups = useMemo(
    () => (wide && !q ? groupByPrefix(visible.map((v) => v.column)) : null),
    [wide, q, visible],
  );

  const indexOf = useMemo(() => {
    const m = new Map<ShapeColumn, number>();
    columns.forEach((c, i) => m.set(c, i));
    return m;
  }, [columns]);

  const row = (column: ShapeColumn, pinned = false) => {
    const i = indexOf.get(column) ?? 0;
    const a = labelFor.get(i);
    return (
      <ColumnRow
        key={`${column.name}-${i}`}
        column={column}
        label={a?.label ?? column.name}
        ordinal={a?.ordinal ?? i + 1}
        ambiguous={a?.ambiguous ?? false}
        rowCount={rowCount}
        // R3: when one type dominates, only the exceptions keep a chip.
        showType={!dominant || dominant.exceptions.includes(column)}
        active={column.name === selected}
        pinned={pinned}
        onSelect={onSelect}
      />
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <Columns3 className="size-3.5 text-muted-foreground" />
        <span className="text-label font-medium">Columns</span>
        <span className="ml-auto text-small text-muted-foreground tabular-nums">
          {columns.length}
        </span>
      </div>

      <div className="px-2 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={`Filter ${columns.length} columns…`}
            aria-label="Filter columns"
            data-testid="column-filter"
            className={cn(controlClass, 'pr-2 pl-7')}
          />
        </div>
      </div>

      {/* R3 — one statement instead of the same badge N times. */}
      {dominant && (
        <Footnote className="px-3 pb-2" data-testid="type-strip">
          {dominant.count} of {columns.length} columns are{' '}
          <span className="font-mono text-foreground">{dominant.dtype}</span> —{' '}
          {(dominant.share * 100).toFixed(1)}%. Per-column chips are suppressed;{' '}
          {dominant.exceptions.length} exception
          {dominant.exceptions.length === 1 ? '' : 's'} keep theirs.
        </Footnote>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {visible.length === 0 && (
          <p className="px-1 py-2 text-body text-muted-foreground">No columns match.</p>
        )}

        {/* R2 — the identity column is pinned out of the scroll, so what tells
         * you WHICH ROW you are looking at never leaves the viewport. */}
        {identity && !q && (
          <div className="mb-2" data-testid="pinned-identity">
            <Footnote className="px-2 pb-0.5">
              {identity.basis === 'measured'
                ? 'Pinned · most distinct non-float column'
                : 'Pinned · first non-float column — run a profile to rank by distinctness'}
            </Footnote>
            {row(identity.column, true)}
          </div>
        )}

        {groups
          ? groups.map((g) => {
              const key = g.prefix || '(ungrouped)';
              const open = openGroups[key] ?? false;
              return (
                <div key={key} className="mb-1">
                  <button
                    onClick={() => setOpenGroups((s) => ({ ...s, [key]: !open }))}
                    className="flex w-full items-center gap-1 rounded px-2 py-1 text-left hover:bg-muted/60"
                    data-testid="column-group"
                  >
                    <ChevronRight
                      className={cn(
                        'size-2.5 shrink-0 text-muted-foreground transition-transform',
                        open && 'rotate-90',
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate font-mono text-micro text-foreground">
                      {g.prefix || 'other'}
                    </span>
                    <span className="text-footnote text-muted-foreground tabular-nums">
                      {g.columns.length}
                    </span>
                  </button>
                  {open && <div className="ml-2">{g.columns.map((c) => row(c))}</div>}
                </div>
              );
            })
          : visible.map(({ column }) => row(column))}
      </div>
    </div>
  );
}
