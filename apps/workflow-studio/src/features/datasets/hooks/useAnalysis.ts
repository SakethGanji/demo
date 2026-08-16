/**
 * Read hooks backing the four analysis lenses.
 *
 * These sit apart from `useDatasets.ts` because they share one property that
 * the grid reads do not: **most of them can be refused outright.** A viewer or
 * editor on a dataset that declares any sensitive column gets
 * `403 sensitive-data-restricted` from profile, transform-preview and join —
 * not a masked result, a refusal. The lenses render that state deliberately
 * rather than looking broken, so `retry: false` is set throughout: retrying a
 * permission refusal three times just delays the message.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, AnalyticsApiError, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { errorText } from './useDatasetActions';

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/* --------------------------------------------------------------- profiling */

export interface TopValue {
  value: unknown;
  count: number;
  percent: number;
}

export interface HistogramBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface ColumnProfile {
  name: string;
  dtype: 'numeric' | 'categorical' | 'datetime' | 'boolean' | 'text';
  /** Total rows in the sheet, nulls included — NOT COUNT(column). */
  count: number;
  non_null_count?: number | null;
  null_count?: number | null;
  null_percent?: number | null;
  unique_count?: number | null;
  top_values?: TopValue[] | null;
  mean?: number | null;
  median?: number | null;
  std?: number | null;
  min?: number | null;
  max?: number | null;
  histogram?: HistogramBin[] | null;
}

export interface ProfileResponse {
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
  duplicate_row_count?: number | null;
}

/**
 * Column statistics for the version/sheet currently on screen.
 *
 * Enabled only when a sheet is resolved — profiling a dataset without naming a
 * sheet is `sheet-selection-required` on any multi-sheet workbook.
 */
export function useProfile(datasetId: string | null, version: number | null, sheet: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'profile', datasetId, version, sheet],
    queryFn: () =>
      analytics.post<ProfileResponse>('/profile', {
        dataset_id: datasetId,
        version_number: version,
        sheet,
        include_histograms: true,
        include_duplicates: true,
        top_n: 5,
      }),
    enabled: Boolean(datasetId) && version != null && Boolean(sheet),
    retry: false,
  });
}

/* ------------------------------------------------------------------ health */

export interface HealthDimension {
  status: 'ok' | 'warn' | 'fail' | 'unknown' | string;
  summary: string;
}

export interface DatasetHealth {
  dataset_id: string;
  current_version_number?: number | null;
  dimensions: Record<string, HealthDimension>;
}

export function useHealth(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'health', datasetId],
    queryFn: () => analytics.get<DatasetHealth>(`/datasets/${datasetId}/health`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/* ----------------------------------------------------------- relationships */

export interface Relationship {
  id: string;
  from_sheet?: string | null;
  from_column?: string | null;
  to_dataset_id?: string | null;
  to_sheet?: string | null;
  to_column?: string | null;
  status: 'suggested' | 'confirmed' | 'rejected' | string;
  method?: string | null;
  confidence?: number | null;
  evidence?: Record<string, unknown> | null;
}

/**
 * Edges pointing at a dataset this seat cannot read are omitted from `items`
 * *and* from `total` — so the count here is "relationships you can see", which
 * is the honest number to render.
 */
export function useRelationships(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'relationships', datasetId],
    queryFn: () => analytics.get<Page<Relationship>>(`/datasets/${datasetId}/relationships`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

export interface LineageEntry {
  id: string;
  relation?: string | null;
  created_at?: string | null;
  parent_visible?: boolean;
  child_visible?: boolean;
  parent_dataset_name?: string | null;
  child_dataset_name?: string | null;
  parent_version_number?: number | null;
  child_version_number?: number | null;
}

export interface LineageResponse {
  dataset_id: string;
  parents: LineageEntry[];
  children: LineageEntry[];
}

export function useLineage(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'lineage', datasetId],
    queryFn: () => analytics.get<LineageResponse>(`/datasets/${datasetId}/lineage`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

function useAnalysisMutation<TData, TVars>(
  fn: (v: TVars) => Promise<TData>,
  success: (d: TData) => string,
) {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<TData, unknown, TVars>({
    mutationFn: fn,
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(success(d));
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

export interface SuggestResponse {
  pairs_examined: number;
  suggested: number;
  skipped: number;
}

/**
 * Statistical FK/overlap discovery. `skipped > 0` means the run was not
 * exhaustive — the panel says so rather than implying a clean sweep.
 */
export function useSuggestRelationships(datasetId: string | null) {
  return useAnalysisMutation<SuggestResponse, void>(
    () =>
      analytics.post<SuggestResponse>(`/datasets/${datasetId}/relationships/suggest`, undefined, {
        sync: true,
      }),
    (d) =>
      `Examined ${d.pairs_examined} pairs, suggested ${d.suggested}` +
      (d.skipped ? ` (${d.skipped} skipped).` : '.'),
  );
}

/** Seeds edges from the dataset's enabled `foreign_key` quality rules. */
export function useSeedRelationships(datasetId: string | null) {
  return useAnalysisMutation<{ created: number }, void>(
    () => analytics.post<{ created: number }>(`/datasets/${datasetId}/relationships/seed`),
    (d) => (d.created ? `Seeded ${d.created} relationship(s).` : 'No new relationships to seed.'),
  );
}

export function useReviewRelationship(datasetId: string | null) {
  return useAnalysisMutation<unknown, { id: string; action: 'confirm' | 'reject' }>(
    ({ id, action }) =>
      analytics.post(`/datasets/${datasetId}/relationships/${id}/${action}`),
    () => 'Relationship reviewed.',
  );
}

/* --------------------------------------------------------------- transform */

export interface Transformation {
  id: string;
  name: string;
  description?: string | null;
  sheet?: string | null;
  steps?: unknown[] | null;
  created_at?: string | null;
}

export function useTransformations(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'transformations', datasetId],
    queryFn: () => analytics.get<Page<Transformation>>(`/datasets/${datasetId}/transformations`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

export interface OutputColumn {
  name: string;
  normalized_name?: string | null;
  dtype?: string | null;
  position?: number | null;
}

export interface CompileResult {
  output_schema: OutputColumn[];
  step_schemas?: OutputColumn[][] | null;
  columns: string[];
  rows: Record<string, unknown>[];
  sampled: boolean;
  version_number?: number | null;
  sheet_name?: string | null;
}

/**
 * Schema-only compile: `rows` is deliberately NOT sent.
 *
 * That is the whole security contract of this surface. Omitting `rows` needs
 * only `dataset:read` and touches no data; setting it makes the call a read of
 * the pipeline's *output* rows, which is gated by `ensure_raw_access` because a
 * `compute` step can copy a sensitive column into a new name — masking by
 * source column name would not hold. The lens previews shape, never values.
 */
export function useCompilePreview(datasetId: string | null) {
  return useMutation<CompileResult, unknown, { sheet: string | null; steps: unknown[] }>({
    mutationFn: ({ sheet, steps }) =>
      analytics.post<CompileResult>(`/datasets/${datasetId}/transformations/compile`, {
        sheet,
        version_selector: { mode: 'current' },
        steps,
      }),
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ----------------------------------------------------------------- library */

export interface Artifact {
  key: string;
  filename: string;
  size_bytes?: number | null;
  file_type?: string | null;
  dataset_id?: string | null;
  created_at?: string | null;
}

/**
 * The artifact table, not a bucket scan — a blob with no row is unreachable by
 * everyone, superusers included. Filtered client-side to the selected dataset
 * because the endpoint has no dataset filter.
 */
export function useArtifacts(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'artifacts'],
    queryFn: () => analytics.get<Page<Artifact>>('/samples', { limit: 200 }),
    enabled: Boolean(datasetId),
    retry: false,
    select: (page) => ({
      ...page,
      items: page.items.filter((a) => !datasetId || a.dataset_id === datasetId),
    }),
  });
}

export interface SavedDefinition {
  id: string;
  name: string;
  description?: string | null;
  kind: string;
  sheet?: string | null;
  created_at?: string | null;
}

export function useSavedAnalytics(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'saved-analytics', datasetId],
    queryFn: () => analytics.get<Page<SavedDefinition>>(`/datasets/${datasetId}/analytics`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/* -------------------------------------------------------------------- tags */

export interface TagInfo {
  tag_name: string;
  version_id?: string | null;
  version_number: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export function useTags(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'tags', datasetId],
    queryFn: () => analytics.get<Page<TagInfo>>(`/datasets/${datasetId}/tags`),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/* ------------------------------------------------------------------ shared */

/** True when a query failed specifically because the seat lacks raw access. */
export function isRestricted(error: unknown): boolean {
  return error instanceof AnalyticsApiError && error.isSensitiveRestricted;
}
