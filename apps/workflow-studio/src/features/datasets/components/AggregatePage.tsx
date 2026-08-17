/**
 * The aggregate builder — group-by + measures, over `POST /api/v1/aggregate`.
 *
 * This screen exists to stop one specific failure, and every decision below is
 * downstream of it: on a column that is 80.4% int64 and 14.9% unparsed text, an
 * unqualified `sum` drops a fifth of the rows and prints a confident total. So
 * EVERY figure here goes through `<Stat coverage={…}>`, whose denominator is a
 * required, branded prop — a number without a population does not compile.
 *
 * SHAPE RULES APPLIED HERE:
 *
 *  - R5 — the builder is CAPABILITY-GATED. `chartCandidates` runs before
 *    anything is offered, functions are type-gated per column, and a withdrawn
 *    offer states its reason instead of disappearing. The reference builder
 *    opened empty on an all-numeric table and stayed empty; here an empty
 *    builder is unreachable, because with no dimension the screen shows the R6
 *    offers rather than a blank shelf.
 *  - R6 — with zero dimension candidates the answer is binned dimensions,
 *    distribution and correlation, each an OFFER with its parameters on screen.
 *    Nothing is ever binned silently.
 *  - R7 — above eight groups the chart ranks, keeps eight and folds the tail
 *    into a graphite "Other" carrying its own count AND share. The palette is
 *    never cycled; below three groups the mark becomes a ratio.
 *  - R9 — zero rows is a state. `ratio()` returns null on an empty population
 *    and every mark here handles it; no `NaN%` can reach the DOM.
 *  - R11 — coverage, everywhere. See `useAggregate.ts` for how the denominator
 *    is actually obtained (companion counts) and why it is named.
 *
 * A refusal is a first-class state: `/aggregate` is refused outright on a
 * dataset that declares a sensitive column, because a computed result could
 * reconstruct the values masking hides.
 */

import { useMemo, useState } from 'react';
import { errorText } from '@/shared/lib/analyticsClient';
import { cn } from '@/shared/lib/utils';
import { compact, num } from '@/shared/lib/format';
import {
  chartCandidates,
  chartOffers,
  detectIdentity,
  isRatioShaped,
  typeConformance,
  type ChartKind,
  type ShapeColumn,
} from '@/shared/components/instrument/shape';
import { coverage, ratio } from '@/shared/components/instrument/coverage';
import { foldTopN, vizSlot, VIZ_SLOTS } from '@/shared/components/instrument/series';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { Severity } from '@/shared/components/instrument/Severity';
import { Status } from '@/shared/components/instrument/Status';
import {
  Eyebrow,
  Figure,
  Footnote,
  Identifier,
  Metric,
  SectionTitle,
} from '@/shared/components/instrument/Typography';
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table';
import { useDatasetCatalog, useSheets, useVersions } from '../hooks/useDatasets';
import { isRestricted, useProfile } from '../hooks/useAnalysis';
import {
  AGG_FUNCTIONS,
  additivityOf,
  bucketGroup,
  DATE_UNITS,
  EMPTY_SPEC,
  functionOffers,
  makeMeasure,
  numberOf,
  pickPopulationBasis,
  plainGroup,
  useAggregate,
  type AggFunction,
  type AggregateSpec,
  type Bucket,
  type DateUnit,
  type Measure,
} from '../hooks/useAggregate';
import { DtypeChip, LensError, LensRestricted, Section } from './lenses/primitives';
import { controlClass, fieldClass } from './fieldStyles';

const LIMITS = [8, 12, 25, 50, 100];

/** Sentence-case labels for the four offers `chartOffers` can return. */
const OFFER_LABEL: Record<ChartKind, string> = {
  bar: 'Rank a dimension',
  line: 'Trend over time',
  distribution: 'Distribution of a measure',
  correlation: 'Two measures against each other',
  ratio: 'Ratio',
};

const OFFER_HINT: Record<ChartKind, string> = {
  bar: 'group by a low-cardinality column and sum a measure',
  line: 'bucket a date column with date_trunc and sum a measure',
  distribution: 'bin one numeric column into equal-width buckets and count the rows',
  correlation: 'bin one numeric column and take the mean of another across the bins',
  ratio: 'too few groups for a distribution — draw the proportion',
};

interface AggregatePageProps {
  /** Preselected dataset, from `?dataset=<id>`. */
  initialDatasetId?: string | null;
}

export function AggregatePage({ initialDatasetId = null }: AggregatePageProps) {
  // Selections are PREFERENCES, resolved against live data during render —
  // storing the resolved value would need an effect on every fetch.
  const [pickedId, setPickedId] = useState<string | null>(initialDatasetId);
  const [pickedVersion, setPickedVersion] = useState<number | null>(null);
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  const [pickedSpec, setPickedSpec] = useState<AggregateSpec | null>(null);
  // Offer parameters live here so they are visible and editable BEFORE the
  // offer is accepted — R6: binning is a choice the reader makes.
  const [binCount, setBinCount] = useState(10);
  const [dateUnit, setDateUnit] = useState<DateUnit>('month');

  const catalog = useDatasetCatalog({});
  const datasets = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const selectedId = pickedId ?? datasets[0]?.id ?? null;
  const dataset = datasets.find((d) => d.id === selectedId) ?? null;

  const versionsQuery = useVersions(selectedId);
  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data]);
  const version =
    pickedVersion != null && versions.some((v) => v.version_number === pickedVersion)
      ? pickedVersion
      : versions.length > 0
        ? Math.max(...versions.map((v) => v.version_number))
        : null;

  const sheetsQuery = useSheets(selectedId, version);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);
  const sheet =
    pickedSheet && sheets.some((s) => s.name === pickedSheet)
      ? pickedSheet
      : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;

  // The profile is what makes capability-gating possible: distinctness decides
  // what is a dimension, and non-null counts decide what a denominator is worth.
  const profile = useProfile(selectedId, version, sheet);

  const shapeColumns = useMemo<ShapeColumn[]>(() => {
    const stats = new Map(profile.data?.columns.map((c) => [c.name, c]) ?? []);
    return (sheetRow?.columns ?? []).map((c) => {
      const p = stats.get(c.name);
      return {
        name: c.name,
        dtype: c.dtype ?? null,
        uniqueCount: p?.unique_count ?? null,
        nonNullCount: p?.non_null_count ?? null,
      };
    });
  }, [sheetRow, profile.data]);

  const rowCount = profile.data?.row_count ?? sheetRow?.row_count ?? 0;

  // R5 — candidates FIRST, offers second, controls third.
  const candidates = useMemo(() => chartCandidates(shapeColumns), [shapeColumns]);
  const offers = useMemo(() => chartOffers(candidates), [candidates]);
  const numericColumns = useMemo(
    () => new Set(candidates.measures.map((c) => c.name)),
    [candidates],
  );

  /**
   * The opening spec. Seeded only from a PLAIN dimension: seeding a bucket
   * would be exactly the silent inference R6 forbids, so with no dimension this
   * is null and the offer panel takes the screen instead of an empty shelf.
   */
  const seeded = useMemo<AggregateSpec | null>(() => {
    const dim = candidates.dimensions[0];
    if (!dim) return null;
    // R2 — count over the identity column when one was MEASURED. A positional
    // fallback is "the first column we found", which is not a claim worth
    // building the opening measure on; the dimension itself is safer there.
    const identity = detectIdentity(shapeColumns);
    const countColumn =
      identity?.basis === 'measured' ? identity.column.name : dim.name;
    const measures: Measure[] = [];
    measures.push(makeMeasure(countColumn, 'count', measures));
    const numeric = candidates.measures.find((m) => m.name !== dim.name);
    if (numeric) measures.push(makeMeasure(numeric.name, 'sum', measures));
    return {
      groupBy: [plainGroup(dim.name)],
      measures,
      sortBy: measures[measures.length - 1].alias,
      sortOrder: 'desc',
      limit: 12,
    };
  }, [candidates, shapeColumns]);

  const spec = pickedSpec ?? seeded;

  const basis = useMemo(
    () =>
      pickPopulationBasis(
        shapeColumns.map((c) => ({ name: c.name, nonNullCount: c.nonNullCount ?? null })),
        rowCount,
        spec?.groupBy[0]?.column ?? null,
      ),
    [shapeColumns, rowCount, spec],
  );

  const { query, view } = useAggregate({
    datasetId: selectedId,
    version,
    sheet,
    spec: spec ?? EMPTY_SPEC,
    basis,
    enabled: Boolean(selectedId) && version != null && Boolean(sheet) && spec != null,
  });

  /* ------------------------------------------------------------- mutations */

  const update = (fn: (s: AggregateSpec) => AggregateSpec) =>
    setPickedSpec(fn(spec ?? EMPTY_SPEC));

  const resetSelection = () => {
    setPickedSpec(null);
    setPickedSheet(null);
  };

  const addMeasure = (column: string, fn: AggFunction) =>
    update((s) => {
      const m = makeMeasure(column, fn, s.measures);
      return { ...s, measures: [...s.measures, m], sortBy: s.sortBy ?? m.alias };
    });

  const setMeasureFn = (id: string, fn: AggFunction) =>
    update((s) => {
      const old = s.measures.find((m) => m.id === id);
      if (!old) return s;
      const others = s.measures.filter((m) => m.id !== id);
      // The alias encodes the function, so changing one renames the output.
      // `makeMeasure` re-derives it against the OTHER measures because two
      // measures under one alias is a 400 (`duplicate-alias`) — the service
      // refuses rather than answering one measure's number under another's name.
      const next = { ...makeMeasure(old.column, fn, others), id: old.id };
      return {
        ...s,
        measures: s.measures.map((m) => (m.id === id ? next : m)),
        sortBy: s.sortBy === old.alias ? next.alias : s.sortBy,
      };
    });

  const removeMeasure = (id: string) =>
    update((s) => {
      const gone = s.measures.find((m) => m.id === id);
      const measures = s.measures.filter((m) => m.id !== id);
      return {
        ...s,
        measures,
        sortBy: s.sortBy === gone?.alias ? (measures[0]?.alias ?? null) : s.sortBy,
      };
    });

  const setGroupBucket = (index: number, bucket: Bucket | null) =>
    update((s) => ({
      ...s,
      groupBy: s.groupBy.map((g, i) =>
        i === index ? (bucket ? bucketGroup(g.column, bucket) : plainGroup(g.column)) : g,
      ),
    }));

  const addGroup = (column: string) =>
    update((s) =>
      s.groupBy.some((g) => g.column === column)
        ? s
        : { ...s, groupBy: [...s.groupBy, plainGroup(column)] },
    );

  const removeGroup = (index: number) =>
    update((s) => ({ ...s, groupBy: s.groupBy.filter((_, i) => i !== index) }));

  /** Accepting an offer writes a COMPLETE, runnable spec — never a half one. */
  const applyOffer = (kind: ChartKind) => {
    const dim = candidates.dimensions[0];
    const time = candidates.temporal[0];
    const [m0, m1] = candidates.measures;
    const build = (groupBy: AggregateSpec['groupBy'], measures: Measure[]): AggregateSpec => ({
      groupBy,
      measures,
      sortBy: measures[measures.length - 1]?.alias ?? null,
      sortOrder: 'desc',
      limit: spec?.limit ?? 12,
    });

    if (kind === 'bar' && dim) {
      const ms: Measure[] = [];
      ms.push(makeMeasure(dim.name, 'count', ms));
      if (m0) ms.push(makeMeasure(m0.name, 'sum', ms));
      setPickedSpec(build([plainGroup(dim.name)], ms));
      return;
    }
    if (kind === 'line' && time && m0) {
      const ms: Measure[] = [];
      ms.push(makeMeasure(m0.name, 'sum', ms));
      setPickedSpec(build([bucketGroup(time.name, { kind: 'date_trunc', unit: dateUnit })], ms));
      return;
    }
    if (kind === 'distribution' && m0) {
      const ms: Measure[] = [];
      ms.push(makeMeasure(m0.name, 'count', ms));
      setPickedSpec(build([bucketGroup(m0.name, { kind: 'bin_count', count: binCount })], ms));
      return;
    }
    if (kind === 'correlation' && m0 && m1) {
      const ms: Measure[] = [];
      ms.push(makeMeasure(m1.name, 'mean', ms));
      ms.push(makeMeasure(m1.name, 'count', ms));
      setPickedSpec(build([bucketGroup(m0.name, { kind: 'bin_count', count: binCount })], ms));
    }
  };

  /* --------------------------------------------------------------- states */

  const loadError = versionsQuery.error ?? sheetsQuery.error ?? null;
  const restricted = isRestricted(query.error) || isRestricted(profile.error);

  const groupKeyHeader = spec?.groupBy.map((g) => g.alias).join(' · ') ?? '—';
  const sortMeasure =
    spec?.measures.find((m) => m.alias === spec.sortBy) ?? spec?.measures[0] ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Scope: which object every number below is computed against. */}
      <header className="flex h-9 shrink-0 items-center gap-2 px-3">
        <select
          value={selectedId ?? ''}
          onChange={(e) => {
            setPickedId(e.target.value);
            setPickedVersion(null);
            resetSelection();
          }}
          className={cn(controlClass, 'w-56 shrink-0')}
          aria-label="Dataset"
          data-testid="aggregate-dataset"
        >
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <select
          value={version ?? ''}
          onChange={(e) => {
            setPickedVersion(Number(e.target.value));
            resetSelection();
          }}
          className={cn(controlClass, 'w-28 shrink-0')}
          aria-label="Version"
          data-testid="aggregate-version"
        >
          {versions.map((v) => (
            <option key={v.version_number} value={v.version_number}>
              v{v.version_number}
            </option>
          ))}
        </select>
        <select
          value={sheet ?? ''}
          onChange={(e) => {
            setPickedSheet(e.target.value);
            setPickedSpec(null);
          }}
          className={cn(controlClass, 'w-40 shrink-0')}
          aria-label="Sheet"
          data-testid="aggregate-sheet"
        >
          {sheets.map((s) => (
            <option key={s.sheet_key} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        {/* shrink-0 + nowrap: the header is a fixed 36px, so a wrapping
            footnote overflows it and collides with the shell above. */}
        <Footnote className="ml-auto shrink-0 truncate font-mono whitespace-nowrap">
          POST /aggregate · {dataset?.name ?? '—'} · {compact(rowCount)} rows
        </Footnote>
      </header>

      {/* First read. Every figure here is a count of things, so its population
       * is the rows the aggregate actually ran over. */}
      <div className="flex shrink-0 items-center gap-8 bg-[var(--s3)]/60 px-3 py-2">
        <Metric
          label="Rows in scope"
          size="figure"
          value={compact(view?.originalCount ?? rowCount)}
          note={basis ? `denominator: count(${basis.column})` : 'no denominator yet'}
        />
        <Metric
          label="Groups"
          size="figure"
          value={view ? view.groupCount.toLocaleString() : '—'}
          note={view?.truncated ? 'capped — raise the limit' : 'this page'}
        />
        <Metric
          label="Measures"
          size="figure"
          value={String(spec?.measures.length ?? 0)}
          note={`${spec?.measures.filter((m) => additivityOf(m.fn) === 'omitted').length ?? 0} without a total`}
        />
        {basis && (
          <div className="ml-auto">
            {/* Scope + liveness is the accent's job; this is the one place on
             * the screen that says what "over" means, so it takes it. */}
            <Status kind={basis.exact ? 'good' : 'warning'} className="text-small">
              {basis.exact ? 'denominator exact' : 'denominator is a lower bound'}
            </Status>
            <Footnote className="mt-0.5">
              rows per group are counted through{' '}
              <span className="font-mono">{basis.column}</span>
              {basis.exact ? ' — it has no nulls' : ' — no profiled column is complete'}
            </Footnote>
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        {/* ------------------------------------------------ columns + gates */}
        <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-border px-2 py-2">
          <SectionTitle className="px-1 pb-1.5">Columns</SectionTitle>
          {profile.isLoading && <Footnote className="px-1">Profiling…</Footnote>}
          {shapeColumns.map((c) => {
            const numeric = numericColumns.has(c.name);
            const gated = functionOffers(numeric).filter((o) => o.available).length;
            const conf = typeConformance(c, rowCount);
            const dim = candidates.dimensions.some((d) => d.name === c.name);
            return (
              <div key={c.name} className="rounded px-1 py-1 hover:bg-accent/40" data-testid="column-row">
                <div className="flex items-center gap-1.5">
                  <Identifier className="min-w-0 flex-1 truncate text-small text-foreground">
                    {c.name}
                  </Identifier>
                  {/* R11 — a partially-parsed column says so ON its type. */}
                  <DtypeChip
                    dtype={
                      conf === null
                        ? (c.dtype ?? '?')
                        : `${c.dtype ?? '?'} · ${Math.round(conf * 100)}%`
                    }
                  />
                </div>
                <div className="mt-0.5 flex items-center gap-2">
                  <Footnote className="font-mono">
                    agg {gated}/{AGG_FUNCTIONS.length}
                    {dim ? ' · groupable' : ''}
                  </Footnote>
                  <button
                    className="ml-auto rounded px-1 text-footnote text-muted-foreground hover:text-foreground"
                    onClick={() => addMeasure(c.name, numeric ? 'sum' : 'count')}
                    data-testid="column-add-measure"
                  >
                    + measure
                  </button>
                  {dim && (
                    <button
                      className="rounded px-1 text-footnote text-muted-foreground hover:text-foreground"
                      onClick={() => addGroup(c.name)}
                      data-testid="column-add-group"
                    >
                      + group
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          <Footnote className="mt-3 border-l border-border pl-2 leading-relaxed">
            Functions are type-gated. <span className="font-mono">sum · mean · median · std</span>{' '}
            need a numeric column; the rest take any type. Offering{' '}
            <span className="font-mono">sum</span> on text is a bug, so the picker never does — the
            API would answer 400.
          </Footnote>
        </aside>

        {/* -------------------------------------------- builder over result */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {loadError && (
            <div className="p-3">
              <LensError>
                {errorText(loadError, {
                  notFound: 'This dataset has no readable version, or is not available to this seat.',
                })}
              </LensError>
            </div>
          )}

          {restricted && (
            <div className="p-3">
              <LensRestricted what="Aggregation" />
            </div>
          )}

          {/* R5 — the builder is never empty and never a dead end. With no
           * spec at all the offers take the screen; with a spec that has lost
           * its dimension the shelves stay and the offers take the result
           * region, so there is always a way forward from here. */}
          {!spec ? (
            <NoDimensionPanel
              candidates={candidates}
              offers={offers}
              binCount={binCount}
              onBinCount={setBinCount}
              dateUnit={dateUnit}
              onDateUnit={setDateUnit}
              onApply={applyOffer}
            />
          ) : (
            <>
              <div className="shrink-0 px-3 py-2">
                {/* group-by shelf */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="w-14 shrink-0 text-small text-muted-foreground">Group by</span>
                  {spec.groupBy.map((g, i) => (
                    <div
                      key={`${g.column}-${i}`}
                      className="flex items-center gap-1.5 rounded bg-secondary px-1.5 py-1"
                      data-testid="groupby-chip"
                    >
                      <Identifier className="text-small text-foreground">{g.column}</Identifier>
                      <BucketControl
                        column={g.column}
                        numeric={numericColumns.has(g.column)}
                        temporal={candidates.temporal.some((t) => t.name === g.column)}
                        bucket={g.bucket}
                        onChange={(b) => setGroupBucket(i, b)}
                      />
                      <button
                        onClick={() => removeGroup(i)}
                        aria-label={`Remove ${g.column}`}
                        data-testid="groupby-remove"
                        className="text-footnote text-muted-foreground hover:text-foreground"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <select
                    value=""
                    onChange={(e) => e.target.value && addGroup(e.target.value)}
                    className={cn(fieldClass, 'w-36')}
                    aria-label="Add a dimension"
                    data-testid="groupby-add"
                  >
                    <option value="">+ dimension…</option>
                    {candidates.dimensions
                      .filter((d) => !spec.groupBy.some((g) => g.column === d.name))
                      .map((d) => (
                        <option key={d.name} value={d.name}>
                          {d.name}
                        </option>
                      ))}
                  </select>
                  <Footnote className="ml-auto">
                    the bucket is part of the dimension — its label IS the group key
                  </Footnote>
                </div>

                {/* measure shelf */}
                <div className="mt-2 flex items-start gap-1.5">
                  <span className="w-14 shrink-0 py-1 text-small text-muted-foreground">
                    Measures
                  </span>
                  <div className="min-w-0 flex-1 rounded bg-[var(--s3)]/70">
                    {spec.measures.map((m) => {
                      const gates = functionOffers(numericColumns.has(m.column));
                      const additivity = additivityOf(m.fn);
                      return (
                        <div
                          key={m.id}
                          className="flex items-center gap-2 px-2 py-1"
                          data-testid="measure-row"
                        >
                          <select
                            value={m.fn}
                            onChange={(e) => setMeasureFn(m.id, e.target.value as AggFunction)}
                            className={cn(fieldClass, 'w-24 font-mono')}
                            aria-label={`Function for ${m.column}`}
                            data-testid="measure-fn"
                          >
                            {gates.map((o) => (
                              <option
                                key={o.fn}
                                value={o.fn}
                                disabled={!o.available}
                                title={o.reason}
                              >
                                {o.fn}
                                {o.available ? '' : ` — ${o.reason}`}
                              </option>
                            ))}
                          </select>
                          <Identifier className="min-w-0 flex-1 truncate text-small">
                            {m.column}
                          </Identifier>
                          <Identifier className="w-40 shrink-0 truncate text-small text-foreground">
                            {m.alias}
                          </Identifier>
                          {/* R7 (visual) — additivity is a PROPERTY, so it is
                           * typographic. `omitted` is the strongest value here
                           * precisely because it is the one that bites. */}
                          <Severity
                            level={additivity === 'omitted' ? 'error' : 'warning'}
                            className="w-16 text-footnote"
                            title={
                              additivity === 'omitted'
                                ? 'non-additive — the response omits its grand total'
                                : additivity === 'derived'
                                  ? 'totalled as sum/count over all rows'
                                  : 'summable across groups'
                            }
                          >
                            {additivity}
                          </Severity>
                          <button
                            onClick={() => removeMeasure(m.id)}
                            aria-label={`Remove ${m.alias}`}
                            data-testid="measure-remove"
                            className="text-footnote text-muted-foreground hover:text-foreground"
                          >
                            ✕
                          </button>
                        </div>
                      );
                    })}
                    {spec.measures.length === 0 && (
                      <Footnote className="px-2 py-2">
                        No measure yet — add one from the column list. A group-by with no measure
                        counts nothing.
                      </Footnote>
                    )}
                  </div>
                </div>

                {/* sort + limit. There is no offset and no cursor on this
                 * endpoint, so `limit` is the only page control that exists. */}
                <div className="mt-2 flex items-center gap-2">
                  <span className="w-14 shrink-0 text-small text-muted-foreground">Sort</span>
                  <select
                    value={spec.sortBy ?? ''}
                    onChange={(e) => update((s) => ({ ...s, sortBy: e.target.value || null }))}
                    className={cn(fieldClass, 'w-44 font-mono')}
                    aria-label="Sort by"
                    data-testid="aggregate-sort"
                  >
                    {spec.groupBy.map((g) => (
                      <option key={g.alias} value={g.alias}>
                        {g.alias}
                      </option>
                    ))}
                    {spec.measures.map((m) => (
                      <option key={m.alias} value={m.alias}>
                        {m.alias}
                      </option>
                    ))}
                  </select>
                  <select
                    value={spec.sortOrder}
                    onChange={(e) =>
                      update((s) => ({ ...s, sortOrder: e.target.value as 'asc' | 'desc' }))
                    }
                    className={cn(fieldClass, 'w-20 font-mono')}
                    aria-label="Sort order"
                    data-testid="aggregate-order"
                  >
                    <option value="desc">desc</option>
                    <option value="asc">asc</option>
                  </select>
                  <span className="ml-2 text-small text-muted-foreground">Limit</span>
                  <select
                    value={spec.limit}
                    onChange={(e) => update((s) => ({ ...s, limit: Number(e.target.value) }))}
                    className={cn(fieldClass, 'w-20 font-mono')}
                    aria-label="Limit"
                    data-testid="aggregate-limit"
                  >
                    {LIMITS.map((l) => (
                      <option key={l} value={l}>
                        {l}
                      </option>
                    ))}
                  </select>
                  <Footnote className="ml-auto">
                    the aggregate endpoint takes a limit and no offset — there is no next page to
                    ask for, only a wider one
                  </Footnote>
                </div>
              </div>

              {/* ------------------------------------------------- result */}
              <div className="flex min-h-0 flex-1 flex-col border-t border-border">
                {spec.groupBy.length === 0 && (
                  <NoDimensionPanel
                    candidates={candidates}
                    offers={offers}
                    binCount={binCount}
                    onBinCount={setBinCount}
                    dateUnit={dateUnit}
                    onDateUnit={setDateUnit}
                    onApply={applyOffer}
                  />
                )}

                {spec.groupBy.length > 0 && spec.measures.length === 0 && (
                  <Footnote className="px-3 py-3">
                    A group-by with no measure counts nothing — add one from the column list.
                  </Footnote>
                )}

                {query.isLoading && <Footnote className="px-3 py-2">Aggregating…</Footnote>}

                {query.error && !restricted && (
                  <div className="p-3">
                    <LensError>{errorText(query.error)}</LensError>
                  </div>
                )}

                {view && view.originalCount === 0 && (
                  <div className="px-3 py-4" data-testid="aggregate-empty">
                    <p className="text-body text-foreground">0 rows — this version is empty.</p>
                    <Footnote className="mt-1">
                      No statistic is printed from a zero population: there is nothing to divide by,
                      so nothing is divided.
                    </Footnote>
                  </div>
                )}

                {view && view.originalCount > 0 && view.groups.length === 0 && (
                  <div className="px-3 py-4" data-testid="aggregate-no-groups">
                    <p className="text-body text-foreground">
                      No groups — every row fell outside this grouping.
                    </p>
                  </div>
                )}

                {view && view.groups.length > 0 && spec.measures.length > 0 && (
                  <ResultTable view={view} spec={spec} groupKeyHeader={groupKeyHeader} />
                )}
              </div>
            </>
          )}
        </main>

        {/* --------------------------------------------------- offers + fold */}
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-border px-3 py-3">
          <Section title="Offers">
            <OfferList
              offers={offers}
              onApply={applyOffer}
              disabled={{
                line: candidates.temporal.length === 0,
                bar: candidates.dimensions.length === 0,
                distribution: candidates.measures.length === 0,
                correlation: candidates.measures.length < 2,
                ratio: true,
              }}
            />
          </Section>

          {view && sortMeasure && (
            <Section title={`${sortMeasure.alias} by group`}>
              <RankedFold view={view} measure={sortMeasure} />
            </Section>
          )}

          {view && (
            <Section title="Totals · additivity">
              <div className="rounded bg-[var(--s3)]/70">
                {view.totals.map((t) => (
                  <div
                    key={t.measure.alias}
                    className="flex items-center gap-2 px-2 py-1"
                    data-testid="additivity-row"
                  >
                    <Identifier className="text-footnote">{t.measure.fn}</Identifier>
                    <Identifier className="min-w-0 flex-1 truncate text-footnote text-muted-foreground">
                      {t.measure.alias}
                    </Identifier>
                    <Severity
                      level={t.omittedReason ? 'error' : 'warning'}
                      className="text-footnote"
                    >
                      {t.omittedReason ? 'omitted' : additivityOf(t.measure.fn)}
                    </Severity>
                  </div>
                ))}
              </div>
              <Footnote className="mt-2 border-l border-border pl-2 leading-relaxed">
                median, std, nunique, first and last cannot be combined from group results —
                summing them would print a number that only looks like a total, so the response
                names them in <span className="font-mono">totals_omitted</span> and the footer
                renders <span className="font-mono">—</span> instead.
              </Footnote>
            </Section>
          )}
        </aside>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

/**
 * R6 — the zero-dimension screen. This is what the reference got wrong: it
 * opened an empty builder on an all-numeric table and offered no way out. Every
 * option here carries its parameters on screen before it is accepted.
 */
function NoDimensionPanel({
  candidates,
  offers,
  binCount,
  onBinCount,
  dateUnit,
  onDateUnit,
  onApply,
}: {
  candidates: ReturnType<typeof chartCandidates>;
  offers: ReturnType<typeof chartOffers>;
  binCount: number;
  onBinCount: (n: number) => void;
  dateUnit: DateUnit;
  onDateUnit: (u: DateUnit) => void;
  onApply: (kind: ChartKind) => void;
}) {
  // The same panel answers two different questions, and they must not be
  // confused: "this data has no dimension" is a fact about the table, while
  // "you removed the dimension" is a fact about the spec.
  const none = candidates.dimensions.length === 0;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3" data-testid="no-dimension">
      <SectionTitle>
        {none ? 'No column qualifies as a dimension' : 'No dimension chosen'}
      </SectionTitle>
      <Footnote className="mt-1 max-w-prose leading-relaxed">
        {none ? (
          <>
            A dimension needs a known, low distinct count. Every candidate here is numeric, too
            distinct, or has never been profiled. That is not "nothing to show" — it is a different
            set of questions, and each one below is an offer with its parameters visible. Nothing
            is binned on your behalf.
          </>
        ) : (
          <>
            An aggregate needs something to group by. Pick a dimension in the shelf above, or take
            one of the offers below — each carries its parameters, so nothing is bucketed silently.
          </>
        )}
      </Footnote>

      <div className="mt-3 flex items-center gap-2">
        <span className="text-small text-muted-foreground">Bins</span>
        <input
          type="number"
          min={2}
          max={100}
          value={binCount}
          onChange={(e) => onBinCount(Math.max(2, Math.min(100, Number(e.target.value) || 2)))}
          className={cn(fieldClass, 'w-20 font-mono')}
          aria-label="Bin count"
          data-testid="offer-bin-count"
        />
        <span className="ml-3 text-small text-muted-foreground">Date unit</span>
        <select
          value={dateUnit}
          onChange={(e) => onDateUnit(e.target.value as DateUnit)}
          className={cn(fieldClass, 'w-24 font-mono')}
          aria-label="Date unit"
          data-testid="offer-date-unit"
        >
          {DATE_UNITS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <Footnote className="ml-2">
          equal <span className="italic">width</span>, not equal frequency — that would be quantiles
        </Footnote>
      </div>

      <div className="mt-3 max-w-xl">
        <OfferList
          offers={offers}
          onApply={onApply}
          disabled={{
            line: candidates.temporal.length === 0,
            bar: candidates.dimensions.length === 0,
            distribution: candidates.measures.length === 0,
            correlation: candidates.measures.length < 2,
            ratio: true,
          }}
        />
      </div>
    </div>
  );
}

/** An offer states its reason when it is withdrawn — never just vanishes. */
function OfferList({
  offers,
  onApply,
  disabled,
}: {
  offers: ReturnType<typeof chartOffers>;
  onApply: (kind: ChartKind) => void;
  disabled: Record<ChartKind, boolean>;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {offers.map((o) => {
        const off = !o.available || disabled[o.kind];
        return (
          <button
            key={o.kind}
            disabled={off}
            onClick={() => onApply(o.kind)}
            data-testid={`offer-${o.kind}`}
            className={cn(
              'rounded px-2 py-1.5 text-left transition-colors',
              off ? 'cursor-not-allowed opacity-70' : 'bg-secondary hover:bg-accent',
            )}
          >
            <span className="text-small text-foreground">{OFFER_LABEL[o.kind]}</span>
            <Footnote className="mt-0.5">{off ? o.reason ?? 'not available' : OFFER_HINT[o.kind]}</Footnote>
          </button>
        );
      })}
    </div>
  );
}

/** The bucket, with its parameters where they can be read and changed. */
function BucketControl({
  column,
  numeric,
  temporal,
  bucket,
  onChange,
}: {
  column: string;
  numeric: boolean;
  temporal: boolean;
  bucket: Bucket | null;
  onChange: (b: Bucket | null) => void;
}) {
  const kind = bucket?.kind ?? 'none';
  return (
    <span className="flex items-center gap-1">
      <select
        value={kind}
        onChange={(e) => {
          const k = e.target.value;
          if (k === 'none') onChange(null);
          else if (k === 'date_trunc') onChange({ kind: 'date_trunc', unit: 'month' });
          else if (k === 'bin_width') onChange({ kind: 'bin_width', width: 100 });
          else onChange({ kind: 'bin_count', count: 10 });
        }}
        className={cn(fieldClass, 'w-24 font-mono')}
        aria-label={`Bucket for ${column}`}
        data-testid="bucket-kind"
      >
        <option value="none">no bucket</option>
        {temporal && <option value="date_trunc">date_trunc</option>}
        {numeric && <option value="bin_width">bin_width</option>}
        {numeric && <option value="bin_count">bin_count</option>}
      </select>

      {bucket?.kind === 'date_trunc' && (
        <select
          value={bucket.unit}
          onChange={(e) => onChange({ kind: 'date_trunc', unit: e.target.value as DateUnit })}
          className={cn(fieldClass, 'w-20 font-mono')}
          aria-label="Date unit"
          data-testid="bucket-unit"
        >
          {DATE_UNITS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      )}
      {bucket?.kind === 'bin_width' && (
        <input
          type="number"
          min={0.0001}
          value={bucket.width}
          onChange={(e) =>
            onChange({ kind: 'bin_width', width: Math.max(0.0001, Number(e.target.value) || 1) })
          }
          className={cn(fieldClass, 'w-20 font-mono')}
          aria-label="Bin width"
          data-testid="bucket-width"
        />
      )}
      {bucket?.kind === 'bin_count' && (
        <input
          type="number"
          min={1}
          max={200}
          value={bucket.count}
          onChange={(e) =>
            onChange({
              kind: 'bin_count',
              count: Math.max(1, Math.min(200, Number(e.target.value) || 1)),
            })
          }
          className={cn(fieldClass, 'w-20 font-mono')}
          aria-label="Bin count"
          data-testid="bucket-count"
        />
      )}
    </span>
  );
}

/**
 * The result. Every cell is a `<Stat>` — the alias is hidden per row because
 * the header already carries it, but the coverage note is not, so a partially
 * covered figure states its share in place, in every row it occurs.
 */
function ResultTable({
  view,
  spec,
  groupKeyHeader,
}: {
  view: NonNullable<ReturnType<typeof useAggregate>['view']>;
  spec: AggregateSpec;
  groupKeyHeader: string;
}) {
  const CELL_STAT = 'justify-end gap-1.5 [&_[data-slot=identifier]]:hidden';

  return (
    <Table containerClassName="min-h-0">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[240px]">
            <Identifier className="text-micro text-foreground">{groupKeyHeader}</Identifier>
            <Footnote>
              group key
              {spec.groupBy.some((g) => g.bucket)
                ? ` · ${spec.groupBy
                    .filter((g) => g.bucket)
                    .map((g) =>
                      g.bucket?.kind === 'date_trunc'
                        ? `date_trunc ${g.bucket.unit}`
                        : g.bucket?.kind === 'bin_width'
                          ? `bin_width ${g.bucket.width}`
                          : `bin_count ${g.bucket?.count}`,
                    )
                    .join(' · ')}`
                : ''}
            </Footnote>
          </TableHead>
          {spec.measures.map((m) => (
            <TableHead key={m.alias} className="text-right">
              <Identifier className="text-micro text-foreground">
                {m.alias}
                {additivityOf(m.fn) === 'omitted' ? '‡' : ''}
              </Identifier>
              <Footnote>
                {m.fn}({m.column})
              </Footnote>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>

      <TableBody>
        {view.groups.map((g, i) => (
          <TableRow key={`${g.label}-${i}`} data-testid="result-row">
            <TableCell className="align-top">
              <div className="flex items-baseline gap-2">
                <span className="text-footnote text-muted-foreground tabular-nums">{i + 1}</span>
                <span className="min-w-0 truncate text-body text-foreground">{g.label}</span>
              </div>
              <Footnote className="tabular-nums">
                {g.population.toLocaleString()} rows{view.basis?.exact ? '' : ' or more'}
              </Footnote>
            </TableCell>
            {g.cells.map((c) => (
              <TableCell key={c.measure.alias} className="align-top" data-testid="result-cell">
                {/* R11 — the denominator is not optional and not decorative:
                 * `coverage` is a required, branded prop. */}
                {/* An unrepresentable figure is NOT an em dash. A blank cell
                  * reads as "no rows matched"; this one means the number is
                  * real and has no finite double. Saying which is the whole
                  * point — the two lead a reader to opposite conclusions. */}
                <Stat
                  name={c.measure.alias}
                  value={c.unrepresentable ? 'no finite value' : num(c.value)}
                  coverage={c.coverage}
                  className={CELL_STAT}
                  data-unrepresentable={c.unrepresentable ? '' : undefined}
                />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>

      <TableFooter>
        <TableRow data-testid="totals-row">
          <TableCell className="align-top">
            <span className="text-body font-medium text-foreground">Totals</span>
            <Footnote className="tabular-nums">
              over all {view.originalCount.toLocaleString()} rows — not just this page
            </Footnote>
          </TableCell>
          {view.totals.map((t) => (
            <TableCell key={t.measure.alias} className="align-top text-right">
              {t.omittedReason ? (
                <>
                  <Severity level="error" className="text-body">
                    —‡
                  </Severity>
                  <Footnote>{t.omittedReason}</Footnote>
                </>
              ) : t.coverage === null || t.value === null ? (
                <>
                  <span className="text-body text-muted-foreground">—</span>
                  <Footnote>no denominator returned</Footnote>
                </>
              ) : (
                <Stat
                  name={t.measure.alias}
                  value={num(t.value)}
                  coverage={t.coverage}
                  className="justify-end gap-1.5 [&_[data-slot=identifier]]:hidden"
                />
              )}
            </TableCell>
          ))}
        </TableRow>
      </TableFooter>
    </Table>
  );
}

/**
 * R7 — rank, keep eight, fold the tail.
 *
 * Zero categorical colour is spent here: this is magnitude, so it runs on the
 * neutral ramp. `vizSlot` is asked for the beyond-the-palette token rather than
 * hard-coding one, which is the same thing that stops the palette being cycled.
 */
function RankedFold({
  view,
  measure,
}: {
  view: NonNullable<ReturnType<typeof useAggregate>['view']>;
  measure: Measure;
}) {
  const points = view.groups.map((g) => {
    const cell = g.cells.find((c) => c.measure.alias === measure.alias);
    return { label: g.label, value: numberOf(cell?.value) ?? 0, coverage: cell?.coverage ?? null };
  });

  // R9 — no population, no mark. Ranking nothing would make `Math.max` return
  // -Infinity and every width NaN, which is exactly what the reference did.
  if (points.length === 0) {
    return <Footnote>No groups on this page — nothing to rank.</Footnote>;
  }

  const total = points.reduce((s, p) => s + p.value, 0);
  const folded = foldTopN(points, (p) => p.value, VIZ_SLOTS);
  const max = Math.max(...folded.head.map((p) => p.value), folded.other?.value ?? 0);

  // R9 — a zero maximum is not a row of zero-length bars, it is a state.
  if (max <= 0) {
    return (
      <Footnote>
        every value of <span className="font-mono">{measure.alias}</span> on this page is zero or
        below — nothing to rank.
      </Footnote>
    );
  }

  // R7 — below three groups a bar chart invites a length comparison where the
  // only fact is a proportion. Draw the proportion.
  if (isRatioShaped(points.length)) {
    const top = folded.head[0];
    const share = coverage(top.value, total);
    const r = ratio(share);
    return (
      <div data-testid="ratio-mark">
        <Eyebrow>{top.label}</Eyebrow>
        <Figure className="mt-1">{r === null ? '—' : `${(r * 100).toFixed(1)}%`}</Figure>
        <MagnitudeBar of={share} className="mt-1.5" />
        <Footnote className="mt-1">
          {points.length} group{points.length === 1 ? '' : 's'} — a ratio, not a distribution.
        </Footnote>
        <StatList coverage={share} className="mt-2">
          {points.map((p) =>
            p.coverage ? (
              <Stat key={p.label} name={p.label} value={num(p.value)} coverage={p.coverage} />
            ) : null,
          )}
        </StatList>
      </div>
    );
  }

  return (
    <div data-testid="ranked-fold">
      {folded.head.map((p) => (
        <div key={p.label} className="mb-1.5" data-testid="fold-bar">
          <div className="flex items-baseline justify-between gap-2">
            <span className="min-w-0 truncate text-footnote text-muted-foreground">{p.label}</span>
            <span className="shrink-0 text-footnote tabular-nums">{compact(p.value)}</span>
          </div>
          <MagnitudeBar of={coverage(p.value, max)} className="mt-0.5" />
        </div>
      ))}

      {folded.other && (
        <div className="mb-1.5" data-testid="fold-other">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-footnote text-muted-foreground">
              Other ×{folded.other.count}
            </span>
            <span className="shrink-0 text-footnote tabular-nums">
              {compact(folded.other.value)}
            </span>
          </div>
          {/* Past the eighth slot the palette has no colour, and inventing one
           * by cycling would put two identities in the same hue. */}
          <MagnitudeBar
            of={coverage(folded.other.value, max)}
            color={vizSlot(VIZ_SLOTS)}
            className="mt-0.5"
          />
          <Footnote className="mt-0.5">
            {folded.other.count} groups folded · {(folded.other.share * 100).toFixed(1)}% of this
            page — folded, never dropped.
          </Footnote>
        </div>
      )}

      {!folded.other && (
        <Footnote className="mt-1">
          {folded.head.length} groups — all of them drawn, none folded.
        </Footnote>
      )}
    </div>
  );
}
