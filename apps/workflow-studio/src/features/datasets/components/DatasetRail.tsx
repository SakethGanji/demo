/**
 * The dataset rail: catalog search + list, and the upload drop target.
 *
 * This replaces the reference UI's separate Catalog and Upload *pages* — picking
 * a dataset never leaves the page, which is the point of the single-surface
 * layout.
 *
 * Density is the job here. A flat list of names answers "what exists"; this
 * answers "which one do I want", which needs the domain it belongs to, how big
 * it is, how sensitive it is and whether it needs attention — all at 11px, in
 * one row, without a single border. Grouping and elevation do the work that
 * rules would otherwise do (rule 3), and the row's health takes a SHAPE beside
 * a value rather than a bare coloured dot (rule 6).
 */

import { Database, Search, Upload } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Footnote } from '@/shared/components/instrument/Typography';
import { Sparkline } from '@/shared/components/instrument/charts';
import { compact, formatBytes } from '@/shared/lib/format';
import type { DatasetInfo } from '../hooks/useDatasets';
import { controlClass } from './fieldStyles';

interface DatasetRailProps {
  datasets: DatasetInfo[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  loading: boolean;
  onUploadClick: () => void;
}

/** Short code for a domain, the way a desk refers to itself. */
function domainCode(domain?: string | null): string {
  if (!domain) return '—';
  const head = domain.split(/[·/,]/)[0].trim();
  const words = head.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.map((w) => w[0]).join('').slice(0, 3).toUpperCase();
}

/**
 * Classification is a LABEL, not a control — it enforces nothing. It is styled
 * as recessive text rather than a badge so it cannot be mistaken for a lock.
 * The thing that actually masks is column sensitivity, and that lives on the
 * column, not here.
 */
function classificationTone(c?: string | null): string {
  if (c === 'restricted') return 'text-[var(--st-serious)]';
  if (c === 'confidential') return 'text-[var(--st-warn)]';
  return 'text-muted-foreground';
}

function attentionShape(d: DatasetInfo): { className: string; title: string } | null {
  if (d.has_schema_drift) {
    return { className: 'rotate-45 rounded-[1px] bg-[var(--st-serious)]', title: 'schema drift' };
  }
  if (d.documentation === 'none') {
    return {
      className: '[clip-path:polygon(50%_0%,100%_100%,0%_100%)] bg-[var(--st-warn)]',
      title: 'undocumented',
    };
  }
  return null;
}

function DatasetRow({
  dataset,
  active,
  onSelect,
}: {
  dataset: DatasetInfo;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const attention = attentionShape(dataset);

  return (
    <button
      onClick={() => onSelect(dataset.id)}
      data-testid="rail-dataset"
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group w-full rounded-md px-2 py-1.5 text-left transition-colors',
        // Repeated active state: value + elevation, never the accent (rule 1).
        active ? 'bg-accent shadow-[var(--hi)]' : 'hover:bg-muted/50',
      )}
    >
      <span className="flex items-baseline gap-1.5">
        {/* Rule 6 — a shape, and it is absent when there is nothing to say. */}
        <span
          aria-hidden="true"
          title={attention?.title}
          className={cn('size-[6px] shrink-0 translate-y-[-1px]', attention?.className ?? 'opacity-0')}
        />
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-body',
            active ? 'font-medium text-foreground' : 'text-foreground/90',
          )}
          title={dataset.name}
        >
          {dataset.name}
        </span>
        <span className="shrink-0 text-micro text-muted-foreground tabular-nums">
          {compact(dataset.row_count)}
        </span>
      </span>

      <span className="mt-0.5 flex items-center gap-1.5">
        <span className="font-mono text-footnote text-muted-foreground">
          {domainCode(dataset.domain)}
        </span>
        <span className={cn('text-footnote', classificationTone(dataset.classification))}>
          {dataset.classification ?? 'unclassified'}
        </span>
        <span className="text-footnote text-muted-foreground/70 tabular-nums">
          {formatBytes(dataset.size_bytes)}
        </span>
        <span className="ml-auto opacity-60">
          <Sparkline points={sparkFor(dataset)} width={40} height={11} />
        </span>
      </span>
    </button>
  );
}

/**
 * A shape for the row, derived from the one number we actually have.
 *
 * `DatasetInfo` carries no history, so this is NOT a trend and must not be read
 * as one — it is the current row count against the version count, which is why
 * it is drawn tiny, unlabelled and at 60% opacity. When there is only one
 * version there is nothing to draw and `Sparkline` withholds it.
 */
function sparkFor(d: DatasetInfo): number[] {
  const versions = d.current_version ?? 1;
  const rows = d.row_count ?? 0;
  if (versions < 2 || rows === 0) return [];
  return Array.from({ length: Math.min(versions, 8) }, (_, i) =>
    Math.round((rows * (i + 1)) / Math.min(versions, 8)),
  );
}

const QUICK_FILTERS = ['All', 'Restricted', 'Needs docs', 'Drift'] as const;

export function DatasetRail({
  datasets,
  selectedId,
  onSelect,
  search,
  onSearchChange,
  loading,
  onUploadClick,
}: DatasetRailProps) {
  // Grouped by domain, because "which desk owns this" is how people actually
  // navigate a catalog — an alphabetical list of 142 names is a lookup table,
  // not a rail.
  const groups = new Map<string, DatasetInfo[]>();
  for (const d of datasets) {
    const key = d.domain?.split(/[·/,]/)[0].trim() || 'Unfiled';
    const list = groups.get(key);
    if (list) list.push(d);
    else groups.set(key, [d]);
  }
  const ordered = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);

  return (
    <>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <Database className="size-3.5 text-muted-foreground" />
        <span className="text-label font-medium">Datasets</span>
        <span className="ml-auto text-small text-muted-foreground tabular-nums">
          {datasets.length}
        </span>
      </div>

      <div className="px-2 pb-1.5">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Filter datasets…"
            data-testid="rail-search"
            className={cn(controlClass, 'pr-7 pl-7')}
          />
          <kbd className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 rounded bg-secondary px-1 font-mono text-footnote text-muted-foreground shadow-[var(--hi)]">
            /
          </kbd>
        </div>
      </div>

      {/* Quick filters are repeated state, so they take value, not the accent. */}
      <div className="flex flex-wrap gap-1 px-2 pb-2">
        {QUICK_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => onSearchChange(f === 'All' ? '' : f.toLowerCase())}
            className={cn(
              'rounded px-1.5 py-0.5 text-footnote transition-colors',
              (f === 'All' && !search) || search === f.toLowerCase()
                ? 'bg-secondary font-medium text-foreground shadow-[var(--hi)]'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {f}
          </button>
        ))}
      </div>

      {/* The rail owns its own scroll — #root is overflow:hidden. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {loading && <p className="px-1 py-2 text-body text-muted-foreground">Loading…</p>}

        {!loading && datasets.length === 0 && (
          <p className="px-1 py-2 text-body text-muted-foreground">
            {search ? 'No datasets match.' : 'No datasets yet.'}
          </p>
        )}

        {ordered.map(([domain, items]) => (
          <div key={domain} className="mb-2">
            <div className="flex items-baseline gap-1.5 px-2 pb-0.5">
              <span className="text-footnote font-medium text-muted-foreground">{domain}</span>
              <span className="text-footnote text-muted-foreground/60 tabular-nums">
                {items.length}
              </span>
            </div>
            {items.map((d) => (
              <DatasetRow
                key={d.id}
                dataset={d}
                active={d.id === selectedId}
                onSelect={onSelect}
              />
            ))}
          </div>
        ))}
      </div>

      <button
        onClick={onUploadClick}
        className="mx-2 mb-1 flex h-7 items-center justify-center gap-1.5 rounded-md bg-secondary text-body text-foreground shadow-[var(--hi)] transition-colors hover:bg-accent"
      >
        <Upload className="size-3" />
        Upload dataset
      </button>

      {/* The footnote register, in its dedicated bar. */}
      <Footnote className="flex items-center gap-3 px-3 py-1.5 shadow-[inset_0_1px_0_var(--r1)]">
        <span className="flex items-center gap-1">
          <Key>J</Key>
          <Key>K</Key> move
        </span>
        <span className="flex items-center gap-1">
          <Key>↵</Key> open
        </span>
        <span className="flex items-center gap-1">
          <Key>U</Key> upload
        </span>
      </Footnote>
    </>
  );
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded bg-secondary px-1 font-mono text-footnote text-foreground shadow-[var(--hi)]">
      {children}
    </kbd>
  );
}
