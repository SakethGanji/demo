/**
 * Sampling — the one write on this surface, plus the reads that let a draw be
 * described honestly before and after it runs.
 *
 * Three things about `POST /sample` shape everything here:
 *
 *   1. **A sample is a statistic about a population.** The response carries
 *      `original_count` alongside `sampled_count`, and every per-step row
 *      carries `pool_before` alongside `rows_selected`. Those pairings are not
 *      decoration — a sampled count with no denominator is unreadable, and a
 *      stratified draw with `class_targets` silently narrows the population to
 *      the classes it names. `strataFor` exists so that narrowing is a number
 *      on screen rather than a surprise in the artifact.
 *   2. **It can be refused.** `/sample` runs `ensure_raw_access`: a dataset
 *      that declares any sensitive column is `403 sensitive-data-restricted`
 *      for a viewer or editor, because a sample of raw values leaks them
 *      verbatim and the unmasked parquet it writes bypasses the preview
 *      entirely. `retry` is therefore off — retrying a refusal three times
 *      just delays the message.
 *   3. **It produces an artifact with a real clock.** `sample_file` is
 *      registered in the `artifacts` table (never a bucket scan) and kept 30
 *      days; an export of it is kept 7. Nothing in the service runs the sweep
 *      on a timer, so "past window" means eligible for collection, not
 *      collected — the page must not imply an automatic cleanup that does not
 *      exist.
 *
 * `llm_semantic` is deliberately absent from `SAMPLING_METHODS`. The method is
 * real, but every one of its modes needs an embeddings provider and key
 * (`llm_api_key` on the step body); asking a browser form for a credential that
 * then rides in a request body is not something this surface should teach.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, errorText } from '@/shared/lib/analyticsClient';
import { coverage, type Coverage } from '@/shared/components/instrument/coverage';
import { useIdentityStore } from '@/shared/lib/identity';
import type { ProfileResponse } from './useAnalysis';

/** Seat-scoped keys: RBAC and masking change what the same URL returns. */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/* ------------------------------------------------------------------ policy */

/** `RETENTION_DAYS['sample_output']` in `files/services/retention.py`. */
export const SAMPLE_RETENTION_DAYS = 30;
/** `RETENTION_DAYS['export']`. An export is the shortest-lived kind there is. */
export const EXPORT_RETENTION_DAYS = 7;

/* ------------------------------------------------------------------ method */

/**
 * The methods this surface offers, in the order it offers them. A plain `as
 * const` tuple rather than an enum — `erasableSyntaxOnly` forbids enums, and a
 * literal union is what the request body wants anyway.
 *
 * `cluster` and `time_stratified` are here because the engine has them;
 * `llm_semantic` is not, for the credential reason above. There is no
 * `head`/first-N method in the engine at all — `systematic` (every k-th row) is
 * the nearest thing that exists, and inventing `head` client-side by sorting
 * and slicing would be a different sample wearing the same word.
 *
 * `deduplicate` is a step method in the API but is NOT a draw: it operates on
 * the rows already selected, so as the only step it selects nothing. It belongs
 * to post-processing, and that is where this surface puts it.
 */
export const SAMPLING_METHODS = [
  'random',
  'stratified',
  'systematic',
  'weighted',
  'cluster',
  'time_stratified',
] as const;

export type SamplingMethod = (typeof SAMPLING_METHODS)[number];

/** One line each, as the engine actually behaves. */
export const METHOD_NOTE: Record<SamplingMethod, string> = {
  random: 'uniform draw over the pool',
  stratified: 'per-class allocation',
  systematic: 'every k-th row, in order',
  weighted: 'probability ∝ a numeric column',
  cluster: 'whole clusters, not rows',
  time_stratified: 'even across time buckets',
};

/**
 * Steps the ENGINE adds that the caller did not ask for, and what each one
 * means for the sample's population.
 *
 * `random_fill` is the one that matters. When the requested steps come in under
 * `target_total_volume`, the engine tops the sample up with a **uniform random
 * draw from whatever is left in the pool** — which is a different sample from
 * the one that was configured. A balanced draw that falls short and gets filled
 * is no longer balanced, and nothing about the sampled count says so. The page
 * names it.
 */
export const SYNTHESISED_STEPS: Record<string, string> = {
  random_fill: 'topped up to the target with a uniform random draw from the remaining pool',
  post_deduplicate: 'duplicate rows removed after the draw',
};

/* ---------------------------------------------------------------- requests */

export interface SamplingStepSpec {
  method: SamplingMethod;
  sample_size?: number | null;
  sample_fraction?: number | null;
  replace?: boolean;
  rounds?: number;
  /** Raw SQL `WHERE` fragment. The engine reports how many rows it matched. */
  filter_expr?: string | null;
  stratify_column?: string | null;
  /**
   * Per-class counts. **Naming classes narrows the population**: the engine
   * draws only from the classes listed here, so anything unnamed contributes
   * nothing to the sample and nothing to its denominator.
   */
  class_targets?: Record<string, number> | null;
  cluster_column?: string | null;
  num_clusters?: number | null;
  weight_column?: string | null;
  time_column?: string | null;
  time_bins?: number | null;
  deduplicate_columns?: string[] | null;
}

export interface DistributionGoalsSpec {
  column: string;
  class_minimums?: Record<string, number> | null;
  target_distribution?: Record<string, number> | null;
}

export interface SampleRequestBody {
  dataset_id: string;
  version_number?: number | null;
  sheet?: string | null;
  /** Required, `> 0`. Validated against the result, so it is a goal, not a hint. */
  target_total_volume: number;
  sampling_steps: SamplingStepSpec[];
  distribution_goals?: DistributionGoalsSpec | null;
  seed?: number | null;
  return_data?: boolean;
  deduplicate?: boolean;
  deduplicate_columns?: string[] | null;
  shuffle?: boolean;
  sort_by?: string | null;
  sort_descending?: boolean;
}

/* --------------------------------------------------------------- responses */

export interface StepResult {
  step_index: number;
  method: string;
  rows_selected: number;
  /** The denominator for `rows_selected`. Always render the pair. */
  pool_before: number;
  pool_after: number;
  rounds_completed?: number | null;
  per_round_counts?: number[] | null;
  class_counts?: Record<string, number> | null;
  filter_applied?: string | null;
  /** Rows the step's filter matched, out of `pool_before`. */
  filter_matched?: number | null;
  warnings?: string[] | null;
}

export interface ClassMinimumResult {
  required: number;
  actual: number;
  met: boolean;
}

export interface DistributionResult {
  target_pct: number;
  actual_pct: number;
  actual_count: number;
  met: boolean;
}

export interface GoalValidationResult {
  met: boolean;
  target_total_volume?: number | null;
  actual_total?: number | null;
  class_minimum_results?: Record<string, ClassMinimumResult> | null;
  distribution_results?: Record<string, DistributionResult> | null;
  warnings?: string[] | null;
}

export interface ReproducibilityInfo {
  seed?: number | null;
  target_total_volume?: number | null;
  steps_config?: Record<string, unknown>[] | null;
  distribution_goals?: Record<string, unknown> | null;
  post_processing?: Record<string, unknown> | null;
  timestamp?: string | null;
}

export interface SampleColumnSummary {
  name: string;
  dtype: string;
  nulls: number;
  unique: number;
}

export interface SampleResponse {
  success: boolean;
  /** Rows in the source the draw was made from — the denominator, always. */
  original_count: number;
  sampled_count: number;
  columns?: SampleColumnSummary[] | null;
  preview?: Record<string, unknown>[] | null;
  /** Registered artifact filename; fetch via `GET /samples/{filename}`. */
  sample_file?: string | null;
  data?: Record<string, unknown>[] | null;
  steps_summary?: StepResult[] | null;
  goal_validation?: GoalValidationResult | null;
  reproducibility?: ReproducibilityInfo | null;
}

export interface ExportResponse {
  export_file: string;
  format: string;
  size_bytes: number;
  media_type: string;
  source_file: string;
}

/* ------------------------------------------------------------------- write */

/**
 * Run the draw.
 *
 * The success toast states the pair, never the numerator alone: "3,000 of
 * 84,213 rows" is the only form of that sentence that can be acted on.
 *
 * Invalidating the whole `['analytics', seat]` subtree is deliberate — the run
 * registers an artifact, so the library lens's list is now stale, and it is not
 * this hook's business to know which keys those are.
 */
export function useRunSample() {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<SampleResponse, unknown, SampleRequestBody>({
    mutationFn: (body) => analytics.post<SampleResponse>('/sample', body),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(
        `Drew ${d.sampled_count.toLocaleString()} of ${d.original_count.toLocaleString()} rows.`,
      );
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/**
 * Convert a stored sample to another format.
 *
 * The export lands beside its source as its own `export` artifact, inheriting
 * ownership — and its own, much shorter, 7-day clock. That difference is the
 * reason this is a separate action with its own retention line rather than a
 * download button.
 */
export function useExportSample() {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<ExportResponse, unknown, { filename: string; format: string }>({
    mutationFn: ({ filename, format }) =>
      analytics.post<ExportResponse>(
        `/samples/${encodeURIComponent(filename)}/export`,
        undefined,
        { format },
      ),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(`Exported ${d.export_file} — kept ${EXPORT_RETENTION_DAYS} days.`);
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/* -------------------------------------------------------------------- read */

/** Enough top values to fill the palette and show what was left out of it. */
export const STRATA_TOP_N = 25;

/**
 * The column profile behind the stratification picker.
 *
 * Separate from `useProfile` in `useAnalysis.ts` because that one asks for
 * `top_n: 5` and histograms: five classes is fine for a lens summary and is
 * useless for allocating a stratified draw, where the question is "how many
 * rows does each class actually have, and how many classes are there".
 *
 * Refusable in exactly the same way `/sample` is, which is convenient: a seat
 * that cannot profile cannot sample either, so one restriction check covers
 * the screen.
 */
export function useSamplingProfile(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'sampling-profile', datasetId, version, sheet],
    queryFn: () =>
      analytics.post<ProfileResponse>('/profile', {
        dataset_id: datasetId,
        version_number: version,
        sheet,
        include_histograms: false,
        include_duplicates: false,
        top_n: STRATA_TOP_N,
      }),
    enabled: Boolean(datasetId) && version != null && Boolean(sheet),
    retry: false,
  });
}

export interface Stratum {
  value: string;
  count: number;
}

export interface Strata {
  column: string;
  /** Ranked by count, descending. At most `STRATA_TOP_N` of them. */
  items: Stratum[];
  /** Distinct values in the column, when the profile knows. */
  distinct: number | null;
  /**
   * Rows the listed strata account for, over the whole sheet. Partial whenever
   * the column has more distinct values than the profile returned — which is
   * the case a stratified allocation must never quietly drop.
   */
  coverage: Coverage;
  /** True when the profile's top-N did not reach every distinct value. */
  truncated: boolean;
}

/**
 * Per-stratum counts for one column, with the population they came out of.
 *
 * Returns `null` rather than an empty shell when the column has no top values:
 * a stratification picker with no counts is a picker that cannot tell you what
 * you are about to narrow, and rendering it empty would imply the classes are
 * empty rather than unknown.
 */
export function strataFor(
  profile: ProfileResponse | undefined,
  column: string | null,
): Strata | null {
  if (!profile || !column) return null;
  const col = profile.columns.find((c) => c.name === column);
  if (!col?.top_values?.length) return null;

  const items: Stratum[] = col.top_values
    .map((t) => ({ value: String(t.value), count: t.count }))
    .sort((a, b) => b.count - a.count);

  const listed = items.reduce((sum, s) => sum + s.count, 0);
  const distinct = col.unique_count ?? null;

  return {
    column,
    items,
    distinct,
    // The population is the sheet, not the column's non-null rows: a class
    // allocation that ignores nulls still leaves those rows undrawn, and the
    // reader is entitled to see that in the denominator.
    coverage: coverage(listed, profile.row_count),
    truncated: distinct != null ? distinct > items.length : listed < profile.row_count,
  };
}

/**
 * Split `n` evenly across `values`, giving the remainder to the earliest
 * (largest) strata. Sums to exactly `n`, so the target the goal validator
 * checks and the targets the engine draws to cannot disagree.
 */
export function allocateEvenly(values: readonly string[], n: number): Record<string, number> {
  const out: Record<string, number> = {};
  if (values.length === 0 || n <= 0) return out;
  const base = Math.floor(n / values.length);
  let remainder = n - base * values.length;
  for (const v of values) {
    out[v] = base + (remainder > 0 ? 1 : 0);
    if (remainder > 0) remainder -= 1;
  }
  return out;
}
