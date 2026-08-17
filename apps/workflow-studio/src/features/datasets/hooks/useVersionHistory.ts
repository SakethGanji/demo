/**
 * The version ledger: what moved, who moved it, what actually changed in the
 * rows, and how to get a version back out of the system.
 *
 * These sit apart from `useAnalysis.ts` and `useDatasetActions.ts` because they
 * share one property neither of those files does: **every one of them reads or
 * writes HISTORY, not state.** A tag's transition log survives the tag being
 * deleted; the timeline is a merge of eight append-only tables; a row diff is a
 * fresh computation whose full output is written to an artifact rather than
 * returned. None of them can be modelled as "the current value of a thing".
 *
 * Three contracts run through the file:
 *
 *  1. **Reads are seat-scoped queries, writes are mutations that do not toast
 *     their failures.** A `diff-key-required` from row-diff is not an error the
 *     user should read as breakage — it is the server asking a question, and the
 *     lens answers it with a key picker. Routing it through a toast would make a
 *     first-class state look like a fault, so `useRowDiff` deliberately has no
 *     `onError`.
 *
 *  2. **`retry: false` throughout.** Every refusal these endpoints produce is
 *     deterministic — a missing primary key, a non-unique key, a seat without
 *     raw access. Retrying three times only delays the answer.
 *
 *  3. **Download is not `analytics.get`.** The client parses JSON; a download is
 *     a stream with a `Content-Disposition`. It still needs the identity headers,
 *     which an `<a href>` cannot carry, so the bytes are fetched, turned into an
 *     object URL, and handed to a synthetic anchor. Failures are re-thrown as
 *     `AnalyticsApiError` so the caller branches on `code` exactly as everywhere
 *     else.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  ANALYTICS_BASE,
  AnalyticsApiError,
  analytics,
  errorText,
  type Page,
} from '@/shared/lib/analyticsClient';
import { getIdentityHeaders, useIdentityStore } from '@/shared/lib/identity';
import type { components } from '@/shared/lib/analyticsSchema';

export type TagHistoryEntry = components['schemas']['TagHistoryEntry'];
export type TimelineEvent = components['schemas']['TimelineEvent'];
export type RowDiffRequest = components['schemas']['RowDiffRequest'];
export type RowDiffResponse = components['schemas']['RowDiffResponse'];
export type ColumnChangeCount = components['schemas']['ColumnChangeCount'];
export type ConfirmRenameRequest = components['schemas']['ConfirmRenameRequest'];
export type ConfirmRenameResponse = components['schemas']['ConfirmRenameResponse'];

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/* ------------------------------------------------------------ tag history */

/**
 * The promote/rollback/set ledger for ONE tag, newest first.
 *
 * Deliberately keyed on the tag NAME rather than on a tag row: the history
 * outlives the tag, so this still answers for a name that no longer appears in
 * `GET /tags`. `enabled` is what makes it a per-tag lazy read — the lens opens
 * one tag's ledger at a time rather than firing N requests to render a list.
 */
export function useTagHistory(datasetId: string | null, tagName: string | null, limit = 20) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'tag-history', datasetId, tagName, limit],
    queryFn: () =>
      analytics.get<Page<TagHistoryEntry>>(
        `/datasets/${datasetId}/tags/${encodeURIComponent(tagName!)}/history`,
        { limit },
      ),
    enabled: Boolean(datasetId) && Boolean(tagName),
    retry: false,
  });
}

/* --------------------------------------------------------------- timeline */

/**
 * The dataset's merged history: uploads, tag transitions, validation and
 * profile runs, transformation runs, lineage both ways, and audited WRITES.
 *
 * Reads are pointedly not in here — they are usage, and live behind
 * `GET /datasets/{id}/usage`. So an empty timeline means nothing has *happened*
 * to this dataset, not that nobody has looked at it.
 */
export function useDatasetTimeline(datasetId: string | null, limit = 20) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'timeline', datasetId, limit],
    queryFn: () =>
      analytics.get<Page<TimelineEvent>>(`/datasets/${datasetId}/timeline`, { limit }),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

/* --------------------------------------------------------------- row diff */

export interface RowDiffVars {
  from: number;
  to: number;
  sheet: string;
  /** Omit to let the server use the sheet's declared primary key. */
  key?: string[] | null;
  sampleLimit?: number;
}

/**
 * The L3 diff: which ROWS changed, and in which cells.
 *
 * A mutation rather than a query because it is not free and not idempotent in
 * the way a cache implies — every call recomputes the join in DuckDB and writes
 * a `diff_output` artifact. Firing it automatically whenever a base is picked
 * would litter the library with artifacts nobody asked for, so the lens runs it
 * on an explicit press.
 *
 * No `onError` toast: the two interesting failures (`diff-key-required`,
 * `ambiguous-diff-key`) are questions, and the lens renders a key picker for
 * them. See `needsDiffKey`.
 */
export function useRowDiff(datasetId: string | null) {
  return useMutation<RowDiffResponse, unknown, RowDiffVars>({
    mutationFn: ({ from, to, sheet, key, sampleLimit }) => {
      const body: RowDiffRequest = { sample_limit: sampleLimit ?? 20 };
      if (key && key.length > 0) body.key = key;
      return analytics.post<RowDiffResponse>(
        `/datasets/${datasetId}/versions/${from}/sheets/${encodeURIComponent(sheet)}/row-diff/${to}`,
        body,
      );
    },
    retry: false,
  });
}

/**
 * True when the server refused because of the KEY, not because of a fault.
 *
 * Three codes, one remedy — name the columns that identify a row:
 *   `diff-key-required`  400 — the sheet declares no primary key and none was sent.
 *   `ambiguous-diff-key` 409 — the key is not unique in one of the versions, so
 *                              the join would fan out and report the same row as
 *                              both added and removed.
 *   `unknown-column`     400 — a named key column is not present in both versions.
 */
export function needsDiffKey(error: unknown): boolean {
  if (!(error instanceof AnalyticsApiError)) return false;
  return (
    error.code === 'diff-key-required' ||
    error.code === 'ambiguous-diff-key' ||
    error.code === 'unknown-column'
  );
}

/** The refusal explained, keyed on `code` — never on the prose, which changes. */
export function diffKeyExplanation(error: unknown): string {
  if (error instanceof AnalyticsApiError) {
    if (error.code === 'diff-key-required')
      return 'This sheet declares no primary key, so there is nothing to match rows on. Name the columns that identify a row.';
    if (error.code === 'ambiguous-diff-key') {
      const dupes = error.body.duplicate_keys;
      const version = error.body.version_number;
      return `The key is not unique in v${version ?? '—'}${
        typeof dupes === 'number' ? ` (${dupes.toLocaleString()} duplicated value${dupes === 1 ? '' : 's'})` : ''
      }. A row diff needs a key that identifies exactly one row, or it reports the same row as both added and removed.`;
    }
    if (error.code === 'unknown-column') {
      const cols = error.body.columns;
      return `Key column${Array.isArray(cols) && cols.length === 1 ? '' : 's'} ${
        Array.isArray(cols) ? cols.join(', ') : '—'
      } are not present in both versions. Only columns in both can match rows.`;
    }
  }
  return errorText(error);
}

/* --------------------------------------------------------- confirm rename */

/**
 * Confirm that a removed+added sheet pair is one sheet under a new name.
 *
 * The consequence is the point: the renamed sheet keeps its LOGICAL identity, so
 * sheet metadata, the column dictionary and quality rules follow it instead of
 * silently detaching from a sheet that "disappeared". Left unconfirmed the diff
 * keeps counting it as one removed plus one added sheet, which is the truthful
 * reading until somebody says otherwise — the server never auto-declares a
 * rename.
 */
export function useConfirmRename(datasetId: string | null, versionNumber: number | null) {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<ConfirmRenameResponse, unknown, ConfirmRenameRequest>({
    mutationFn: (body) =>
      analytics.post<ConfirmRenameResponse>(
        `/datasets/${datasetId}/versions/${versionNumber}/confirm-rename`,
        body,
      ),
    retry: false,
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(
        `"${d.from_sheet}" → "${d.to_sheet}" relinked across ${d.versions_relinked} version${
          d.versions_relinked === 1 ? '' : 's'
        }.`,
      );
    },
  });
}

/* --------------------------------------------------------------- download */

export type DownloadFormat = 'csv' | 'parquet' | 'xlsx';

export interface DownloadVars {
  /**
   * `null` targets `GET /datasets/{id}/download`, which is the dataset's CURRENT
   * version — not necessarily the one on screen. The lens says so.
   */
  versionNumber: number | null;
  format: DownloadFormat;
  /** Required on a multi-sheet workbook; the server 400s without it. */
  sheet?: string | null;
}

export interface DownloadResult {
  filename: string;
  bytes: number;
  versionNumber: number | null;
}

/** `attachment; filename="orders_v3.csv"` → `orders_v3.csv`. */
function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const quoted = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return quoted?.[1] ? decodeURIComponent(quoted[1].trim()) : null;
}

/**
 * Export a version's bytes.
 *
 * Both routes are gated by `ensure_raw_access` — masking would be theatre if the
 * raw file were still downloadable — so a seat without it gets
 * `sensitive-data-restricted`, which the lens renders as a refusal rather than
 * an error. Cross-tenant is 404, never 403.
 */
export function useDownloadVersion(datasetId: string | null) {
  return useMutation<DownloadResult, unknown, DownloadVars>({
    mutationFn: async ({ versionNumber, format, sheet }) => {
      const path =
        versionNumber == null
          ? `/datasets/${datasetId}/download`
          : `/datasets/${datasetId}/versions/${versionNumber}/download`;
      const params = new URLSearchParams({ format });
      if (sheet) params.append('sheet', sheet);

      const res = await fetch(`${ANALYTICS_BASE}${path}?${params.toString()}`, {
        headers: getIdentityHeaders(),
      });

      if (!res.ok) {
        const text = await res.text();
        let body: Record<string, unknown>;
        try {
          body = text ? (JSON.parse(text) as Record<string, unknown>) : {};
        } catch {
          body = { detail: text };
        }
        throw new AnalyticsApiError(res.status, body);
      }

      const blob = await res.blob();
      const filename =
        filenameFromDisposition(res.headers.get('content-disposition')) ??
        `${datasetId}${versionNumber == null ? '' : `_v${versionNumber}`}.${format}`;

      // The anchor is synthetic because the identity headers above cannot ride
      // on a plain `href`; the object URL is revoked immediately after the click.
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      return { filename, bytes: blob.size, versionNumber };
    },
    onSuccess: (d) => toast.success(`Downloaded ${d.filename}.`),
  });
}
