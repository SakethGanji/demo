/**
 * The aggregate builder's data layer — `POST /api/v1/aggregate`.
 *
 * Two things here are not obvious, and both exist to satisfy SHAPE-R11.
 *
 * 1. EVERY MEASURE IS SENT WITH A COMPANION `count` ON ITS OWN COLUMN.
 *    `sum(amount)` over a column that is 80.4% parseable prints a confident
 *    total for 80.4% of the rows. The API cannot tell you that — the response
 *    carries the sum and nothing about how many rows fed it — so the client
 *    asks. `COUNT(col)` in DuckDB counts non-null values, so the companion IS
 *    the numerator of the coverage the figure has to carry. It costs no extra
 *    request: the counts ride in the same GROUP BY.
 *
 * 2. THE DENOMINATOR IS NAMED, NOT ASSUMED. There is no `COUNT(*)` available
 *    through this API — `AggregationSpec.column` is required — so "rows in this
 *    group" has to be bought with a count over some real column. The basis is
 *    chosen from the profile (a column with zero nulls counts every row in its
 *    group exactly) and reported back to the caller, so the screen can say
 *    which column the denominator came from and whether it is exact or a lower
 *    bound. A denominator nobody can name is how the original bug got in.
 *
 * Note what is NOT here: paging. `AggregateRequest` has `limit` and no offset
 * and no cursor, so there is no next page to ask for — only a wider limit and
 * the `truncated` flag. A pager on this surface would be a lie.
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { analytics } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { coverage, type Coverage } from '@/shared/components/instrument/coverage';

/* ------------------------------------------------------------------ shapes */

/** The API's enum, in the order the picker offers them. */
export const AGG_FUNCTIONS = [
  'count',
  'sum',
  'mean',
  'median',
  'min',
  'max',
  'std',
  'nunique',
  'first',
  'last',
] as const;

export type AggFunction = (typeof AGG_FUNCTIONS)[number];

/**
 * Type gate. `sum · mean · median · std` need a numeric column; offering
 * `sum(plan)` is a bug, and the API answers it with a 400 rather than a number.
 */
const NUMERIC_ONLY: ReadonlySet<AggFunction> = new Set<AggFunction>([
  'sum',
  'mean',
  'median',
  'std',
]);

/**
 * The only two functions whose grand total is a single well-defined number over
 * every filtered row. Everything else arrives in `totals_omitted` — see the
 * service's `TOTALABLE_FUNCTIONS`.
 */
const ADDITIVE: ReadonlySet<AggFunction> = new Set<AggFunction>(['sum', 'count']);

/** `mean` has a total, but it is sum/count over all rows — not a summed column. */
const DERIVED: ReadonlySet<AggFunction> = new Set<AggFunction>(['mean']);

export type Additivity = 'additive' | 'derived' | 'omitted';

export function additivityOf(fn: AggFunction): Additivity {
  if (ADDITIVE.has(fn)) return 'additive';
  if (DERIVED.has(fn)) return 'derived';
  return 'omitted';
}

export interface FunctionOffer {
  fn: AggFunction;
  available: boolean;
  /** Why it is withdrawn. Present only when `available` is false. */
  reason?: string;
}

/**
 * R5 applied to the function picker: an aggregation is offered only when its
 * requirements are met, and a withdrawn one says why rather than vanishing.
 */
export function functionOffers(numeric: boolean): FunctionOffer[] {
  return AGG_FUNCTIONS.map((fn) =>
    !NUMERIC_ONLY.has(fn) || numeric
      ? { fn, available: true }
      : { fn, available: false, reason: `${fn} needs a numeric column` },
  );
}

export const DATE_UNITS = ['year', 'quarter', 'month', 'week', 'day', 'hour'] as const;
export type DateUnit = (typeof DATE_UNITS)[number];

/**
 * A bucket is part of the dimension, so the bucket label IS the group key.
 * Exactly one of the three, which is what `GroupByBucket` requires.
 */
export type Bucket =
  | { kind: 'date_trunc'; unit: DateUnit }
  | { kind: 'bin_width'; width: number }
  | { kind: 'bin_count'; count: number };

export interface GroupEntry {
  column: string;
  /** Output name. For a plain entry this IS the column name. */
  alias: string;
  bucket: Bucket | null;
}

export interface Measure {
  id: string;
  column: string;
  fn: AggFunction;
  alias: string;
}

export interface AggregateSpec {
  groupBy: GroupEntry[];
  measures: Measure[];
  /** A group-by output name or an aggregation alias — never a raw column. */
  sortBy: string | null;
  sortOrder: 'asc' | 'desc';
  limit: number;
}

export const EMPTY_SPEC: AggregateSpec = {
  groupBy: [],
  measures: [],
  sortBy: null,
  sortOrder: 'desc',
  limit: 12,
};

export function plainGroup(column: string): GroupEntry {
  // A plain group-by is sent as a bare string, and the API names the output
  // column after the source column. Keeping `alias` equal to it means the rest
  // of this module can read a group key by one name in both cases.
  return { column, alias: column, bucket: null };
}

export function bucketGroup(column: string, bucket: Bucket): GroupEntry {
  return { column, alias: `${column}_bucket`, bucket };
}

/** Aliases are the API's join between measures, HAVING and sort — keep unique. */
export function makeMeasure(column: string, fn: AggFunction, taken: readonly Measure[]): Measure {
  const base = `${column}_${fn}`;
  let alias = base;
  let n = 2;
  while (taken.some((m) => m.alias === alias)) alias = `${base}_${n++}`;
  return { id: `${alias}:${Date.now()}`, column, fn, alias };
}

/* ------------------------------------------------------- population basis */

/**
 * Which column's non-null count stands in for "rows in this group".
 *
 * `exact` is true only when the profile says the column has no nulls — then
 * `COUNT(basis)` per group is the group's row count to the row. Otherwise the
 * count is a LOWER BOUND on the group's rows, and the screen has to say so:
 * quoting a lower bound as a denominator overstates coverage, which is the
 * exact direction of error R11 exists to stop.
 */
export interface PopulationBasis {
  column: string;
  exact: boolean;
}

export interface ColumnCompleteness {
  name: string;
  /** Non-null values, from the profile. Null when nothing has been profiled. */
  nonNullCount: number | null;
}

export function pickPopulationBasis(
  columns: readonly ColumnCompleteness[],
  rowCount: number,
  preferred: string | null,
): PopulationBasis | null {
  const complete = (c: ColumnCompleteness) =>
    rowCount > 0 && c.nonNullCount != null && c.nonNullCount >= rowCount;

  const pref = columns.find((c) => c.name === preferred);
  if (pref && complete(pref)) return { column: pref.name, exact: true };

  const any = columns.find(complete);
  if (any) return { column: any.name, exact: true };

  if (pref) return { column: pref.name, exact: false };
  return columns.length > 0 ? { column: columns[0].name, exact: false } : null;
}

/* ------------------------------------------------------------- the request */

export interface AggregateResponse {
  success: boolean;
  original_count: number;
  group_count: number;
  columns: string[];
  data?: Record<string, unknown>[] | null;
  totals?: Record<string, unknown> | null;
  /** alias -> reason, for every aggregation with no entry in `totals`. */
  totals_omitted?: Record<string, string> | null;
  /**
   * Aliases whose value is null in at least one returned group because the
   * true answer has no finite double.
   *
   * This exists because a null cell and an unrepresentable cell look identical
   * on screen and mean opposite things: "no rows matched" versus "the number
   * is real and too large to hold". `STDDEV_SAMP` squares its input, so one
   * legitimate value near 1e308 in one group is enough. Without this the empty
   * cell reads as an absence, which is exactly the confident-wrong-answer shape
   * the coverage discipline exists to prevent.
   */
  unavailable_measures?: string[] | null;
  truncated?: boolean;
  result_file?: string | null;
}

/** Companion aliases are positional and prefixed, so they cannot collide with
 * a measure alias (which is always `{column}_{function}`). */
const COUNT_PREFIX = '__n';

export interface RequestPlan {
  body: Record<string, unknown>;
  /** column -> the alias its companion `count` came back under. */
  countAlias: Record<string, string>;
}

export function buildAggregateRequest(
  datasetId: string | null,
  version: number | null,
  sheet: string | null,
  spec: AggregateSpec,
  basis: PopulationBasis | null,
): RequestPlan {
  const columns: string[] = [];
  for (const m of spec.measures) if (!columns.includes(m.column)) columns.push(m.column);
  if (basis && !columns.includes(basis.column)) columns.push(basis.column);

  const countAlias: Record<string, string> = {};
  columns.forEach((c, i) => {
    countAlias[c] = `${COUNT_PREFIX}${i}`;
  });

  const aggregations = [
    ...spec.measures.map((m) => ({ column: m.column, function: m.fn, alias: m.alias })),
    ...columns.map((c) => ({ column: c, function: 'count' as const, alias: countAlias[c] })),
  ];

  const group_by = spec.groupBy.map((g) => {
    if (!g.bucket) return g.column;
    const base = { column: g.column, alias: g.alias };
    if (g.bucket.kind === 'date_trunc') return { ...base, date_trunc: g.bucket.unit };
    if (g.bucket.kind === 'bin_width') return { ...base, bin_width: g.bucket.width };
    return { ...base, bin_count: g.bucket.count };
  });

  return {
    countAlias,
    body: {
      dataset_id: datasetId,
      version_number: version,
      sheet,
      group_by,
      aggregations,
      sort_by: spec.sortBy,
      sort_order: spec.sortOrder,
      limit: spec.limit,
      return_data: true,
    },
  };
}

/* -------------------------------------------------------------- the result */

export interface AggregateCell {
  measure: Measure;
  value: unknown;
  /** Rows that fed this figure, over rows observed in this group. */
  coverage: Coverage;
  /**
   * The value is null because it has no finite double — NOT because no rows
   * matched. The two look identical in a table cell and mean opposite things.
   */
  unrepresentable: boolean;
}

export interface AggregateGroup {
  keyParts: { name: string; value: unknown }[];
  label: string;
  /** Rows observed in this group — exact when the basis is complete. */
  population: number;
  cells: AggregateCell[];
}

export interface TotalCell {
  measure: Measure;
  value: number | null;
  /** The service's reason, e.g. `non-additive`. Null when a total exists. */
  omittedReason: string | null;
  /**
   * Null when the response carried no companion count, in which case the
   * figure must not be rendered at all — there is nothing to divide by.
   */
  coverage: Coverage | null;
}

export interface AggregateView {
  groups: AggregateGroup[];
  totals: TotalCell[];
  /** Groups in the returned page — NOT the number of groups that exist. */
  groupCount: number;
  /** Rows in the source the aggregate ran over. */
  originalCount: number;
  truncated: boolean;
  basis: PopulationBasis | null;
}

/** Numbers arrive as numbers, but a decimal column can come back as a string. */
export function numberOf(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function groupKeyText(v: unknown): string {
  if (v === null || v === undefined) return '(null)';
  if (typeof v === 'number') return v.toLocaleString();
  return String(v);
}

export function toAggregateView(
  res: AggregateResponse,
  spec: AggregateSpec,
  plan: RequestPlan,
  basis: PopulationBasis | null,
): AggregateView {
  const rows = res.data ?? [];
  const companionAliases = Object.values(plan.countAlias);

  const groups: AggregateGroup[] = rows.map((row) => {
    // The widest non-null count in the group. When the basis column is
    // complete this IS the group's row count; when it is not, it is the
    // largest number of rows we can prove the group has.
    const population = companionAliases.reduce((max, alias) => {
      const n = numberOf(row[alias]);
      return n != null && n > max ? n : max;
    }, 0);

    const keyParts = spec.groupBy.map((g) => ({ name: g.alias, value: row[g.alias] }));

    return {
      keyParts,
      label: keyParts.map((p) => groupKeyText(p.value)).join(' · '),
      population,
      cells: spec.measures.map((m) => ({
        measure: m,
        value: row[m.alias],
        coverage: coverage(numberOf(row[plan.countAlias[m.column]]) ?? 0, population),
        // A null here means one of two opposite things. The service tells us
        // which; passing that through is what stops an unrepresentable figure
        // reading as an absent one.
        unrepresentable:
          row[m.alias] == null && (res.unavailable_measures ?? []).includes(m.alias),
      })),
    };
  });

  const totals: TotalCell[] = spec.measures.map((m) => {
    const counted = numberOf(res.totals?.[plan.countAlias[m.column]]);
    return {
      measure: m,
      value: numberOf(res.totals?.[m.alias]),
      omittedReason: res.totals_omitted?.[m.alias] ?? null,
      coverage: counted == null ? null : coverage(counted, res.original_count),
    };
  });

  return {
    groups,
    totals,
    groupCount: res.group_count,
    originalCount: res.original_count,
    truncated: res.truncated ?? false,
    basis,
  };
}

/* ---------------------------------------------------------------- the hook */

/**
 * Run the spec.
 *
 * `retry: false` for the same reason the analysis lenses set it: this endpoint
 * refuses outright (`403 sensitive-data-restricted`) on a dataset that declares
 * a sensitive column, because an aggregate over a masked column could
 * reconstruct the values it hides. Retrying a refusal three times only delays
 * the message.
 */
export function useAggregate(args: {
  datasetId: string | null;
  version: number | null;
  sheet: string | null;
  spec: AggregateSpec;
  basis: PopulationBasis | null;
  enabled: boolean;
}) {
  const { datasetId, version, sheet, spec, basis, enabled } = args;
  const seat = useIdentityStore((s) => s.identity.userId);

  const plan = useMemo(
    () => buildAggregateRequest(datasetId, version, sheet, spec, basis),
    [datasetId, version, sheet, spec, basis],
  );

  const query = useQuery({
    queryKey: ['analytics', seat, 'aggregate', plan.body],
    queryFn: () => analytics.post<AggregateResponse>('/aggregate', plan.body),
    enabled: enabled && spec.groupBy.length > 0 && spec.measures.length > 0,
    retry: false,
    // Deliberately NO `placeholderData: prev`. Holding the previous response
    // across a spec change would have `toAggregateView` read the new spec's
    // aliases out of the old response, and every missing alias would map to a
    // figure of 0 over a denominator that belongs to a different question. A
    // brief loading state is the honest alternative.
  });

  const view = useMemo(() => {
    if (!query.data) return null;
    // Belt and braces on the same hazard: only map a response that actually
    // answers THIS spec.
    const returned = new Set(query.data.columns);
    const expected = [
      ...spec.measures.map((m) => m.alias),
      ...Object.values(plan.countAlias),
    ];
    if (!expected.every((alias) => returned.has(alias))) return null;
    return toAggregateView(query.data, spec, plan, basis);
  }, [query.data, spec, plan, basis]);

  return { query, view, plan };
}
