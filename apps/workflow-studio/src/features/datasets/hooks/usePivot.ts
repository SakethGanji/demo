/**
 * Pivot and ad-hoc SQL over one version of a dataset.
 *
 * Two endpoints, one property in common: **both can be refused outright.**
 *
 *   POST /api/v1/pivot
 *       — row dims × ONE pivot dim × value aggregations. Goes through
 *         `ensure_raw_access`, so a viewer or editor on a dataset that declares
 *         a sensitive column gets `403 sensitive-data-restricted`, not a masked
 *         pivot: a pivot widens the distinct values of its column dimension
 *         into output column NAMES, which masking cannot reach.
 *   POST /api/v1/datasets/{id}/versions/{n}/sql
 *       — one sandboxed SELECT; every ready sheet is a table named by its
 *         `sheet_key`. Same gate, same reason — arbitrary SQL can alias or
 *         aggregate a masked column, and the result is persisted as a parquet
 *         artifact the whole team can fetch.
 *
 * `retry: false` throughout: retrying a refusal three times only delays the
 * message.
 *
 * NEITHER ENDPOINT PAGES. The pivot takes a server-capped `limit` and returns
 * the whole widened result; SQL returns a row-capped result with a `truncated`
 * flag. There is no cursor and no offset, so the page windows what it already
 * holds — and says so, rather than drawing a pager that implies more is
 * fetchable.
 */

import { useQuery } from '@tanstack/react-query';
import type { CSSProperties } from 'react';
import { analytics, AnalyticsApiError } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { VIZ_SLOTS, foldTopN, vizSlot } from '@/shared/components/instrument/series';

/** Every seat sees a different answer, so every key carries the seat. */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/* ------------------------------------------------------------------ shapes */

/** The ten functions `PivotValue.function` accepts. Server-enumerated. */
export const AGG_FUNCTIONS = [
  'sum',
  'mean',
  'median',
  'count',
  'min',
  'max',
  'std',
  'nunique',
  'first',
  'last',
] as const;
export type AggFunction = (typeof AGG_FUNCTIONS)[number];

/** `PivotValue.display` — raw, or a share of its row / column / grand total. */
export const DISPLAY_MODES = [
  'value',
  'pct_of_row',
  'pct_of_column',
  'pct_of_grand_total',
] as const;
export type DisplayMode = (typeof DISPLAY_MODES)[number];

/**
 * Only `sum` and `count` survive being added up again.
 *
 * This matters at exactly one place: the folded "Other" column, whose cells the
 * client computes by adding the folded members together. Doing that to a `mean`
 * or a `nunique` produces a confident number that is simply wrong — the silent
 * wrong answer SHAPE-R11 exists to prevent. Everything the SERVER re-aggregates
 * (row totals, column totals, the grand total) is correct for every function,
 * because it re-runs the aggregation at the coarser grain rather than summing
 * cells; only the client-side fold is restricted.
 */
export function isAdditive(fn: AggFunction): boolean {
  return fn === 'sum' || fn === 'count';
}

/**
 * SHAPE-R7 — the member limit on the COLUMN shelf.
 *
 * A pivot column dimension with 500 members is 64,000px of horizontal scroll,
 * and the reference pivot had no limit at all. Above this the shelf refuses the
 * field and offers the fold instead. The server's own hard cap is far higher
 * (`too-many-pivot-columns` at 200), which is a protection for the query, not
 * for the reader.
 */
export const COLUMN_MEMBER_LIMIT = 24;

/** The server's cap on distinct pivot values; past it the request is a 400. */
export const SERVER_PIVOT_COLUMN_CAP = 200;

/** Rows requested per pivot. The service caps this again on its side. */
export const PIVOT_ROW_LIMIT = 500;

/** `app/features/files/services/retention.py` — the real policy, not a guess. */
export const PIVOT_RETENTION_DAYS = 30;
export const SQL_RETENTION_DAYS = 7;

export interface PivotValueSpec {
  column: string;
  function: AggFunction;
  /** Sent explicitly so the cell keys below are predictable. */
  alias: string;
  display: DisplayMode;
}

export interface PivotSpec {
  /** Row dimensions, outer first. At least one — the API requires it. */
  rows: string[];
  /** The ONE pivot dimension whose members become output columns. */
  columns: string | null;
  values: PivotValueSpec[];
  include_row_totals: boolean;
  include_column_totals: boolean;
  limit: number;
}

export interface PivotResponse {
  success: boolean;
  /** Rows the aggregation actually read. */
  original_count: number;
  /** Output (pivoted) rows returned. */
  row_count: number;
  columns: string[];
  /** The distinct pivot-dimension values that became columns. */
  pivot_columns: string[];
  data?: Record<string, unknown>[] | null;
  /** Grand total per value alias — value-display specs only. */
  totals?: Record<string, unknown> | null;
  /** Per-output-column total, keyed by CELL name, not by member. */
  column_totals?: Record<string, unknown> | null;
  truncated: boolean;
  /** Filename of the persisted parquet, under /samples. */
  result_file?: string | null;
}

export interface SqlResponse {
  columns: string[];
  items: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  /** Sheet tables that were queryable — the FROM vocabulary. */
  tables: string[];
  result_file: string;
}

/* ------------------------------------------------------------------- reads */

/**
 * Run a pivot. `spec` is the COMMITTED spec, not the draft — the shelves edit a
 * draft and "Run" commits it, so typing in a shelf does not fire a query per
 * keystroke against a 500-row aggregation.
 */
export function usePivot(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
  spec: PivotSpec | null,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'pivot', datasetId, version, sheet, spec],
    queryFn: () =>
      analytics.post<PivotResponse>('/pivot', {
        dataset_id: datasetId,
        version_number: version,
        sheet,
        ...spec,
      }),
    enabled: Boolean(datasetId) && version != null && Boolean(sheet) && spec != null,
    retry: false,
  });
}

/**
 * Run one SELECT against a version.
 *
 * Read-only and single-statement, enforced server-side before execution — this
 * client offers no write path because the endpoint has none.
 */
export function useSqlQuery(datasetId: string | null, version: number | null, sql: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'sql', datasetId, version, sql],
    queryFn: () =>
      analytics.post<SqlResponse>(`/datasets/${datasetId}/versions/${version}/sql`, { sql }),
    enabled: Boolean(datasetId) && version != null && Boolean(sql),
    retry: false,
  });
}

/**
 * The problem+json `code`, for display as an identifier beside the prose.
 *
 * A code is a branching key, and it is also the only part of a refusal that is
 * stable enough to quote in a ticket — `detail` and `title` are localised.
 */
export function problemCode(e: unknown): string | null {
  return e instanceof AnalyticsApiError ? e.code : null;
}

/* -------------------------------------------------------------- the fold */

/**
 * The output-column key carrying one (member, value spec) cell.
 *
 * Mirrors `_cell_name` in the pivot service: bare member label for a single
 * value spec, `{member}_{alias}` when several were requested.
 */
export function cellKey(member: string, alias: string, multiValue: boolean): string {
  return multiValue ? `${member}_${alias}` : member;
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * One member's total. Prefers `column_totals`, which the SERVER re-aggregated
 * at the pivot-dim grain and is therefore correct for `mean` and `nunique` too.
 * Falls back to adding the returned cells, which is only sound for an additive
 * function — hence the `additive` gate on everything that uses the fallback.
 */
export function memberTotal(res: PivotResponse, cell: string, additive: boolean): number | null {
  const served = asNumber(res.column_totals?.[cell]);
  if (served !== null) return served;
  if (!additive) return null;
  let sum = 0;
  let seen = false;
  for (const row of res.data ?? []) {
    const v = asNumber(row[cell]);
    if (v !== null) {
      sum += v;
      seen = true;
    }
  }
  return seen ? sum : null;
}

export interface PivotMember {
  /** The distinct value of the pivot dimension, as the API stringified it. */
  label: string;
  /** Key into each data row. */
  cell: string;
  /** Ranking total; null when it cannot be established honestly. */
  total: number | null;
  /** Identity hue. Categorical, fixed order, NEVER cycled. */
  color: string;
}

export interface MemberFold {
  /** Members actually drawn as columns, in rank order. */
  head: PivotMember[];
  /**
   * The folded tail. Graphite, never a palette slot, and it carries its own
   * count and share so the part that is not drawn is still stated.
   */
  other: {
    members: PivotMember[];
    count: number;
    value: number;
    /** Share of the ranked measure. Null when the function is not additive. */
    share: number | null;
    color: string;
  } | null;
  /** Members the server returned, folded or not. */
  memberCount: number;
  /** SHAPE-R7 — more members than a reader can hold, fold or not. */
  overLimit: boolean;
  /** Fewer than three members: a "distribution" that is really a ratio. */
  degenerate: boolean;
}

/**
 * Rank the pivot members, keep the top eight, fold the rest into a graphite
 * "Other" (SHAPE-R7 / `foldTopN`).
 *
 * With `fold` off every member keeps its own column and slots past the eighth
 * take the graphite token rather than wrapping back to slot 1 — two categories
 * wearing one colour is worse than no colour at all.
 */
export function foldMembers(
  res: PivotResponse,
  alias: string,
  multiValue: boolean,
  additive: boolean,
  fold: boolean,
): MemberFold {
  const all: PivotMember[] = res.pivot_columns.map((label) => {
    const cell = cellKey(label, alias, multiValue);
    return { label, cell, total: memberTotal(res, cell, additive), color: 'var(--m5)' };
  });

  const memberCount = all.length;
  const overLimit = memberCount > COLUMN_MEMBER_LIMIT;
  const degenerate = memberCount > 0 && memberCount < 3;

  if (!fold || memberCount <= VIZ_SLOTS) {
    return {
      head: all.map((m, i) => ({ ...m, color: vizSlot(i) })),
      other: null,
      memberCount,
      overLimit,
      degenerate,
    };
  }

  const folded = foldTopN(all, (m) => Math.abs(m.total ?? 0), VIZ_SLOTS);
  const headLabels = new Set(folded.head.map((m) => m.label));
  const tail = all.filter((m) => !headLabels.has(m.label));

  return {
    head: folded.head.map((m, i) => ({ ...m, color: vizSlot(i) })),
    other: {
      members: tail,
      count: tail.length,
      value: folded.other?.value ?? 0,
      // A share of a mean is not a quantity. State the count, withhold the share.
      share: additive ? (folded.other?.share ?? null) : null,
      color: 'var(--m5)',
    },
    memberCount,
    overLimit,
    degenerate: folded.degenerate,
  };
}

/**
 * The value of the folded "Other" cell on one row.
 *
 * Null for every non-additive function — see `isAdditive`. A dash is a worse
 * cell than a number right up until the number is wrong.
 */
export function otherCellValue(
  row: Record<string, unknown>,
  fold: MemberFold,
  additive: boolean,
): number | null {
  if (!fold.other || !additive) return null;
  let sum = 0;
  let seen = false;
  for (const m of fold.other.members) {
    const v = asNumber(row[m.cell]);
    if (v !== null) {
      sum += v;
      seen = true;
    }
  }
  return seen ? sum : null;
}

/* -------------------------------------------------------------- the ramp */

/**
 * The heat ramp is MAGNITUDE, so it is the neutral ink ramp, not a hue.
 *
 * `--m1` is the most contrasting ink against the page in BOTH themes and `--m6`
 * the least, so ordering m6 → m1 gives one monotonic ramp that survives the
 * light/dark switch. It is drawn as a wash (a mix into transparent) rather than
 * as a fill so the ink token on top keeps its measured contrast either way.
 *
 * Six steps, and step 0 is the FLOOR, not "no fill" — a zero cell is a measured
 * zero, and drawing nothing there would make it read as a missing cell.
 */
const HEAT_RAMP: readonly { token: string; alpha: number }[] = [
  { token: '--m6', alpha: 8 },
  { token: '--m5', alpha: 12 },
  { token: '--m4', alpha: 17 },
  { token: '--m3', alpha: 22 },
  { token: '--m2', alpha: 27 },
  { token: '--m1', alpha: 32 },
];

export const HEAT_STEPS = HEAT_RAMP.length;

/** Step 0…5 for a cell, or null when the cell has no value to place. */
export function heatStep(value: number | null, max: number): number | null {
  if (value === null) return null;
  if (!(max > 0)) return 0;
  const r = Math.min(1, Math.abs(value) / max);
  return Math.min(HEAT_STEPS - 1, Math.floor(r * HEAT_STEPS));
}

export function heatStyle(step: number | null): CSSProperties {
  if (step === null) return {};
  const { token, alpha } = HEAT_RAMP[Math.min(HEAT_STEPS - 1, Math.max(0, step))];
  return { background: `color-mix(in srgb, var(${token}) ${alpha}%, transparent)` };
}

/** The legend swatches, low → high. */
export function heatLegend(): CSSProperties[] {
  return HEAT_RAMP.map((_, i) => heatStyle(i));
}
