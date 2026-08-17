/**
 * Reads for the two §16 quality explorers — duplicates and missing data.
 *
 * Both endpoints existed in the service from the start and nothing in the
 * studio had ever called them, so the quality lens could tell you that the
 * `duplicates` health dimension said `warning` and then had no way to answer
 * "which rows?". These two GETs are that answer.
 *
 * Three properties of the wire shape the hooks:
 *
 *  1. **Both routes come in a sheet-scoped and a version-scoped form.** The
 *     version-scoped one auto-resolves only on a single-sheet workbook, so we
 *     prefer the sheet-scoped path whenever a sheet is on screen and fall back
 *     to the version one otherwise — which is what makes a single-sheet CSV
 *     work before the sheet rail has resolved anything.
 *  2. **Neither route refuses a low-privilege seat.** They MASK instead: group
 *     keys on a sensitive column come back as a stable pseudonym (so distinct
 *     groups stay distinct) and example/probe rows come back masked. The
 *     response says which columns that happened to in `masked_columns`, and
 *     the renderer must honour it rather than printing the pseudonym as if it
 *     were the value.
 *  3. **`groups` is capped and `group_count` is not.** `truncated` states the
 *     difference. A count of listed groups is therefore never the count of
 *     groups, and the two must never be printed as the same number.
 *
 * `retry: false` throughout, matching `useAnalysis`: these can 400 with
 * `sheet-selection-required` or `unknown-column`, and retrying a refusal three
 * times only delays the sentence that explains it.
 */

import { useQuery } from '@tanstack/react-query';
import { analytics } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';

/* ---------------------------------------------------------------- the wire */

/** One set of rows sharing the same values on the grouped columns. */
export interface DuplicateGroup {
  /** Normalized column -> shared value. Masked columns carry a pseudonym. */
  key: Record<string, unknown>;
  count: number;
  /** A few full rows from the group; absent when the server sent none. */
  examples?: Record<string, unknown>[] | null;
}

export interface DuplicatesResponse {
  sheet_name: string;
  /** Normalized columns grouped on. */
  columns: string[];
  /** True when grouped on every column. */
  exact: boolean;
  row_count: number;
  /** Total duplicate groups BEFORE the cap — not `groups.length`. */
  group_count: number;
  /** Total rows inside duplicate groups. */
  duplicate_rows: number;
  groups?: DuplicateGroup[] | null;
  truncated?: boolean;
  masked_columns?: string[] | null;
}

/** Missingness of one column. */
export interface ColumnMissing {
  column: string;
  null_count: number;
  null_percent: number;
}

/** A row ranked by how many of its values are null. */
export interface MissingRow {
  null_count: number;
  row: Record<string, unknown>;
}

export interface MissingResponse {
  sheet_name: string;
  /** `"profile_run"` or `"computed"`. */
  source: string;
  profile_run_id?: string | null;
  row_count: number;
  /** All columns, worst null rate first. */
  columns?: ColumnMissing[] | null;
  /** Rows with nulls, most nulls first. */
  rows_most_missing?: MissingRow[] | null;
  masked_columns?: string[] | null;
}

/* --------------------------------------------------------------- the hooks */

/**
 * The server caps `limit` at 100 and defaults to 25. We ask for 25: the dock is
 * 384px wide, and the honest statement about the rest is `truncated`, not a
 * longer list nobody scrolls to the bottom of.
 */
export const DUPLICATE_GROUP_LIMIT = 25;

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/**
 * Sheet-scoped when a sheet is on screen, version-scoped otherwise.
 *
 * The version form is not a shortcut — it is the documented single-sheet
 * auto-resolve, and it is the only form that works before a sheet name exists.
 */
function explorerPath(
  datasetId: string,
  version: number,
  sheet: string | null,
  leaf: 'duplicates' | 'missing',
): string {
  const base = `/datasets/${datasetId}/versions/${version}`;
  return sheet ? `${base}/sheets/${encodeURIComponent(sheet)}/${leaf}` : `${base}/${leaf}`;
}

/**
 * Duplicate-row groups.
 *
 * `columns` is a comma-separated subset to group on; `null` means group on
 * every column, which is what the server calls an EXACT duplicate. The two are
 * genuinely different questions — "this whole row appears twice" and "two rows
 * share an email" — so the subset rides in the query key.
 */
export function useDuplicates(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
  columns: string | null,
  limit: number = DUPLICATE_GROUP_LIMIT,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'duplicates', datasetId, version, sheet, columns, limit],
    queryFn: () =>
      analytics.get<DuplicatesResponse>(
        explorerPath(datasetId!, version!, sheet, 'duplicates'),
        // `qs` drops null/undefined/'', so an absent subset never becomes
        // `?columns=` — which the service rejects as `empty-column-selection`.
        { columns, limit },
      ),
    enabled: Boolean(datasetId) && version != null,
    retry: false,
  });
}

/**
 * Per-column null stats plus the rows-most-missing probe.
 *
 * Column stats come from the persisted profile run when one exists and are
 * computed live otherwise — `source` says which, and that difference is worth
 * showing: a profile-backed report describes the version as profiled, not as it
 * is now.
 */
export function useMissing(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'missing', datasetId, version, sheet],
    queryFn: () =>
      analytics.get<MissingResponse>(explorerPath(datasetId!, version!, sheet, 'missing')),
    enabled: Boolean(datasetId) && version != null,
    retry: false,
  });
}
