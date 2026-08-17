/**
 * The datasets page: one surface, three regions.
 *
 *   rail (catalog + upload)  |  header + persistent table  |  lens panel
 *
 * The whole page is a fixed-height flex row that owns its own scrolling —
 * `html, body, #root` are all `overflow: hidden`, so anything that scrolls has
 * to contain it or the content is silently clipped.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Check, Lock } from 'lucide-react';
import { errorText } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { Footnote } from '@/shared/components/instrument/Typography';
import { rememberDataset } from '@/app/shell/StudioCommandPalette';
import { CockpitStrip } from './CockpitStrip';
import { QueryTokenRow, type QueryToken } from './QueryTokenRow';
import { cn } from '@/shared/lib/utils';
import { isWideTable, type ShapeColumn } from '@/shared/components/instrument/shape';
import {
  useAuthMe,
  useDatasetCatalog,
  useQualityRules,
  useRows,
  useSheets,
  useVersions,
  type QuerySpec,
} from '../hooks/useDatasets';
import { ColumnManager } from './ColumnManager';
import { DatasetRail } from './DatasetRail';
import { DataSurface } from './DataSurface';
import { LensPanel, type LensId } from './LensPanel';
import { UploadDialog } from './UploadDialog';

const PAGE_SIZE = 50;

/**
 * Classification is a LABEL and enforces nothing — it is tinted, never badged,
 * so it cannot be mistaken for the lock that masking actually is.
 */
function classificationTone(c?: string | null): string {
  if (c === 'restricted') return 'text-[var(--st-serious)]';
  if (c === 'confidential') return 'text-[var(--st-warn)]';
  return 'text-muted-foreground';
}

interface DatasetsPageProps {
  /** Preselected dataset, e.g. from the catalog's `?dataset=<id>`. */
  initialDatasetId?: string | null;
}

export function DatasetsPage({ initialDatasetId = null }: DatasetsPageProps) {
  const [search, setSearch] = useState('');
  const [lens, setLens] = useState<LensId>('overview');
  // Each selection is a *preference*, resolved against live data during render.
  // Storing the resolved value instead would mean syncing it from an effect on
  // every fetch, which cascades renders (and the repo's lint rule forbids it).
  const [pickedId, setPickedId] = useState<string | null>(initialDatasetId);
  const [pickedVersion, setPickedVersion] = useState<number | null>(null);
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  // Cursor history, so "previous" is possible with opaque forward-only cursors.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  // The rail is contextual. On a wide sheet it becomes a COLUMN MANAGER (R1) —
  // a flat list of 183 columns is a 6,000px scroll that answers no question.
  const [railMode, setRailMode] = useState<'datasets' | 'columns' | null>(null);
  const [focusedColumn, setFocusedColumn] = useState<string | null>(null);
  // Filters applied to the grid. The full builder lives at /query; this row is
  // the always-visible summary of what is narrowing the sheet right now.
  const [queryTokens, setQueryTokens] = useState<QueryToken[]>([]);

  const navigate = useNavigate();
  // The ROLE the service is acting on, not the label stored at switch time.
  //
  // `identity.label` is written to localStorage once by the seat switcher and
  // never reconciled, so after an admin changes a seat's role the row went on
  // claiming the old one — while the service masked per the new one. A row
  // whose whole job is "this count is what THIS seat sees" must not be able to
  // name the wrong seat. `/auth/me` is already fetched, and it is the service's
  // own answer.
  const me = useAuthMe();
  const actingRole = me.data?.memberships?.[0]?.role ?? null;
  const identityUserId = useIdentityStore((st) => st.identity.userId);

  // Two reads of the same endpoint, for two different jobs.
  //
  // `railCatalog` is what the rail LISTS, and it narrows as you type. `known`
  // is what the selection is RESOLVED against, and never narrows. Sharing one
  // filtered query for both meant typing in the search box could silently swap
  // the dataset on screen: the moment your current dataset stopped matching the
  // filter it fell out of the list, and the page fell back to whatever was
  // first. You would be reading one dataset's rows under another's name.
  //
  // With an empty search both calls resolve to the same query key, so this
  // costs one request in the common case.
  const trimmed = search.trim();
  const railCatalog = useDatasetCatalog(trimmed ? { q: trimmed } : {});
  const known = useDatasetCatalog({});

  const datasets = useMemo(() => railCatalog.data?.items ?? [], [railCatalog.data]);
  const knownItems = useMemo(() => known.data?.items ?? [], [known.data]);

  // A selection, once made, is sticky. Falling back to the first dataset is
  // only for the opening state, where nothing has been chosen yet.
  const selectedId = pickedId ?? knownItems[0]?.id ?? null;
  const dataset =
    knownItems.find((d) => d.id === selectedId) ??
    datasets.find((d) => d.id === selectedId) ??
    null;

  const versionsQuery = useVersions(selectedId);
  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data]);

  // Default to the newest version; a picked version only survives if the current
  // dataset actually has it.
  const version =
    pickedVersion != null && versions.some((v) => v.version_number === pickedVersion)
      ? pickedVersion
      : versions.length > 0
        ? Math.max(...versions.map((v) => v.version_number))
        : null;

  const sheetsQuery = useSheets(selectedId, version);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);

  // Resolving the sheet against the *currently loaded* list is also what stops a
  // dataset switch from pairing the new id with the previous dataset's sheet
  // name, which the API answers with a 404.
  const sheet =
    pickedSheet && sheets.some((s) => s.name === pickedSheet)
      ? pickedSheet
      : (sheets[0]?.name ?? null);

  // The metadata routes address a sheet by its stable `sheet_key`, not the
  // display `name` — the two diverge after a confirmed rename, so resolve it
  // here rather than letting each lens guess.
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;
  const sheetKey = sheetRow?.sheet_key ?? null;
  const sheetColumns = useMemo(() => sheetRow?.columns ?? [], [sheetRow]);

  // Shape state, derived once and shared by the rail and the grid so both
  // adapt on the same evidence rather than each deciding for itself.
  const shapeColumns = useMemo<ShapeColumn[]>(
    () => sheetColumns.map((c) => ({ name: c.name, dtype: c.dtype ?? null })),
    [sheetColumns],
  );
  const wideSheet = isWideTable(shapeColumns.length);
  const effectiveRailMode = railMode ?? (wideSheet ? 'columns' : 'datasets');

  const spec = useMemo<QuerySpec>(() => ({ limit: PAGE_SIZE }), []);

  // Rendered once per load rather than ticking: a clock that moves on its own
  // implies a live feed, and this surface polls nothing.
  // Feed the palette's Recent group. Written during render is wrong, so this
  // rides the same memo the dataset identity does and only fires when the
  // resolved dataset actually changes.
  useEffect(() => {
    if (dataset) rememberDataset(identityUserId, dataset.id, dataset.name);
  }, [dataset, identityUserId]);

  const syncedAt = useMemo(
    () => new Date().toLocaleTimeString(undefined, { hour12: false }),
    [],
  );

  /** A cursor is bound to (version, spec); changing either invalidates paging. */
  const resetPaging = () => {
    setCursors([null]);
    setPageIndex(0);
  };

  const rowsQuery = useRows(selectedId, version, sheet, spec, cursors[pageIndex] ?? null, Boolean(sheet));
  const rulesQuery = useQualityRules(selectedId);

  /** Masked for THIS seat — derived once and passed down, never recomputed. */
  const maskedColumns = useMemo(
    () => rowsQuery.data?.masked_columns ?? [],
    [rowsQuery.data],
  );

  // Surface the FIRST thing that failed, not just the row query. A dataset that
  // does not exist (or is not visible to this seat) fails at `versions`, which
  // leaves the row query disabled and would otherwise render an empty grid with
  // no explanation — indistinguishable from a dataset that is genuinely empty.
  const error = versionsQuery.error ?? sheetsQuery.error ?? rowsQuery.error;
  const message = error
    ? errorText(error, {
        // More specific than the generic 404 wording, because at this point we
        // know the failure was loading a dataset. Still says nothing about
        // whether it exists — cross-tenant reads are 404 by design.
        notFound: 'This dataset has no readable version, or is not available to this seat.',
      })
    : null;

  const handleNext = () => {
    const next = rowsQuery.data?.next_cursor;
    if (!next) return;
    setCursors((cs) => {
      const copy = cs.slice(0, pageIndex + 1);
      copy.push(next);
      return copy;
    });
    setPageIndex((i) => i + 1);
  };

  // Every selection change invalidates the cursor, so reset paging alongside it.
  const selectDataset = (id: string) => {
    setPickedId(id);
    setPickedSheet(null);
    setPickedVersion(null);
    resetPaging();
  };
  const selectVersion = (v: number) => {
    setPickedVersion(v);
    setPickedSheet(null);
    resetPaging();
  };
  const selectSheet = (s: string) => {
    setPickedSheet(s);
    resetPaging();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Identity, brand and route nav now live in the studio shell
       * (`app/shell/StudioShell.tsx`). What stays here is the part that is
       * about the OBJECT rather than the app: which dataset, which version,
       * how it is classified. */}
      {/* Tenant scope, before any one dataset. */}
      <CockpitStrip
        datasets={knownItems}
        total={known.data?.total ?? null}
        maskedHere={maskedColumns.length}
        scopeName={dataset?.name ?? null}
        syncedAt={syncedAt}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[270px] shrink-0 flex-col bg-card shadow-[1px_0_0_var(--r1)]">
          {/* Two modes, and the default is chosen by the DATA, not by a stored
           * preference: above 30 columns the column manager is what the screen
           * is actually for. An explicit click still wins. */}
          {wideSheet && (
            <div className="flex gap-0.5 p-1.5" role="tablist" aria-label="Rail mode">
              {(['datasets', 'columns'] as const).map((m) => (
                <button
                  key={m}
                  role="tab"
                  aria-selected={effectiveRailMode === m}
                  onClick={() => setRailMode(m)}
                  data-testid={`rail-mode-${m}`}
                  className={cn(
                    'flex-1 rounded px-2 py-1 text-micro capitalize transition-colors',
                    effectiveRailMode === m
                      ? 'bg-accent font-medium text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          )}

          {effectiveRailMode === 'columns' ? (
            <ColumnManager
              columns={shapeColumns}
              rowCount={sheetRow?.row_count ?? null}
              selected={focusedColumn}
              onSelect={setFocusedColumn}
            />
          ) : (
            <DatasetRail
              datasets={datasets}
              selectedId={selectedId}
              onSelect={selectDataset}
              search={search}
              onSearchChange={setSearch}
              loading={railCatalog.isLoading}
              onUploadClick={() => setUploadOpen(true)}
            />
          )}
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {/* The object header: what this dataset IS, and how it is governed.
           * Everything about the app itself lives in the shell above. */}
          {dataset && (
            <header className="shrink-0 px-3 pt-2.5 pb-1.5" data-testid="dataset-header">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-[7px] shrink-0 rounded-full bg-[var(--st-good)]"
                />
                <h1 className="min-w-0 truncate text-lead font-medium text-foreground">
                  {dataset.name}
                </h1>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-micro">
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Check className="size-2.5" />
                  {dataset.validation_status === 'passed' ? 'Validated' : 'Not validated'}
                </span>
                <span className={classificationTone(dataset.classification)}>
                  {dataset.classification ?? 'unclassified'}
                </span>
                {maskedColumns.length > 0 && (
                  <span className="flex items-center gap-1 rounded bg-[var(--st-warn)]/12 px-1.5 py-0.5 text-[var(--st-warn)]">
                    <Lock className="size-2.5" />
                    {maskedColumns.length} column{maskedColumns.length === 1 ? '' : 's'} masked
                  </span>
                )}
                {dataset.domain && (
                  <span className="text-muted-foreground">
                    domain <span className="text-foreground">{dataset.domain}</span>
                  </span>
                )}
              </div>
              {/* One footnote register absorbs all provenance. */}
              <Footnote className="mt-1 truncate font-mono">
                {dataset.source_system ?? 'manual upload'} ·{' '}
                {(dataset.row_count ?? 0).toLocaleString()} rows · {sheetColumns.length} cols ·
                as-of v{version ?? '—'}
              </Footnote>
            </header>
          )}

          <QueryTokenRow
            tokens={queryTokens}
            onRemove={(i) => setQueryTokens((t) => t.filter((_, n) => n !== i))}
            onAdd={() => navigate({ to: '/query', search: { dataset: selectedId ?? undefined } })}
            matched={rowsQuery.data?.total ?? null}
            total={dataset?.row_count ?? null}
            maskedColumns={maskedColumns}
            role={actingRole}
          />

          <DataSurface
            versions={versions}
            sheets={sheets}
            version={version}
            sheet={sheet}
            onVersionChange={selectVersion}
            onSheetChange={selectSheet}
            page={rowsQuery.data}
            loading={rowsQuery.isFetching}
            error={message}
            onNext={handleNext}
            onPrev={() => setPageIndex((i) => Math.max(0, i - 1))}
            canPrev={pageIndex > 0}
            rowOffset={pageIndex * PAGE_SIZE}
          />
        </main>

        <LensPanel
          lens={lens}
          onLensChange={setLens}
          dataset={dataset}
          datasetId={selectedId}
          versions={versions}
          version={version}
          sheet={sheet}
          sheetKey={sheetKey}
          columns={sheetColumns}
          rules={rulesQuery.data?.items ?? []}
          rulesLoading={rulesQuery.isLoading}
          maskedColumns={maskedColumns}
        />
      </div>

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        currentDatasetId={selectedId}
        currentDatasetName={dataset?.name ?? null}
        // Jump to whatever was produced — a new dataset, or the dataset that
        // just gained a version. Clearing the picked version lets the newest
        // one win, which is what "I just uploaded this" should show.
        onUploaded={(id) => {
          setPickedId(id);
          setPickedVersion(null);
          setPickedSheet(null);
          resetPaging();
        }}
      />
    </div>
  );
}
