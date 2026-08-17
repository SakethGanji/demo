/**
 * Copy-on-write sheet replacement — `POST /datasets/{id}/sheets/{sheet}/replace`.
 *
 * ── What the handler actually does, because the name invites the wrong reading ─
 *
 * "Replace" sounds like an edit. It is not one. `files/services/replace.py`
 * calls `repo.create_version(...)` and then `repo.complete_version(...)`: the
 * result is a NEW version, numbered after the base, and the base version is not
 * touched by a single write. Every sheet other than the named one is carried
 * across copy-on-write — the new version's sheet rows point at the SAME parquet
 * `storage_key` the base version's rows point at, so nothing is duplicated and
 * nothing is rewritten.
 *
 * Three consequences the UI has to state, since they are the whole reason this
 * endpoint is safe to expose:
 *
 *   1. **Immutability holds.** Tags live in `dataset_version_tags` keyed by
 *      `version_id`. The base version keeps its id, its artifacts and its row
 *      counts, so every tag pointing at it still points at exactly the same
 *      bytes, and a diff already computed against it still describes it.
 *   2. **The base is always the CURRENT version.** The service resolves it with
 *      `_get_current_version_or_404` and takes no version argument at all — an
 *      older version cannot be used as the base, so this is never a way to fork
 *      history from behind the head.
 *   3. **The new version becomes current** (`complete_version` advances
 *      `current_version_id` only when the new number outranks the old one, so
 *      concurrent uploads cannot move it backwards).
 *
 * ── Refusals worth naming ──────────────────────────────────────────────────
 *
 * `400` when the upload is not single-table: the file must be one CSV, one
 * parquet, or a one-sheet workbook, because the endpoint replaces ONE sheet and
 * will not guess which sheet of a workbook was meant.
 * `404` when the sheet name is not in the current version — and, being this
 * service, also when the dataset belongs to another tenant. 404 hides
 * existence; never render that as a refusal.
 * `409` for a pre-Phase-1 version whose other sheets have no addressable
 * artifact: there is nothing to point the new version at, so a full re-upload
 * is the only path.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, errorText } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';

/** `_ALLOWED` in `files/services/replace.py`. Enforced server-side too. */
export const REPLACE_EXTENSIONS = ['.csv', '.parquet', '.xlsx', '.xls'] as const;
export const REPLACE_ACCEPT_ATTR = REPLACE_EXTENSIONS.join(',');

/** `SheetReplaceResponse`. Six fields, all required. */
export interface SheetReplaceResult {
  dataset_id: string;
  version_id: string;
  version_number: number;
  /** The sheet whose data was replaced, by `sheet_name` (not `sheet_key`). */
  replaced_sheet: string;
  /** Sheets carried over copy-on-write from the base version. */
  reused_sheets: string[];
  /** Total rows across EVERY sheet of the new version, not the replaced one. */
  row_count: number;
}

export interface SheetReplaceVars {
  datasetId: string;
  /** Sheet `name`, as the sheets list returns it. */
  sheetName: string;
  file: File;
}

/** Local pre-check, so an unsupported file is refused before it is uploaded. */
export function replaceRejection(file: File): string | null {
  const dot = file.name.lastIndexOf('.');
  const ext = dot === -1 ? '' : file.name.slice(dot).toLowerCase();
  if (!(REPLACE_EXTENSIONS as readonly string[]).includes(ext)) {
    return `Unsupported type ${ext || '(none)'} — accepted: ${REPLACE_EXTENSIONS.join(' ')}`;
  }
  return null;
}

/**
 * Replace one sheet's data.
 *
 * The toast says "new version", never "replaced", because the word the endpoint
 * is named after is the one that misleads: nothing was overwritten.
 *
 * `retry` is off. This is a write that creates a version row before it reads a
 * byte of the upload; a silent second attempt would leave a second failed
 * version behind and tell nobody.
 */
export function useReplaceSheet() {
  const qc = useQueryClient();
  const seat = useIdentityStore((s) => s.identity.userId);
  return useMutation<SheetReplaceResult, unknown, SheetReplaceVars>({
    mutationFn: ({ datasetId, sheetName, file }) =>
      analytics.upload<SheetReplaceResult>(
        `/datasets/${encodeURIComponent(datasetId)}/sheets/${encodeURIComponent(sheetName)}/replace`,
        file,
      ),
    retry: false,
    onSuccess: (d) => {
      // A new version changes the catalog row, the version list, the sheet
      // list and every lens keyed off "current" — which keys those are is not
      // this hook's business.
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(
        `v${d.version_number} created — ${d.replaced_sheet} replaced, ${d.reused_sheets.length} sheet${
          d.reused_sheets.length === 1 ? '' : 's'
        } carried over.`,
      );
    },
    onError: (e) =>
      toast.error(
        errorText(e, {
          notFound: 'That dataset or sheet is not available to this seat.',
        }),
      ),
  });
}
