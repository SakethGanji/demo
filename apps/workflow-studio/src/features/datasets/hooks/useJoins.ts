/**
 * Joins, the derivation graph, global search, and raw download.
 *
 * These four sit together because they share one property the grid reads do
 * not: **each of them can be refused, and the refusal is the interesting
 * answer.** A join reads raw values from BOTH sides, so `ensure_raw_access`
 * runs twice before a single row is measured; `/download` is the raw file
 * itself, so masking would be theatre if it were not gated; and every
 * cross-tenant read anywhere in here is answered as 404, never 403. So
 * `retry: false` throughout — retrying a permission refusal three times just
 * delays the message.
 *
 * Four service facts are encoded here rather than documented elsewhere:
 *
 *  1. **A cross-dataset join binds to a CONFIRMED `relationship_id`**, never to
 *     free-form keys. `JoinSpec` therefore carries an id and a `how`, and there
 *     is no shape in this file that can express a key pair. An unreviewed edge
 *     is `409 relationship-not-confirmed`.
 *  2. **`how` is `inner` or `left`.** No right, full, cross or anti join exists
 *     in the engine, and no composite keys.
 *  3. **`/datasets/{id}/download` accepts `columns`, `limit` and `filter_expr`;
 *     the per-version route accepts only `format` and `sheet`.** Sending the
 *     extra four to the version route would be silently ignored, so
 *     `downloadDataset` drops them and the caller is expected to say so.
 *  4. **The download must go through `fetch`, not an `<a href>`.** Identity is
 *     the `X-User-Id` header pair, which an anchor cannot carry — so the file
 *     lands in memory as a Blob before it lands on disk. That is exactly why
 *     the row limit is a real control rather than a nicety.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ANALYTICS_BASE,
  AnalyticsApiError,
  analytics,
  type Page,
} from '@/shared/lib/analyticsClient';
import { getIdentityHeaders, useIdentityStore } from '@/shared/lib/identity';

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/**
 * The problem+json `code` to branch on, or `'error'` for anything that never
 * reached the service. Branch on this, never on the prose in `detail`.
 */
export function errorCodeOf(e: unknown): string {
  return e instanceof AnalyticsApiError ? e.code : 'error';
}

/* -------------------------------------------------------------------- joins */

/** The whole join vocabulary. Equi-join, single column, two directions. */
export type JoinHow = 'inner' | 'left';

/**
 * `JoinBuildSpec` minus the parts this surface does not offer.
 *
 * `left_version` / `right_version` default to each side's current version and
 * `select_columns` to "keep everything"; the builder sends neither rather than
 * drawing a control that restates a default.
 */
export interface JoinSpec {
  relationship_id: string;
  how: JoinHow;
}

/**
 * What the join will actually do, measured before it runs.
 *
 * `unmatched_left_pct` / `unmatched_right_pct` are PERCENTAGES (0–100, two
 * decimals) counted over ROWS, not distinct keys — a null key counts as
 * unmatched. `row_expansion_factor` is output rows per left input row.
 */
export interface JoinWarnings {
  left_rows: number;
  right_rows: number;
  left_duplicate_keys: number;
  right_duplicate_keys: number;
  many_to_many: boolean;
  estimated_output_rows: number;
  row_expansion_factor: number;
  unmatched_left_pct: number;
  unmatched_right_pct: number;
  /** Non-key column names present on both sides. */
  column_collisions?: string[];
}

/** `RelationshipOut` as the join routes echo it back. */
export interface JoinRelationship {
  id: string;
  dataset_id: string;
  from_sheet?: string | null;
  from_column: string;
  to_dataset_id: string;
  to_sheet?: string | null;
  to_column: string;
  status: string;
  method: string;
}

export interface JoinPreviewResult {
  warnings: JoinWarnings;
  output_columns?: string[];
  /** At most five rows. Real joined data from both sides — hence the gate. */
  preview?: Record<string, unknown>[];
  relationship: JoinRelationship;
}

export interface JoinExecuteResult {
  run_id: string;
  sample_file: string;
  row_count: number;
  warnings: JoinWarnings;
  output_columns?: string[];
  relationship: JoinRelationship;
}

export interface JoinPublishResult {
  dataset_id: string;
  dataset_name: string;
  version_id: string;
  version_number: number;
  mode: string;
}

/** Measure only. Persists nothing, so it needs no invalidation. */
export function useJoinPreview() {
  return useMutation<JoinPreviewResult, unknown, JoinSpec>({
    mutationFn: (spec) => analytics.post<JoinPreviewResult>('/joins/preview', spec),
    retry: false,
  });
}

/**
 * Run the join. A WRITE on the left dataset: it creates a definition, a job, a
 * run and a parquet artifact that all outlive the request — which is why the
 * left side needs `dataset:write` while the right stays at `dataset:read`.
 */
export function useJoinExecute() {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<JoinExecuteResult, unknown, JoinSpec>({
    mutationFn: (spec) => analytics.post<JoinExecuteResult>('/joins/execute', spec),
    retry: false,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
    },
  });
}

export interface JoinPublishVars {
  runId: string;
  mode: 'new_dataset' | 'new_version';
  /** New name for `new_dataset`; ignored by `new_version`. */
  name?: string | null;
}

/**
 * Publish an executed join as a dataset or as a new version of the left side.
 * Both parents are recorded in lineage, so the result stays traceable to each
 * source — which is what makes the derivation graph below say something.
 */
export function useJoinPublish() {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<JoinPublishResult, unknown, JoinPublishVars>({
    mutationFn: (vars) =>
      analytics.post<JoinPublishResult>(`/joins/${vars.runId}/publish`, {
        mode: vars.mode,
        name: vars.name ?? null,
      }),
    retry: false,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
    },
  });
}

/* ----------------------------------------------------------- lineage graph */

export interface LineageGraphNode {
  id: string;
  name: string;
  domain?: string | null;
  deprecated?: boolean;
  created_at: string;
  is_root?: boolean;
}

/** `child_id` was derived from `parent_id` by `relation`, `depth` hops out. */
export interface LineageGraphEdge {
  child_id: string;
  parent_id: string;
  relation: string;
  depth: number;
}

export interface LineageGraph {
  dataset_id: string;
  nodes?: LineageGraphNode[];
  edges?: LineageGraphEdge[];
  max_depth: number;
  /** The DAG really does continue past `max_depth` — raise it to see further. */
  truncated?: boolean;
  /** Datasets in teams this seat cannot read. Withheld with their edges. */
  hidden_nodes?: number;
}

/**
 * The whole derivation DAG, not one hop.
 *
 * `/lineage` answers the immediate parents and children; this walks the chain,
 * which is what turns lineage from a list into a story. Cycle-safe and
 * depth-capped on the server; the walk below is cycle-safe again on the client
 * because a DAG is not a promise.
 */
export function useLineageGraph(datasetId: string | null, maxDepth = 6) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'lineage-graph', datasetId, maxDepth],
    queryFn: () =>
      analytics.get<LineageGraph>(`/datasets/${datasetId}/lineage/graph`, {
        max_depth: maxDepth,
      }),
    enabled: Boolean(datasetId),
    retry: false,
  });
}

export type LineageDirection = 'upstream' | 'downstream';

/** One row of the flattened walk — a node, its hop count and how it got there. */
export interface LineageBranchRow {
  /** Unique per row, not per node: a diamond reaches the same node twice. */
  key: string;
  id: string;
  depth: number;
  relation: string;
  node: LineageGraphNode | null;
  /**
   * This node is already on the path above it, so the walk stopped rather than
   * looping. A cycle is real data (A published from B, B republished from A),
   * not an error.
   */
  revisited: boolean;
}

/** A hard stop so a wide DAG cannot render an unbounded list into a 384px dock. */
export const LINEAGE_ROW_CAP = 60;

/**
 * Flatten one direction of the DAG into an indented list, in reading order.
 *
 * Depth-first, so a chain reads as a chain. Every path is walked because a
 * dataset genuinely reachable two ways was derived two ways — but a node
 * already on the current path terminates that branch, and the total is capped.
 */
export function lineageBranch(
  graph: LineageGraph | undefined,
  rootId: string | null,
  direction: LineageDirection,
): LineageBranchRow[] {
  const rows: LineageBranchRow[] = [];
  if (!graph || !rootId) return rows;

  const edges = graph.edges ?? [];
  const byId = new Map((graph.nodes ?? []).map((n) => [n.id, n]));

  const walk = (id: string, path: string[], depth: number): void => {
    if (rows.length >= LINEAGE_ROW_CAP) return;
    const next = edges.filter((e) => (direction === 'upstream' ? e.child_id : e.parent_id) === id);
    for (const e of next) {
      if (rows.length >= LINEAGE_ROW_CAP) return;
      const otherId = direction === 'upstream' ? e.parent_id : e.child_id;
      const revisited = path.includes(otherId);
      rows.push({
        key: `${path.join('>')}>${otherId}:${e.relation}`,
        id: otherId,
        depth,
        relation: e.relation,
        node: byId.get(otherId) ?? null,
        revisited,
      });
      if (!revisited) walk(otherId, [...path, otherId], depth + 1);
    }
  };

  walk(rootId, [rootId], 0);
  return rows;
}

/* ------------------------------------------------------------------ search */

/** A dataset matched by name or description, with its versions inline. */
export interface DatasetSearchHit {
  id: string;
  name: string;
  description?: string | null;
  current_version_id?: string | null;
  created_at: string;
  updated_at: string;
  versions?: { id: string; version_number: number; status: string; row_count?: number | null }[];
}

/** A column matched by name fragment, served from captured schemas. */
export interface ColumnSearchHit {
  dataset_id: string;
  dataset_name: string;
  domain?: string | null;
  sheet_name: string;
  sheet_key: string;
  column_name: string;
  normalized_name: string;
  dtype: string;
  position?: number | null;
}

/** Below this a fragment matches most of the catalog; the box says so. */
export const SEARCH_MIN_CHARS = 2;

/**
 * Datasets you can access, by name and description.
 *
 * Both search routes are offset-paged rather than cursor-paged, and both
 * return `total` — so the honest rendering is "the first N of `total`", with
 * the remainder stated. Neither gets a numbered pager: paging a search is
 * re-typing the fragment.
 */
export function useDatasetSearch(q: string, limit = 6) {
  const seat = useSeat();
  const needle = q.trim();
  return useQuery({
    queryKey: ['analytics', seat, 'dataset-search', needle, limit],
    queryFn: () => analytics.get<Page<DatasetSearchHit>>('/datasets/search', { q: needle, limit }),
    enabled: needle.length >= SEARCH_MIN_CHARS,
    retry: false,
  });
}

/** Columns across the current versions of every dataset you can access. */
export function useColumnSearch(q: string, limit = 6) {
  const seat = useSeat();
  const needle = q.trim();
  return useQuery({
    queryKey: ['analytics', seat, 'column-search', needle, limit],
    queryFn: () => analytics.get<Page<ColumnSearchHit>>('/search/columns', { q: needle, limit }),
    enabled: needle.length >= SEARCH_MIN_CHARS,
    retry: false,
  });
}

/* ---------------------------------------------------------------- download */

export type DownloadFormat = 'csv' | 'parquet' | 'xlsx';

export interface DownloadRequest {
  datasetId: string;
  /**
   * `null` selects `/datasets/{id}/download` — the CURRENT version, and the
   * only route that accepts a column subset, a row limit or a filter. A number
   * selects the per-version route, which takes format and sheet only.
   */
  versionNumber: number | null;
  format: DownloadFormat;
  sheet: string | null;
  /** Omitted means every column, raw. Ignored on the per-version route. */
  columns?: string[] | null;
  limit?: number | null;
  /** SQL `WHERE`, evaluated by DuckDB — NOT a compiled QuerySpec. */
  filterExpr?: string | null;
}

export interface DownloadResult {
  filename: string;
  /** Measured off the delivered blob, so it is the file, not an estimate. */
  bytes: number;
  /** The route that actually ran, so the panel can name it afterwards. */
  path: string;
}

/** `attachment; filename="q3.csv"` → `q3.csv`. Quotes optional in the wild. */
function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return match ? decodeURIComponent(match[1].trim()) : null;
}

export function downloadPath(datasetId: string, versionNumber: number | null): string {
  return versionNumber == null
    ? `/datasets/${datasetId}/download`
    : `/datasets/${datasetId}/versions/${versionNumber}/download`;
}

async function downloadDataset(req: DownloadRequest): Promise<DownloadResult> {
  const path = downloadPath(req.datasetId, req.versionNumber);
  const params = new URLSearchParams({ format: req.format });
  if (req.sheet) params.set('sheet', req.sheet);
  // The per-version route does not accept these four. Sending them would be
  // ignored server-side, and a UI that shows a filter it did not apply is the
  // silent-wrong-answer class this codebase exists to prevent.
  if (req.versionNumber == null) {
    if (req.columns && req.columns.length > 0) params.set('columns', req.columns.join(','));
    if (req.limit != null && req.limit > 0) params.set('limit', String(req.limit));
    const expr = req.filterExpr?.trim();
    if (expr) params.set('filter_expr', expr);
  }

  const res = await fetch(`${ANALYTICS_BASE}${path}?${params.toString()}`, {
    headers: getIdentityHeaders(),
  });

  if (!res.ok) {
    // Failures are problem+json even on a route whose success is a byte
    // stream, so the `code` survives and `isRestricted` still works.
    let body: Record<string, unknown> = {};
    try {
      const text = await res.text();
      const parsed: unknown = text ? JSON.parse(text) : null;
      body =
        typeof parsed === 'object' && parsed !== null
          ? (parsed as Record<string, unknown>)
          : { detail: text };
    } catch {
      body = { detail: `HTTP ${res.status}` };
    }
    throw new AnalyticsApiError(res.status, body);
  }

  const blob = await res.blob();
  const filename =
    filenameFrom(res.headers.get('content-disposition')) ??
    `${req.datasetId.slice(0, 8)}.${req.format}`;

  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);

  return { filename, bytes: blob.size, path };
}

/**
 * Raw egress.
 *
 * `ensure_raw_access` guards both routes: a viewer or editor on a dataset that
 * declares ANY sensitive column is refused outright with
 * `403 sensitive-data-restricted` — the service will not write a masked file,
 * because a masked export would still have to decide, per column, what the
 * reader is allowed, and the file outlives the decision. So the refusal is a
 * state to render (`isRestricted` → `LensRestricted`), not an error to log.
 */
export function useDatasetDownload() {
  return useMutation<DownloadResult, unknown, DownloadRequest>({
    mutationFn: downloadDataset,
    retry: false,
  });
}
