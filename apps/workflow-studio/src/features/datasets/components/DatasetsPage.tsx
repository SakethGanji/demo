/**
 * The datasets page: one surface, three regions.
 *
 *   rail (catalog + upload)  |  header + persistent table  |  lens panel
 *
 * The whole page is a fixed-height flex row that owns its own scrolling —
 * `html, body, #root` are all `overflow: hidden`, so anything that scrolls has
 * to contain it or the content is silently clipped.
 */

import { useMemo, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ChevronLeft } from 'lucide-react';
import { AnalyticsApiError } from '@/shared/lib/analyticsClient';
import { Badge } from '@/shared/components/ui/badge';
import {
  useDatasetCatalog,
  useQualityRules,
  useRows,
  useSheets,
  useVersions,
  type QuerySpec,
} from '../hooks/useDatasets';
import { DatasetRail } from './DatasetRail';
import { DataSurface } from './DataSurface';
import { LensPanel, type LensId } from './LensPanel';
import { SeatSwitcher } from './SeatSwitcher';
import { UploadDialog } from './UploadDialog';

const PAGE_SIZE = 50;

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

  const spec = useMemo<QuerySpec>(() => ({ limit: PAGE_SIZE }), []);

  /** A cursor is bound to (version, spec); changing either invalidates paging. */
  const resetPaging = () => {
    setCursors([null]);
    setPageIndex(0);
  };

  const rowsQuery = useRows(selectedId, version, sheet, spec, cursors[pageIndex] ?? null, Boolean(sheet));
  const rulesQuery = useQualityRules(selectedId);

  // Surface the FIRST thing that failed, not just the row query. A dataset that
  // does not exist (or is not visible to this seat) fails at `versions`, which
  // leaves the row query disabled and would otherwise render an empty grid with
  // no explanation — indistinguishable from a dataset that is genuinely empty.
  const error = versionsQuery.error ?? sheetsQuery.error ?? rowsQuery.error;
  const errorText =
    error instanceof AnalyticsApiError
      ? // Cross-tenant reads are 404 by design; never say "access denied".
        error.isNotFound
        ? 'This dataset has no readable version, or is not available to this seat.'
        : `${error.code}: ${error.detail}`
      : error
        ? String(error)
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
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Top bar — keeps the page inside "one UI" without touching the other routes. */}
      <header className="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <Link
          to="/projects"
          className="flex items-center gap-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronLeft className="size-3" />
          Projects
        </Link>
        <span className="text-border">/</span>
        <Link
          to="/catalog"
          className="text-[13px] font-medium transition-colors hover:text-primary"
        >
          Catalog
        </Link>

        {dataset && (
          <>
            <span className="text-border">/</span>
            <span className="text-[13px]">{dataset.name}</span>
            <Badge variant="glass">v{dataset.current_version ?? '—'}</Badge>
            {dataset.classification && (
              <Badge variant="glass">{dataset.classification}</Badge>
            )}
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          <SeatSwitcher />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <DatasetRail
          datasets={datasets}
          selectedId={selectedId}
          onSelect={selectDataset}
          search={search}
          onSearchChange={setSearch}
          loading={railCatalog.isLoading}
          onUploadClick={() => setUploadOpen(true)}
        />

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <DataSurface
            versions={versions}
            sheets={sheets}
            version={version}
            sheet={sheet}
            onVersionChange={selectVersion}
            onSheetChange={selectSheet}
            page={rowsQuery.data}
            loading={rowsQuery.isFetching}
            error={errorText}
            onNext={handleNext}
            onPrev={() => setPageIndex((i) => Math.max(0, i - 1))}
            canPrev={pageIndex > 0}
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
          maskedColumns={rowsQuery.data?.masked_columns ?? []}
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
