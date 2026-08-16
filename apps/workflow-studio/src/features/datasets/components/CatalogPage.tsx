/**
 * The dataset catalog: everything you can see, at a glance.
 *
 * The /data rail is a *picker* — narrow, optimised for switching while you work.
 * This is the browse surface: full metadata, sortable, with the totals you want
 * before you have picked anything. Clicking a row opens it in the workspace.
 */

import { useMemo, useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { ArrowUpDown, ChevronLeft, ChevronRight, Database, Search, Table2 } from 'lucide-react';
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
import { useDatasetCatalogSweep, MAX_CATALOG_ROWS, type DatasetInfo } from '../hooks/useDatasets';
import { SeatSwitcher } from './SeatSwitcher';

function formatBytes(n?: number | null): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(s?: string | null): string {
  if (!s) return '—';
  // Most entities emit a Postgres `::text` timestamp; JobOut emits ISO-8601.
  // `new Date` parses both, but guard rather than render "Invalid Date".
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString();
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-[var(--surface)]/40 px-3 py-2.5">
      <div className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </div>
      <div className="mt-0.5 text-[20px] leading-tight font-medium tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-muted-foreground/70">{hint}</div>}
    </div>
  );
}

type SortKey = 'name' | 'row_count' | 'size_bytes' | 'updated_at';

const DOC_FILTERS = ['all', 'none', 'partial', 'full'] as const;
const VALIDATION_FILTERS = ['all', 'passed', 'failed', 'none'] as const;

const PAGE_SIZE = 25;

export function CatalogPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [doc, setDoc] = useState<(typeof DOC_FILTERS)[number]>('all');
  const [validation, setValidation] = useState<(typeof VALIDATION_FILTERS)[number]>('all');
  const [sort, setSort] = useState<SortKey>('name');
  const [asc, setAsc] = useState(true);
  const [page, setPage] = useState(0);

  // Every filter here is applied by the server, so the sweep below stays small.
  const catalog = useDatasetCatalogSweep({
    q: search.trim() || undefined,
    documentation: doc === 'all' ? undefined : doc,
    validation_status: validation === 'all' ? undefined : validation,
  });

  const items = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const total = catalog.data?.total ?? items.length;
  /** True only when the sweep could not reach the end — sorting is then partial. */
  const truncated = catalog.data ? !catalog.data.complete : false;

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const dir = asc ? 1 : -1;
      if (sort === 'name') return dir * a.name.localeCompare(b.name);
      if (sort === 'updated_at')
        return dir * String(a.updated_at).localeCompare(String(b.updated_at));
      return dir * ((a[sort] ?? 0) - (b[sort] ?? 0));
    });
    return copy;
  }, [items, sort, asc]);

  // Page over the sorted whole, so page 2 is genuinely "the next 25 by this
  // ordering" rather than a re-sorted slice of a different server page.
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const rows = useMemo(
    () => sorted.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [sorted, safePage],
  );

  /** Any change to ordering or filtering invalidates the current page index. */
  const resetPage = () => setPage(0);

  const totals = useMemo(
    () =>
      items.reduce(
        (acc, d) => ({
          rows: acc.rows + (d.row_count ?? 0),
          bytes: acc.bytes + (d.size_bytes ?? 0),
          drift: acc.drift + (d.has_schema_drift ? 1 : 0),
          undocumented: acc.undocumented + (d.documentation === 'none' ? 1 : 0),
        }),
        { rows: 0, bytes: 0, drift: 0, undocumented: 0 },
      ),
    [items],
  );

  const open = (d: DatasetInfo) => navigate({ to: '/data', search: { dataset: d.id } });

  const sortable = (key: SortKey, label: string, className?: string) => (
    <TableHead className={className}>
      <button
        data-testid={`catalog-sort-${key}`}
        onClick={() => {
          if (sort === key) setAsc((v) => !v);
          else {
            setSort(key);
            setAsc(true);
          }
          resetPage();
        }}
        className={cn(
          'inline-flex items-center gap-1 transition-colors hover:text-foreground',
          sort === key && 'text-foreground',
        )}
      >
        {label}
        <ArrowUpDown className="size-2.5 opacity-60" />
      </button>
    </TableHead>
  );

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <Link
          to="/projects"
          className="text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          Projects
        </Link>
        <span className="text-border">/</span>
        <span className="text-[13px] font-medium">Catalog</span>
        <div className="ml-auto flex items-center gap-3">
          <Link
            to="/data"
            className="flex items-center gap-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <Table2 className="size-3" />
            Workspace
          </Link>
          <SeatSwitcher />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 p-5">
          <div>
            <h1 className="flex items-center gap-2 text-[18px] font-medium">
              <Database className="size-4 text-muted-foreground" />
              Dataset catalog
            </h1>
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              Every dataset this seat can see, with health and documentation at a glance.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <StatTile
              label="Datasets"
              value={String(total)}
              hint={truncated ? `swept first ${items.length}` : undefined}
            />
            <StatTile label="Total rows" value={totals.rows.toLocaleString()} />
            <StatTile label="Storage" value={formatBytes(totals.bytes)} />
            <StatTile
              label="Needs attention"
              value={String(totals.drift + totals.undocumented)}
              hint={`${totals.drift} drift · ${totals.undocumented} undocumented`}
            />
          </div>

          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  resetPage();
                }}
                placeholder="Search datasets…"
                data-testid="catalog-search"
                className="h-7 w-full rounded-md border border-border bg-background pr-2 pl-7 text-[12px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              />
            </div>
            <select
              value={doc}
              onChange={(e) => {
                setDoc(e.target.value as (typeof DOC_FILTERS)[number]);
                resetPage();
              }}
              aria-label="Documentation"
              data-testid="catalog-doc-filter"
              className="h-7 rounded-md border border-border bg-background px-2 text-[12px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {DOC_FILTERS.map((f) => (
                <option key={f} value={f}>
                  {f === 'all' ? 'All documentation' : `Docs: ${f}`}
                </option>
              ))}
            </select>
            <select
              value={validation}
              onChange={(e) => {
                setValidation(e.target.value as (typeof VALIDATION_FILTERS)[number]);
                resetPage();
              }}
              aria-label="Validation status"
              data-testid="catalog-validation-filter"
              className="h-7 rounded-md border border-border bg-background px-2 text-[12px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {VALIDATION_FILTERS.map((f) => (
                <option key={f} value={f}>
                  {f === 'all' ? 'All validation' : `Validation: ${f}`}
                </option>
              ))}
            </select>
          </div>

          <div className="overflow-hidden rounded-lg border border-border">
            <Table containerClassName="max-h-[60vh]">
              <TableHeader>
                <TableRow>
                  {sortable('name', 'Name')}
                  <TableHead>Classification</TableHead>
                  <TableHead>Validation</TableHead>
                  <TableHead>Docs</TableHead>
                  {sortable('row_count', 'Rows', 'text-right')}
                  {sortable('size_bytes', 'Size', 'text-right')}
                  <TableHead className="text-right">Ver</TableHead>
                  {sortable('updated_at', 'Updated')}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((d) => (
                  <TableRow
                    key={d.id}
                    onClick={() => open(d)}
                    className="cursor-pointer"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        open(d);
                      }
                    }}
                  >
                    <TableCell>
                      <span className="font-medium text-primary">{d.name}</span>
                      {d.deprecated && (
                        <Badge variant="destructive" className="ml-1.5">
                          deprecated
                        </Badge>
                      )}
                      {d.has_schema_drift && (
                        <Badge variant="destructive" className="ml-1.5">
                          drift
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{d.classification}</TableCell>
                    <TableCell>
                      <Badge variant={d.validation_status === 'passed' ? 'success' : 'glass'}>
                        {d.validation_status ?? 'none'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={d.documentation === 'full' ? 'success' : 'glass'}>
                        {d.documentation ?? 'none'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {(d.row_count ?? 0).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatBytes(d.size_bytes)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      v{d.current_version ?? '—'}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDate(d.updated_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {!catalog.isLoading && rows.length === 0 && (
              <p className="px-3 py-6 text-center text-[12px] text-muted-foreground">
                {search || doc !== 'all' || validation !== 'all'
                  ? 'No datasets match those filters.'
                  : 'No datasets yet.'}
              </p>
            )}
            {catalog.isLoading && (
              <p className="px-3 py-6 text-center text-[12px] text-muted-foreground">Loading…</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground tabular-nums" data-testid="catalog-range">
              {sorted.length === 0
                ? '0 datasets'
                : `${safePage * PAGE_SIZE + 1}–${safePage * PAGE_SIZE + rows.length} of ${sorted.length}`}
            </span>
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={safePage === 0}
                aria-label="Previous page"
                data-testid="catalog-prev"
                className="flex size-6 items-center justify-center rounded-md border border-border transition-colors hover:bg-muted disabled:opacity-40"
              >
                <ChevronLeft className="size-3" />
              </button>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {safePage + 1} / {pageCount}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={safePage >= pageCount - 1}
                aria-label="Next page"
                data-testid="catalog-next"
                className="flex size-6 items-center justify-center rounded-md border border-border transition-colors hover:bg-muted disabled:opacity-40"
              >
                <ChevronRight className="size-3" />
              </button>
            </div>
          </div>

          {/* Only shown when sorting really is over a partial set. */}
          {truncated && (
            <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
              This tenant has {total.toLocaleString()} datasets; the first{' '}
              {MAX_CATALOG_ROWS.toLocaleString()} were loaded. Sorting and totals above cover only
              those — narrow with search or a filter to see the rest.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
