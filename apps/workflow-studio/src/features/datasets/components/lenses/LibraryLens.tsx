/**
 * The library lens: everything this dataset carries that is not its rows.
 *
 * Four object kinds share this dock, and the reason they share it is the line
 * that runs between them:
 *
 *   **A chart, a view and a definition are CONFIGURATION. An artifact is the
 *   only one of the four that is stored bytes.**
 *
 * Saving a definition costs nothing and produces nothing. Running it is a
 * separate, explicit step, and *that* is what writes an artifact — except for a
 * `profile` run, which returns statistics and stores no file, so there is
 * nothing to publish afterwards. Rendering a chart re-runs its bound source and
 * persists nothing at all. Nowhere in this panel may a saved definition be
 * presented as a result.
 *
 * Artifacts keep the top of the panel because they are the part with a clock on
 * them. Three honesty constraints shape how they are drawn:
 *
 *   - Artifacts are listed from the `artifacts` TABLE, never a bucket scan. The
 *     row is the only thing that maps a filename to a storage key, so a blob
 *     written without one is unreachable by everyone, superusers included —
 *     which is why both the download link and the row reader go through
 *     `/samples/{filename}` instead of composing a key.
 *   - The service stamps `expires_at` at write time, but `/samples` does not
 *     return it. The countdown here is derived from `created_at` plus the
 *     published policy, which is how the stamp was computed in the first place.
 *     If the policy is edited server-side, already-written rows keep their old
 *     deadline — so it is a countdown, not a guarantee.
 *   - Nothing in the service runs the sweep on a timer. `artifact_gc` exists as
 *     a handler and no scheduler calls it, so "past window" means eligible for
 *     collection, not collected.
 *
 * Deletes ARE wired for charts, views and definitions, and are not for datasets
 * or versions. That line is the same one `useDatasetActions` draws: these three
 * are annotations over data that stays exactly where it was. The one thing a
 * definition's delete does take with it is its run history, and the dialog says
 * so before the click.
 *
 * Design (INSTRUMENT): the dock spends ZERO accent — the shell already spends
 * it on the active route. Magnitude uses the neutral ramp; the one place a
 * categorical hue appears is where identity genuinely needs it (chart types, and
 * the categories of a rendered chart), ranked and folded above eight rather than
 * cycled.
 */

import { useState, type ReactNode } from 'react';
import { Download, FileBox, Play, Plus, Rows3, Star, Trash2 } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog';
import { Guard } from '@/shared/components/instrument/Guard';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { Footnote, Identifier, Metric } from '@/shared/components/instrument/Typography';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { complete, coverage } from '@/shared/components/instrument/coverage';
import { foldTopN, vizSlot } from '@/shared/components/instrument/series';
import { middleTruncate } from '@/shared/components/instrument/shape';
import { ANALYTICS_BASE, errorText } from '@/shared/lib/analyticsClient';
import { compact, formatBytes, num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { useArtifacts, type Artifact } from '../../hooks/useAnalysis';
import { useSheets, useVersions } from '../../hooks/useDatasets';
import {
  CHART_TYPES,
  DEFINITION_KINDS,
  useArtifactRows,
  useChart,
  useCharts,
  useCreateChart,
  useCreateDefinition,
  useCreateView,
  useDatasetUsage,
  useDefinition,
  useDefinitionRuns,
  useDefinitions,
  useDeleteChart,
  useDeleteDefinition,
  useDeleteView,
  useFavorite,
  usePublishRun,
  useRenderChart,
  useRunDefinition,
  useRunView,
  useSetFavorite,
  useUpdateChart,
  useUpdateDefinition,
  useUpdateView,
  useView,
  useViews,
  type AnalyticsDefinition,
  type AnalyticsRun,
  type ChartRender,
  type ChartType,
  type SavedChart,
  type SavedView,
  type VersionSelector,
  type ViewRun,
} from '../../hooks/useLibraryObjects';
import { fieldClass } from '../fieldStyles';
import { DtypeChip, LensError, LensList, LensLoading, Section } from './primitives';

/**
 * Server-side retention policy, surfaced so "it vanished" is never a surprise.
 * Mirrors `app/features/files/services/retention.py`; `null` = kept forever.
 */
const RETENTION_DAYS: Record<string, number | null> = {
  published_source: null,
  validation_failures: 90,
  transform_output: 30,
  join_output: 30,
  sample_output: 30,
  aggregation_output: 30,
  pivot_output: 30,
  diff_output: 14,
  export: 7,
  query_output: 7,
};

/**
 * The service's `DEFAULT_RETENTION_DAYS`. An unknown kind is new code, not a
 * licence to keep bytes forever, so it gets the conservative middle — and the
 * panel marks it as inferred rather than quoting it as policy.
 */
const DEFAULT_RETENTION_DAYS = 30;

/** `useArtifacts` asks for this many; beyond it, this dataset's list can be short. */
const ARTIFACT_PAGE = 200;

/** Rows read back from an artifact when the reader is opened. A peek, not a table. */
const PEEK_ROWS = 5;

/** Columns a peek shows before it starts folding. The dock is 384px wide. */
const PEEK_COLUMNS = 6;

const DAY_MS = 86_400_000;

/**
 * A size split into figure and unit, which is what `Figure`/`Metric` want and
 * what `formatBytes` (one string) cannot give.
 */
function sizeParts(bytes: number): { value: string; unit: string } {
  if (bytes >= 1024 ** 3) return { value: (bytes / 1024 ** 3).toFixed(1), unit: 'GB' };
  if (bytes >= 1024 ** 2) return { value: (bytes / 1024 ** 2).toFixed(1), unit: 'MB' };
  if (bytes >= 1024) return { value: (bytes / 1024).toFixed(0), unit: 'KB' };
  return { value: String(bytes), unit: 'B' };
}

/**
 * Aggregate sizes only. Per-artifact sizes keep the shared `formatBytes` so a
 * row here reads the same as a row in any other lens.
 */
function formatSizeWide(bytes: number): string {
  const { value, unit } = sizeParts(bytes);
  return `${value} ${unit}`;
}

/** `MM-DD`, in local time — the dock has no room for a year that never varies. */
function shortDay(iso?: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const d = new Date(t);
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Elapsed time between two stamps, or null when the run has not finished. */
function duration(from?: string | null, to?: string | null): string | null {
  if (!from || !to) return null;
  const a = Date.parse(from);
  const b = Date.parse(to);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  const ms = b - a;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

/**
 * Provenance, as far as it goes. The storage key is
 * `artifacts/{team}/{dataset}/{kind}/{filename}` and the kind and filename are
 * already on the row, so the middle segments are the part that says where this
 * came from.
 */
function provenance(key: string, kind: string, filename: string): string | null {
  const parts = key.split('/').filter(Boolean);
  const middle = parts.filter((p) => p !== 'artifacts' && p !== kind && p !== filename);
  return middle.length ? middle.join(' / ') : null;
}

/* ------------------------------------------------------------- shared bits */

/**
 * A destructive confirm. Every delete in this panel removes an ANNOTATION, so
 * the dialog's job is to say what stays, not to look frightening.
 */
function ConfirmDelete({
  open,
  onOpenChange,
  title,
  keeps,
  loses,
  onConfirm,
  pending,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  /** What survives the delete. The reassuring half, and the true one. */
  keeps: string;
  /** What actually goes. */
  loses: string;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{loses}</AlertDialogDescription>
        </AlertDialogHeader>
        <Guard>{keeps}</Guard>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? 'Deleting…' : 'Delete'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** A row of dense actions under an object. Fixed height, so lists stay a grid. */
function Actions({ children }: { children: ReactNode }) {
  return <div className="mt-1 flex h-6 items-center gap-1">{children}</div>;
}

/** Job/run status as shape + hue + word. Unknown states read as absent, not good. */
function runStatus(status: string): StatusKind {
  if (status === 'completed' || status === 'succeeded') return 'good';
  if (status === 'failed' || status === 'error') return 'critical';
  if (status === 'running' || status === 'pending' || status === 'queued') return 'warning';
  return 'unknown';
}

/** `{mode: 'version', version_number: 42}` → `pinned v42`. */
function selectorWord(selector: Record<string, unknown> | undefined): string {
  const mode = selector?.mode;
  if (mode === 'version' && typeof selector?.version_number === 'number') {
    return `pinned v${selector.version_number}`;
  }
  if (mode === 'tag' && typeof selector?.tag === 'string') return `tag ${selector.tag}`;
  return 'always latest';
}

/**
 * The sheets of the newest version, for the two create forms.
 *
 * Both forms need the same list and both share the query keys `useVersions` and
 * `useSheets` already use, so this costs no extra request when the versions
 * lens has been opened — and one when it has not.
 */
function useLatestSheets(datasetId: string | null) {
  const versions = useVersions(datasetId);
  const numbers = (versions.data?.items ?? []).map((v) => v.version_number);
  const latest = numbers.length ? Math.max(...numbers) : null;
  const sheets = useSheets(datasetId, latest);
  return { latest, sheets: sheets.data?.items ?? [], isLoading: versions.isLoading || sheets.isLoading };
}

/* -------------------------------------------------------------- artifacts */

interface ArtifactRow {
  artifact: Artifact;
  kind: string;
  /** Days the policy keeps this kind; `null` = forever. */
  ttl: number | null;
  /** False when the kind is not in the published policy and the TTL is inferred. */
  declared: boolean;
  /** Whole days left, or `null` when there is no clock to read. */
  remaining: number | null;
  status: StatusKind;
  word: string;
}

function describe(a: Artifact, now: number): ArtifactRow {
  const kind = a.file_type ?? 'unknown';
  const declared = kind in RETENTION_DAYS;
  const ttl = declared ? RETENTION_DAYS[kind] : DEFAULT_RETENTION_DAYS;
  const created = a.created_at ? Date.parse(a.created_at) : NaN;

  if (ttl === null) {
    return { artifact: a, kind, ttl, declared, remaining: null, status: 'unknown', word: 'kept forever' };
  }
  if (Number.isNaN(created)) {
    return { artifact: a, kind, ttl, declared, remaining: null, status: 'unknown', word: 'age unknown' };
  }

  const remaining = Math.ceil(ttl - (now - created) / DAY_MS);
  if (remaining <= 0) {
    // Eligible for the sweep, not swept. Nothing schedules `artifact_gc`.
    return { artifact: a, kind, ttl, declared, remaining, status: 'critical', word: 'past window' };
  }
  const status: StatusKind =
    remaining <= 2 ? 'critical' : remaining <= 7 ? 'serious' : remaining <= 14 ? 'warning' : 'good';
  return { artifact: a, kind, ttl, declared, remaining, status, word: `in ${remaining}d` };
}

/**
 * One clock read for the whole list, so two rows can never disagree about what
 * "today" is. It lives here rather than in the component because reading the
 * clock during render is an impure call.
 */
function describeAll(items: readonly Artifact[]): ArtifactRow[] {
  const now = Date.now();
  return items.map((a) => describe(a, now));
}

interface KindGroup {
  kind: string;
  ttl: number | null;
  declared: boolean;
  rows: ArtifactRow[];
  bytes: number;
  /** Soonest expiry in the group; `Infinity` when nothing in it expires. */
  soonest: number;
}

/**
 * Ascending, no-clock last. Subtraction is not used because two rows that never
 * expire are both `Infinity`, and `Infinity - Infinity` is `NaN` — a comparator
 * that returns NaN leaves the order up to the engine.
 */
function soonestFirst(a: number, b: number): number {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/**
 * Group by kind, then order the groups by what expires first. Kind is what
 * determines the clock, so grouping by it lets one retention statement cover a
 * whole block instead of being repeated on every row.
 */
function groupByKind(rows: ArtifactRow[]): KindGroup[] {
  const byKind = new Map<string, KindGroup>();
  for (const row of rows) {
    let g = byKind.get(row.kind);
    if (!g) {
      g = { kind: row.kind, ttl: row.ttl, declared: row.declared, rows: [], bytes: 0, soonest: Infinity };
      byKind.set(row.kind, g);
    }
    g.rows.push(row);
    g.bytes += row.artifact.size_bytes ?? 0;
    if (row.remaining != null) g.soonest = Math.min(g.soonest, row.remaining);
  }
  for (const g of byKind.values()) {
    g.rows.sort((a, b) => soonestFirst(a.remaining ?? Infinity, b.remaining ?? Infinity));
  }
  return [...byKind.values()].sort(
    (a, b) => soonestFirst(a.soonest, b.soonest) || a.kind.localeCompare(b.kind),
  );
}

function retentionLabel(g: KindGroup): string {
  if (g.ttl === null) return 'kept forever';
  return g.declared ? `kept ${g.ttl}d` : `kept ${g.ttl}d (default)`;
}

/** A cell value squeezed into a footnote line. Never `[object Object]`. */
function cellText(value: unknown): string {
  if (value === null || value === undefined) return '∅';
  if (typeof value === 'number' || typeof value === 'boolean') return num(value);
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

/**
 * The first few rows of a stored artifact, read back through the same
 * artifact-row authorization the download link uses.
 *
 * This is a peek, not a viewer: five rows and six columns in a 384px dock, with
 * everything beyond that stated as a number rather than hidden in a scrollbox.
 */
function ArtifactPeek({ filename }: { filename: string }) {
  const rows = useArtifactRows(filename, PEEK_ROWS);

  if (rows.isLoading) return <LensLoading>Reading rows…</LensLoading>;
  if (rows.error) {
    return <LensError>{errorText(rows.error, { notFound: 'This artifact is no longer readable.' })}</LensError>;
  }
  const d = rows.data;
  if (!d) return null;

  const shown = d.data.length;
  const columns = d.columns.slice(0, PEEK_COLUMNS);
  const hiddenColumns = d.columns.length - columns.length;

  return (
    <div className="mt-1.5">
      <StatList coverage={complete(d.filtered_count, 'rows')}>
        <Stat
          name="rows"
          value={compact(d.filtered_count)}
          coverage={coverage(shown, d.filtered_count, 'rows')}
        />
        <Stat
          name="columns"
          value={compact(d.columns.length)}
          coverage={coverage(columns.length, d.columns.length, 'columns')}
        />
      </StatList>

      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {columns.map((c) => (
          <span key={c.name} className="flex items-baseline gap-1">
            <Identifier className="text-footnote text-foreground">
              {middleTruncate(c.name, 16)}
            </Identifier>
            <DtypeChip dtype={c.dtype} />
          </span>
        ))}
      </div>

      {d.data.map((row, i) => (
        <p
          key={i}
          className="mt-1 flex h-4 items-center overflow-hidden font-mono text-footnote text-muted-foreground"
        >
          {middleTruncate(columns.map((c) => cellText(row[c.name])).join(' · '), 52)}
        </p>
      ))}

      {hiddenColumns > 0 && (
        <Footnote className="mt-1">
          {hiddenColumns} more column{hiddenColumns === 1 ? '' : 's'} not shown — download the file
          to read them.
        </Footnote>
      )}
      {d.filtered_count !== d.total_count && (
        <Guard>
          {compact(d.filtered_count)} of {compact(d.total_count)} rows match the filter this read
          applied.
        </Guard>
      )}
    </div>
  );
}

function ArtifactEntry({ row }: { row: ArtifactRow }) {
  const a = row.artifact;
  const [peeking, setPeeking] = useState(false);
  const created = shortDay(a.created_at);
  const path = provenance(a.key, row.kind, a.filename);

  return (
    <div className="mb-1 rounded-md bg-card px-2 py-1.5" data-testid="artifact">
      <div className="flex items-center gap-1.5">
        <FileBox className="size-3 shrink-0 text-muted-foreground" />
        {/* Middle truncation, not an ellipsis: two artifacts of the same kind
            differ in their tail, and a leading-truncated name makes every row
            in a group read alike. */}
        <span className="truncate font-mono text-micro" title={a.filename}>
          {middleTruncate(a.filename, 30)}
        </span>
        <button
          type="button"
          onClick={() => setPeeking((p) => !p)}
          aria-pressed={peeking}
          className={cn(
            'ml-auto transition-colors hover:text-foreground',
            peeking ? 'text-foreground' : 'text-muted-foreground',
          )}
          title={peeking ? 'Hide rows' : 'Read rows'}
        >
          <Rows3 className="size-3" />
        </button>
        {/* Download is a plain link: the browser handles Content-Disposition,
            and the identity header is not needed for a same-seat GET here
            only because the link opens in the app's own context. */}
        <a
          href={`${ANALYTICS_BASE}/samples/${encodeURIComponent(a.filename)}`}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground transition-colors hover:text-foreground"
          title="Download"
          data-testid="artifact-download"
        >
          <Download className="size-3" />
        </a>
      </div>

      <div className="mt-1 flex items-baseline gap-2 text-micro text-muted-foreground">
        <span className="tabular-nums">{formatBytes(a.size_bytes)}</span>
        {created && <span className="tabular-nums">{created}</span>}
        <span className="ml-auto flex items-baseline gap-1.5" data-testid="artifact-retention">
          <Status kind={row.status} className="text-micro">
            {row.word}
          </Status>
          {row.ttl !== null && <span className="tabular-nums">of {row.ttl}d</span>}
        </span>
      </div>

      {/* Life left, not size — magnitude, so the neutral ramp. Status hue only
          once the clock is genuinely short, and never on its own: the word
          beside it says the same thing. */}
      {row.ttl !== null && row.remaining != null && (
        <MagnitudeBar
          className="mt-1 h-1"
          of={coverage(Math.max(0, row.remaining), row.ttl)}
          color={
            row.status === 'critical'
              ? 'var(--st-crit)'
              : row.status === 'serious'
                ? 'var(--st-serious)'
                : undefined
          }
        />
      )}

      {path && (
        <Footnote className="mt-1 truncate" title={a.key}>
          {middleTruncate(path, 44)}
        </Footnote>
      )}

      {peeking && <ArtifactPeek filename={a.filename} />}
    </div>
  );
}

/* ------------------------------------------------------------------ usage */

/**
 * What this dataset is worth to the people using it, plus this seat's own mark.
 *
 * `downloads` and `writes` are counts over the SAME population of logged
 * events, so both carry that denominator rather than standing alone: `12
 * downloads` and `12 downloads of 340 events` are different claims.
 */
function UsageSection({ datasetId }: { datasetId: string | null }) {
  const usage = useDatasetUsage(datasetId);
  const favorite = useFavorite(datasetId);
  const setFavorite = useSetFavorite(datasetId);
  const u = usage.data;
  const marked = favorite.isFavorite;

  return (
    <Section title="Usage">
      {usage.isLoading && <LensLoading />}
      {usage.error && <LensError>{errorText(usage.error)}</LensError>}
      {u && (
        <>
          <StatList coverage={complete(u.total_events, 'events')}>
            <Stat
              name="downloads"
              value={compact(u.downloads)}
              coverage={coverage(u.downloads, u.total_events, 'events')}
            />
            <Stat
              name="writes"
              value={compact(u.writes)}
              coverage={coverage(u.writes, u.total_events, 'events')}
            />
          </StatList>
          <Footnote className="mt-1">
            {compact(u.total_events)} logged event{u.total_events === 1 ? '' : 's'}
            {u.last_activity_at ? ` · last ${shortDay(u.last_activity_at) ?? '—'}` : ' · no activity yet'}
          </Footnote>
        </>
      )}

      <Actions>
        <Button
          size="xs"
          variant={marked ? 'secondary' : 'ghost'}
          disabled={marked === null || setFavorite.isPending}
          onClick={() => setFavorite.mutate(!marked)}
          data-testid="library-favorite"
        >
          <Star className={cn('size-3', marked && 'fill-current')} />
          {marked ? 'Favourited' : 'Add to favourites'}
        </Button>
      </Actions>

      {marked === null && !favorite.isLoading && (
        <Guard>
          The favourite mark is a field of the catalog listing, and this dataset is not in the page
          this seat holds — so there is nothing to read and nothing to toggle from here.
        </Guard>
      )}
    </Section>
  );
}

/* ----------------------------------------------------------------- charts */

/** Categories of a rendered chart are identity, so they get a categorical hue. */
function RenderResult({ render }: { render: ChartRender }) {
  const categories = render.categories ?? [];
  const series = render.series ?? [];
  const first = series[0];

  const points = categories
    .map((label, i) => ({ label, value: Number(first?.data?.[i]) }))
    .filter((p) => Number.isFinite(p.value));
  const peak = points.reduce((m, p) => Math.max(m, Math.abs(p.value)), 0);
  const folded = foldTopN(points, (p) => Math.abs(p.value));

  const rowCount = render.row_count ?? 0;
  const totalRows = render.total_rows;
  const masked = render.masked_columns ?? [];

  return (
    <div className="mt-1.5">
      <StatList coverage={complete(rowCount, 'rows')}>
        <Stat
          name="rows"
          value={compact(totalRows ?? rowCount)}
          coverage={coverage(rowCount, totalRows ?? rowCount, 'rows')}
        />
        {/* One series is drawn, however many came back — the dock has room for
            one axis, and saying which fraction is drawn beats a legend that
            implies all of them are. */}
        <Stat
          name="drawn"
          value={compact(first ? 1 : 0)}
          coverage={coverage(first ? 1 : 0, series.length, 'series')}
        />
      </StatList>

      {folded.head.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {folded.head.map((p, rank) => (
            <div key={p.label} className="flex h-4 items-center gap-1.5">
              <Identifier className="w-16 shrink-0 truncate text-footnote" title={p.label}>
                {middleTruncate(p.label, 12)}
              </Identifier>
              <MagnitudeBar
                className="h-1 flex-1"
                of={coverage(Math.abs(p.value), peak, 'values')}
                color={vizSlot(rank)}
              />
              <span className="w-12 shrink-0 text-right text-footnote tabular-nums">
                {num(p.value)}
              </span>
            </div>
          ))}
          {folded.other && (
            <div className="flex h-4 items-center gap-1.5">
              {/* Graphite, never a ninth hue: the tail is not a category. */}
              <Identifier className="w-16 shrink-0 text-footnote text-muted-foreground">
                other {folded.other.count}
              </Identifier>
              <MagnitudeBar
                className="h-1 flex-1"
                of={coverage(Math.abs(folded.other.value), peak, 'values')}
                color="var(--m5)"
              />
              <span className="w-12 shrink-0 text-right text-footnote tabular-nums">
                {Math.round(folded.other.share * 100)}%
              </span>
            </div>
          )}
        </div>
      )}

      {first && (
        <Footnote className="mt-1">
          drawn: {first.name}
          {render.x_field ? ` · x ${render.x_field}` : ''}
          {render.series_field ? ` · split ${render.series_field}` : ''}
        </Footnote>
      )}

      {render.truncated && (
        <Guard tone="warning">
          This render does not show everything — the category axis was capped, or the source
          returned more rows than one page.
          {totalRows == null && ' The source reported no total, so the size of what is missing is unknown.'}
        </Guard>
      )}
      {masked.length > 0 && (
        <Guard>
          Masked for this seat: {masked.map((c) => middleTruncate(c, 18)).join(', ')} — the data
          dictionary marks them sensitive.
        </Guard>
      )}
      <Footnote className="mt-1">
        Nothing was stored. A render re-runs the bound source and returns series; it writes no
        artifact and no run row.
      </Footnote>
    </div>
  );
}

function ChartEntry({
  datasetId,
  chart,
  sourceLabel,
}: {
  datasetId: string | null;
  chart: SavedChart;
  sourceLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState(chart.name);
  const detail = useChart(datasetId, open ? chart.id : null);
  const render = useRenderChart(datasetId);
  const update = useUpdateChart(datasetId);
  const remove = useDeleteChart(datasetId);

  return (
    <div className="mb-1 rounded-md bg-card px-2 py-1.5" data-testid="saved-chart">
      <div className="flex items-baseline gap-1.5">
        <span className="truncate text-body">{chart.name}</span>
        <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
          {chart.chart_type}
        </Identifier>
      </div>
      <div className="mt-0.5 flex items-baseline gap-1.5 text-footnote text-muted-foreground">
        <Identifier className="truncate" title={sourceLabel}>
          {middleTruncate(sourceLabel, 30)}
        </Identifier>
        {chart.created_by && <span className="truncate">{chart.created_by}</span>}
        <span className="ml-auto shrink-0 tabular-nums">{shortDay(chart.updated_at)}</span>
      </div>

      <Actions>
        <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)} aria-pressed={open}>
          {open ? 'Close' : 'Open'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={render.isPending}
          onClick={() => render.mutate(chart.id)}
        >
          <Play className="size-3" />
          {render.isPending ? 'Rendering…' : 'Render'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto text-muted-foreground"
          onClick={() => setConfirming(true)}
        >
          <Trash2 className="size-3" />
        </Button>
      </Actions>

      {render.error ? <LensError>{errorText(render.error)}</LensError> : null}
      {render.data && render.data.chart_id === chart.id && <RenderResult render={render.data} />}

      {open && (
        <div className="mt-1.5">
          {detail.isLoading && <LensLoading>Re-reading…</LensLoading>}
          {detail.error && (
            <LensError>
              {errorText(detail.error, { notFound: 'This chart is no longer there.' })}
            </LensError>
          )}
          {detail.data && (
            <>
              <Footnote>
                id <span className="font-mono">{middleTruncate(detail.data.id, 20)}</span>
                {detail.data.description ? ` · ${detail.data.description}` : ''}
              </Footnote>
              <div className="mt-1 flex items-center gap-1">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  aria-label="Chart name"
                  className={fieldClass}
                />
                <Button
                  size="xs"
                  disabled={!name.trim() || name === detail.data.name || update.isPending}
                  onClick={() =>
                    update.mutate({ chartId: chart.id, patch: { name: name.trim() } })
                  }
                >
                  Rename
                </Button>
              </div>
              <div className="mt-1 flex items-center gap-1">
                <select
                  value={detail.data.chart_type}
                  aria-label="Chart type"
                  className={fieldClass}
                  onChange={(e) =>
                    update.mutate({
                      chartId: chart.id,
                      patch: { chart_type: e.target.value as ChartType },
                    })
                  }
                >
                  {CHART_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                  {/* A type the API returned that is not one of the seven still
                      has to be selectable, or the select would silently rewrite
                      it on the next change. */}
                  {!CHART_TYPES.includes(detail.data.chart_type as ChartType) && (
                    <option value={detail.data.chart_type}>{detail.data.chart_type}</option>
                  )}
                </select>
              </div>
              <Guard>
                Re-read from the server on open, so a rename here cannot overwrite an edit made
                while this list was on screen.
              </Guard>
            </>
          )}
        </div>
      )}

      <ConfirmDelete
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete chart “${chart.name}”?`}
        loses="Removes the chart row — its name, type and the binding to its source."
        keeps="The definition or view it renders is untouched, and so is every row of data. A chart owns no query logic and stores no result."
        pending={remove.isPending}
        onConfirm={() => {
          // Close the detail panel BEFORE the delete lands. `useChart` is
          // enabled on `open`, and the mutation's coarse invalidate refetches
          // the id we just removed — a routine delete then logs a 404 that the
          // user never caused and cannot act on.
          setOpen(false);
          remove.mutate({ chartId: chart.id, name: chart.name });
          setConfirming(false);
        }}
      />
    </div>
  );
}

function ChartsSection({
  datasetId,
  definitions,
  views,
}: {
  datasetId: string | null;
  definitions: AnalyticsDefinition[];
  views: SavedView[];
}) {
  const charts = useCharts(datasetId);
  const create = useCreateChart(datasetId);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState<ChartType>('bar');
  const [source, setSource] = useState('');

  const items = charts.data?.items ?? [];
  const total = charts.data?.total ?? items.length;
  const boundToDefinition = items.filter((c) => c.definition_id).length;

  const byType = items.reduce<Record<string, number>>((acc, c) => {
    acc[c.chart_type] = (acc[c.chart_type] ?? 0) + 1;
    return acc;
  }, {});
  // Chart type is IDENTITY — a word, not a magnitude — so it earns hue. Ranked
  // and folded above eight rather than cycled: `chart_type` is a bare string on
  // the wire, so "there are only seven" is not something to bet a palette on.
  const folded = foldTopN(Object.entries(byType), ([, n]) => n);

  const label = (v: SavedView | AnalyticsDefinition) => v.name;
  const sourceLabel = (c: SavedChart) => {
    if (c.definition_id) {
      const d = definitions.find((x) => x.id === c.definition_id);
      return d ? `definition · ${label(d)}` : `definition · ${middleTruncate(c.definition_id, 18)}`;
    }
    if (c.view_id) {
      const v = views.find((x) => x.id === c.view_id);
      return v ? `view · ${label(v)}` : `view · ${middleTruncate(c.view_id, 18)}`;
    }
    return 'no source';
  };

  const canCreate = definitions.length > 0 || views.length > 0;

  const submit = () => {
    const [kind, id] = source.split(':');
    if (!name.trim() || !id) return;
    create.mutate(
      {
        name: name.trim(),
        chart_type: type,
        ...(kind === 'view' ? { view_id: id } : { definition_id: id }),
      },
      {
        onSuccess: () => {
          setName('');
          setSource('');
          setAdding(false);
        },
      },
    );
  };

  return (
    <Section
      title={`Charts (${items.length})`}
      action={
        <Button size="xs" variant="ghost" onClick={() => setAdding((a) => !a)} aria-pressed={adding}>
          <Plus className="size-3" />
          New
        </Button>
      }
    >
      {items.length > 0 && (
        <>
          <Stat
            name="on a def"
            value={compact(boundToDefinition)}
            coverage={coverage(boundToDefinition, items.length, 'charts')}
            className="mb-1.5"
          />
          <div className="mb-2 space-y-1">
            {folded.head.map(([kind, n], rank) => (
              <div key={kind} className="flex h-4 items-center gap-1.5">
                <Identifier className="w-14 shrink-0 text-footnote">{kind}</Identifier>
                <MagnitudeBar
                  className="h-1 flex-1"
                  of={coverage(n, items.length, 'charts')}
                  color={vizSlot(rank)}
                />
                <span className="w-6 shrink-0 text-right text-footnote tabular-nums">{n}</span>
              </div>
            ))}
            {folded.other && (
              <div className="flex h-4 items-center gap-1.5">
                <Identifier className="w-14 shrink-0 text-footnote text-muted-foreground">
                  other {folded.other.count}
                </Identifier>
                <MagnitudeBar
                  className="h-1 flex-1"
                  of={coverage(folded.other.value, items.length, 'charts')}
                  color="var(--m5)"
                />
                <span className="w-6 shrink-0 text-right text-footnote tabular-nums">
                  {Math.round(folded.other.share * 100)}%
                </span>
              </div>
            )}
          </div>
        </>
      )}

      {adding && (
        <div className="mb-2 space-y-1">
          {canCreate ? (
            <>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Chart name"
                aria-label="New chart name"
                className={fieldClass}
              />
              <div className="flex gap-1">
                <select
                  value={type}
                  onChange={(e) => setType(e.target.value as ChartType)}
                  aria-label="New chart type"
                  className={fieldClass}
                >
                  {CHART_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <select
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  aria-label="New chart source"
                  className={fieldClass}
                >
                  <option value="">Bind to…</option>
                  {definitions.map((d) => (
                    <option key={d.id} value={`definition:${d.id}`}>
                      definition · {d.name}
                    </option>
                  ))}
                  {views.map((v) => (
                    <option key={v.id} value={`view:${v.id}`}>
                      view · {v.name}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                size="xs"
                disabled={!name.trim() || !source || create.isPending}
                onClick={submit}
              >
                {create.isPending ? 'Saving…' : 'Save chart'}
              </Button>
              <Guard>
                A chart owns no query logic. Filters, grouping and version pinning live on the
                definition or view it binds to, and the chart cannot override them.
              </Guard>
            </>
          ) : (
            <Guard>
              A chart renders a saved definition or a saved view, and this dataset has neither yet.
              Save one below first.
            </Guard>
          )}
        </div>
      )}

      <LensList
        query={charts}
        items={items}
        empty="No saved charts. A chart is a rendering of a source you already saved — it stores no query and no rows."
      >
        {(c) => (
          <ChartEntry key={c.id} datasetId={datasetId} chart={c} sourceLabel={sourceLabel(c)} />
        )}
      </LensList>

      {total > items.length && (
        <Footnote className="mt-1">
          Showing {items.length} of {compact(total)} charts.
        </Footnote>
      )}
    </Section>
  );
}

/* ------------------------------------------------------------ saved views */

/** The QuerySpec fields a saved view can carry, stated as facts not guesses. */
function querySummary(query: Record<string, unknown> | undefined): string {
  const parts: string[] = [];
  const columns = query?.columns;
  parts.push(Array.isArray(columns) ? `${columns.length} cols` : 'all cols');
  if (query?.filters) parts.push('filtered');
  const sort = query?.sort;
  if (Array.isArray(sort) && sort.length > 0) parts.push(`sort ${sort.length}`);
  if (typeof query?.search === 'string' && query.search) parts.push('search');
  if (typeof query?.limit === 'number') parts.push(`limit ${query.limit}`);
  return parts.join(' · ');
}

function ViewRunResult({ run }: { run: ViewRun }) {
  const rows = run.result.items ?? [];
  const total = run.result.total;
  const masked = run.result.masked_columns ?? [];

  return (
    <div className="mt-1.5">
      {total != null ? (
        <Stat
          name="rows"
          value={compact(total)}
          coverage={coverage(rows.length, total, 'rows')}
        />
      ) : (
        <Footnote>{compact(rows.length)} rows returned; the run reported no total.</Footnote>
      )}
      <Footnote className="mt-1">
        ran against v{run.version_number} · sheet{' '}
        <span className="font-mono">{middleTruncate(run.sheet_name, 20)}</span>
      </Footnote>
      {run.result.next_cursor && (
        <Guard>
          There is another page. Paging here is cursor-based, so a view captures a page size, never
          a page number.
        </Guard>
      )}
      {masked.length > 0 && (
        <Guard>Masked for this seat: {masked.map((c) => middleTruncate(c, 18)).join(', ')}.</Guard>
      )}
    </div>
  );
}

function ViewEntry({ datasetId, view }: { datasetId: string | null; view: SavedView }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState(view.name);
  const detail = useView(datasetId, open ? view.id : null);
  const run = useRunView(datasetId);
  const update = useUpdateView(datasetId);
  const remove = useDeleteView(datasetId);

  const sheet = view.sheet_name ?? view.sheet_key ?? view.logical_sheet_id;

  return (
    <div className="mb-1 rounded-md bg-card px-2 py-1.5" data-testid="saved-view">
      <div className="flex items-baseline gap-1.5">
        <span className="truncate text-body">{view.name}</span>
        <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
          {selectorWord(view.version_selector)}
        </Identifier>
      </div>
      <div className="mt-0.5 flex items-baseline gap-1.5 text-footnote text-muted-foreground">
        <Identifier className="truncate" title={sheet}>
          {middleTruncate(sheet, 18)}
        </Identifier>
        <span className="truncate">{querySummary(view.query)}</span>
        <span className="ml-auto shrink-0 tabular-nums">{shortDay(view.updated_at)}</span>
      </div>

      <Actions>
        <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)} aria-pressed={open}>
          {open ? 'Close' : 'Open'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={run.isPending}
          onClick={() => run.mutate({ viewId: view.id })}
        >
          <Play className="size-3" />
          {run.isPending ? 'Running…' : 'Run'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto text-muted-foreground"
          onClick={() => setConfirming(true)}
        >
          <Trash2 className="size-3" />
        </Button>
      </Actions>

      {run.error ? <LensError>{errorText(run.error)}</LensError> : null}
      {run.data && run.data.view_id === view.id && <ViewRunResult run={run.data} />}

      {open && (
        <div className="mt-1.5">
          {detail.isLoading && <LensLoading>Re-reading…</LensLoading>}
          {detail.error && (
            <LensError>
              {errorText(detail.error, { notFound: 'This view is no longer there.' })}
            </LensError>
          )}
          {detail.data && (
            <>
              <Footnote>
                id <span className="font-mono">{middleTruncate(detail.data.id, 20)}</span>
                {detail.data.created_by ? ` · ${detail.data.created_by}` : ''}
                {detail.data.description ? ` · ${detail.data.description}` : ''}
              </Footnote>
              <div className="mt-1 flex items-center gap-1">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  aria-label="View name"
                  className={fieldClass}
                />
                <Button
                  size="xs"
                  disabled={!name.trim() || name === detail.data.name || update.isPending}
                  onClick={() => update.mutate({ viewId: view.id, patch: { name: name.trim() } })}
                >
                  Rename
                </Button>
              </div>
              <Guard>
                Versions are immutable, so a pinned view keeps returning the same rows after the
                next version lands. The selector is the only thing that decides.
              </Guard>
            </>
          )}
        </div>
      )}

      <ConfirmDelete
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete view “${view.name}”?`}
        loses="Removes the saved QuerySpec — projection, filters, sort, limit — and its version selector, for everyone on the team."
        keeps="The dataset, its sheets and every version stay: a view stores no rows. Artifacts already produced from it keep their own retention clock."
        pending={remove.isPending}
        onConfirm={() => {
          // Same reason as the chart panel above: stop reading a row that is
          // about to stop existing.
          setOpen(false);
          remove.mutate({ viewId: view.id, name: view.name });
          setConfirming(false);
        }}
      />
    </div>
  );
}

function ViewsSection({ datasetId }: { datasetId: string | null }) {
  const views = useViews(datasetId);
  const create = useCreateView(datasetId);
  const { latest, sheets } = useLatestSheets(datasetId);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [sheet, setSheet] = useState('');
  const [pinned, setPinned] = useState(false);

  const items = views.data?.items ?? [];
  const total = views.data?.total ?? items.length;
  const pinnedCount = items.filter((v) => v.version_selector?.mode !== 'current').length;

  const submit = () => {
    const chosen = sheet || sheets[0]?.name;
    if (!name.trim() || !chosen) return;
    const selector: VersionSelector =
      pinned && latest != null ? { mode: 'version', version_number: latest } : { mode: 'current' };
    create.mutate(
      {
        name: name.trim(),
        sheet: chosen,
        version_selector: selector,
        query: { limit: 100 },
      },
      {
        onSuccess: () => {
          setName('');
          setAdding(false);
        },
      },
    );
  };

  return (
    <Section
      title={`Saved views (${items.length})`}
      action={
        <Button size="xs" variant="ghost" onClick={() => setAdding((a) => !a)} aria-pressed={adding}>
          <Plus className="size-3" />
          New
        </Button>
      }
    >
      {items.length > 0 && (
        <Stat
          name="pinned"
          value={compact(pinnedCount)}
          coverage={coverage(pinnedCount, items.length, 'views')}
          className="mb-1.5"
        />
      )}

      {adding && (
        <div className="mb-2 space-y-1">
          {sheets.length === 0 ? (
            <Guard>
              A view is saved over one logical sheet, and no sheet is readable for the newest
              version yet. Selection is required here — it is never inferred.
            </Guard>
          ) : (
            <>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="View name"
                aria-label="New view name"
                className={fieldClass}
              />
              <div className="flex gap-1">
                <select
                  value={sheet || sheets[0]?.name || ''}
                  onChange={(e) => setSheet(e.target.value)}
                  aria-label="New view sheet"
                  className={fieldClass}
                >
                  {sheets.map((s) => (
                    <option key={s.sheet_key} value={s.name}>
                      {s.name}
                    </option>
                  ))}
                </select>
                <select
                  value={pinned ? 'pin' : 'current'}
                  onChange={(e) => setPinned(e.target.value === 'pin')}
                  aria-label="New view version selector"
                  className={fieldClass}
                >
                  <option value="current">always latest</option>
                  {latest != null && <option value="pin">pin to v{latest}</option>}
                </select>
              </div>
              <Button size="xs" disabled={!name.trim() || create.isPending} onClick={submit}>
                {create.isPending ? 'Saving…' : 'Save view'}
              </Button>
              <Guard>
                This captures a projection of every column and a page size of 100. Creating it runs
                nothing and writes no artifact — Run is a separate step.
              </Guard>
            </>
          )}
        </div>
      )}

      <LensList
        query={views}
        items={items}
        empty="No saved views. A view is a persisted QuerySpec over one sheet plus a version selector; it stores no rows."
      >
        {(v) => <ViewEntry key={v.id} datasetId={datasetId} view={v} />}
      </LensList>

      {total > items.length && (
        <Footnote className="mt-1">
          Showing {items.length} of {compact(total)} views.
        </Footnote>
      )}
    </Section>
  );
}

/* -------------------------------------------------- analytics definitions */

/**
 * One historical run, with the publish affordance attached to the only thing
 * that can be published: a run that actually wrote an artifact.
 */
function RunEntry({
  datasetId,
  run,
  defaultName,
}: {
  datasetId: string | null;
  run: AnalyticsRun;
  defaultName: string;
}) {
  const publish = usePublishRun(datasetId);
  const [publishing, setPublishing] = useState(false);
  const [mode, setMode] = useState<'new_version' | 'new_dataset'>('new_version');
  const [name, setName] = useState(defaultName);
  const took = duration(run.started_at, run.completed_at);
  const summary = run.result_summary ?? {};
  const facts = Object.entries(summary)
    .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
    .slice(0, 3)
    .map(([k, v]) => `${k} ${typeof v === 'number' ? compact(v) : String(v)}`);

  return (
    <div className="mt-1" data-testid="analysis-run">
      <div className="flex h-4 items-baseline gap-2 text-footnote text-muted-foreground">
        <Status kind={runStatus(run.status)} className="text-footnote">
          {run.status}
        </Status>
        <span className="tabular-nums">{shortDay(run.started_at)}</span>
        {took && <span className="tabular-nums">{took}</span>}
        {run.artifact_id ? (
          <span className="ml-auto shrink-0">wrote an artifact</span>
        ) : (
          <span className="ml-auto shrink-0">no artifact</span>
        )}
      </div>
      {facts.length > 0 && <Footnote className="truncate">{facts.join(' · ')}</Footnote>}
      {run.error && <Footnote className="truncate text-destructive">{run.error}</Footnote>}

      {run.artifact_id && (
        <>
          <Actions>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setPublishing((p) => !p)}
              aria-pressed={publishing}
            >
              {publishing ? 'Cancel publish' : 'Publish'}
            </Button>
          </Actions>
          {publishing && (
            <div className="space-y-1">
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as 'new_version' | 'new_dataset')}
                aria-label="Publish mode"
                className={fieldClass}
              >
                <option value="new_version">new version of this dataset</option>
                <option value="new_dataset">new dataset</option>
              </select>
              {mode === 'new_dataset' && (
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  aria-label="New dataset name"
                  className={fieldClass}
                />
              )}
              <Button
                size="xs"
                disabled={publish.isPending || (mode === 'new_dataset' && !name.trim())}
                onClick={() =>
                  publish.mutate(
                    {
                      runId: run.id,
                      mode,
                      ...(mode === 'new_dataset' ? { name: name.trim() } : {}),
                    },
                    { onSuccess: () => setPublishing(false) },
                  )
                }
              >
                {publish.isPending ? 'Publishing…' : 'Confirm publish'}
              </Button>
              <Guard>
                Publishing promotes the run's output to a `published_source`, which is retained
                forever. It is the only way an artifact escapes its clock.
              </Guard>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RunHistory({
  datasetId,
  definition,
}: {
  datasetId: string | null;
  definition: AnalyticsDefinition;
}) {
  const runs = useDefinitionRuns(datasetId, definition.id);
  const items = runs.data?.items ?? [];
  const succeeded = items.filter((r) => runStatus(r.status) === 'good').length;

  return (
    <div className="mt-1.5">
      {items.length > 0 && (
        <Stat
          name="completed"
          value={compact(succeeded)}
          coverage={coverage(succeeded, items.length, 'runs')}
        />
      )}
      <LensList
        query={runs}
        items={items}
        empty="Never run. The definition is saved configuration; nothing has been computed from it."
        loading="Reading run history…"
      >
        {(r) => (
          <RunEntry key={r.id} datasetId={datasetId} run={r} defaultName={definition.name} />
        )}
      </LensList>
    </div>
  );
}

function DefinitionEntry({
  datasetId,
  definition,
}: {
  datasetId: string | null;
  definition: AnalyticsDefinition;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState(definition.name);
  const detail = useDefinition(datasetId, open ? definition.id : null);
  const run = useRunDefinition(datasetId);
  const update = useUpdateDefinition(datasetId);
  const remove = useDeleteDefinition(datasetId);

  const created = shortDay(definition.created_at);
  // A `join` definition is a real row here and lists with the rest, but it is
  // executed through /joins/execute — which authorizes both sides — so running
  // it from the library is a 400, not a run.
  const runnable = (DEFINITION_KINDS as readonly string[]).includes(definition.kind);

  return (
    <div className="mb-1 rounded-md bg-card px-2 py-1.5" data-testid="saved-analysis">
      <div className="flex items-baseline gap-1.5">
        <span className="truncate text-body">{definition.name}</span>
        <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
          {definition.kind}
        </Identifier>
      </div>
      <div className="mt-0.5 flex items-baseline gap-1.5 text-footnote text-muted-foreground">
        {definition.sheet && <Identifier className="truncate">{definition.sheet}</Identifier>}
        <span className="shrink-0">{selectorWord(definition.version_selector)}</span>
        {definition.created_by && <span className="truncate">{definition.created_by}</span>}
        {created && <span className="ml-auto shrink-0 tabular-nums">{created}</span>}
      </div>
      {definition.description && (
        <p className="mt-1 line-clamp-2 text-micro leading-tight text-muted-foreground">
          {definition.description}
        </p>
      )}

      <Actions>
        <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)} aria-pressed={open}>
          {open ? 'Close' : 'Open'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={!runnable || run.isPending}
          onClick={() => run.mutate({ definitionId: definition.id, name: definition.name })}
        >
          <Play className="size-3" />
          {run.isPending ? 'Running…' : 'Run'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto text-muted-foreground"
          onClick={() => setConfirming(true)}
        >
          <Trash2 className="size-3" />
        </Button>
      </Actions>

      {!runnable && (
        <Guard>
          A `{definition.kind}` definition cannot be run from the library — joins go through
          /joins/execute, which authorizes both sides.
        </Guard>
      )}
      {run.error ? <LensError>{errorText(run.error)}</LensError> : null}
      {run.data && run.data.definition_id === definition.id && (
        <div className="mt-1.5">
          <div className="flex h-4 items-baseline gap-2 text-footnote text-muted-foreground">
            <Status kind={runStatus(run.data.status)} className="text-footnote">
              {run.data.status}
            </Status>
            {duration(run.data.started_at, run.data.completed_at) && (
              <span className="tabular-nums">
                {duration(run.data.started_at, run.data.completed_at)}
              </span>
            )}
            <span className="ml-auto shrink-0">
              {run.data.artifact_id ? 'wrote an artifact' : 'no artifact written'}
            </span>
          </div>
          {!run.data.artifact_id && definition.kind === 'profile' && (
            <Guard>
              A profile run returns statistics and stores no file, so there is nothing to publish
              and nothing on a retention clock.
            </Guard>
          )}
        </div>
      )}

      {open && (
        <div className="mt-1.5">
          {detail.isLoading && <LensLoading>Re-reading…</LensLoading>}
          {detail.error && (
            <LensError>
              {errorText(detail.error, { notFound: 'This definition is no longer there.' })}
            </LensError>
          )}
          {detail.data && (
            <>
              <Footnote className="truncate" title={JSON.stringify(detail.data.params ?? {})}>
                params {middleTruncate(JSON.stringify(detail.data.params ?? {}), 46)}
              </Footnote>
              <div className="mt-1 flex items-center gap-1">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  aria-label="Definition name"
                  className={fieldClass}
                />
                <Button
                  size="xs"
                  disabled={!name.trim() || name === detail.data.name || update.isPending}
                  onClick={() =>
                    update.mutate({ definitionId: definition.id, patch: { name: name.trim() } })
                  }
                >
                  Rename
                </Button>
              </div>
              {/* `kind` is deliberately absent from DefinitionUpdate: a
                  different kind is a different object, not an edit. */}
              <Guard>
                Name, sheet, selector and params are patchable; the kind is not. Renaming onto a
                sibling's name is a 409 — names are unique per dataset.
              </Guard>
            </>
          )}
          <RunHistory datasetId={datasetId} definition={definition} />
        </div>
      )}

      <ConfirmDelete
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete definition “${definition.name}”?`}
        loses="Removes the saved config and its run history — the record of what was computed, and when."
        keeps="No dataset and no version is touched: a definition owns no data. Artifacts already written keep their own retention clock, and anything already published stays a dataset forever."
        pending={remove.isPending}
        onConfirm={() => {
          remove.mutate({ definitionId: definition.id, name: definition.name });
          setConfirming(false);
        }}
      />
    </div>
  );
}

function DefinitionsSection({
  datasetId,
  query,
  items,
}: {
  datasetId: string | null;
  query: { isLoading: boolean; error: unknown };
  items: AnalyticsDefinition[];
}) {
  const create = useCreateDefinition(datasetId);
  const { sheets } = useLatestSheets(datasetId);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [sheet, setSheet] = useState('');

  const kindTally = items.reduce<Record<string, number>>((acc, d) => {
    acc[d.kind] = (acc[d.kind] ?? 0) + 1;
    return acc;
  }, {});

  const submit = () => {
    const chosen = sheet || sheets[0]?.name || null;
    if (!name.trim()) return;
    create.mutate(
      {
        name: name.trim(),
        kind: 'profile',
        sheet: chosen,
        version_selector: { mode: 'current' },
        // The published defaults of `ProfileRequest`, sent explicitly so the
        // stored definition says the same thing this panel does.
        params: { include_histograms: true, include_duplicates: true, top_n: 10 },
      },
      {
        onSuccess: () => {
          setName('');
          setAdding(false);
        },
      },
    );
  };

  return (
    <Section
      title={`Saved analyses (${items.length})`}
      action={
        <Button size="xs" variant="ghost" onClick={() => setAdding((a) => !a)} aria-pressed={adding}>
          <Plus className="size-3" />
          New
        </Button>
      }
    >
      {items.length > 0 && (
        <Footnote className="mb-1.5">
          {Object.entries(kindTally)
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([k, n]) => `${k} ${n}`)
            .join(' · ')}
        </Footnote>
      )}

      {adding && (
        <div className="mb-2 space-y-1">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Definition name"
            aria-label="New definition name"
            className={fieldClass}
          />
          <div className="flex gap-1">
            <select value="profile" aria-label="New definition kind" className={fieldClass} disabled>
              {DEFINITION_KINDS.map((k) => (
                <option key={k} value={k} disabled={k !== 'profile'}>
                  {k}
                </option>
              ))}
            </select>
            <select
              value={sheet || sheets[0]?.name || ''}
              onChange={(e) => setSheet(e.target.value)}
              aria-label="New definition sheet"
              className={fieldClass}
              disabled={sheets.length === 0}
            >
              {sheets.map((s) => (
                <option key={s.sheet_key} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <Button size="xs" disabled={!name.trim() || create.isPending} onClick={submit}>
            {create.isPending ? 'Saving…' : 'Save definition'}
          </Button>
          <Guard>
            There are four kinds, and only `profile` is composable here: a definition's params are
            the whole body of the operation it saves, so a `sample` needs a target volume and steps
            and an `aggregate` a group-by and metrics. Those builders are the Sampling, Aggregate
            and Pivot pages — this dock will not invent them.
          </Guard>
        </div>
      )}

      <LensList
        query={query}
        items={items}
        empty="No saved analyses. A definition is saved configuration — running it is a separate step, and the run writes an artifact."
      >
        {(d) => <DefinitionEntry key={d.id} datasetId={datasetId} definition={d} />}
      </LensList>
    </Section>
  );
}

/* ------------------------------------------------------------------- lens */

export function LibraryLens({ datasetId }: { datasetId: string | null }) {
  const artifacts = useArtifacts(datasetId);
  const saved = useDefinitions(datasetId);
  const views = useViews(datasetId);

  const items = artifacts.data?.items ?? [];
  const defs = saved.data?.items ?? [];
  const viewItems = views.data?.items ?? [];

  const rows = describeAll(items);
  const groups = groupByKind(rows);
  const totalBytes = rows.reduce((sum, r) => sum + (r.artifact.size_bytes ?? 0), 0);
  const stored = sizeParts(totalBytes);
  // Kept apart on purpose: "gone in a week" and "already eligible for the
  // sweep" are different decisions, and one number covering both would hide the
  // second behind the first.
  const overdue = rows.filter((r) => r.remaining != null && r.remaining <= 0).length;
  const expiring = rows.filter(
    (r) => r.remaining != null && r.remaining > 0 && r.remaining <= 7,
  ).length;
  const imminent = rows.some((r) => r.remaining != null && r.remaining > 0 && r.remaining <= 2);
  const permanent = rows.filter((r) => r.ttl === null).length;

  // `total` is the count of every artifact this seat can see, not this
  // dataset's — the endpoint has no dataset filter, so the page is fetched and
  // filtered here. Past one page, this dataset's list can be missing rows, and
  // a short list that looks complete is the failure worth naming.
  const truncated = (artifacts.data?.total ?? 0) > ARTIFACT_PAGE;

  return (
    <>
      {rows.length > 0 && (
        <div className="mb-4">
          <div className="flex items-start gap-6">
            <Metric
              size="figure"
              label="Artifacts"
              value={compact(rows.length)}
              note={`${groups.length} kind${groups.length === 1 ? '' : 's'}`}
            />
            <Metric
              size="figure"
              label="Stored"
              value={stored.value}
              unit={stored.unit}
              note={permanent > 0 ? `${permanent} kept forever` : 'all on a clock'}
              data-testid="library-total"
            />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            {overdue > 0 && (
              <Status kind="critical" className="text-small" data-testid="library-overdue">
                {overdue} past window
              </Status>
            )}
            {expiring > 0 && (
              <Status
                kind={imminent ? 'critical' : 'serious'}
                className="text-small"
                data-testid="library-expiring"
              >
                {expiring} expire{expiring === 1 ? 's' : ''} within 7 days
              </Status>
            )}
            {overdue === 0 && expiring === 0 && (
              <Footnote>Nothing expires in the next 7 days.</Footnote>
            )}
          </div>

          {truncated && (
            <Footnote className="mt-1">
              Showing the first {ARTIFACT_PAGE} artifacts visible to this seat, of{' '}
              {compact(artifacts.data?.total)} across all datasets — older outputs of this dataset
              may not be listed.
            </Footnote>
          )}
        </div>
      )}

      <UsageSection datasetId={datasetId} />

      <Section title={`Artifacts (${rows.length})`}>
        <LensList
          query={artifacts}
          items={groups}
          empty="Nothing derived yet. Query results, exports and transform outputs land here."
        >
          {(g) => (
            <div key={g.kind} className="mb-3 last:mb-0" data-testid="artifact-group">
              {/* A group is made by space and a heading, never by a box around
                  rows that already have their own surface. */}
              <div className="mb-1 flex items-baseline gap-2">
                <Identifier className="text-micro text-foreground">{g.kind}</Identifier>
                <span className="text-footnote text-muted-foreground tabular-nums">
                  {g.rows.length} · {formatSizeWide(g.bytes)}
                  {totalBytes > 0 && ` · ${Math.round((g.bytes / totalBytes) * 100)}%`}
                </span>
                <span className="ml-auto text-footnote text-muted-foreground">
                  {retentionLabel(g)}
                </span>
              </div>
              {g.rows.map((row) => (
                <ArtifactEntry key={row.artifact.key} row={row} />
              ))}
            </div>
          )}
        </LensList>
      </Section>

      <ChartsSection datasetId={datasetId} definitions={defs} views={viewItems} />

      <ViewsSection datasetId={datasetId} />

      <DefinitionsSection datasetId={datasetId} query={saved} items={defs} />

      {/* The architectural truths of this surface. A left rule and an indent,
          not a stack of boxes — and all of it in the footnote register, so the
          figures above stay the first read. */}
      <div className="space-y-1.5">
        <Guard>
          A chart, a view and a definition are configuration. Saving one computes nothing; the run
          is a separate step, and only a run writes an artifact — a `profile` run not even that.
          Deleting any of them removes an annotation, never a row.
        </Guard>
        {rows.length > 0 && (
          <>
            <Guard>
              Retention is stamped when an artifact is written, so editing the policy later never
              shortens something already stored.{' '}
              <span className="text-foreground">Publish</span> is the way to keep one: it promotes
              the output to a published source, retained forever.
            </Guard>
            <Guard>
              Nothing runs the sweep on a schedule. An artifact past its window is eligible for
              collection, not collected — it stays listed and downloadable until a sweep is actually
              run, so treat “past window” as gone rather than as still available.
            </Guard>
            <Guard>
              This list comes from the artifacts table, never a bucket scan. The row is the only
              thing that maps a filename to a storage key, so a blob written without one is
              unreachable by everyone, superusers included.
            </Guard>
          </>
        )}
      </div>
    </>
  );
}
