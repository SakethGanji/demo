/**
 * Read hooks for the analytics service. Queries only — imperative writes live in
 * `useDatasetActions.ts`, matching the split used by `features/projects`.
 */

import { useQuery } from '@tanstack/react-query';
import { analytics, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import type { components } from '@/shared/lib/analyticsSchema';

export type DatasetInfo = components['schemas']['DatasetInfo'];
export type QueryPage = components['schemas']['QueryPage'];
export type QuerySpec = components['schemas']['QuerySpec'];

/**
 * Every key is scoped by the acting seat: switching seats must refetch, because
 * RBAC and column masking change what the same URL returns.
 */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/**
 * Filters the catalog endpoint applies **server-side**. Everything here narrows
 * the result set before it reaches us, which is what keeps the fetch below
 * bounded on a large tenant.
 */
export interface CatalogFilters {
  q?: string;
  documentation?: string;
  domain?: string;
  validation_status?: 'passed' | 'failed' | 'none';
  has_schema_drift?: boolean;
  include_deprecated?: boolean;
}

/** The API caps `limit` at 200; asking for more is a 422, not a bigger page. */
export const MAX_PAGE_LIMIT = 200;

/**
 * Hard bound on the catalog sweep. Past this we stop and say so rather than
 * issuing an unbounded number of requests.
 */
export const MAX_CATALOG_ROWS = 1000;

/** One page. Used by the rail, which is a picker and never needs global order. */
export function useDatasetCatalog(filters: CatalogFilters = {}) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'datasets', filters],
    queryFn: () =>
      analytics.get<Page<DatasetInfo>>('/datasets', { ...filters, limit: MAX_PAGE_LIMIT }),
  });
}

export interface CatalogSweep {
  items: DatasetInfo[];
  /** What the server says exists for these filters, not what we hold. */
  total: number;
  /** False when the sweep hit `MAX_CATALOG_ROWS` before reaching `total`. */
  complete: boolean;
}

/**
 * The whole catalog for the current filters, walked page by page.
 *
 * This exists because `GET /datasets` has **no sort parameter**. Ordering a
 * single page client-side and calling it "sorted by rows, descending" would be
 * a lie — it sorts the arbitrary 200 the server happened to return, so the
 * largest dataset in the tenant can simply be absent from "the top". Either the
 * client holds the whole result set and sorts it, or it must not offer sorting
 * at all. Dataset catalogs are metadata and small, so holding them is cheap and
 * the honest option is also the fast one.
 *
 * When a tenant really is bigger than `MAX_CATALOG_ROWS`, `complete` goes false
 * and the page says which filters would narrow it — rather than silently
 * sorting a fraction.
 */
export function useDatasetCatalogSweep(filters: CatalogFilters = {}) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'catalog-sweep', filters],
    queryFn: async (): Promise<CatalogSweep> => {
      const items: DatasetInfo[] = [];
      let offset = 0;
      let total = 0;

      for (;;) {
        const page = await analytics.get<Page<DatasetInfo>>('/datasets', {
          ...filters,
          limit: MAX_PAGE_LIMIT,
          offset,
        });
        total = page.total;
        items.push(...page.items);
        offset += MAX_PAGE_LIMIT;

        // Stop on a short page too: that is the real end, whatever `total` says.
        if (page.items.length < MAX_PAGE_LIMIT) break;
        if (items.length >= Math.min(total, MAX_CATALOG_ROWS)) break;
      }

      return { items, total, complete: items.length >= total };
    },
    placeholderData: (prev) => prev,
  });
}

export interface VersionSummary {
  version_number: number;
  status?: string | null;
  row_count?: number | null;
  size_bytes?: number | null;
  sheet_count?: number | null;
  created_at?: string | null;
}

export function useVersions(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'versions', datasetId],
    queryFn: () => analytics.get<Page<VersionSummary>>(`/datasets/${datasetId}/versions`),
    enabled: Boolean(datasetId),
  });
}

export interface SheetColumn {
  name: string;
  dtype?: string | null;
  [key: string]: unknown;
}

/**
 * A sheet as the API returns it. Note the field is `name`, NOT `sheet_name` —
 * `sheet_key` is the stable key used by the sheet-metadata routes, while `name`
 * is what per-sheet data paths take (and it resolves across a confirmed rename).
 */
export interface SheetSummary {
  name: string;
  sheet_key: string;
  row_count?: number | null;
  column_count?: number | null;
  status?: string | null;
  is_default?: boolean | null;
  columns?: SheetColumn[];
  [key: string]: unknown;
}

export function useSheets(datasetId: string | null, version: number | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'sheets', datasetId, version],
    queryFn: () =>
      analytics.get<Page<SheetSummary>>(`/datasets/${datasetId}/versions/${version}/sheets`),
    enabled: Boolean(datasetId) && version != null,
  });
}

/**
 * One page of rows. The cursor is opaque and bound to the version + spec —
 * replaying it against a different one returns `400 invalid-cursor`, so the
 * cursor is part of the query key and is reset whenever the spec changes.
 */
export function useRows(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
  spec: QuerySpec,
  cursor: string | null,
  /**
   * Gate for stale state. Selecting a different dataset updates `datasetId`
   * a render before `sheet` catches up, which otherwise fires a query for the
   * previous dataset's sheet against the new one — a spurious 404. Callers pass
   * false until the chosen sheet is known to belong to the loaded dataset.
   */
  sheetBelongsToDataset: boolean,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'rows', datasetId, version, sheet, spec, cursor],
    queryFn: () =>
      analytics.post<QueryPage>(
        `/datasets/${datasetId}/versions/${version}/sheets/${encodeURIComponent(sheet!)}/query`,
        { ...spec, cursor: cursor ?? undefined },
      ),
    enabled: Boolean(datasetId) && version != null && Boolean(sheet) && sheetBelongsToDataset,
    placeholderData: (prev) => prev,
  });
}

export interface QualityRule {
  id: string;
  name?: string | null;
  rule_type?: string | null;
  /** Selector, not a plain column name — a rule may be dataset- or sheet-scoped. */
  scope_type?: string | null;
  sheet_selector?: string | null;
  column_selector?: string | null;
  severity?: string | null;
  enabled?: boolean | null;
  [key: string]: unknown;
}

export function useQualityRules(datasetId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'rules', datasetId],
    queryFn: () => analytics.get<Page<QualityRule>>(`/datasets/${datasetId}/rules`),
    enabled: Boolean(datasetId),
  });
}

/** The acting seat and its team memberships, for the "act as" switcher. */
export interface AuthMe {
  user: { id: string; email?: string | null; name?: string | null; is_superuser?: boolean };
  memberships: { team_id: string; team_name: string; role: string }[];
}

export function useAuthMe() {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'auth-me'],
    queryFn: () => analytics.get<AuthMe>('/auth/me'),
    retry: false,
  });
}

export interface TeamMember {
  user_id: string;
  email: string;
  name: string;
  role: string;
}

/**
 * Team members, which back the "act as" switcher. Seeing the app as a viewer is
 * the only way to confirm masking and RBAC refusals actually bite — masking
 * hides values from a viewer AND an editor, so an admin-only view proves nothing.
 */
export function useTeamMembers(teamId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'members', teamId],
    queryFn: () => analytics.get<Page<TeamMember>>(`/teams/${teamId}/members`),
    enabled: Boolean(teamId),
    retry: false,
  });
}
