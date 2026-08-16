/**
 * The persistent data surface — the table is always on screen; lenses change
 * what you do *to* it rather than replacing it.
 *
 * Cursor paging: the cursor is opaque and bound to (version, spec). Any change
 * to the version, sheet or spec resets it to null; replaying a stale cursor
 * returns `400 invalid-cursor`.
 */

import { ChevronLeft, ChevronRight, EyeOff } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table';
import { Badge } from '@/shared/components/ui/badge';
import { cn } from '@/shared/lib/utils';
import type { QueryPage, SheetSummary, VersionSummary } from '../hooks/useDatasets';

interface DataSurfaceProps {
  versions: VersionSummary[];
  sheets: SheetSummary[];
  version: number | null;
  sheet: string | null;
  onVersionChange: (v: number) => void;
  onSheetChange: (s: string) => void;
  page?: QueryPage;
  loading: boolean;
  error: string | null;
  onNext: () => void;
  onPrev: () => void;
  canPrev: boolean;
}

const selectClass =
  'h-7 rounded-md border border-border bg-background px-2 text-[12px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50';

export function DataSurface({
  versions,
  sheets,
  version,
  sheet,
  onVersionChange,
  onSheetChange,
  page,
  loading,
  error,
  onNext,
  onPrev,
  canPrev,
}: DataSurfaceProps) {
  const rows = page?.items ?? [];
  const masked = new Set(page?.masked_columns ?? []);
  // Column order comes from the sheet's declared schema when available, so it
  // stays stable across pages even if a row happens to omit a key.
  const declared = sheet ? sheets.find((s) => s.name === sheet)?.columns?.map((c) => c.name) : undefined;
  const columns = declared?.length ? declared : Object.keys(rows[0] ?? {});

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Floating control bar — the studio's pill-toolbar idiom, not a header band. */}
      <div className="flex items-center gap-2 px-3 py-2">
        <select
          value={version ?? ''}
          onChange={(e) => onVersionChange(Number(e.target.value))}
          className={selectClass}
          aria-label="Version"
        >
          {versions.map((v) => (
            <option key={v.version_number} value={v.version_number}>
              v{v.version_number}
              {v.row_count != null ? ` · ${v.row_count} rows` : ''}
            </option>
          ))}
        </select>

        {/* Multi-sheet workbooks require an explicit sheet — the API refuses to guess. */}
        <select
          value={sheet ?? ''}
          onChange={(e) => onSheetChange(e.target.value)}
          className={selectClass}
          aria-label="Sheet"
        >
          {sheets.map((s) => (
            <option key={s.sheet_key} value={s.name}>
              {s.name}
              {s.row_count != null ? ` · ${s.row_count} rows` : ''}
            </option>
          ))}
        </select>

        <div className="ml-auto flex items-center gap-2">
          {masked.size > 0 && (
            <Badge variant="glass" className="gap-1">
              <EyeOff className="size-3" />
              {masked.size} masked
            </Badge>
          )}
          <span data-testid="row-count" className="text-[11px] text-muted-foreground tabular-nums">
            {page?.total != null ? `${page.total} rows` : `${rows.length} rows`}
          </span>
          <button
            onClick={onPrev}
            disabled={!canPrev}
            className="flex size-6 items-center justify-center rounded-md border border-border transition-colors hover:bg-muted disabled:opacity-40"
            aria-label="Previous page"
          >
            <ChevronLeft className="size-3" />
          </button>
          <button
            onClick={onNext}
            disabled={!page?.next_cursor}
            className="flex size-6 items-center justify-center rounded-md border border-border transition-colors hover:bg-muted disabled:opacity-40"
            aria-label="Next page"
          >
            <ChevronRight className="size-3" />
          </button>
        </div>
      </div>

      {error && (
        <div
          data-testid="data-error"
          className="mx-3 mb-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12px] text-destructive"
        >
          {error}
        </div>
      )}

      {!error && columns.length === 0 && !loading && (
        <div className="flex flex-1 items-center justify-center text-[12px] text-muted-foreground">
          No rows to show.
        </div>
      )}

      {columns.length > 0 && (
        <Table containerClassName={cn('border-t border-border', loading && 'opacity-60')}>
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c}>
                  <span className="flex items-center gap-1">
                    {c}
                    {masked.has(c) && <EyeOff className="size-3 text-muted-foreground/70" />}
                  </span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) => (
              <TableRow key={i}>
                {columns.map((c) => {
                  const v = (row as Record<string, unknown>)[c];
                  return (
                    <TableCell
                      key={c}
                      className={cn(
                        masked.has(c) && 'text-muted-foreground/70 italic',
                        typeof v === 'number' && 'text-right tabular-nums',
                      )}
                    >
                      {v === null || v === undefined ? (
                        <span className="text-muted-foreground/50">—</span>
                      ) : (
                        String(v)
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
