/**
 * The column deep-dive: ONE column, examined properly.
 *
 * Every other surface in the studio shows a column as a row in a list. This is
 * the screen you open when the row is not enough — when you need to know what
 * is actually in the column, what it is shaped like, and what it moves with.
 *
 * Three things govern the whole file, and all three are the shape rules paying
 * off rather than styling decisions:
 *
 *   R11 — every statistic carries the population it was computed over. A column
 *     that is 80% parsed carries `· 80%` on its type badge and a denominator on
 *     each figure, because an unqualified `mean` over a narrowed denominator is
 *     the one failure mode that produces a wrong number a human acts on. The
 *     type system enforces it: `<Stat>` cannot be rendered without a Coverage.
 *
 *   R4 — above 95% distinct a top-values chart is meaningless (every bar is one
 *     row tall), so an identity panel replaces it. Critically the suppression is
 *     STATED: the reference implementation drew no card at all for such a
 *     column, and a column with no card reads as a column with no problem.
 *
 *   R9 — zero rows is a state, not a failure. Nothing here divides by a
 *     population it has not checked; `ratio()` returns null and the null is
 *     rendered as "—", never as `NaN%`.
 *
 * Two backend facts shape the flow:
 *
 *   - Nothing profiles automatically. `POST /profile` is a computation, so this
 *     screen opens with an explicit "run a profile" affordance rather than
 *     firing one on route entry and reading as broken while it does.
 *   - Profiling is REFUSED outright — not masked — for a viewer or editor on a
 *     dataset that declares a sensitive column, because `top_values` would leak
 *     raw values verbatim. That refusal is a first-class state here.
 */

import { useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, ExternalLink, Play } from 'lucide-react';

import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import {
  complete,
  coverage,
  ratio,
  type Coverage,
} from '@/shared/components/instrument/coverage';
import { foldTopN, VIZ_SLOTS, vizSlot } from '@/shared/components/instrument/series';
import {
  addressColumns,
  chartCandidates,
  chartOffers,
  isIdentityLike,
  isRatioShaped,
  middleTruncate,
  typeConformance,
  type ShapeColumn,
} from '@/shared/components/instrument/shape';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { Guard } from '@/shared/components/instrument/Guard';
import { Status } from '@/shared/components/instrument/Status';
import { Footnote, Identifier, Metric } from '@/shared/components/instrument/Typography';
import { errorText } from '@/shared/lib/analyticsClient';
import { compact, num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { isRestricted, useProfile, type ColumnProfile } from '../hooks/useAnalysis';
import {
  useDatasetCatalog,
  useRows,
  useSheets,
  useVersions,
  type QuerySpec,
} from '../hooks/useDatasets';
import { controlClass } from './fieldStyles';
import { DtypeChip, LensEmpty, LensError, LensLoading, LensRestricted } from './lenses/primitives';

/**
 * One page of rows, read once, used three ways: the correlation matrix, the
 * length/charset signature and the sampled values. It is a SAMPLE and every
 * number derived from it says so through its Coverage — the profile endpoint
 * returns no per-value lengths, and inventing them from a full scan the client
 * never did would be exactly the confident wrong number R11 exists to stop.
 */
const SAMPLE_LIMIT = 200;

/** How many sampled values the identity panel shows. */
const IDENTITY_SAMPLES = 5;

/* ------------------------------------------------------------------ helpers */

/** A cell as a number, or null. Strings are accepted — CSV columns arrive typed. */
function toNumber(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string') {
    const t = v.trim().replace(/,/g, '');
    if (t === '') return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * Pearson r over paired observations.
 *
 * Returns null rather than a number in the two cases where r does not exist:
 * fewer than three pairs, and a constant series. A constant series has zero
 * variance, so the correlation is undefined — printing 0 there would claim
 * "measured, and unrelated" when the truth is "not measurable".
 */
function pearson(pairs: readonly (readonly [number, number])[]): number | null {
  const n = pairs.length;
  if (n < 3) return null;

  let sx = 0;
  let sy = 0;
  for (const [x, y] of pairs) {
    sx += x;
    sy += y;
  }
  const mx = sx / n;
  const my = sy / n;

  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (const [x, y] of pairs) {
    const dx = x - mx;
    const dy = y - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  if (sxx <= 0 || syy <= 0) return null;
  return sxy / Math.sqrt(sxx * syy);
}

/**
 * The character-class signature of a value: `RT-40118822` → `A{2}-9{8}`.
 *
 * This is what replaces a distribution on an identity-like column. A top-values
 * chart of 84,213 unique ids says nothing; "100% of the sample matches
 * A{2}-9{8}" says the column is well-formed, and a second signature at 0.4%
 * says it is not.
 */
function charSignature(value: string): string {
  const classes = [...value].map((ch) => {
    if (/[0-9]/.test(ch)) return '9';
    if (/[A-Za-z]/.test(ch)) return 'A';
    if (/\s/.test(ch)) return '␣';
    return ch;
  });

  let out = '';
  let i = 0;
  while (i < classes.length) {
    let j = i;
    while (j < classes.length && classes[j] === classes[i]) j += 1;
    const run = j - i;
    out += run > 1 ? `${classes[i]}{${run}}` : classes[i];
    i = j;
  }
  return out;
}

function percent(c: Coverage): string {
  const r = ratio(c);
  return r === null ? '—' : `${(r * 100).toFixed(1)}%`;
}

/** Median of an already-sorted-or-not numeric list. Null on an empty list (R9). */
function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

/* --------------------------------------------------------------- sub-marks */

interface DistBar {
  key: string;
  label: string;
  count: number;
  /** Categorical hue. Omitted for magnitude — the default and the common case. */
  color?: string;
  /** True for the folded "Other" residual, which is stated but not emphasised. */
  residual?: boolean;
}

/**
 * A labelled distribution.
 *
 * Bars are scaled to the PEAK so lengths are comparable within the group, while
 * the share beside each is computed against the POPULATION — two different
 * denominators, which is precisely why neither is a bare percentage here.
 */
function Distribution({
  rows,
  peak,
  population,
  testid,
}: {
  rows: DistBar[];
  peak: number;
  population: number;
  testid: string;
}) {
  return (
    <div className="flex flex-col gap-1.5" data-testid={testid}>
      {rows.map((r) => (
        <div
          key={r.key}
          data-testid={r.residual ? 'distribution-other' : 'distribution-row'}
          className="grid grid-cols-[minmax(0,8rem)_1fr_3.5rem_3rem] items-center gap-2"
        >
          <Identifier
            className={cn(
              'truncate text-small',
              r.residual ? 'text-muted-foreground' : 'text-foreground',
            )}
            title={r.label}
          >
            {middleTruncate(r.label, 18)}
          </Identifier>
          <MagnitudeBar of={coverage(r.count, peak)} color={r.color} />
          <span className="text-right text-micro tabular-nums text-muted-foreground">
            {compact(r.count)}
          </span>
          <span className="text-right text-micro tabular-nums text-muted-foreground">
            {percent(coverage(r.count, population))}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------- page */

interface ColumnPageProps {
  /** From `?dataset=<id>`. Null falls back to the first dataset this seat sees. */
  datasetId: string | null;
  /** From `?column=<name>`. Null falls back to the sheet's first column. */
  columnName: string | null;
}

export function ColumnPage({ datasetId, columnName }: ColumnPageProps) {
  const navigate = useNavigate();

  // The search params are the OPENING preference, not the live selection: the
  // picker below is local state, the same shape `DatasetsPage` uses for its
  // `?dataset=` param. Resolving against live data during render (rather than
  // syncing it from an effect) is what stops a stale column name pairing with a
  // freshly loaded sheet.
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  // Held as an ORDINAL, not a name (R10): two columns both called `Amount` are
  // one value in a picker, and "which one did I open?" becomes a coin flip.
  const [pickedOrdinal, setPickedOrdinal] = useState<number | null>(null);
  const [profileRequested, setProfileRequested] = useState(false);

  const catalog = useDatasetCatalog({});
  const datasets = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const selectedId = datasetId ?? datasets[0]?.id ?? null;
  const dataset = datasets.find((d) => d.id === selectedId) ?? null;

  const versionsQuery = useVersions(selectedId);
  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data]);
  const version =
    versions.length > 0 ? Math.max(...versions.map((v) => v.version_number)) : null;

  const sheetsQuery = useSheets(selectedId, version);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);
  const sheet =
    pickedSheet && sheets.some((s) => s.name === pickedSheet)
      ? pickedSheet
      : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;
  const sheetColumns = useMemo(() => sheetRow?.columns ?? [], [sheetRow]);

  // Profiling is a computation, and it is refused outright for a seat without
  // raw access. Both are reasons not to fire it on route entry: passing a null
  // sheet keeps the query disabled until someone asks for it.
  const profile = useProfile(selectedId, version, profileRequested ? sheet : null);

  // The row sample is a plain read, so it is not gated — it is what lets the
  // screen say something true about a column before any profile exists.
  const spec = useMemo<QuerySpec>(() => ({ limit: SAMPLE_LIMIT }), []);
  const rowsQuery = useRows(selectedId, version, sheet, spec, null, Boolean(sheet));
  const sampleRows = useMemo(() => rowsQuery.data?.items ?? [], [rowsQuery.data]);
  const maskedColumns = useMemo(
    () => rowsQuery.data?.masked_columns ?? [],
    [rowsQuery.data],
  );

  /**
   * The sheet's columns, merged from both sources. The sheet listing carries the
   * storage dtype (`DOUBLE`, `VARCHAR`) which is what the badge should show; the
   * profile carries the counts every shape rule needs.
   */
  const columnList = useMemo<ShapeColumn[]>(() => {
    const profiled = new Map<string, ColumnProfile>(
      (profile.data?.columns ?? []).map((c) => [c.name, c]),
    );
    const declared = new Map(sheetColumns.map((c) => [c.name, c]));
    const names =
      sheetColumns.length > 0
        ? sheetColumns.map((c) => c.name)
        : (profile.data?.columns ?? []).map((c) => c.name);

    return names.map((name) => {
      const p = profiled.get(name);
      return {
        name,
        dtype: declared.get(name)?.dtype ?? p?.dtype ?? null,
        uniqueCount: p?.unique_count ?? null,
        nonNullCount: p?.non_null_count ?? null,
      };
    });
  }, [profile.data, sheetColumns]);

  // R10 — a column is addressed by ORDINAL + NAME. Two columns both called
  // `Amount` are indistinguishable after truncation, so duplicates carry ⟨n⟩.
  const addressed = useMemo(
    () => addressColumns(columnList.map((c) => c.name)),
    [columnList],
  );
  const activeIndex =
    pickedOrdinal != null && addressed[pickedOrdinal - 1]
      ? pickedOrdinal - 1
      : Math.max(
          0,
          addressed.findIndex((a) => a.name === columnName),
        );
  const active = addressed[activeIndex] ?? null;
  const activeName = active?.name ?? null;
  const shapeCol = columnList[activeIndex] ?? null;
  const colProfile =
    profile.data?.columns.find((c) => c.name === activeName) ?? null;

  /* ------------------------------------------------------- denominators */

  // The population every statistic on this screen claims to describe.
  const totalRows = colProfile?.count ?? sheetRow?.row_count ?? 0;
  const filled = colProfile?.non_null_count ?? 0;
  /** R11 — what the column's own statistics were actually computed over. */
  const parsed = coverage(filled, totalRows);
  /** Nulls are counted over every row, so they get the full population. */
  const allRows = complete(totalRows);
  /** Everything derived from the row sample rather than from the profile. */
  const sampleCoverage = coverage(sampleRows.length, totalRows);

  const conformance = shapeCol ? typeConformance(shapeCol, totalRows) : null;
  const identityLike = shapeCol ? isIdentityLike(shapeCol) : false;
  const columnMasked = activeName != null && maskedColumns.includes(activeName);

  /* ------------------------------------------------- sample-derived facts */

  const sampleValues = useMemo(() => {
    if (!activeName || columnMasked) return [] as string[];
    const out: string[] = [];
    for (const row of sampleRows) {
      const v = row[activeName];
      if (v === null || v === undefined || v === '') continue;
      out.push(String(v));
    }
    return out;
  }, [sampleRows, activeName, columnMasked]);

  const lengths = useMemo(() => sampleValues.map((v) => v.length), [sampleValues]);
  const lengthBars = useMemo<DistBar[]>(() => {
    if (lengths.length === 0) return [];
    const lo = Math.min(...lengths);
    const hi = Math.max(...lengths);
    if (lo === hi) {
      return [{ key: 'fixed', label: `${lo} chars · fixed`, count: lengths.length }];
    }
    const bins = Math.min(8, hi - lo + 1);
    const width = (hi - lo + 1) / bins;
    const counts = new Array<number>(bins).fill(0);
    for (const len of lengths) {
      const idx = Math.min(bins - 1, Math.floor((len - lo) / width));
      counts[idx] += 1;
    }
    return counts.map((count, i) => {
      const start = Math.round(lo + i * width);
      const end = Math.round(lo + (i + 1) * width) - 1;
      return {
        key: `len-${i}`,
        label: start === end ? `${start}` : `${start}–${end}`,
        count,
      };
    });
  }, [lengths]);

  const signatures = useMemo(() => {
    const counts = new Map<string, number>();
    for (const v of sampleValues) {
      const sig = charSignature(v);
      counts.set(sig, (counts.get(sig) ?? 0) + 1);
    }
    return [...counts.entries()].map(([signature, count]) => ({ signature, count }));
  }, [sampleValues]);
  const topSignature = useMemo(
    () => foldTopN(signatures, (s) => s.count, 3),
    [signatures],
  );

  /* --------------------------------------------------------- correlations */

  const candidates = useMemo(() => chartCandidates(columnList), [columnList]);
  /** True when this column is something you aggregate rather than something you read. */
  const isMeasure = candidates.measures.some((m) => m.name === activeName);
  const correlationOffer = useMemo(
    () => chartOffers(candidates).find((o) => o.kind === 'correlation') ?? null,
    [candidates],
  );

  const correlations = useMemo(() => {
    if (!activeName || sampleRows.length === 0) return [];
    // A masked column contributes NO correlations — it is dropped before the
    // matrix is computed, not after, because a correlation against a hidden
    // series is still a fact derived from its values.
    if (columnMasked) return [];

    const partners = candidates.measures.filter(
      (c) => c.name !== activeName && !maskedColumns.includes(c.name),
    );

    const out: { name: string; r: number; pairs: number }[] = [];
    for (const partner of partners) {
      const pairs: [number, number][] = [];
      for (const row of sampleRows) {
        const x = toNumber(row[activeName]);
        const y = toNumber(row[partner.name]);
        if (x === null || y === null) continue;
        pairs.push([x, y]);
      }
      const r = pearson(pairs);
      if (r === null) continue;
      out.push({ name: partner.name, r, pairs: pairs.length });
    }
    return out;
  }, [activeName, sampleRows, candidates.measures, maskedColumns, columnMasked]);

  // R7 applies to a ranked list as much as to a chart: rank, take the top 8,
  // and state what was folded rather than letting it fall off the bottom.
  const foldedCorrelations = useMemo(
    () => foldTopN(correlations, (c) => Math.abs(c.r)),
    [correlations],
  );

  /* --------------------------------------------------------- top values */

  // Not memoised: top-k is bounded by the profile request (five entries), so
  // the fold is cheaper than the dependency array that would guard it.
  const tops = (colProfile?.top_values ?? []).filter(
    (t) => t.value !== null && t.value !== undefined,
  );
  const foldedTops = foldTopN(tops, (t) => t.count);

  /**
   * Rows covered by NO listed value, and how many distinct values that is.
   *
   * R7's fold, extended to the fact that the profile itself returns a bounded
   * top-k: `foldTopN` can only fold what it was given, so everything the
   * endpoint never listed would otherwise vanish. The residual is stated with
   * its own count and share instead — the reference implementation left 61.2%
   * of the rows unaccounted for anywhere on the screen.
   */
  const listedRows = tops.reduce((sum, t) => sum + t.count, 0);
  const residualRows = (foldedTops.other?.value ?? 0) + Math.max(0, filled - listedRows);
  const residual =
    colProfile && residualRows > 0
      ? {
          rows: residualRows,
          distinct:
            colProfile.unique_count != null
              ? Math.max(0, colProfile.unique_count - foldedTops.head.length)
              : null,
        }
      : null;

  /* -------------------------------------------------------------- render */

  const message =
    versionsQuery.error ?? sheetsQuery.error
      ? errorText(versionsQuery.error ?? sheetsQuery.error, {
          notFound: 'This dataset has no readable version, or is not available to this seat.',
        })
      : null;

  const go = (delta: number) => {
    const next = addressed[activeIndex + delta];
    if (next) setPickedOrdinal(next.ordinal);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="column-page">
      {/* ------------------------------------------------------- header */}
      <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
        <div className="min-w-0">
          <Identifier
            className="block truncate text-hero leading-none font-semibold text-foreground"
            title={activeName ?? undefined}
            data-testid="column-name"
          >
            {active?.label ?? '—'}
          </Identifier>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
            {/* The one accent this screen spends: scope and as-of. Everything
             * else earns emphasis with value, position and elevation. */}
            <span className="inline-flex items-center gap-1.5 text-micro text-muted-foreground">
              <span
                aria-hidden="true"
                className="size-[5px] shrink-0 rounded-full bg-[var(--sig)]"
              />
              <Identifier className="text-foreground">{sheet ?? '—'}</Identifier>
              <span>· as-of v{version ?? '—'}</span>
            </span>
            <Footnote>
              {dataset?.name ?? '—'} · column {activeIndex + 1} of {addressed.length}
            </Footnote>
            {active?.ambiguous && (
              <Footnote>name repeats on this sheet — addressed by ordinal</Footnote>
            )}
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {/* R11 — the badge is qualified whenever the column does not cover
           * every row: `int64 · 80%`. An unqualified badge is a promise. */}
          <span className="inline-flex items-center gap-1" data-testid="column-type">
            <DtypeChip dtype={shapeCol?.dtype ?? colProfile?.dtype} />
            {conformance !== null && (
              <Identifier className="text-footnote text-muted-foreground">
                · {(conformance * 100).toFixed(0)}%
              </Identifier>
            )}
          </span>
          {columnMasked && (
            <Badge variant="glass" className="font-mono text-micro" data-testid="column-masked">
              masked
            </Badge>
          )}

          <select
            value={active ? String(active.ordinal) : ''}
            onChange={(e) => setPickedOrdinal(Number(e.target.value))}
            aria-label="Column"
            className={cn(controlClass, 'w-44')}
            data-testid="column-picker"
          >
            {addressed.map((a) => (
              <option key={`${a.name}-${a.ordinal}`} value={String(a.ordinal)}>
                {a.label}
              </option>
            ))}
          </select>

          {sheets.length > 1 && (
            <select
              value={sheet ?? ''}
              onChange={(e) => {
                setPickedSheet(e.target.value);
                setPickedOrdinal(null);
              }}
              aria-label="Sheet"
              className={cn(controlClass, 'w-32')}
              data-testid="sheet-picker"
            >
              {sheets.map((s) => (
                <option key={s.sheet_key} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          )}

          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Previous column"
            disabled={activeIndex <= 0}
            onClick={() => go(-1)}
            data-testid="column-prev"
          >
            <ChevronLeft />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Next column"
            disabled={activeIndex >= addressed.length - 1}
            onClick={() => go(1)}
            data-testid="column-next"
          >
            <ChevronRight />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              navigate({ to: '/data', search: { dataset: selectedId ?? undefined } })
            }
            data-testid="open-workspace"
          >
            <ExternalLink />
            Workspace
          </Button>
        </div>
      </header>

      {/* --------------------------------------------------------- body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-8">
        {message && <LensError>{message}</LensError>}

        {!message && addressed.length === 0 && (
          <LensEmpty>
            {sheetsQuery.isLoading
              ? 'Loading the sheet…'
              : 'This sheet reports no columns, so there is nothing to examine.'}
          </LensEmpty>
        )}

        {!message && addressed.length > 0 && (
          <>
            {/* ------------------------------------------- population strip */}
            <div className="mb-3 flex flex-wrap items-start gap-x-8 gap-y-3 py-1">
              {/* These two are the DENOMINATORS, not statistics — they are the
               * population everything below is measured against, which is why
               * they are figures rather than <Stat> rows. */}
              <Metric
                label="rows in sheet"
                value={compact(totalRows)}
                note={sheet ? `sheet ${sheet}` : undefined}
                data-testid="metric-rows"
              />
              <Metric
                label="columns"
                value={String(addressed.length)}
                note={`${candidates.measures.length} numeric · ${candidates.dimensions.length} groupable`}
                data-testid="metric-columns"
              />
              <div className="flex flex-col gap-1 pt-1">
                {profile.data ? (
                  <Status kind="good" className="text-micro">
                    profiled
                  </Status>
                ) : profile.isFetching ? (
                  <Status kind="unknown" className="text-micro">
                    profiling
                  </Status>
                ) : (
                  <Status kind="unknown" className="text-micro">
                    not profiled
                  </Status>
                )}
                <Footnote>
                  {rowsQuery.error
                    ? errorText(rowsQuery.error)
                    : `${compact(sampleRows.length)} rows sampled for the signature and correlations`}
                </Footnote>
              </div>

              <Button
                size="sm"
                className="ml-auto mt-1"
                disabled={!sheet || profile.isFetching}
                onClick={() => {
                  if (profileRequested) void profile.refetch();
                  else setProfileRequested(true);
                }}
                data-testid="run-profile"
              >
                <Play />
                {profile.isFetching
                  ? 'Profiling…'
                  : profile.data
                    ? 'Re-run profile'
                    : 'Run profile'}
              </Button>
            </div>

            {/* R9 — zero rows is a state. Say it once, at the top, rather than
             * letting nine "—" figures imply something failed. */}
            {totalRows === 0 && (
              <Card className="mb-3" data-testid="empty-population">
                <CardHeader>
                  <CardTitle>No rows in this sheet</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-body text-muted-foreground">
                    Every statistic below has a zero denominator, so none is computed. This is a
                    state, not a failure — an empty sheet has no mean, and printing one would be
                    a claim the data cannot support.
                  </p>
                </CardContent>
              </Card>
            )}

            {/* --------------------------------------------- profile states */}
            {!profileRequested && (
              <Card className="mb-3" data-testid="profile-gate">
                <CardHeader>
                  <CardTitle>No profile has been run</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-body text-muted-foreground">
                    Nothing profiles automatically on upload. Until someone runs one, this column
                    has no distinct count, no null count and no distribution — every one of those
                    reads <span className="font-medium text-foreground">unknown</span>, which is
                    a different answer from <span className="font-medium text-foreground">good</span>.
                  </p>
                  <Footnote className="mt-2">
                    A profile computes over raw values, so it is refused outright for a viewer or
                    editor on a dataset that declares a sensitive column.
                  </Footnote>
                </CardContent>
              </Card>
            )}

            {profileRequested && profile.isLoading && <LensLoading>Profiling…</LensLoading>}

            {isRestricted(profile.error) && (
              <div className="mb-3" data-testid="profile-restricted">
                <LensRestricted what="Profiling" />
              </div>
            )}

            {profile.error && !isRestricted(profile.error) && (
              <LensError>{errorText(profile.error)}</LensError>
            )}

            {/* ------------------------------------------ headline figures */}
            {colProfile && (
              <Card className="mb-3" data-testid="headline">
                <CardHeader>
                  <CardTitle>Statistics</CardTitle>
                  <Footnote className="ml-auto">
                    every figure over the population it was computed on
                  </Footnote>
                </CardHeader>
                <CardContent className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                  {/* Counts describe the whole sheet — their denominator is
                   * every row, including the nulls. */}
                  <StatList coverage={allRows} data-testid="stats-counts">
                    <Stat name="rows" value={num(colProfile.count)} coverage={allRows} />
                    <Stat name="filled" value={num(colProfile.non_null_count)} coverage={allRows} />
                    <Stat name="null" value={num(colProfile.null_count)} coverage={allRows} />
                    <Stat
                      name="null %"
                      value={percent(coverage(colProfile.null_count ?? 0, totalRows))}
                      coverage={allRows}
                    />
                    <Stat
                      name="distinct"
                      value={num(colProfile.unique_count)}
                      coverage={parsed}
                    />
                    <Stat
                      name="unique %"
                      value={percent(coverage(colProfile.unique_count ?? 0, filled))}
                      coverage={parsed}
                    />
                  </StatList>

                  {/* Value statistics only ever saw the filled rows. On a mixed
                   * or partly-null column that is a NARROWER population than the
                   * sheet, and every row here says so. */}
                  <StatList coverage={parsed} data-testid="stats-values">
                    <Stat name="min" value={num(colProfile.min)} coverage={parsed} />
                    <Stat name="max" value={num(colProfile.max)} coverage={parsed} />
                    <Stat name="mean" value={num(colProfile.mean)} coverage={parsed} />
                    <Stat name="p50" value={num(colProfile.median)} coverage={parsed} />
                    <Stat name="std" value={num(colProfile.std)} coverage={parsed} />
                  </StatList>

                  {/* A statistic can be null for two different reasons, and an
                    * em dash cannot tell them apart. When the service says the
                    * value has no finite double, say THAT — "not computed" and
                    * "too large to represent" lead a reader to opposite
                    * conclusions about their data. */}
                  {(colProfile.unavailable_stats?.length ?? 0) > 0 && (
                    <Guard tone="warning" className="mt-2" data-testid="unavailable-stats">
                      {colProfile.unavailable_stats!.join(', ')}{' '}
                      {colProfile.unavailable_stats!.length === 1 ? 'has' : 'have'} no finite
                      value on this column — the true answer overflows a double, so it is
                      withheld rather than rounded to something wrong. Every other statistic
                      here is unaffected.
                    </Guard>
                  )}
                </CardContent>
              </Card>
            )}

            {/* -------------------------------------------- distribution */}
            {colProfile && (
              <Card className="mb-3" data-testid="distribution-card">
                <CardHeader>
                  <CardTitle>
                    {identityLike
                      ? 'Identity'
                      : isRatioShaped(colProfile.unique_count ?? 0)
                        ? 'Ratio'
                        : 'Distribution'}
                  </CardTitle>
                  <Footnote className="ml-auto">
                    {compact(colProfile.unique_count)} distinct over {compact(filled)} filled rows
                  </Footnote>
                </CardHeader>
                <CardContent>
                  {identityLike ? (
                    /* R4 — the identity panel. The suppression is STATED: a
                     * column with no card reads as a column with no problem. */
                    <div data-testid="identity-panel">
                      <p className="text-body" data-testid="suppression-note">
                        <span className="font-medium text-foreground">
                          Distribution suppressed, not omitted.
                        </span>{' '}
                        <span className="text-muted-foreground">
                          This column is {percent(coverage(colProfile.unique_count ?? 0, filled))}{' '}
                          distinct. Above 95% a top-values chart is meaningless — every bar would
                          be one row tall. What follows describes the column&apos;s identity
                          instead: how many values, how long they are, and what shape they take.
                        </span>
                      </p>

                      <div className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-2">
                        <StatList coverage={parsed}>
                          <Stat
                            name="distinct"
                            value={`${num(colProfile.unique_count)} / ${num(filled)}`}
                            coverage={parsed}
                          />
                          <Stat name="null" value={num(colProfile.null_count)} coverage={allRows} />
                          <Stat
                            name="length"
                            value={
                              lengths.length === 0
                                ? '—'
                                : `${Math.min(...lengths)}–${Math.max(...lengths)} · p50 ${num(median(lengths))}`
                            }
                            coverage={sampleCoverage}
                          />
                          <Stat
                            name="charset"
                            value={
                              topSignature.head.length === 0
                                ? '—'
                                : middleTruncate(topSignature.head[0].signature, 24)
                            }
                            coverage={sampleCoverage}
                            data-testid="charset-signature"
                          />
                        </StatList>

                        <div className="min-w-0">
                          <Footnote className="mb-1.5">
                            length distribution · sampled
                          </Footnote>
                          {lengthBars.length > 0 ? (
                            <Distribution
                              rows={lengthBars}
                              peak={Math.max(...lengthBars.map((b) => b.count))}
                              population={sampleValues.length}
                              testid="length-distribution"
                            />
                          ) : (
                            <Footnote>
                              {columnMasked
                                ? 'values are masked for this seat — no length signature'
                                : 'no sampled values'}
                            </Footnote>
                          )}
                        </div>
                      </div>

                      <div className="mt-3">
                        <Footnote className="mb-1.5">
                          {Math.min(IDENTITY_SAMPLES, sampleValues.length)} values from a{' '}
                          {compact(sampleRows.length)}-row sample · middle-truncated so the
                          discriminating head AND tail both survive
                        </Footnote>
                        {sampleValues.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5" data-testid="sample-values">
                            {sampleValues.slice(0, IDENTITY_SAMPLES).map((v, i) => (
                              <Identifier
                                key={`${v}-${i}`}
                                className="rounded bg-muted px-1.5 py-0.5 text-small text-foreground"
                                title={v}
                              >
                                {middleTruncate(v, 26)}
                              </Identifier>
                            ))}
                          </div>
                        ) : (
                          <Footnote>
                            {columnMasked
                              ? 'Values are masked for this seat, so no sample is shown. Masking is applied on the way out — the column keeps its shape while its values never leave the service.'
                              : 'No sampled values.'}
                          </Footnote>
                        )}
                      </div>
                    </div>
                  ) : colProfile.dtype === 'numeric' && (colProfile.histogram?.length ?? 0) > 0 ? (
                    (() => {
                      const bins = colProfile.histogram ?? [];
                      const peak = Math.max(...bins.map((b) => b.count), 0);
                      if (peak <= 0) {
                        return <LensEmpty>Every bin is empty — nothing to plot.</LensEmpty>;
                      }
                      return (
                        <>
                          <Distribution
                            rows={bins.map((b, i) => ({
                              key: `bin-${i}`,
                              label: `${num(b.bin_start)} – ${num(b.bin_end)}`,
                              count: b.count,
                            }))}
                            peak={peak}
                            population={filled}
                            testid="histogram"
                          />
                          <Footnote className="mt-2">
                            {bins.length} equal-width bins over [{num(colProfile.min)},{' '}
                            {num(colProfile.max)}] · shares are of the {compact(filled)} filled
                            rows, not of the sheet
                          </Footnote>
                        </>
                      );
                    })()
                  ) : isRatioShaped(colProfile.unique_count ?? 0) && tops.length > 0 ? (
                    /* R7 — below three distinct values a bar chart is a ratio.
                     * Two bars side by side invite a comparison of lengths when
                     * the only fact is a proportion, so draw the proportion. */
                    <div data-testid="ratio">
                      <div className="flex h-3 w-full overflow-hidden rounded-[3px] bg-[var(--m6)]">
                        {foldedTops.head.map((t, i) => {
                          const share = ratio(coverage(t.count, filled));
                          if (share === null) return null;
                          return (
                            <span
                              key={String(t.value)}
                              style={{ width: `${share * 100}%`, background: vizSlot(i) }}
                            />
                          );
                        })}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                        {foldedTops.head.map((t, i) => (
                          <span
                            key={String(t.value)}
                            className="inline-flex items-center gap-1.5 text-small"
                          >
                            <span
                              aria-hidden="true"
                              className="size-2 shrink-0 rounded-[2px]"
                              style={{ background: vizSlot(i) }}
                            />
                            <Identifier className="text-foreground">
                              {middleTruncate(String(t.value), 20)}
                            </Identifier>
                            <span className="text-muted-foreground tabular-nums">
                              {percent(coverage(t.count, filled))}
                            </span>
                          </span>
                        ))}
                      </div>
                      <Footnote className="mt-2">
                        {colProfile.unique_count} distinct values — this is a proportion, not a
                        distribution, so it is drawn as one.
                      </Footnote>
                    </div>
                  ) : tops.length > 0 ? (
                    <>
                      <Distribution
                        rows={[
                          ...foldedTops.head.map((t, i) => ({
                            key: String(t.value),
                            label: String(t.value),
                            count: t.count,
                            color: vizSlot(i),
                          })),
                          // R7 — the tail is FOLDED, carrying its own count and
                          // share, never dropped off the bottom of the list.
                          ...(residual
                            ? [
                                {
                                  key: '__other__',
                                  label:
                                    residual.distinct != null && residual.distinct > 0
                                      ? `${compact(residual.distinct)} other values`
                                      : 'rows in no listed value',
                                  count: residual.rows,
                                  color: vizSlot(VIZ_SLOTS),
                                  residual: true,
                                },
                              ]
                            : []),
                        ]}
                        peak={Math.max(
                          ...foldedTops.head.map((t) => t.count),
                          residual?.rows ?? 0,
                        )}
                        population={filled}
                        testid="top-values"
                      />
                      <Footnote className="mt-2">
                        Top {foldedTops.head.length} by count. The profile returns a bounded top-k,
                        so everything past it is folded into one graphite row that carries its own
                        count and share rather than going unstated.
                      </Footnote>
                    </>
                  ) : (
                    <LensEmpty>
                      This run returned no top values for the column — a numeric column profiled
                      without histograms has no distribution to draw.
                    </LensEmpty>
                  )}
                </CardContent>
              </Card>
            )}

            {/* ------------------------------------- text/charset signature */}
            {!identityLike && !isMeasure && sampleValues.length > 0 && (
              <Card className="mb-3" data-testid="signature-card">
                <CardHeader>
                  <CardTitle>Length and character-class signature</CardTitle>
                  <Footnote className="ml-auto">from the row sample</Footnote>
                </CardHeader>
                <CardContent className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                  <div className="min-w-0">
                    <Footnote className="mb-1.5">length distribution</Footnote>
                    <Distribution
                      rows={lengthBars}
                      peak={Math.max(...lengthBars.map((b) => b.count))}
                      population={sampleValues.length}
                      testid="length-distribution"
                    />
                  </div>
                  <StatList coverage={sampleCoverage} data-testid="stats-signature">
                    <Stat
                      name="length"
                      value={`${Math.min(...lengths)}–${Math.max(...lengths)}`}
                      coverage={sampleCoverage}
                    />
                    <Stat name="p50 len" value={num(median(lengths))} coverage={sampleCoverage} />
                    {topSignature.head.map((s) => (
                      <Stat
                        key={s.signature}
                        name="charset"
                        value={`${middleTruncate(s.signature, 20)} · ${percent(
                          coverage(s.count, sampleValues.length),
                        )}`}
                        coverage={coverage(s.count, sampleValues.length)}
                      />
                    ))}
                  </StatList>
                </CardContent>
              </Card>
            )}

            {/* ------------------------------------------------ correlations */}
            <Card className="mb-3" data-testid="correlations-card">
              <CardHeader>
                <CardTitle>Correlation with other numeric columns</CardTitle>
                <Footnote className="ml-auto">Pearson r · over the row sample</Footnote>
              </CardHeader>
              <CardContent>
                {correlationOffer && !correlationOffer.available ? (
                  <LensEmpty>Withdrawn — {correlationOffer.reason}.</LensEmpty>
                ) : columnMasked ? (
                  <LensEmpty>
                    This column is masked for your seat, so it contributes no correlations. It is
                    dropped before the matrix is computed, not after — a correlation against a
                    hidden series is still derived from its values.
                  </LensEmpty>
                ) : rowsQuery.isLoading ? (
                  <LensLoading>Sampling rows…</LensLoading>
                ) : rowsQuery.error ? (
                  <LensError>{errorText(rowsQuery.error)}</LensError>
                ) : foldedCorrelations.head.length === 0 ? (
                  <LensEmpty>
                    No pair in the sample had three or more rows where both columns parse as
                    numbers, so no r exists to report.
                  </LensEmpty>
                ) : (
                  <>
                    <div className="flex flex-col gap-1.5" data-testid="correlations">
                      {foldedCorrelations.head.map((c) => (
                        <div
                          key={c.name}
                          data-testid="correlation-row"
                          className="grid grid-cols-[minmax(0,9rem)_1fr_4.5rem_auto] items-center gap-3"
                        >
                          <Identifier
                            className="truncate text-small text-foreground"
                            title={c.name}
                          >
                            {middleTruncate(c.name, 20)}
                          </Identifier>
                          {/* |r| is a magnitude, so it takes the neutral ramp.
                           * The direction is carried by a WORD beside it and by
                           * the sign on the figure — never by hue alone, which
                           * is what a two-colour diverging cell would rely on. */}
                          <MagnitudeBar
                            of={coverage(Math.round(Math.abs(c.r) * 1000), 1000)}
                          />
                          <span className="text-micro text-muted-foreground">
                            {c.r >= 0 ? 'positive' : 'negative'}
                          </span>
                          <Stat
                            name="r"
                            value={`${c.r >= 0 ? '+' : '−'}${Math.abs(c.r).toFixed(3)}`}
                            coverage={coverage(c.pairs, sampleRows.length)}
                            className="justify-end"
                          />
                        </div>
                      ))}
                    </div>

                    {foldedCorrelations.other && (
                      <Footnote className="mt-2" data-testid="correlations-other">
                        {foldedCorrelations.other.count} further numeric columns fall below the
                        top {VIZ_SLOTS} by |r| and are folded here rather than dropped.
                      </Footnote>
                    )}

                    <Footnote className="mt-2">
                      Computed client-side over {compact(sampleRows.length)} sampled rows of{' '}
                      {compact(totalRows)} — {percent(sampleCoverage)} of the sheet. It is a
                      sample, so it is a signal to check, not a population statistic. Masked
                      columns are excluded before the matrix is computed.
                    </Footnote>
                  </>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
