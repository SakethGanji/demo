/**
 * The two §16 quality explorers, compressed into the 384px dock.
 *
 * The health lens above these can say `duplicates: warning` and `missing_data:
 * warning`. Neither statement is actionable on its own, and until now the
 * studio had no way to ask the obvious follow-up — WHICH rows, and WHICH
 * columns. Both endpoints have existed in the service the whole time.
 *
 * What governs the rendering:
 *
 *  - **R11.** Every figure here is a count over a population, and the two
 *    populations differ constantly: `group_count` is over the GROUPS the server
 *    found (which is why the cap is visible), `duplicate_rows` is over the rows
 *    of the sheet, `null_count` is over rows, and a probe row's `null_count` is
 *    over COLUMNS. `<Stat>` makes each one name its own denominator, and the
 *    unit argument is what stops "4 of 9 columns null" being printed as rows.
 *  - **R9.** An empty sheet is a state, not a zero. Every percentage on this
 *    surface comes out of `ratio()`, which returns `null` rather than `NaN`,
 *    and a null share prints an em dash. `null_percent` arrives on the wire but
 *    is only shown when its denominator is non-zero — the profile computes it
 *    as `0.0` for a zero-row sheet, which is a number the reader would believe.
 *  - **R7.** A missing-data report returns EVERY column. Nine bars is a chart;
 *    241 bars is a scroll with no structure and a palette that has wrapped four
 *    times. So above eight the tail is folded into one graphite row that
 *    carries its own column count and its own share of the nulls.
 *  - **R10.** Group keys and example rows hold arbitrary uploaded values, so
 *    every one of them is middle-truncated and every row is a fixed height.
 *
 * MASKING. Neither endpoint refuses a low-privilege seat — it masks. A group
 * key on a sensitive column comes back as a stable pseudonym (a digest), so
 * distinct groups stay distinguishable without the value ever crossing the
 * wire. Rendering that pseudonym as ordinary text would invite a reader to
 * treat it as the value, so it goes in the recessed well `CellValue` already
 * uses for masked cells, and `masked_columns` is stated in a guard.
 *
 * This surface REPORTS. There is no remediation here and no button that
 * pretends otherwise: de-duplication is a transform step, and it produces a new
 * version rather than editing this one.
 */

import { useState } from 'react';
import { errorText } from '@/shared/lib/analyticsClient';
import { compact, num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { Footnote, Identifier } from '@/shared/components/instrument/Typography';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat } from '@/shared/components/instrument/Stat';
import { Status } from '@/shared/components/instrument/Status';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { complete, coverage, ratio } from '@/shared/components/instrument/coverage';
import { foldTopN, VIZ_SLOTS, vizSlot } from '@/shared/components/instrument/series';
import { middleTruncate } from '@/shared/components/instrument/shape';
import {
  useDuplicates,
  useMissing,
  DUPLICATE_GROUP_LIMIT,
  type DuplicateGroup,
} from '../../hooks/useQualityExplorers';
import { CellValue } from '../CellValue';
import { fieldClass } from '../fieldStyles';
import { LensEmpty, LensError, LensLoading, Section } from './primitives';

/* ------------------------------------------------------------- formatting */

/**
 * A share as text, or an em dash when there was no population.
 *
 * Never takes a pre-computed percentage — that is the shape that lets a 0/0
 * arrive already broken (R9). The caller passes `ratio(coverage(...))`, which
 * is `null` exactly when nothing may be printed.
 */
function pctText(share: number | null): string {
  if (share === null) return '—';
  const v = share * 100;
  if (v === 0) return '0%';
  if (v > 0 && v < 0.1) return '<0.1%';
  // Two decimals below 10 (1.15% is a real distinction), one above; trailing
  // zeros dropped so `0.40%` and `100.0%` do not read as false precision.
  return `${Number(v < 10 ? v.toFixed(2) : v.toFixed(1))}%`;
}

/** A cell value as one short line. NULL is named, never blank. */
function valueText(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  return middleTruncate(String(v), 18);
}

/* ------------------------------------------------------------ duplicates */

/**
 * One group's key.
 *
 * Rendered as `column value` pairs rather than a bare tuple, because on an
 * exact (all-column) grouping the key IS the whole row and a naked list of
 * values in a 336px column is unreadable. Three pairs, then the count of what
 * is not shown — the same discipline as the evidence line in the health list.
 */
function GroupKey({
  entries,
  masked,
}: {
  entries: [string, unknown][];
  masked: ReadonlySet<string>;
}) {
  const shown = entries.slice(0, 3);
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      {shown.map(([column, value]) => (
        <span key={column} className="inline-flex items-baseline gap-1">
          <Identifier className="text-footnote text-muted-foreground">{column}</Identifier>
          {masked.has(column) ? (
            // A pseudonym, not a value. The well says so materially; the guard
            // below the list says so in words.
            <CellValue value={value} categorical={false} masked />
          ) : (
            <Identifier
              className={cn(
                'text-small',
                value === null || value === undefined
                  ? 'text-muted-foreground/70'
                  : 'text-foreground',
              )}
            >
              {valueText(value)}
            </Identifier>
          )}
        </span>
      ))}
      {entries.length > shown.length && (
        <span className="text-footnote text-muted-foreground">
          +{entries.length - shown.length} more
        </span>
      )}
    </span>
  );
}

/**
 * The example rows for one group, on one fixed-height line each.
 *
 * Only the columns NOT in the group key are shown: those are the ones that
 * differ, and repeating the key on every example row would spend the whole
 * width restating what the header already said. On an exact grouping there are
 * none by construction — the rows are identical — and saying that is better
 * than rendering five visually identical lines.
 */
function ExampleRows({
  rows,
  keyColumns,
  masked,
}: {
  rows: Record<string, unknown>[];
  keyColumns: ReadonlySet<string>;
  masked: ReadonlySet<string>;
}) {
  const others = Object.keys(rows[0] ?? {}).filter((c) => !keyColumns.has(c));

  if (others.length === 0) {
    return (
      <Footnote className="mt-1">
        {rows.length} example row{rows.length === 1 ? '' : 's'} — grouped on every column, so they
        are identical to the key above.
      </Footnote>
    );
  }

  return (
    <div className="mt-1">
      <Footnote className="mb-0.5">
        example rows · {others.length} column{others.length === 1 ? '' : 's'} outside the key
      </Footnote>
      {rows.map((row, i) => (
        <div
          key={i}
          className="flex h-5 items-baseline gap-2 overflow-hidden"
          data-testid="duplicate-example"
        >
          <Footnote className="w-4 shrink-0 tabular-nums">{i + 1}</Footnote>
          <span className="flex min-w-0 flex-1 items-baseline gap-2 overflow-hidden whitespace-nowrap">
            {others.slice(0, 4).map((c) =>
              masked.has(c) ? (
                <CellValue key={c} value={row[c]} categorical={false} masked />
              ) : (
                <Identifier
                  key={c}
                  className={cn(
                    'shrink-0 text-footnote',
                    row[c] === null || row[c] === undefined
                      ? 'text-muted-foreground/70'
                      : 'text-muted-foreground',
                  )}
                  title={`${c}: ${String(row[c])}`}
                >
                  {valueText(row[c])}
                </Identifier>
              ),
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

interface DuplicatesExplorerProps {
  datasetId: string | null;
  version: number | null;
  sheet: string | null;
  columns: { name: string }[];
}

export function DuplicatesExplorer({
  datasetId,
  version,
  sheet,
  columns,
}: DuplicatesExplorerProps) {
  /** '' = every column, i.e. an EXACT duplicate. Anything else is a subset. */
  const [subset, setSubset] = useState('');
  const [openGroup, setOpenGroup] = useState<number | null>(null);

  const q = useDuplicates(datasetId, version, sheet, subset || null);
  const data = q.data;

  const groups: DuplicateGroup[] = data?.groups ?? [];
  const masked = new Set(data?.masked_columns ?? []);
  const keyColumns = new Set(data?.columns ?? []);
  const rowCount = data?.row_count ?? 0;
  const groupCount = data?.group_count ?? 0;
  const duplicateRows = data?.duplicate_rows ?? 0;
  // `groups` is ordered biggest-first by the server, so the head of the capped
  // page is still the true largest group — the cap trims the tail, not the top.
  const largest = groups.length > 0 ? groups[0].count : null;
  const dupShare = ratio(coverage(duplicateRows, rowCount));

  return (
    <Section
      title="Duplicates"
      action={<Footnote className="shrink-0">read-only</Footnote>}
    >
      <label className="text-micro text-muted-foreground" htmlFor="duplicates-subset">
        Group on
      </label>
      <select
        id="duplicates-subset"
        value={subset}
        onChange={(e) => {
          setSubset(e.target.value);
          setOpenGroup(null);
        }}
        aria-label="Duplicate grouping columns"
        className={fieldClass}
        data-testid="duplicates-subset"
      >
        <option value="">every column · exact duplicates</option>
        {columns.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>

      {q.isLoading && <LensLoading>Scanning for duplicate groups…</LensLoading>}
      {!q.isLoading && q.error && <LensError>{errorText(q.error)}</LensError>}

      {!q.isLoading && !q.error && data && (
        <div className="mt-2" data-testid="duplicates-explorer">
          <div className="flex items-baseline gap-2">
            <Status kind={groupCount > 0 ? 'warning' : 'good'} className="text-small">
              {groupCount > 0 ? 'duplicates found' : 'no duplicates'}
            </Status>
            <Identifier className="ml-auto min-w-0 shrink truncate text-footnote text-muted-foreground">
              {data.exact ? 'every column' : data.columns.join(' + ')}
            </Identifier>
          </div>

          {groupCount === 0 ? (
            <div className="mt-2">
              <LensEmpty>
                No group of {data.exact ? 'identical rows' : 'rows sharing those values'} occurs
                more than once across the {compact(rowCount)} rows of{' '}
                {data.sheet_name || 'this sheet'}.
              </LensEmpty>
            </div>
          ) : (
            <>
              <div className="mt-2 flex flex-col gap-1">
                {/* The listed groups over the groups that exist — this is where
                    the cap becomes visible instead of silently trimming. */}
                <Stat
                  name="groups"
                  value={compact(groupCount)}
                  coverage={coverage(groups.length, groupCount, 'groups')}
                  data-testid="duplicates-group-count"
                />
                <Stat
                  name="dup rows"
                  value={compact(duplicateRows)}
                  coverage={coverage(duplicateRows, rowCount)}
                  data-testid="duplicates-row-count"
                />
                {largest != null && (
                  <Stat
                    name="largest"
                    value={num(largest)}
                    coverage={coverage(largest, duplicateRows)}
                    data-testid="duplicates-largest"
                  />
                )}
              </div>

              <MagnitudeBar of={coverage(duplicateRows, rowCount)} className="mt-2.5" />
              <Footnote className="mt-1 tabular-nums">
                {pctText(dupShare)} of the sheet — {compact(duplicateRows)} of {compact(rowCount)}{' '}
                rows sit in a duplicate group. `largest` is a share of those duplicate rows, not of
                the sheet.
              </Footnote>

              <div className="mt-2 divide-y divide-[var(--r1)]">
                {groups.map((g, i) => {
                  const entries = Object.entries(g.key);
                  const examples = g.examples ?? [];
                  const open = openGroup === i;
                  return (
                    <div key={i} className="py-1.5 first:pt-0" data-testid="duplicate-group">
                      <div className="flex items-baseline gap-2">
                        <Footnote className="w-4 shrink-0 tabular-nums">
                          {String(i + 1).padStart(2, '0')}
                        </Footnote>
                        <span className="min-w-0 flex-1">
                          <GroupKey entries={entries} masked={masked} />
                        </span>
                        <span className="shrink-0 text-body font-medium tabular-nums text-foreground">
                          {num(g.count)}
                        </span>
                      </div>

                      {examples.length > 0 && (
                        <button
                          type="button"
                          aria-expanded={open}
                          onClick={() => setOpenGroup(open ? null : i)}
                          data-testid="duplicate-examples-toggle"
                          className={cn(
                            'mt-0.5 ml-6 rounded px-1.5 py-px font-mono text-footnote',
                            open
                              ? 'bg-secondary text-foreground shadow-[var(--hi)]'
                              : 'text-muted-foreground hover:bg-muted',
                          )}
                        >
                          {open ? 'hide' : `${examples.length} example rows`}
                        </button>
                      )}

                      {open && (
                        <div className="ml-6">
                          <ExampleRows
                            rows={examples}
                            keyColumns={keyColumns}
                            masked={masked}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {data.truncated && (
                <div className="mt-2">
                  <Guard>
                    The {compact(groups.length)} largest groups are listed; {compact(groupCount)}{' '}
                    exist. The counts above are totals over every group, so nothing is understated
                    — only the list is capped at {DUPLICATE_GROUP_LIMIT}.
                  </Guard>
                </div>
              )}

              {masked.size > 0 && (
                <div className="mt-2">
                  <Guard>
                    {masked.size} column{masked.size === 1 ? ' is' : 's are'} masked for this seat (
                    {[...masked].join(', ')}). Its key value is a stable pseudonym, not the value —
                    equal rows still group together, and nothing here can be read back.
                  </Guard>
                </div>
              )}

              <div className="mt-2">
                <Guard>
                  Read-only. There is no remediation on this surface: de-duplicating is a transform
                  step, and it writes a new version rather than editing the one you are looking at.
                </Guard>
              </div>
            </>
          )}
        </div>
      )}
    </Section>
  );
}

/* --------------------------------------------------------- missing data */

interface MissingExplorerProps {
  datasetId: string | null;
  version: number | null;
  sheet: string | null;
}

export function MissingExplorer({ datasetId, version, sheet }: MissingExplorerProps) {
  const q = useMissing(datasetId, version, sheet);
  const data = q.data;

  const cols = data?.columns ?? [];
  const probe = data?.rows_most_missing ?? [];
  const masked = new Set(data?.masked_columns ?? []);
  const rowCount = data?.row_count ?? 0;
  const withNulls = cols.filter((c) => c.null_count > 0).length;

  // R7 — every column comes back, so rank and fold rather than drawing 241
  // bars against an 8-slot palette.
  const folded = foldTopN(cols, (c) => c.null_count);
  const worst = folded.head[0] ?? null;

  return (
    <Section
      title="Missing data"
      action={<Footnote className="shrink-0">worst first</Footnote>}
    >
      {q.isLoading && <LensLoading>Reading null rates…</LensLoading>}
      {!q.isLoading && q.error && <LensError>{errorText(q.error)}</LensError>}

      {!q.isLoading && !q.error && data && (
        <div data-testid="missing-explorer">
          <Footnote className="tabular-nums">
            {data.source === 'profile_run' ? (
              <>
                from profile run{' '}
                <Identifier>{data.profile_run_id ? data.profile_run_id.slice(0, 8) : '—'}</Identifier>
              </>
            ) : (
              'computed live — no profile run exists for this sheet'
            )}{' '}
            · {compact(rowCount)} rows · {cols.length} columns
          </Footnote>

          {cols.length === 0 ? (
            <div className="mt-2">
              <LensEmpty>The report carries no per-column statistics for this sheet.</LensEmpty>
            </div>
          ) : (
            <>
              <div className="mt-2 flex flex-col gap-1">
                {/* Counting COLUMNS here, not rows — the unit is the whole point
                    of R11's third argument. */}
                <Stat
                  name="affected"
                  value={num(withNulls)}
                  coverage={coverage(withNulls, cols.length, 'columns')}
                  data-testid="missing-affected"
                />
                {worst && (
                  <Stat
                    name="worst"
                    value={compact(worst.null_count)}
                    coverage={coverage(worst.null_count, rowCount)}
                    data-testid="missing-worst"
                  />
                )}
                <Stat name="rows" value={compact(rowCount)} coverage={complete(rowCount)} />
              </div>
              {/* A sheet with no nulls has no worst column. Naming one anyway
                * implies an offender exists — the same failure the duplicates
                * explorer avoids with its own zero-state sentence. */}
              {worst && worst.null_count > 0 ? (
                <Footnote className="mt-1">
                  worst column <Identifier className="text-foreground">{worst.column}</Identifier>
                </Footnote>
              ) : (
                <Footnote className="mt-1">
                  No missing values in any of the {cols.length} columns across{' '}
                  {compact(rowCount)} rows.
                </Footnote>
              )}

              <div className="mt-2.5 divide-y divide-[var(--r1)]">
                {folded.head.map((c, i) => {
                  const cov = coverage(c.null_count, rowCount);
                  const share = ratio(cov);
                  return (
                    <div key={c.column} className="py-1 first:pt-0" data-testid="missing-column">
                      <div className="flex h-4 items-baseline gap-2">
                        <Identifier
                          className={cn(
                            'min-w-0 flex-1 truncate text-small',
                            c.null_count > 0 ? 'text-foreground' : 'text-muted-foreground',
                          )}
                          title={c.column}
                        >
                          {middleTruncate(c.column, 22)}
                          {masked.has(c.column) && (
                            <span className="ml-1 text-footnote text-muted-foreground">masked</span>
                          )}
                        </Identifier>
                        <span className="shrink-0 text-small tabular-nums text-muted-foreground">
                          {/* `null_percent` is on the wire, but only printed
                              where the denominator exists — R9. */}
                          {compact(c.null_count)} ·{' '}
                          {share === null ? '—' : pctText(c.null_percent / 100)}
                        </span>
                      </div>
                      <MagnitudeBar of={cov} color={vizSlot(i)} className="mt-1" />
                    </div>
                  );
                })}

                {/* R7 — the tail, carrying its own count and its own share. */}
                {folded.other && (
                  <div className="py-1" data-testid="missing-column-other">
                    <div className="flex h-4 items-baseline gap-2">
                      <span
                        aria-hidden="true"
                        className="size-2 shrink-0 rounded-[2px]"
                        style={{ background: vizSlot(VIZ_SLOTS) }}
                      />
                      <span className="min-w-0 flex-1 truncate text-small text-muted-foreground">
                        {folded.other.count} other column
                        {folded.other.count === 1 ? '' : 's'}
                      </span>
                      <span className="shrink-0 text-small tabular-nums text-muted-foreground">
                        {compact(folded.other.value)} · {pctText(folded.other.share)}
                      </span>
                    </div>
                    <Footnote className="mt-0.5">
                      folded, not dropped — that share is of all missing values, not of the sheet
                    </Footnote>
                  </div>
                )}
              </div>

              {probe.length > 0 && (
                <div className="mt-3">
                  <Footnote className="mb-1">
                    Rows most missing — ranked by how many of the {cols.length} columns are null.
                  </Footnote>
                  <div className="flex flex-col gap-1">
                    {probe.map((r, i) => {
                      const nullColumns = Object.entries(r.row)
                        .filter(([, v]) => v === null || v === undefined)
                        .map(([c]) => c);
                      return (
                        <div key={i} data-testid="missing-row">
                          <Stat
                            name={`row ${i + 1}`}
                            value={num(r.null_count)}
                            coverage={coverage(r.null_count, cols.length, 'columns')}
                          />
                          {nullColumns.length > 0 && (
                            <Footnote className="h-4 truncate" title={nullColumns.join(', ')}>
                              null in {nullColumns.slice(0, 3).map((c) => middleTruncate(c, 16)).join(', ')}
                              {nullColumns.length > 3 ? ` +${nullColumns.length - 3}` : ''}
                            </Footnote>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="mt-1.5">
                    <Guard>
                      Which columns are null, never the values that are present — the probe carries
                      whole rows and there is no reason to spend the dock reprinting them.
                    </Guard>
                  </div>
                </div>
              )}

              {data.source === 'profile_run' && (
                <div className="mt-2">
                  <Guard>
                    These null rates come from a stored profile of this version, so they describe
                    the sheet as profiled. The rows-most-missing probe above is always live.
                  </Guard>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </Section>
  );
}
