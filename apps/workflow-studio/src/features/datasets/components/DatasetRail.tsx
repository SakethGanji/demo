/**
 * The dataset rail: catalog search + list, and the upload drop target.
 *
 * This replaces the reference UI's separate Catalog and Upload *pages* — picking
 * a dataset never leaves the page, which is the point of the single-surface
 * layout.
 */

import { Database, Search, Upload } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Badge } from '@/shared/components/ui/badge';
import type { DatasetInfo } from '../hooks/useDatasets';

function formatBytes(n?: number | null): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

interface DatasetRailProps {
  datasets: DatasetInfo[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  loading: boolean;
  onUploadClick: () => void;
}

export function DatasetRail({
  datasets,
  selectedId,
  onSelect,
  search,
  onSearchChange,
  loading,
  onUploadClick,
}: DatasetRailProps) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-[var(--surface)]/40">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <Database className="size-3.5 text-muted-foreground" />
        <span className="text-[13px] font-medium">Datasets</span>
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
          {datasets.length}
        </span>
      </div>

      <div className="px-2 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search datasets…"
            className="h-7 w-full rounded-md border border-border bg-background pr-2 pl-7 text-[12px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        </div>
      </div>

      {/* The rail owns its own scroll — #root is overflow:hidden. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {loading && <p className="px-1 py-2 text-[12px] text-muted-foreground">Loading…</p>}

        {!loading && datasets.length === 0 && (
          <p className="px-1 py-2 text-[12px] text-muted-foreground">
            {search ? 'No datasets match.' : 'No datasets yet.'}
          </p>
        )}

        {datasets.map((d) => {
          const active = d.id === selectedId;
          return (
            <button
              key={d.id}
              onClick={() => onSelect(d.id)}
              className={cn(
                'mb-0.5 w-full rounded-md px-2 py-1.5 text-left transition-colors',
                active ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/60',
              )}
            >
              <span
                className={cn(
                  'block truncate text-[12px]',
                  active ? 'font-medium text-primary' : 'text-foreground',
                )}
              >
                {d.name}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span className="tabular-nums">{d.row_count ?? 0} rows</span>
                <span>·</span>
                <span className="tabular-nums">{formatBytes(d.size_bytes)}</span>
                {d.documentation === 'partial' && (
                  <Badge variant="glass" className="h-4 px-1 text-[9px]">
                    partial
                  </Badge>
                )}
                {d.has_schema_drift && (
                  <Badge variant="destructive" className="h-4 px-1 text-[9px]">
                    drift
                  </Badge>
                )}
              </span>
            </button>
          );
        })}
      </div>

      <div className="border-t border-border p-2">
        <button
          onClick={onUploadClick}
          className="flex h-7 w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-border text-[12px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
        >
          <Upload className="size-3" />
          Upload dataset
        </button>
      </div>
    </aside>
  );
}
