/**
 * The persistent data surface — the table is always on screen; lenses change
 * what you do *to* it rather than replacing it.
 *
 * Cursor paging: the cursor is opaque and bound to (version, spec). Any change
 * to the version, sheet or spec resets it to null; replaying a stale cursor
 * returns `400 invalid-cursor`.
 *
 * SHAPE RULES APPLIED HERE (see `instrument/shape.ts` for why each exists):
 *
 *  - R1/R2 — above 30 columns the table stops trying to fit. The reference grid
 *    was `table{width:100%}` with no colgroup and `nowrap` cells, so at 241
 *    columns it did not scroll, it CRUSHED, and cells overflowed instead of
 *    truncating. Here the table sizes to content and the container scrolls, the
 *    identity column is pinned left so it never leaves the viewport, and a
 *    ruler states what you are looking at.
 *  - R3 — when one dtype covers ≥95% of columns, the per-header chip becomes a
 *    single statement above the grid instead of the same badge N times.
 *  - R9 — zero rows is a STATE, not a failure. The reference rendered the same
 *    "no data" for an empty version as for a failed load.
 *  - R10 — columns are addressed by ordinal + name, values middle-truncate, and
 *    ROW HEIGHT NEVER VARIES WITH CONTENT. A 1,400-char cell must not make one
 *    row forty times taller than its neighbours.
 */

import { useMemo } from 'react';
import { EyeOff, Lock } from 'lucide-react';
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
import { Footnote } from '@/shared/components/instrument/Typography';
import {
  addressColumns,
  detectIdentity,
  dominantType,
  isWideTable,
  type ShapeColumn,
} from '@/shared/components/instrument/shape';
import type { QueryPage, SheetSummary, VersionSummary } from '../hooks/useDatasets';
import { CellValue } from './CellValue';
import { ScopePicker } from './ScopePicker';
import { PagerButton } from './PagerButton';

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
  /** Absolute index of the first row on this page, for the R10 ordinal gutter. */
  rowOffset: number;
}

/** Cells clamp to one line at a fixed height — R10. */
const CELL = 'h-7 max-w-[280px] truncate whitespace-nowrap';

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
  rowOffset,
}: DataSurfaceProps) {
  const rows = page?.items ?? [];
  const masked = new Set(page?.masked_columns ?? []);

  // Column order comes from the sheet's declared schema when available, so it
  // stays stable across pages even if a row happens to omit a key.
  const sheetRow = sheet ? sheets.find((s) => s.name === sheet) : undefined;
  const declared = sheetRow?.columns;
  const columnNames = declared?.length ? declared.map((c) => c.name) : Object.keys(rows[0] ?? {});

  const shapeColumns: ShapeColumn[] = columnNames.map((name) => ({
    name,
    dtype: declared?.find((c) => c.name === name)?.dtype ?? null,
  }));

  const wide = isWideTable(columnNames.length);
  const dominant = dominantType(shapeColumns);
  const identity = detectIdentity(shapeColumns);
  const addressed = addressColumns(columnNames);

  /**
   * Which columns read as a small closed set — the ones that earn a status
   * shape in the grid.
   *
   * Derived from the CARDINALITY of the loaded page, not from dtype: `email`
   * and `signup_id` are both text, and dotting 512 distinct emails would turn
   * the rhythm into noise. Derived from the page rather than a profile because
   * profiling is not automatic on upload, and a grid that only comes alive
   * after someone runs a profile is a grid that mostly looks dead.
   *
   * This is a PRESENTATION heuristic over the rows on screen. It never feeds a
   * statistic — nothing here is counted, only marked.
   */
  const categorical = useMemo(() => {
    const out = new Set<string>();
    if (rows.length < 4) return out;
    for (const c of columnNames) {
      const seen = new Set<string>();
      let usable = true;
      for (const r of rows) {
        const v = (r as Record<string, unknown>)[c];
        if (v === null || v === undefined) continue;
        if (typeof v === 'number') { usable = false; break; }
        const t = String(v);
        if (t.length > 24) { usable = false; break; }
        seen.add(t);
        if (seen.size > 8) { usable = false; break; }
      }
      if (usable && seen.size > 0 && seen.size <= 8) out.add(c);
    }
    return out;
  }, [rows, columnNames]);

  const total = page?.total ?? rows.length;
  // R9 — an empty version is a legitimate state. Distinguish it from a failure
  // and from "still loading", and never divide by its row count.
  const emptyVersion = !loading && !error && columnNames.length > 0 && rows.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Floating control bar — the studio's pill-toolbar idiom, not a header band. */}
      <div className="flex items-center gap-2 px-3 py-2">
        <ScopePicker
          label="Version"
          testid="version-picker"
          value={version != null ? String(version) : ''}
          onValueChange={(v) => onVersionChange(Number(v))}
          options={versions.map((v) => ({
            value: String(v.version_number),
            label: `v${v.version_number}`,
            hint: v.row_count != null ? `${v.row_count.toLocaleString()} rows` : undefined,
          }))}
        />

        {/* Multi-sheet workbooks require an explicit sheet — the API refuses to guess. */}
        <ScopePicker
          label="Sheet"
          testid="sheet-picker"
          value={sheet ?? ''}
          onValueChange={onSheetChange}
          options={sheets.map((s) => ({
            value: s.name,
            label: s.name,
            hint: s.row_count != null ? `${s.row_count.toLocaleString()} rows` : undefined,
          }))}
        />

        <div className="ml-auto flex items-center gap-2">
          {masked.size > 0 && (
            <Badge variant="glass" className="gap-1" data-testid="grid-masked-count">
              <EyeOff className="size-3" />
              {masked.size} masked
            </Badge>
          )}
          <span data-testid="row-count" className="text-small text-muted-foreground tabular-nums">
            {page?.total != null ? `${page.total} rows` : `${rows.length} rows`}
          </span>
          <PagerButton direction="prev" onClick={onPrev} disabled={!canPrev} />
          <PagerButton direction="next" onClick={onNext} disabled={!page?.next_cursor} />
        </div>
      </div>

      {/* R3 + R2 — one type statement and one scope statement, instead of a
       * badge repeated once per column and no idea where you are. */}
      {(dominant || wide) && (
        <Footnote className="flex items-center gap-3 px-3 pb-1.5" data-testid="grid-ruler">
          {dominant && (
            <span>
              {dominant.count} of {columnNames.length} columns are{' '}
              <span className="font-mono text-foreground">{dominant.dtype}</span> ·{' '}
              {(dominant.share * 100).toFixed(1)}%
            </span>
          )}
          {wide && (
            <span>
              {columnNames.length} columns — scroll horizontally
              {identity ? (
                <>
                  {' · '}
                  <span className="font-mono text-foreground">{identity.column.name}</span>{' '}
                  {/* Say which kind of pin this is. Without a profile there is
                    * no distinctness to rank by, so the pick is the rule's
                    * positional fallback — useful, but not a measured claim. */}
                  {identity.basis === 'measured' ? 'pinned' : 'pinned by position — no profile yet'}
                </>
              ) : null}
            </span>
          )}
        </Footnote>
      )}

      {error && (
        <div
          data-testid="data-error"
          className="mx-3 mb-2 rounded-md bg-destructive/10 px-3 py-2 text-body text-destructive"
        >
          {error}
        </div>
      )}

      {!error && columnNames.length === 0 && !loading && (
        <div className="flex flex-1 items-center justify-center text-body text-muted-foreground">
          No rows to show.
        </div>
      )}

      {columnNames.length > 0 && (
        <Table
          containerClassName={cn('border-t border-border', loading && 'opacity-60')}
          // R1: size to content and let the container scroll. `w-full` is what
          // made the reference crush rather than scroll above ~30 columns.
          className={wide ? 'w-max min-w-full' : undefined}
        >
          <TableHeader>
            <TableRow>
              {/* Row-ordinal gutter. Deliberately NOT a data column: tests
               * select data cells by `data-testid`, never by position. */}
              <TableHead
                data-slot="row-gutter"
                className="sticky left-0 z-20 w-11 bg-card text-right"
              >
                #
              </TableHead>
              {addressed.map((a) => {
                const isIdentity = wide && identity?.column.name === a.name;
                return (
                  <TableHead
                    key={`${a.name}-${a.ordinal}`}
                    data-column={a.name}
                    title={a.ambiguous ? `${a.name} — column ${a.ordinal}` : a.name}
                    className={cn('whitespace-nowrap', isIdentity && 'sticky left-11 z-20 bg-card')}
                  >
                    <span className="flex items-center gap-1">
                      {/* R10 — the ordinal appears only when the name is
                       * genuinely ambiguous; it would be noise otherwise. */}
                      {a.ambiguous ? a.label : a.name}
                      {masked.has(a.name) && <Lock className="size-2.5 text-muted-foreground/70" />}
                    </span>
                  </TableHead>
                );
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) => (
              <TableRow key={i}>
                <TableCell
                  data-slot="row-gutter"
                  className="sticky left-0 z-10 w-11 bg-background text-right text-micro text-muted-foreground tabular-nums"
                >
                  {rowOffset + i + 1}
                </TableCell>
                {columnNames.map((c, ci) => {
                  const v = (row as Record<string, unknown>)[c];
                  const isIdentity = wide && identity?.column.name === c;
                  const numeric = typeof v === 'number';
                  const text = v === null || v === undefined ? null : String(v);
                  return (
                    <TableCell
                      key={c}
                      // Named so a test can say "the first data cell" without
                      // counting past the ordinal gutter.
                      data-testid={ci === 0 ? 'first-cell' : 'cell'}
                      title={text ?? undefined}
                      className={cn(
                        CELL,
                        numeric && 'text-right tabular-nums',
                        isIdentity && 'sticky left-11 z-10 bg-background font-mono',
                      )}
                    >
                      <CellValue
                        value={v}
                        categorical={categorical.has(c)}
                        masked={masked.has(c)}
                      />
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* R9 — say which state this is. An empty version and a failed load must
       * never render the same thing. */}
      {emptyVersion && (
        <div className="px-3 py-4" data-testid="empty-version">
          <p className="text-body text-foreground">0 rows — this version is empty.</p>
          <Footnote className="mt-1">
            The schema above is declared and real. Every ratio is suppressed rather than divided
            by zero.
          </Footnote>
        </div>
      )}

      {rows.length > 0 && (
        <Footnote className="flex items-center gap-2 px-3 py-1.5">
          <span className="tabular-nums">
            Showing {rowOffset + 1}–{rowOffset + rows.length} of {total.toLocaleString()}
          </span>
          {/* Cursor paging: the count is exact because `total` is returned, but
           * there is no offset to jump to. Say so rather than implying pages. */}
          <span>· cursor paging — forward steps and a back stack, no page jumps</span>
        </Footnote>
      )}
    </div>
  );
}
