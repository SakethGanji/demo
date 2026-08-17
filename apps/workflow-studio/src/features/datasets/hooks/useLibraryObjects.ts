/**
 * The saved objects a dataset carries beside its rows.
 *
 * Three kinds live here — charts, saved views and analytics definitions — plus
 * the two facts that describe the dataset *as an entry in the library* (its
 * usage counters and this seat's favourite mark) and the reader that turns a
 * stored artifact back into rows.
 *
 * One distinction governs the whole module, and every caller has to keep it:
 *
 *   **A saved object is CONFIGURATION. Running it is a separate step.**
 *
 * A definition stores a kind plus params; a view stores a QuerySpec plus a
 * version selector; a chart stores a name, a type and a binding to one of the
 * other two. None of them stores rows. `POST .../run` on a definition is what
 * writes an artifact — and even then not always: a `profile` run returns
 * statistics and writes nothing, so there is no artifact to publish. `POST
 * .../render` on a chart re-runs its bound source and persists nothing at all.
 *
 * The rest of the contract, as the service actually enforces it:
 *
 *  - **Running a definition needs raw access**, not merely write. `POST /run`
 *    returns the result rows unmasked, so it is gated by `ensure_raw_access`
 *    exactly like `/aggregate` — an editor on a dataset with sensitive columns
 *    gets `403 sensitive-data-restricted`, and the saved definition is not a
 *    second door around masking.
 *  - **A `join` definition lists here but cannot be run here** (400
 *    `kind-not-runnable`): joins are executed through `/joins/execute`, which
 *    authorizes both sides.
 *  - **DELETE removes an annotation, never data.** A chart, a view and a
 *    definition each own no rows; deleting one loses no dataset and no version.
 *    A definition's *run history* does go with it, which is the one thing worth
 *    saying out loud before the click.
 *  - Names are unique per dataset: create and rename both answer a collision
 *    with 409 (`definition-name-taken` and its siblings).
 *  - Cross-tenant reads are **404, never 403** — 404 hides existence. Nothing
 *    here may render "access denied" for a missing id.
 *
 * `retry: false` throughout, for the same reason as `useAnalysis.ts`: a refusal
 * and a 404 are answers, and retrying them three times only delays the answer.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, errorText, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { MAX_PAGE_LIMIT, useDatasetCatalog } from './useDatasets';

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/**
 * Shared write wiring: invalidate everything this seat holds, then say what
 * happened. Coarse on purpose — publishing a run creates a dataset, running a
 * definition writes an artifact, and a stale panel beside a fresh one is worse
 * than one extra fetch.
 */
function useLibraryMutation<TData, TVars>(
  fn: (vars: TVars) => Promise<TData>,
  success: (data: TData, vars: TVars) => string,
) {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<TData, unknown, TVars>({
    mutationFn: fn,
    onSuccess: (data, vars) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(success(data, vars));
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ------------------------------------------------------------------ charts */

/** `ChartOut`. `chart_type` is a bare string on the wire, not an enum. */
export interface SavedChart {
  id: string;
  dataset_id: string;
  definition_id?: string | null;
  view_id?: string | null;
  name: string;
  description?: string | null;
  chart_type: string;
  config?: Record<string, unknown>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

/** The seven `ChartCreate.chart_type` values. There are no others. */
export const CHART_TYPES = [
  'bar',
  'line',
  'area',
  'scatter',
  'pie',
  'table',
  'kpi',
] as const;

export type ChartType = (typeof CHART_TYPES)[number];

/** `ChartCreate`. Exactly one of `definition_id` / `view_id`. */
export interface ChartCreate {
  name: string;
  description?: string | null;
  chart_type: ChartType;
  definition_id?: string | null;
  view_id?: string | null;
  config?: Record<string, unknown>;
}

/** `ChartUpdate`. Omitted keeps; retargeting the source clears the other side. */
export interface ChartPatch {
  name?: string;
  description?: string | null;
  chart_type?: ChartType;
  definition_id?: string;
  view_id?: string;
  config?: Record<string, unknown>;
}

/** `ChartSeries` — aligned positionally to the shared category axis. */
export interface ChartSeries {
  name: string;
  data?: unknown[];
}

/** `ChartRenderResponse`. Nothing is persisted: rendering is a read. */
export interface ChartRender {
  chart_id: string;
  chart_type: string;
  categories?: string[];
  series?: ChartSeries[];
  x_field?: string | null;
  y_fields?: string[];
  series_field?: string | null;
  row_count?: number;
  /**
   * Rows the source matched in total. Larger than `row_count` means only the
   * first page was read; `null` with `truncated` means the source clipped the
   * result without reporting a total.
   */
  total_rows?: number | null;
  masked_columns?: string[];
  source?: Record<string, unknown>;
  truncated?: boolean;
}

export function useCharts(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'charts', datasetId],
    queryFn: () =>
      analytics.get<Page<SavedChart>>(`/datasets/${datasetId}/charts`, {
        limit: MAX_PAGE_LIMIT,
      }),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/**
 * One chart, re-read from the server.
 *
 * Not redundant with the list: the row on screen is a copy taken when the list
 * was fetched, and an edit form built from it would silently overwrite whatever
 * a colleague changed in between. Opening a chart re-reads it, so what is
 * edited is what the server currently holds.
 */
export function useChart(datasetId: string | null, chartId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'chart', datasetId, chartId],
    queryFn: () => analytics.get<SavedChart>(`/datasets/${datasetId}/charts/${chartId}`),
    enabled: Boolean(datasetId) && Boolean(chartId),
    retry: false,
  });
}

export function useCreateChart(datasetId: string | null) {
  return useLibraryMutation<SavedChart, ChartCreate>(
    (body) => analytics.post<SavedChart>(`/datasets/${datasetId}/charts`, body),
    (d) => `Chart "${d.name}" saved.`,
  );
}

export function useUpdateChart(datasetId: string | null) {
  return useLibraryMutation<SavedChart, { chartId: string; patch: ChartPatch }>(
    ({ chartId, patch }) =>
      analytics.patch<SavedChart>(`/datasets/${datasetId}/charts/${chartId}`, patch),
    (d) => `Chart "${d.name}" updated.`,
  );
}

/** 204. Removes the chart row only — the bound source and its data stay. */
export function useDeleteChart(datasetId: string | null) {
  return useLibraryMutation<unknown, { chartId: string; name: string }>(
    ({ chartId }) => analytics.del(`/datasets/${datasetId}/charts/${chartId}`),
    (_d, v) => `Chart "${v.name}" deleted.`,
  );
}

/**
 * Re-run the chart's bound source and shape the result. A read: no artifact,
 * no run row, nothing stored. Kept a mutation rather than a query because it is
 * an action with a cost, taken on demand.
 */
export function useRenderChart(datasetId: string | null) {
  return useMutation<ChartRender, unknown, string>({
    mutationFn: (chartId) =>
      analytics.post<ChartRender>(`/datasets/${datasetId}/charts/${chartId}/render`),
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ------------------------------------------------------------- saved views */

/** `DatasetViewOut`. Stores a QuerySpec and a selector — never rows. */
export interface SavedView {
  id: string;
  dataset_id: string;
  logical_sheet_id: string;
  sheet_key?: string | null;
  sheet_name?: string | null;
  name: string;
  description?: string | null;
  version_selector: Record<string, unknown>;
  query: Record<string, unknown>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

/** Which version a saved object runs against. `current` follows the latest. */
export interface VersionSelector {
  mode: 'current' | 'tag' | 'version';
  tag?: string | null;
  version_number?: number | null;
}

/** `DatasetViewIn`. `sheet` is required — selection is never inferred. */
export interface ViewCreate {
  name: string;
  description?: string | null;
  sheet: string;
  version_selector?: VersionSelector;
  query?: Record<string, unknown>;
}

/** `DatasetViewUpdate`. Omitted fields keep their value. */
export interface ViewPatch {
  name?: string;
  description?: string | null;
  sheet?: string;
  version_selector?: VersionSelector;
  query?: Record<string, unknown>;
}

/** `QueryPage` — cursor-paged, unlike the offset-based `Page[T]`. */
export interface QueryResultPage {
  items: Record<string, unknown>[];
  next_cursor?: string | null;
  total?: number | null;
  masked_columns?: string[];
}

/** `ViewRunResponse` — the view executed against its selector-pinned version. */
export interface ViewRun {
  view_id: string;
  version_number: number;
  sheet_name: string;
  result: QueryResultPage;
}

export function useViews(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'views', datasetId],
    queryFn: () =>
      analytics.get<Page<SavedView>>(`/datasets/${datasetId}/views`, { limit: MAX_PAGE_LIMIT }),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/** One view, re-read. Same reason as `useChart`. */
export function useView(datasetId: string | null, viewId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'view', datasetId, viewId],
    queryFn: () => analytics.get<SavedView>(`/datasets/${datasetId}/views/${viewId}`),
    enabled: Boolean(datasetId) && Boolean(viewId),
    retry: false,
  });
}

export function useCreateView(datasetId: string | null) {
  return useLibraryMutation<SavedView, ViewCreate>(
    (body) => analytics.post<SavedView>(`/datasets/${datasetId}/views`, body),
    (d) => `View "${d.name}" saved. Nothing has run yet.`,
  );
}

export function useUpdateView(datasetId: string | null) {
  return useLibraryMutation<SavedView, { viewId: string; patch: ViewPatch }>(
    ({ viewId, patch }) => analytics.patch<SavedView>(`/datasets/${datasetId}/views/${viewId}`, patch),
    (d) => `View "${d.name}" updated.`,
  );
}

/** 204. The QuerySpec goes; the sheet, the versions and any artifact stay. */
export function useDeleteView(datasetId: string | null) {
  return useLibraryMutation<unknown, { viewId: string; name: string }>(
    ({ viewId }) => analytics.del(`/datasets/${datasetId}/views/${viewId}`),
    (_d, v) => `View "${v.name}" deleted.`,
  );
}

/**
 * Execute a saved view. Reads rows and writes nothing — a view run is not a
 * definition run and leaves no artifact and no run row behind.
 */
export function useRunView(datasetId: string | null) {
  return useMutation<ViewRun, unknown, { viewId: string; limit?: number }>({
    mutationFn: ({ viewId, limit }) =>
      analytics.post<ViewRun>(`/datasets/${datasetId}/views/${viewId}/run`, { limit }),
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ----------------------------------------------- analytics definitions */

/** `DefinitionOut`. `kind` is a bare string: `join` rows list here too. */
export interface AnalyticsDefinition {
  id: string;
  dataset_id: string;
  name: string;
  description?: string | null;
  kind: string;
  version_selector: Record<string, unknown>;
  sheet?: string | null;
  params?: Record<string, unknown>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

/** The four kinds `DefinitionCreate` accepts. `join` is not creatable here. */
export const DEFINITION_KINDS = ['sample', 'aggregate', 'profile', 'pivot'] as const;

export type DefinitionKind = (typeof DEFINITION_KINDS)[number];

/** `DefinitionCreate`. `params` is the body of the underlying operation. */
export interface DefinitionCreate {
  name: string;
  description?: string | null;
  kind: DefinitionKind;
  version_selector?: VersionSelector;
  sheet?: string | null;
  params?: Record<string, unknown>;
}

/** `DefinitionUpdate`. `kind` is absent by design — it is immutable. */
export interface DefinitionPatch {
  name?: string;
  description?: string | null;
  version_selector?: VersionSelector;
  sheet?: string | null;
  params?: Record<string, unknown>;
}

/** `AnalyticsRunOut` — one row of run history. */
export interface AnalyticsRun {
  id: string;
  definition_id: string;
  dataset_version_id?: string | null;
  job_id?: string | null;
  status: string;
  result_summary?: Record<string, unknown> | null;
  /** Null for a `profile` run: profiling returns statistics and stores no file. */
  artifact_id?: string | null;
  triggered_by?: string | null;
  started_at: string;
  completed_at?: string | null;
  error?: string | null;
}

/** `RunResponse` — a run row plus the inline result of the operation. */
export interface DefinitionRun extends AnalyticsRun {
  result?: Record<string, unknown> | null;
}

/**
 * Saved definitions for a dataset.
 *
 * The query key is deliberately the one `useAnalysis.useSavedAnalytics` already
 * uses, for the same URL: two keys for one request would put two copies of the
 * same list in the cache and let them drift apart after a write. This hook is
 * the fully-typed reader (`DefinitionOut` in full, not the four-field subset),
 * so a panel that needs `params` or `created_by` does not have to widen a type
 * locally to get at fields that are already on the wire.
 */
export function useDefinitions(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'saved-analytics', datasetId],
    queryFn: () => analytics.get<Page<AnalyticsDefinition>>(`/datasets/${datasetId}/analytics`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/** One definition, re-read. Same reason as `useChart`. */
export function useDefinition(datasetId: string | null, definitionId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'definition', datasetId, definitionId],
    queryFn: () =>
      analytics.get<AnalyticsDefinition>(`/datasets/${datasetId}/analytics/${definitionId}`),
    enabled: Boolean(datasetId) && Boolean(definitionId),
    retry: false,
  });
}

/** Run history for one definition, newest first. */
export function useDefinitionRuns(datasetId: string | null, definitionId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'definition-runs', datasetId, definitionId],
    queryFn: () =>
      analytics.get<Page<AnalyticsRun>>(
        `/datasets/${datasetId}/analytics/${definitionId}/runs`,
        { limit: MAX_PAGE_LIMIT },
      ),
    enabled: Boolean(datasetId) && Boolean(definitionId),
    retry: false,
  });
}

export function useCreateDefinition(datasetId: string | null) {
  return useLibraryMutation<AnalyticsDefinition, DefinitionCreate>(
    (body) => analytics.post<AnalyticsDefinition>(`/datasets/${datasetId}/analytics`, body),
    (d) => `Definition "${d.name}" saved. Running it is a separate step.`,
  );
}

export function useUpdateDefinition(datasetId: string | null) {
  return useLibraryMutation<
    AnalyticsDefinition,
    { definitionId: string; patch: DefinitionPatch }
  >(
    ({ definitionId, patch }) =>
      analytics.patch<AnalyticsDefinition>(
        `/datasets/${datasetId}/analytics/${definitionId}`,
        patch,
      ),
    (d) => `Definition "${d.name}" updated.`,
  );
}

/** 204. The config and its run history go; artifacts already written stay. */
export function useDeleteDefinition(datasetId: string | null) {
  return useLibraryMutation<unknown, { definitionId: string; name: string }>(
    ({ definitionId }) => analytics.del(`/datasets/${datasetId}/analytics/${definitionId}`),
    (_d, v) => `Definition "${v.name}" deleted.`,
  );
}

/**
 * Execute a saved definition now. This is the step that writes an artifact —
 * except for `profile`, which returns statistics and stores no file.
 *
 * Gated by raw access, not merely write: the response carries result rows
 * unmasked. A seat without it gets `sensitive-data-restricted`.
 */
export function useRunDefinition(datasetId: string | null) {
  return useLibraryMutation<DefinitionRun, { definitionId: string; name: string }>(
    ({ definitionId }) =>
      analytics.post<DefinitionRun>(`/datasets/${datasetId}/analytics/${definitionId}/run`),
    (d, v) => `Ran "${v.name}" — ${d.status}.`,
  );
}

/** `PublishResponse` — the run's stored output, promoted to a real dataset. */
export interface PublishResult {
  dataset_id: string;
  dataset_name: string;
  version_id: string;
  version_number: number;
  mode: string;
}

/**
 * Promote a run's artifact into a dataset — the only way to escape retention.
 * `new_dataset` needs a name; `new_version` appends to this dataset instead.
 */
export function usePublishRun(datasetId: string | null) {
  return useLibraryMutation<
    PublishResult,
    { runId: string; mode: 'new_dataset' | 'new_version'; name?: string }
  >(
    ({ runId, mode, name }) =>
      analytics.post<PublishResult>(`/datasets/${datasetId}/analytics/runs/${runId}/publish`, {
        mode,
        name,
      }),
    (d) => `Published to "${d.dataset_name}" v${d.version_number}.`,
  );
}

/* ------------------------------------------------------------------ usage */

/** `UsageResponse` — access-log counters for one dataset. */
export interface DatasetUsage {
  dataset_id: string;
  downloads: number;
  writes: number;
  total_events: number;
  last_activity_at?: string | null;
}

export function useDatasetUsage(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'usage', datasetId],
    queryFn: () => analytics.get<DatasetUsage>(`/datasets/${datasetId}/usage`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/* -------------------------------------------------------------- favourite */

export interface FavoriteState {
  /**
   * `null` when this seat's catalog page does not contain the dataset — the
   * mark lives on `DatasetInfo`, which only the *list* route returns, so past
   * one page there is genuinely nothing to read. A guess would be a claim about
   * someone's own bookmark, which is not a claim to guess at.
   */
  isFavorite: boolean | null;
  isLoading: boolean;
}

/**
 * This seat's favourite mark for a dataset.
 *
 * `is_favorite` is a field of `DatasetInfo`, and `DatasetInfo` is returned by
 * `GET /datasets` alone — there is no per-dataset read for it. So this reuses
 * the catalog query the page already holds (same key, so no extra request)
 * rather than inventing a second source of truth.
 */
export function useFavorite(datasetId: string | null): FavoriteState {
  const catalog = useDatasetCatalog({});
  const row = catalog.data?.items.find((d) => d.id === datasetId);
  return {
    isFavorite: row ? Boolean(row.is_favorite) : null,
    isLoading: catalog.isLoading,
  };
}

/** PUT to mark, DELETE to clear. Both 204; the mark is per seat, not per team. */
export function useSetFavorite(datasetId: string | null) {
  return useLibraryMutation<unknown, boolean>(
    (on) =>
      on
        ? analytics.put(`/datasets/${datasetId}/favorite`)
        : analytics.del(`/datasets/${datasetId}/favorite`),
    (_d, on) => (on ? 'Added to favourites.' : 'Removed from favourites.'),
  );
}

/* --------------------------------------------------------- artifact rows */

/** One column of a stored artifact, as DuckDB describes it. */
export interface ArtifactColumn {
  name: string;
  dtype: string;
}

/**
 * `GET /samples/{filename}/data`.
 *
 * The OpenAPI document declares no response model for this route (the handler
 * returns a plain dict), so these field names come from the handler itself —
 * `read_sample_data` in `app/features/files/services/downloads.py` — and not
 * from a guess. `filtered_count` is the count after `filter_expr`; with no
 * filter sent it equals `total_count`.
 */
export interface ArtifactRows {
  filename: string;
  total_count: number;
  filtered_count: number;
  offset: number;
  limit: number;
  columns: ArtifactColumn[];
  data: Record<string, unknown>[];
}

/**
 * Read rows back out of a stored artifact.
 *
 * Authorized through the artifact ROW, exactly like the download link beside
 * it: the row is the only thing that maps a filename to a storage key, so this
 * cannot reach a blob nobody registered.
 */
export function useArtifactRows(filename: string | null, limit = 5) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'artifact-rows', filename, limit],
    queryFn: () =>
      analytics.get<ArtifactRows>(`/samples/${encodeURIComponent(filename!)}/data`, { limit }),
    enabled: Boolean(filename),
    retry: false,
  });
}
