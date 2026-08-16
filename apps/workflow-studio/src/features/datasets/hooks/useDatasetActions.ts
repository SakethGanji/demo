/**
 * Imperative writes against the analytics service — the mutation half of the
 * split declared in `useDatasets.ts`.
 *
 * Two rules shape everything here:
 *
 *  1. **Deletes that destroy data are not wired.** `DELETE /datasets/{id}` drops
 *     every version and its storage files, and there is no "delete a version"
 *     verb to soften it. Rules and dictionary entries are deletable because they
 *     are annotations — removing one loses no rows. That line is deliberate;
 *     don't move it without a conversation.
 *
 *  2. **A toast is not evidence.** Every mutation invalidates the seat-scoped
 *     cache so the UI re-reads the server rather than trusting an optimistic
 *     local edit. Ten call sites in the previous UI reported failure while
 *     succeeding; re-reading is what makes that class of bug impossible.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, AnalyticsApiError } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';

/**
 * Invalidate everything this seat has cached. Deliberately coarse: a write can
 * move row counts, version lists, validation status and catalog facets at once,
 * and a stale panel beside a fresh one is worse than one extra fetch.
 */
function useInvalidate() {
  const qc = useQueryClient();
  const seat = useIdentityStore((s) => s.identity.userId);
  return () => qc.invalidateQueries({ queryKey: ['analytics', seat] });
}

/** Human-readable failure text. Branch on `code`, never on prose. */
export function errorText(e: unknown): string {
  if (e instanceof AnalyticsApiError) {
    // Cross-tenant reads are 404 by design — never say "access denied".
    if (e.isNotFound) return 'Not found, or not available to this seat.';
    if (e.isSensitiveRestricted)
      return 'This dataset declares sensitive columns; only admin, owner or superuser may run this.';
    return e.detail;
  }
  return e instanceof Error ? e.message : String(e);
}

/**
 * Shared mutation wiring: invalidate on success, surface a typed message on
 * failure. `onSuccess` from the caller runs after the invalidation is queued.
 */
function useAnalyticsMutation<TData, TVars>(
  fn: (vars: TVars) => Promise<TData>,
  opts: { success?: (data: TData, vars: TVars) => string } = {},
) {
  const invalidate = useInvalidate();
  return useMutation<TData, unknown, TVars>({
    mutationFn: fn,
    onSuccess: (data, vars) => {
      void invalidate();
      if (opts.success) toast.success(opts.success(data, vars));
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ------------------------------------------------------------------ upload */

export interface UploadResponse {
  dataset_id: string;
  version_id?: string | null;
  status: string;
  row_count?: number | null;
  column_count?: number | null;
  error?: string | null;
  error_kind?: string | null;
  message?: string | null;
}

export interface UploadVars {
  file: File;
  /** Present ⇒ this becomes a NEW VERSION of that dataset, not a new dataset. */
  datasetId?: string | null;
  /** Comma-separated sheet names; the partial-workbook opt-in. */
  includeSheets?: string | null;
}

/**
 * `POST /upload` is create-dataset-and-ingest in one call — there is no separate
 * `POST /datasets`. Passing `dataset_id` switches it to "new version of this
 * dataset"; versions are immutable, so this is the only way to add data.
 *
 * `sync=true` keeps the id immediately queryable, which is what lets the UI
 * select the new dataset the moment the dialog closes.
 */
export function useUpload() {
  return useAnalyticsMutation<UploadResponse, UploadVars>(
    ({ file, datasetId, includeSheets }) => {
      const fields: Record<string, string> = {};
      if (datasetId) fields.dataset_id = datasetId;
      if (includeSheets?.trim()) fields.include_sheets = includeSheets.trim();
      return analytics.upload<UploadResponse>('/upload', file, fields, { sync: true });
    },
    {
      success: (d) =>
        d.row_count != null
          ? `Ingested ${d.row_count.toLocaleString()} rows.`
          : 'Upload complete.',
    },
  );
}

/* ---------------------------------------------------------------- metadata */

export interface DatasetPatch {
  name?: string;
  description?: string | null;
  classification?: 'public' | 'internal' | 'confidential' | 'restricted';
  domain?: string | null;
  source_system?: string | null;
  refresh_frequency?: string | null;
  deprecated?: boolean;
  deprecation_reason?: string | null;
}

/**
 * PATCH, not PUT — there is no `PUT /datasets/{id}` (405). Merge semantics:
 * an omitted field is unchanged, an explicit null clears it. So only send the
 * fields the form actually touched.
 */
export function useUpdateDataset(datasetId: string | null) {
  return useAnalyticsMutation<unknown, DatasetPatch>(
    (patch) => analytics.patch(`/datasets/${datasetId}`, patch),
    { success: () => 'Dataset updated.' },
  );
}

/* ------------------------------------------------------------------- rules */

export const RULE_TYPES = [
  'sheet_exists',
  'row_count_min',
  'not_null',
  'unique',
  'accepted_values',
  'range',
  'regex_match',
  'foreign_key',
] as const;

export type RuleType = (typeof RULE_TYPES)[number];

/** Rule types whose shape requires a column selector, per `check_rule_shape`. */
export const COLUMN_SCOPED_RULES: readonly RuleType[] = [
  'not_null',
  'unique',
  'accepted_values',
  'range',
  'regex_match',
  'foreign_key',
];

export interface RuleCreate {
  name: string;
  rule_type: RuleType;
  /** Required in practice — every rule_type rejects a null selector. */
  sheet_selector: string;
  column_selector?: string | null;
  parameters?: Record<string, unknown>;
  severity?: 'error' | 'warning';
  description?: string | null;
}

/** `scope_type` is derived server-side from `rule_type` — never send it. */
export function useCreateRule(datasetId: string | null) {
  return useAnalyticsMutation<unknown, RuleCreate>(
    (body) => analytics.post(`/datasets/${datasetId}/rules`, body),
    { success: (_d, v) => `Rule "${v.name}" created.` },
  );
}

export interface RulePatch {
  name?: string;
  description?: string | null;
  sheet_selector?: string | null;
  column_selector?: string | null;
  parameters?: Record<string, unknown> | null;
  severity?: 'error' | 'warning';
  enabled?: boolean;
}

/**
 * Enable/disable is this endpoint with `{enabled}` — there is no separate route.
 * `rule_type` and `scope_type` are not patchable.
 */
export function useUpdateRule(datasetId: string | null) {
  return useAnalyticsMutation<unknown, { ruleId: string; patch: RulePatch }>(
    ({ ruleId, patch }) => analytics.patch(`/datasets/${datasetId}/rules/${ruleId}`, patch),
    { success: () => 'Rule updated.' },
  );
}

/** 204, no body — the client returns null rather than trying to parse it. */
export function useDeleteRule(datasetId: string | null) {
  return useAnalyticsMutation<unknown, string>(
    (ruleId) => analytics.del(`/datasets/${datasetId}/rules/${ruleId}`),
    { success: () => 'Rule deleted.' },
  );
}

export interface ValidationDetail {
  id: string;
  status: string;
  rules_total?: number | null;
  rules_passed?: number | null;
  rules_failed?: number | null;
  error_failures?: number | null;
  warning_failures?: number | null;
  results?: {
    id?: string;
    rule_id?: string;
    rule_name: string;
    rule_type: string;
    severity: string;
    status: string;
    failure_count?: number | null;
    message?: string | null;
  }[];
}

/**
 * Runs every *enabled* rule synchronously. A dataset with no enabled rules is a
 * 400, not an empty pass — the UI should keep the button disabled in that case
 * rather than let the user discover it as an error.
 */
export function useValidate(datasetId: string | null) {
  return useAnalyticsMutation<ValidationDetail, number>(
    (versionNumber) =>
      analytics.post<ValidationDetail>(`/datasets/${datasetId}/versions/${versionNumber}/validate`),
    {
      success: (d) =>
        (d.error_failures ?? 0) > 0
          ? `${d.rules_failed ?? 0} of ${d.rules_total ?? 0} rules failed.`
          : `All ${d.rules_total ?? 0} rules passed.`,
    },
  );
}

/* ----------------------------------------------------- column dictionary */

export interface ColumnMetadata {
  business_name?: string | null;
  description?: string | null;
  semantic_type?: string | null;
  unit?: string | null;
  /**
   * The switch that turns masking on. Matched case-insensitively against
   * confidential | restricted | pii | sensitive | secret | phi.
   */
  sensitivity?: string | null;
}

/**
 * PUT is whole-record replace (an omitted field is CLEARED) and creates the
 * entry if absent; PATCH merges but 404s when no entry exists yet. The dialog
 * always sends every field it renders, so PUT is the honest verb here.
 */
export function useSetColumnMetadata(datasetId: string | null) {
  return useAnalyticsMutation<
    unknown,
    { sheetKey: string; column: string; body: ColumnMetadata }
  >(
    ({ sheetKey, column, body }) =>
      analytics.put(
        `/datasets/${datasetId}/sheet-metadata/${encodeURIComponent(sheetKey)}/columns/${encodeURIComponent(column)}`,
        body,
      ),
    { success: (_d, v) => `Updated "${v.column}".` },
  );
}

/* -------------------------------------------------------------------- tags */

/**
 * A tag points at a whole version, never a sheet. `PUT /tags` is the ungated
 * escape hatch — it skips the status and quality gates that `promote` enforces,
 * so the UI labels them differently.
 */
export function useSetTag(datasetId: string | null) {
  return useAnalyticsMutation<unknown, { tag_name: string; version_number: number }>(
    (body) => analytics.put(`/datasets/${datasetId}/tags`, body),
    { success: (_d, v) => `Tag "${v.tag_name}" → v${v.version_number}.` },
  );
}

/**
 * Gated: the version must be `ready`, and if the dataset has any enabled rule
 * the target needs a clean validation run. Those refusals arrive as
 * `validation-required` / `validation-failed`, which the caller surfaces.
 */
export function usePromoteTag(datasetId: string | null) {
  return useAnalyticsMutation<
    unknown,
    { tag: string; version_number: number; reason?: string }
  >(
    ({ tag, ...body }) => analytics.post(`/datasets/${datasetId}/tags/${encodeURIComponent(tag)}/promote`, body),
    { success: (_d, v) => `Promoted "${v.tag}" to v${v.version_number}.` },
  );
}

/** Moves the tag to the previous version in its history. Never gated. */
export function useRollbackTag(datasetId: string | null) {
  return useAnalyticsMutation<unknown, { tag: string; reason?: string }>(
    ({ tag, reason }) =>
      analytics.post(`/datasets/${datasetId}/tags/${encodeURIComponent(tag)}/rollback`, { reason }),
    { success: (_d, v) => `Rolled back "${v.tag}".` },
  );
}
