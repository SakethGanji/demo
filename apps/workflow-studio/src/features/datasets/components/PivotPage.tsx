/**
 * The cross-tab and the SQL console: the two surfaces where a figure on screen
 * is never a stored value.
 *
 * Every cell of a pivot is an AGGREGATE, which is why this screen is written
 * around three refusals to guess:
 *
 *   - SHAPE-R11 — a figure carries the population it was computed over. The
 *     summary uses `<Stat coverage={…}>`, where the denominator is a required
 *     prop, and the folded "Other" column stays empty for `mean`/`nunique`
 *     rather than adding cells that cannot be added.
 *   - SHAPE-R7 — the COLUMN shelf has a member limit. The reference pivot had
 *     none, and its row labels were not sticky, so a 500-member dimension was
 *     64,000px of horizontal scroll with nothing on the left to say which row
 *     you were reading. Here the row-label columns are sticky and anything past
 *     eight members folds into a graphite "Other" with its own count and share.
 *   - SHAPE-R9 — zero rows is a state. Every share goes through `ratio()`,
 *     which returns null on an empty population instead of `NaN%`.
 *
 * Rule 3's heterogeneous-table exception is spent deliberately and exactly
 * twice: one vertical rule splitting the row dimensions (definition) from the
 * value grid (outcome), and one leading into the totals column. There is no
 * per-row hairline. The totals band is separated by ELEVATION, never by heat,
 * so it cannot be mistaken for a data cell even when a data cell runs hot.
 */

import { useMemo, useState, type ReactNode } from 'react';
import CodeEditor from '@/shared/components/ui/code-editor';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableNumericCell,
  TableRow,
} from '@/shared/components/ui/table';
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTab } from '@/shared/components/ui/tabs';
import { Eyebrow, Footnote, Identifier, SectionTitle } from '@/shared/components/instrument/Typography';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { Status } from '@/shared/components/instrument/Status';
import { Severity } from '@/shared/components/instrument/Severity';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage, ratio } from '@/shared/components/instrument/coverage';
import { VIZ_SLOTS } from '@/shared/components/instrument/series';
import { chartCandidates, middleTruncate, type ShapeColumn } from '@/shared/components/instrument/shape';
import { errorText } from '@/shared/lib/analyticsClient';
import { compact, num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { useSheets, useVersions, useDatasetCatalog } from '../hooks/useDatasets';
import { isRestricted, useProfile } from '../hooks/useAnalysis';
import {
  AGG_FUNCTIONS,
  COLUMN_MEMBER_LIMIT,
  DISPLAY_MODES,
  PIVOT_RETENTION_DAYS,
  PIVOT_ROW_LIMIT,
  SERVER_PIVOT_COLUMN_CAP,
  SQL_RETENTION_DAYS,
  foldMembers,
  heatLegend,
  heatStep,
  heatStyle,
  isAdditive,
  otherCellValue,
  problemCode,
  useSqlQuery,
  usePivot,
  type AggFunction,
  type DisplayMode,
  type MemberFold,
  type PivotResponse,
  type PivotSpec,
} from '../hooks/usePivot';
import { PagerButton } from './PagerButton';
import { LensEmpty, LensError, LensRestricted } from './lenses/primitives';
import { controlClass } from './fieldStyles';

/** Fixed, because a sticky column needs a known left offset to stack against. */
const DIM_WIDTH = 132;
const PAGE_SIZE = 25;

/* ------------------------------------------------------------- small parts */

/** A shelf row of the builder: a label, its chips, and its own guard line. */
function Shelf({
  label,
  hint,
  children,
  testid,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  testid?: string;
}) {
  return (
    <div className="flex min-h-6 items-center gap-2" data-testid={testid}>
      <span className="w-14 shrink-0 text-micro text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">{children}</div>
      {hint ? <span className="ml-auto shrink-0 text-footnote text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

/**
 * A dimension / measure chip. Elevation, not a border — and the accent appears
 * on exactly one of these (the pivot dimension), because it is the scope the
 * whole cross-tab is built on.
 */
function Chip({
  children,
  onRemove,
  scoped,
  testid,
}: {
  children: ReactNode;
  onRemove?: () => void;
  scoped?: boolean;
  testid?: string;
}) {
  return (
    <span
      data-testid={testid}
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded-md px-2 font-mono text-micro whitespace-nowrap',
        scoped ? 'bg-[var(--s4)] text-foreground' : 'bg-[var(--s3)] text-muted-foreground',
      )}
    >
      {scoped ? (
        <span
          aria-hidden="true"
          className="size-[5px] shrink-0 rounded-full bg-[var(--sig)] shadow-[0_0_8px_-1px_var(--sig)]"
        />
      ) : null}
      {children}
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove"
          className="cursor-pointer text-muted-foreground hover:text-foreground"
        >
          ✕
        </button>
      ) : null}
    </span>
  );
}

/** The primary action wins on VALUE, not on the accent (INSTRUMENT rule 2). */
function RunButton({
  onClick,
  disabled,
  busy,
  testid,
}: {
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  testid: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testid}
      className="inline-flex h-6 shrink-0 cursor-pointer items-center gap-1.5 rounded-md bg-[var(--key)] px-2.5 text-micro font-semibold text-[#0B0E13] shadow-[inset_0_1px_0_rgba(255,255,255,.5)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {busy ? 'Running…' : 'Run'}
    </button>
  );
}

/** A toggle whose active state is bought with value + elevation, never the accent. */
function Toggle({
  on,
  onClick,
  children,
  testid,
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
  testid?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      className={cn(
        'h-5 cursor-pointer rounded px-1.5 font-mono text-footnote whitespace-nowrap transition-colors',
        on
          ? 'bg-[var(--s4)] font-semibold text-foreground'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

/** A refusal, an unmet precondition or a cap — consequential, so it gets a surface. */
function GuardBlock({
  title,
  children,
  code,
  testid,
}: {
  title: string;
  children: ReactNode;
  code?: string | null;
  testid?: string;
}) {
  return (
    <div className="rounded-md bg-muted/40 px-3 py-2.5" data-testid={testid}>
      <div className="flex items-baseline gap-2">
        <Severity level="error">{title}</Severity>
        {code ? (
          <Identifier className="text-footnote text-muted-foreground">{code}</Identifier>
        ) : null}
      </div>
      <div className="mt-1 text-small leading-snug text-muted-foreground">{children}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- the grid */

function MemberLegend({ fold, additive }: { fold: MemberFold; additive: boolean }) {
  if (!fold.other) return null;
  const other = fold.other;
  // The denominator the fold's share is a share OF: the ranked measure across
  // every member, drawn and folded. Named here rather than reconstructed from
  // the share itself, so the zero case is a null ratio and never a divide.
  const rankTotal =
    fold.head.reduce((s, m) => s + Math.abs(m.total ?? 0), 0) + Math.abs(other.value);
  const otherCoverage = coverage(Math.abs(other.value), rankTotal);
  const share = additive ? ratio(otherCoverage) : null;
  return (
    <div className="rounded-md bg-[var(--s2)] px-3 py-2" data-testid="pivot-fold-legend">
      <div className="flex items-baseline gap-2">
        <SectionTitle>Folded to the top {VIZ_SLOTS} members</SectionTitle>
        <Footnote>
          {fold.memberCount} members · {fold.other.count} folded into Other
        </Footnote>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        {fold.head.map((m) => (
          <span key={m.label} className="inline-flex items-center gap-1.5" title={m.label}>
            <span aria-hidden="true" className="size-[7px] shrink-0 rounded-[1px]" style={{ background: m.color }} />
            <Identifier className="text-micro text-muted-foreground">
              {middleTruncate(m.label, 16)}
            </Identifier>
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="size-[7px] shrink-0 rounded-[1px]"
            style={{ background: fold.other.color }}
          />
          <Identifier className="text-micro text-foreground">Other</Identifier>
        </span>
      </div>
      <div className="mt-2 max-w-64">
        {share === null ? (
          <Footnote>
            {additive
              ? 'Share unavailable — the folded members reported no total, so no percentage is printed.'
              : 'No share: a share of a non-additive aggregate is not a quantity, so the count is stated and the share is withheld.'}
          </Footnote>
        ) : (
          <>
            <MagnitudeBar of={otherCoverage} />
            <Footnote className="mt-1">
              Other carries {(share * 100).toFixed(1)}% of the ranked measure across {other.count} members.
            </Footnote>
          </>
        )}
      </div>
    </div>
  );
}

function PivotGrid({
  res,
  spec,
  fold,
  additive,
  page,
}: {
  res: PivotResponse;
  spec: PivotSpec;
  fold: MemberFold;
  additive: boolean;
  page: number;
}) {
  const alias = spec.values[0].alias;
  const rows = useMemo(() => res.data ?? [], [res.data]);
  const visible = rows.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const rowTotalKey = `total_${alias}`;
  const grand = res.totals?.[alias];
  const grandNum = typeof grand === 'number' ? grand : null;

  // The ramp is scaled over the WHOLE result, not the visible window — a heat
  // map whose scale changes when you page is a heat map that means nothing.
  const heatMax = useMemo(() => {
    let max = 0;
    for (const row of rows) {
      for (const m of fold.head) {
        const v = row[m.cell];
        if (typeof v === 'number') max = Math.max(max, Math.abs(v));
      }
      const other = otherCellValue(row, fold, additive);
      if (other !== null) max = Math.max(max, Math.abs(other));
    }
    return max;
  }, [rows, fold, additive]);

  const columnTotal = (cell: string): number | null => {
    const v = res.column_totals?.[cell];
    return typeof v === 'number' ? v : null;
  };

  const otherColumnTotal = (): number | null => {
    if (!fold.other || !additive) return null;
    let sum = 0;
    let seen = false;
    for (const m of fold.other.members) {
      const v = columnTotal(m.cell);
      if (v !== null) {
        sum += v;
        seen = true;
      }
    }
    return seen ? sum : null;
  };

  const totalsSpan = spec.rows.length;
  const headWidth = spec.rows.length * DIM_WIDTH;

  return (
    <Table
      containerClassName="min-h-0 flex-1 overflow-auto"
      className="text-small"
      data-testid="pivot-grid"
    >
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {spec.rows.map((dim, i) => (
            <th
              key={dim}
              rowSpan={2}
              scope="col"
              style={{ width: DIM_WIDTH, minWidth: DIM_WIDTH, left: i * DIM_WIDTH }}
              className={cn(
                'sticky top-0 z-30 bg-[var(--s2)] px-2 text-left align-bottom font-mono text-footnote font-medium whitespace-nowrap text-muted-foreground',
                i === spec.rows.length - 1 && 'border-r border-border',
              )}
            >
              {dim}
            </th>
          ))}
          <th
            colSpan={fold.head.length + (fold.other ? 1 : 0)}
            scope="colgroup"
            className="sticky top-0 z-20 h-[18px] bg-[var(--s2)] px-2 text-center font-mono text-footnote font-medium whitespace-nowrap text-muted-foreground"
          >
            {spec.columns} — pivot dimension · {fold.head.length + (fold.other ? 1 : 0)} of{' '}
            {fold.memberCount} members
          </th>
          <th
            rowSpan={2}
            scope="col"
            className="sticky top-0 z-20 border-l border-border bg-[var(--s3)] px-2 text-right align-bottom font-mono text-footnote font-medium whitespace-nowrap text-muted-foreground"
          >
            row total
          </th>
        </TableRow>
        <TableRow className="hover:bg-transparent">
          {fold.head.map((m) => (
            <th
              key={m.cell}
              scope="col"
              title={m.label}
              style={{ top: 18 }}
              className="sticky z-20 h-[18px] bg-[var(--s2)] px-2 pb-1 text-right font-mono text-footnote font-medium whitespace-nowrap text-muted-foreground"
            >
              <span className="inline-flex items-center gap-1">
                {middleTruncate(m.label, 14)}
                <span
                  aria-hidden="true"
                  className="size-[5px] shrink-0 rounded-[1px]"
                  style={{ background: m.color }}
                />
              </span>
            </th>
          ))}
          {fold.other ? (
            <th
              scope="col"
              style={{ top: 18 }}
              data-testid="pivot-other-column"
              className="sticky z-20 h-[18px] bg-[var(--s2)] px-2 pb-1 text-right font-mono text-footnote font-medium whitespace-nowrap text-foreground"
            >
              <span className="inline-flex items-center gap-1">
                Other · {fold.other.count}
                <span
                  aria-hidden="true"
                  className="size-[5px] shrink-0 rounded-[1px]"
                  style={{ background: fold.other.color }}
                />
              </span>
            </th>
          ) : null}
        </TableRow>
      </TableHeader>

      <TableBody>
        {visible.map((row, ri) => {
          const other = otherCellValue(row, fold, additive);
          return (
            <TableRow key={`${page}-${ri}`}>
              {spec.rows.map((dim, i) => {
                const raw = row[dim];
                const label = raw === null || raw === undefined ? '—' : String(raw);
                return (
                  <td
                    key={dim}
                    title={label}
                    data-testid={i === 0 ? 'pivot-row-label' : undefined}
                    style={{ width: DIM_WIDTH, minWidth: DIM_WIDTH, left: i * DIM_WIDTH }}
                    className={cn(
                      // Sticky, opaque, and fixed height — R10. Scroll 64,000px
                      // of members and the label is still on the left.
                      'sticky z-10 h-6 truncate bg-[var(--s2)] px-2 text-left align-middle text-small',
                      i === spec.rows.length - 1 && 'border-r border-border',
                    )}
                  >
                    {middleTruncate(label, 18)}
                  </td>
                );
              })}
              {fold.head.map((m) => {
                const v = row[m.cell];
                const n = typeof v === 'number' ? v : null;
                return (
                  <td
                    key={m.cell}
                    style={heatStyle(heatStep(n, heatMax))}
                    className="h-6 px-2 text-right align-middle font-mono text-small tabular-nums"
                  >
                    {n === null ? <span className="text-muted-foreground">—</span> : num(n)}
                  </td>
                );
              })}
              {fold.other ? (
                <td
                  style={heatStyle(heatStep(other, heatMax))}
                  className="h-6 px-2 text-right align-middle font-mono text-small tabular-nums"
                  title={
                    other === null
                      ? 'A non-additive aggregate cannot be re-aggregated by adding cells.'
                      : undefined
                  }
                >
                  {other === null ? <span className="text-muted-foreground">—</span> : num(other)}
                </td>
              ) : null}
              <td className="h-6 border-l border-border bg-[var(--s3)] px-2 text-right align-middle font-mono text-small font-semibold text-foreground tabular-nums">
                {typeof row[rowTotalKey] === 'number' ? num(row[rowTotalKey]) : '—'}
              </td>
            </TableRow>
          );
        })}
      </TableBody>

      {/* The totals band: its own elevation, never heated, pinned to the bottom. */}
      <tfoot>
        <tr>
          <td
            colSpan={totalsSpan}
            style={{ left: 0, minWidth: headWidth }}
            className="sticky bottom-5 z-20 h-6 border-r border-border bg-[var(--s4)] px-2 text-left align-middle text-small font-semibold text-foreground"
          >
            column total
          </td>
          {fold.head.map((m) => (
            <td
              key={m.cell}
              className="sticky bottom-5 z-10 h-6 bg-[var(--s4)] px-2 text-right align-middle font-mono text-small font-semibold text-foreground tabular-nums"
            >
              {columnTotal(m.cell) === null ? '—' : num(columnTotal(m.cell))}
            </td>
          ))}
          {fold.other ? (
            <td className="sticky bottom-5 z-10 h-6 bg-[var(--s4)] px-2 text-right align-middle font-mono text-small font-semibold text-foreground tabular-nums">
              {otherColumnTotal() === null ? '—' : num(otherColumnTotal())}
            </td>
          ) : null}
          <td className="sticky bottom-5 z-10 h-6 border-l border-border bg-[var(--s5)] px-2 text-right align-middle font-mono text-small font-semibold text-foreground tabular-nums">
            {grandNum === null ? '—' : num(grandNum)}
          </td>
        </tr>
        {/* SHAPE-R9 — every share goes through ratio(); a null prints an em dash. */}
        <tr>
          <td
            colSpan={totalsSpan}
            style={{ left: 0, minWidth: headWidth }}
            className="sticky bottom-0 z-20 h-5 border-r border-border bg-[var(--s3)] px-2 text-left align-middle font-mono text-footnote text-muted-foreground"
          >
            {additive ? 'pct_of_grand_total' : 'share not defined for this function'}
          </td>
          {fold.head.map((m) => {
            const r =
              additive && grandNum !== null && columnTotal(m.cell) !== null
                ? ratio(coverage(columnTotal(m.cell) ?? 0, grandNum))
                : null;
            return (
              <td
                key={m.cell}
                className="sticky bottom-0 z-10 h-5 bg-[var(--s3)] px-2 text-right align-middle font-mono text-footnote text-muted-foreground tabular-nums"
              >
                {r === null ? '—' : `${(r * 100).toFixed(1)}%`}
              </td>
            );
          })}
          {fold.other ? (
            <td className="sticky bottom-0 z-10 h-5 bg-[var(--s3)] px-2 text-right align-middle font-mono text-footnote text-muted-foreground tabular-nums">
              {(() => {
                const ot = otherColumnTotal();
                const r = additive && grandNum !== null && ot !== null ? ratio(coverage(ot, grandNum)) : null;
                return r === null ? '—' : `${(r * 100).toFixed(1)}%`;
              })()}
            </td>
          ) : null}
          <td className="sticky bottom-0 z-10 h-5 border-l border-border bg-[var(--s4)] px-2 text-right align-middle font-mono text-footnote text-muted-foreground tabular-nums">
            {additive && grandNum !== null ? '100.0%' : '—'}
          </td>
        </tr>
      </tfoot>
    </Table>
  );
}

/* ---------------------------------------------------------------- the page */

interface PivotPageProps {
  /** Preselected dataset, e.g. from `/pivot?dataset=<id>`. */
  initialDatasetId?: string | null;
}

export function PivotPage({ initialDatasetId = null }: PivotPageProps) {
  const [tab, setTab] = useState<'pivot' | 'sql'>('pivot');

  // Selections are PREFERENCES, resolved against live data during render —
  // storing the resolved value means syncing it from an effect on every fetch.
  const [pickedId, setPickedId] = useState<string | null>(initialDatasetId);
  const [pickedVersion, setPickedVersion] = useState<number | null>(null);
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);

  // The builder draft. "Run" commits it; nothing fetches while you edit.
  const [rowDims, setRowDims] = useState<string[]>([]);
  const [colDim, setColDim] = useState<string | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [aggFn, setAggFn] = useState<AggFunction>('sum');
  const [display, setDisplay] = useState<DisplayMode>('value');
  const [fold, setFold] = useState(true);
  const [committed, setCommitted] = useState<{ scope: string; spec: PivotSpec } | null>(null);
  const [pivotPage, setPivotPage] = useState(0);

  const [sqlDraft, setSqlDraft] = useState<string | null>(null);
  const [sqlCommitted, setSqlCommitted] = useState<{ scope: string; sql: string } | null>(null);
  const [sqlPage, setSqlPage] = useState(0);

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
    pickedSheet && sheets.some((s) => s.name === pickedSheet) ? pickedSheet : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;
  const sheetKey = sheetRow?.sheet_key ?? null;
  const sheetRows = typeof sheetRow?.row_count === 'number' ? sheetRow.row_count : null;

  // The profile is what makes the member limit checkable BEFORE the run. It is
  // also refusable, so its absence is a documented state, not a broken shelf.
  const profile = useProfile(selectedId, version, sheet);
  const profileRestricted = isRestricted(profile.error);
  const distinct = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of profile.data?.columns ?? []) {
      if (typeof c.unique_count === 'number') m.set(c.name, c.unique_count);
    }
    return m;
  }, [profile.data]);

  const shapeColumns = useMemo<ShapeColumn[]>(
    () =>
      (sheetRow?.columns ?? []).map((c) => {
        const p = profile.data?.columns.find((x) => x.name === c.name);
        return {
          name: c.name,
          dtype: c.dtype ?? null,
          uniqueCount: p?.unique_count ?? null,
          nonNullCount: p?.non_null_count ?? null,
        };
      }),
    [sheetRow, profile.data],
  );

  // R5 — capability-gate the shelves rather than offering every column and
  // letting emptiness be discovered after a 400. With no profile, distinctness
  // is unknown, so every column is offered and the shelf says so.
  const candidates = useMemo(() => chartCandidates(shapeColumns), [shapeColumns]);
  const allNames = useMemo(() => shapeColumns.map((c) => c.name), [shapeColumns]);
  const dimensionNames =
    candidates.dimensions.length > 0 ? candidates.dimensions.map((c) => c.name) : allNames;
  const measureNames = candidates.measures.length > 0 ? candidates.measures.map((c) => c.name) : allNames;

  const scope = `${selectedId ?? ''}|${version ?? ''}|${sheet ?? ''}`;
  const spec = committed && committed.scope === scope ? committed.spec : null;
  const pivot = usePivot(selectedId, version, sheet, spec);
  const res = pivot.data ?? null;

  const additive = spec ? isAdditive(spec.values[0].function) : isAdditive(aggFn);
  const memberFold = useMemo(
    () => (res && spec ? foldMembers(res, spec.values[0].alias, false, additive, fold) : null),
    [res, spec, additive, fold],
  );

  // SHAPE-R7 — the guard, pre-run where the profile allows it and post-run
  // always, because `pivot_columns.length` is the only count that is certain.
  const colDistinct = colDim ? (distinct.get(colDim) ?? null) : null;
  const preBlocked = colDistinct !== null && colDistinct > COLUMN_MEMBER_LIMIT && !fold;
  const postBlocked = Boolean(memberFold?.overLimit) && !fold;

  const canRun = rowDims.length > 0 && Boolean(colDim) && Boolean(measure) && !preBlocked;

  const runPivot = () => {
    if (!canRun || !measure || !colDim) return;
    setPivotPage(0);
    setCommitted({
      scope,
      spec: {
        rows: rowDims,
        columns: colDim,
        values: [{ column: measure, function: aggFn, alias: `${measure}_${aggFn}`, display }],
        include_row_totals: true,
        include_column_totals: true,
        limit: PIVOT_ROW_LIMIT,
      },
    });
  };

  const defaultSql = sheetKey
    ? `-- One SELECT. Every ready sheet of this version is a table named by its sheet_key.\nSELECT *\nFROM ${sheetKey}\nLIMIT 100`
    : '';
  const sql = sqlDraft ?? defaultSql;
  const sqlScope = `${selectedId ?? ''}|${version ?? ''}`;
  const activeSql = sqlCommitted && sqlCommitted.scope === sqlScope ? sqlCommitted.sql : null;
  const sqlQuery = useSqlQuery(selectedId, version, activeSql);

  const pivotRows = res?.data ?? [];
  // Aliased to a const so the narrowing survives into the row callbacks below.
  const sqlData = sqlQuery.data ?? null;
  const sqlRows = sqlData?.items ?? [];
  const sqlWindow = sqlRows.slice(sqlPage * PAGE_SIZE, sqlPage * PAGE_SIZE + PAGE_SIZE);

  const scopeError = versionsQuery.error ?? sheetsQuery.error;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* ---------------------------------------------------------- scope */}
      <div className="flex flex-none items-center gap-3 bg-[var(--s2)] px-4 py-2">
        <div className="min-w-0">
          <SectionTitle className="truncate">{dataset?.name ?? 'Pivot and SQL'}</SectionTitle>
          <Footnote className="mt-0.5 truncate">
            {selectedId ? (
              <Identifier>
                {selectedId} · version {version ?? '—'} · sheet {sheetKey ?? '—'}
              </Identifier>
            ) : (
              'Pick a dataset to build a cross-tab.'
            )}
          </Footnote>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <select
            aria-label="Dataset"
            data-testid="pivot-dataset-select"
            className={cn(controlClass, 'w-52')}
            value={selectedId ?? ''}
            onChange={(e) => {
              setPickedId(e.target.value || null);
              setPickedVersion(null);
              setPickedSheet(null);
              setRowDims([]);
              setColDim(null);
              setMeasure(null);
              setCommitted(null);
              setSqlDraft(null);
              setSqlCommitted(null);
            }}
          >
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Version"
            data-testid="pivot-version-select"
            className={cn(controlClass, 'w-24')}
            value={version ?? ''}
            onChange={(e) => setPickedVersion(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number}
              </option>
            ))}
          </select>
          <select
            aria-label="Sheet"
            data-testid="pivot-sheet-select"
            className={cn(controlClass, 'w-36')}
            value={sheet ?? ''}
            onChange={(e) => setPickedSheet(e.target.value || null)}
          >
            {sheets.map((s) => (
              <option key={s.sheet_key} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
          {/* Accent 1 of 2 — the version this whole screen is scoped to. */}
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="size-[6px] rounded-full bg-[var(--sig)] shadow-[0_0_9px_-1px_var(--sig)]"
            />
            <Identifier className="text-micro text-muted-foreground">
              v{version ?? '—'} · immutable
            </Identifier>
          </span>
        </div>
      </div>

      {scopeError ? (
        <div className="flex-none px-4 py-2">
          <LensError>
            {errorText(scopeError, { notFound: 'No such dataset, or not available to this seat.' })}
          </LensError>
        </div>
      ) : null}

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(String(v) === 'sql' ? 'sql' : 'pivot')}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList className="flex-none px-4 py-1.5">
          <TabsIndicator />
          <TabsTab value="pivot" data-testid="pivot-tab">
            Pivot
          </TabsTab>
          <TabsTab value="sql" data-testid="sql-tab">
            SQL console
          </TabsTab>
        </TabsList>

        {/* ======================================================== PIVOT */}
        <TabsPanel value="pivot" className="flex min-h-0 flex-1 flex-col">
          {/* ------------------------------------------------- shelves */}
          <div className="flex flex-none flex-col gap-1.5 px-4 py-2">
            <Shelf label="Rows" testid="pivot-row-shelf" hint={`${rowDims.length} dims`}>
              {rowDims.map((d, i) => (
                <Chip key={d} onRemove={() => setRowDims(rowDims.filter((x) => x !== d))}>
                  <span className="text-muted-foreground">{i + 1}</span>
                  {d}
                </Chip>
              ))}
              <select
                aria-label="Add a row dimension"
                data-testid="pivot-add-row"
                className={cn(controlClass, 'h-6 w-36 text-micro')}
                value=""
                onChange={(e) => {
                  const v = e.target.value;
                  if (v && !rowDims.includes(v)) setRowDims([...rowDims, v]);
                }}
              >
                <option value="">+ dim</option>
                {dimensionNames
                  .filter((n) => !rowDims.includes(n) && n !== colDim)
                  .map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
              </select>
            </Shelf>

            <Shelf
              label="Column"
              testid="pivot-column-shelf"
              hint={
                colDistinct !== null
                  ? `${colDistinct} distinct · limit ${COLUMN_MEMBER_LIMIT}`
                  : 'distinct unknown'
              }
            >
              {colDim ? (
                <Chip scoped testid="pivot-column-chip" onRemove={() => setColDim(null)}>
                  {colDim}
                  {colDistinct !== null ? (
                    <span className="text-muted-foreground">· {colDistinct} members</span>
                  ) : null}
                </Chip>
              ) : null}
              <select
                aria-label="Pivot dimension"
                data-testid="pivot-column-select"
                className={cn(controlClass, 'h-6 w-40 text-micro')}
                value={colDim ?? ''}
                onChange={(e) => setColDim(e.target.value || null)}
              >
                <option value="">exactly one…</option>
                {dimensionNames
                  .filter((n) => !rowDims.includes(n))
                  .map((n) => {
                    const d = distinct.get(n) ?? null;
                    return (
                      <option key={n} value={n}>
                        {n}
                        {d !== null ? ` · ${d}` : ''}
                      </option>
                    );
                  })}
              </select>
              <Toggle on={fold} onClick={() => setFold(!fold)} testid="pivot-fold-toggle">
                fold top {VIZ_SLOTS}
              </Toggle>
            </Shelf>

            <Shelf label="Values" testid="pivot-measure-shelf" hint={`${AGG_FUNCTIONS.length} aggs`}>
              <select
                aria-label="Measure column"
                data-testid="pivot-measure-select"
                className={cn(controlClass, 'h-6 w-40 text-micro')}
                value={measure ?? ''}
                onChange={(e) => setMeasure(e.target.value || null)}
              >
                <option value="">measure…</option>
                {measureNames.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              <div className="flex flex-wrap items-center gap-0.5">
                {AGG_FUNCTIONS.map((f) => (
                  <Toggle key={f} on={aggFn === f} onClick={() => setAggFn(f)} testid={`pivot-agg-${f}`}>
                    {f}
                  </Toggle>
                ))}
              </div>
            </Shelf>

            <Shelf label="Display" hint={`heat is magnitude · ${PIVOT_ROW_LIMIT} row cap`}>
              <div className="flex flex-wrap items-center gap-0.5">
                {DISPLAY_MODES.map((d) => (
                  <Toggle key={d} on={display === d} onClick={() => setDisplay(d)} testid={`pivot-display-${d}`}>
                    {d}
                  </Toggle>
                ))}
              </div>
              <span className="ml-3 inline-flex items-center gap-1">
                <span className="text-footnote text-muted-foreground">0</span>
                {heatLegend().map((s, i) => (
                  <span key={i} aria-hidden="true" className="h-2 w-4 rounded-[1px]" style={s} />
                ))}
                <span className="text-footnote text-muted-foreground">high</span>
              </span>
              <div className="ml-auto flex items-center gap-2">
                {pivot.isFetching ? <Status kind="unknown">running</Status> : null}
                <RunButton onClick={runPivot} disabled={!canRun} busy={pivot.isFetching} testid="pivot-run" />
              </div>
            </Shelf>

            {preBlocked ? (
              <GuardBlock title="Column shelf blocked" testid="pivot-member-guard">
                <Identifier>{colDim}</Identifier> has {colDistinct} distinct members — above the{' '}
                {COLUMN_MEMBER_LIMIT}-member limit for a readable cross-tab. Every member becomes an output
                column, so this pivot would be tens of thousands of pixels wide. Turn on{' '}
                <b>fold top {VIZ_SLOTS}</b> to rank the members and fold the tail into a single "Other"
                column that carries its own count and share, or bucket the dimension first. The service's own
                hard cap is {SERVER_PIVOT_COLUMN_CAP} distinct values (
                <Identifier>too-many-pivot-columns</Identifier>), which protects the query, not the reader.
              </GuardBlock>
            ) : null}

            {!preBlocked && profileRestricted ? (
              <Footnote>
                <Severity level="warning">Distinct counts unavailable</Severity> — profiling is refused for
                this seat, so the member limit is enforced on the result instead of before the run.
              </Footnote>
            ) : null}
          </div>

          {/* --------------------------------------------------- result */}
          <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 pb-2">
            {isRestricted(pivot.error) ? <LensRestricted what="Pivoting" /> : null}

            {pivot.error && !isRestricted(pivot.error) ? (
              <GuardBlock title="Pivot refused" code={problemCode(pivot.error)}>
                {errorText(pivot.error, { notFound: 'No such dataset or version for this seat.' })}
              </GuardBlock>
            ) : null}

            {!spec && !pivot.error ? (
              <LensEmpty>
                Pick row dimensions, one pivot dimension and a measure, then run. Nothing is computed until
                you do.
              </LensEmpty>
            ) : null}

            {res && spec && memberFold ? (
              res.row_count === 0 ? (
                // SHAPE-R9 — zero rows is a state, and no statistic is printed
                // from a zero denominator.
                <LensEmpty>
                  No rows in this version matched. Nothing was aggregated, so no totals and no shares are
                  shown — an empty population has no percentage.
                </LensEmpty>
              ) : postBlocked ? (
                <GuardBlock title="Too many members to draw" testid="pivot-result-guard">
                  The pivot returned {memberFold.memberCount} members, above the {COLUMN_MEMBER_LIMIT}-member
                  limit. Turn on <b>fold top {VIZ_SLOTS}</b> — the result is already here, so folding is
                  immediate and costs no second query.
                </GuardBlock>
              ) : (
                <>
                  <div className="flex flex-wrap items-start gap-6">
                    <StatList coverage={coverage(res.original_count, sheetRows ?? res.original_count)}>
                      <Stat
                        name={spec.values[0].function}
                        value={num(res.totals?.[spec.values[0].alias] ?? null)}
                        coverage={coverage(res.original_count, sheetRows ?? res.original_count)}
                      />
                      <Stat
                        name="rows read"
                        value={compact(res.original_count)}
                        coverage={coverage(res.original_count, sheetRows ?? res.original_count)}
                      />
                      <Stat
                        name="members"
                        value={`${memberFold.head.length + (memberFold.other ? 1 : 0)} drawn`}
                        coverage={coverage(memberFold.head.length, memberFold.memberCount)}
                      />
                    </StatList>

                    <div className="min-w-0">
                      <Eyebrow>Cross-tab</Eyebrow>
                      <Footnote className="mt-1">
                        {res.row_count} output rows × {memberFold.memberCount} members. Row, column and grand
                        totals are re-aggregated by the service at the coarser grain — correct for{' '}
                        <Identifier>mean</Identifier> and <Identifier>nunique</Identifier> too, where adding
                        cells would lie.
                      </Footnote>
                      {res.truncated ? (
                        <Footnote className="mt-1">
                          <Severity level="error">Truncated</Severity> — the {PIVOT_ROW_LIMIT}-row cap cut
                          this result. The totals still cover every row read.
                        </Footnote>
                      ) : null}
                      {memberFold.degenerate ? (
                        <Footnote className="mt-1">
                          Under three members this cross-tab is really a ratio (SHAPE-R7).
                        </Footnote>
                      ) : null}
                      {spec.values[0].display !== 'value' ? (
                        <Footnote className="mt-1">
                          The service withholds row, column and grand totals for a percentage display — a
                          total of percentages is a different unit sitting beside its own cells. The totals
                          band reads em dashes on purpose.
                        </Footnote>
                      ) : null}
                    </div>
                  </div>

                  {memberFold.other ? <MemberLegend fold={memberFold} additive={additive} /> : null}

                  <PivotGrid res={res} spec={spec} fold={memberFold} additive={additive} page={pivotPage} />

                  <div className="flex flex-none items-center gap-3">
                    <Footnote>
                      Rows {pivotPage * PAGE_SIZE + 1}–
                      {Math.min(pivotRows.length, (pivotPage + 1) * PAGE_SIZE)} of {pivotRows.length}. The
                      pivot endpoint returns one capped result and carries no cursor, so these steps window
                      what is already here.
                    </Footnote>
                    <div className="ml-auto flex items-center gap-1">
                      <PagerButton
                        direction="prev"
                        disabled={pivotPage === 0}
                        onClick={() => setPivotPage(Math.max(0, pivotPage - 1))}
                        testid="pivot-prev"
                      />
                      <PagerButton
                        direction="next"
                        disabled={(pivotPage + 1) * PAGE_SIZE >= pivotRows.length}
                        onClick={() => setPivotPage(pivotPage + 1)}
                        testid="pivot-next"
                      />
                    </div>
                  </div>

                  <Footnote>
                    Result persisted as{' '}
                    <Identifier>{res.result_file ?? 'pivot_output'}</Identifier> — artifact type{' '}
                    <Identifier>pivot_output</Identifier>, parquet, retention {PIVOT_RETENTION_DAYS} days.
                    The artifact row is the only way to resolve its key; after the retention sweep the link
                    is gone.
                  </Footnote>
                </>
              )
            ) : null}
          </div>
        </TabsPanel>

        {/* ========================================================== SQL */}
        <TabsPanel value="sql" className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-none flex-wrap items-center gap-2 px-4 py-2">
            <Identifier className="text-footnote text-muted-foreground">
              POST /datasets/{selectedId ?? '…'}/versions/{version ?? '…'}/sql
            </Identifier>
            <span className="ml-auto flex items-center gap-2">
              {sqlQuery.isFetching ? <Status kind="unknown">running</Status> : null}
              <RunButton
                onClick={() => {
                  setSqlPage(0);
                  setSqlCommitted({ scope: sqlScope, sql });
                }}
                disabled={!selectedId || version == null || sql.trim().length === 0}
                busy={sqlQuery.isFetching}
                testid="sql-run"
              />
            </span>
          </div>

          <div className="flex flex-none flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-2">
            <Status kind="good">one statement</Status>
            <Status kind="good">SELECT only</Status>
            <Status kind="critical">no DDL or DML</Status>
            <Footnote>
              Read-only sandbox. Joins and CTEs across the sheets of this version are allowed; a{' '}
              <Identifier>sheet_key</Identifier> belonging to another team resolves as{' '}
              <Identifier>404</Identifier>, never "access denied". Datasets that declare a sensitive column
              require <Identifier>dataset:read_sensitive</Identifier> here — there is no redacted SQL mode,
              because a SELECT can reconstruct a masked value.
            </Footnote>
          </div>

          <div className="flex-none px-4" data-testid="sql-editor">
            {/* The shared editor. It ships JSON and JavaScript modes only — there
             * is no SQL grammar in the repo and adding an editor library for one
             * screen is not a trade worth making, so keywords are not coloured. */}
            <CodeEditor
              value={sql}
              onChange={setSqlDraft}
              language="javascript"
              minHeight="120px"
              maxHeight="220px"
              placeholder="SELECT …"
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 py-2">
            {isRestricted(sqlQuery.error) ? <LensRestricted what="Ad-hoc SQL" /> : null}

            {sqlQuery.error && !isRestricted(sqlQuery.error) ? (
              <GuardBlock title="Query refused" code={problemCode(sqlQuery.error)} testid="sql-error">
                {errorText(sqlQuery.error, { notFound: 'No such dataset or version for this seat.' })}
              </GuardBlock>
            ) : null}

            {!activeSql && !sqlQuery.error ? (
              <LensEmpty>Nothing has run. Edit the statement above and run it.</LensEmpty>
            ) : null}

            {activeSql ? (
              <div className="rounded-md bg-[var(--s2)] px-3 py-2">
                <Eyebrow>Statement that ran</Eyebrow>
                <pre className="mt-1 overflow-x-auto font-mono text-footnote leading-relaxed text-muted-foreground">
                  {activeSql}
                </pre>
                {sqlData ? (
                  <Footnote className="mt-1">
                    Tables in scope: {sqlData.tables.join(', ') || '—'}
                  </Footnote>
                ) : null}
              </div>
            ) : null}

            {sqlData ? (
              sqlData.row_count === 0 ? (
                <LensEmpty>The statement ran and returned no rows. That is a result, not a failure.</LensEmpty>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-4">
                    <StatList coverage={coverage(sqlData.row_count, sqlData.row_count)}>
                      <Stat
                        name="rows"
                        value={num(sqlData.row_count)}
                        coverage={coverage(sqlData.row_count, sqlData.row_count)}
                      />
                      <Stat
                        name="columns"
                        value={num(sqlData.columns.length)}
                        coverage={coverage(sqlData.columns.length, sqlData.columns.length)}
                      />
                    </StatList>
                    {sqlData.truncated ? (
                      <Footnote>
                        <Severity level="error">Row-capped</Severity> — the service cut this result. There is
                        no cursor on this endpoint, so narrow the statement to see the rest.
                      </Footnote>
                    ) : null}
                  </div>

                  <Table containerClassName="min-h-0 flex-1 overflow-auto" data-testid="sql-result">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        {sqlData.columns.map((c) => (
                          <TableHead
                            key={c}
                            className="bg-[var(--s2)] font-mono text-footnote normal-case"
                          >
                            {c}
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sqlWindow.map((row, i) => (
                        <TableRow key={`${sqlPage}-${i}`}>
                          {sqlData.columns.map((c) => {
                            const v = row[c];
                            return typeof v === 'number' ? (
                              <TableNumericCell key={c} className="border-b-0 font-mono text-small">
                                {num(v)}
                              </TableNumericCell>
                            ) : (
                              <TableCell key={c} className="border-b-0 font-mono text-small">
                                {v === null || v === undefined ? (
                                  <span className="text-muted-foreground">null</span>
                                ) : (
                                  middleTruncate(String(v), 28)
                                )}
                              </TableCell>
                            );
                          })}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>

                  <div className="flex flex-none items-center gap-3">
                    <Footnote>
                      Rows {sqlPage * PAGE_SIZE + 1}–{Math.min(sqlRows.length, (sqlPage + 1) * PAGE_SIZE)} of{' '}
                      {sqlRows.length} held. Result persisted as{' '}
                      <Identifier>{sqlData.result_file}</Identifier> — artifact type{' '}
                      <Identifier>query_output</Identifier>, parquet, retention {SQL_RETENTION_DAYS} days.
                    </Footnote>
                    <div className="ml-auto flex items-center gap-1">
                      <PagerButton
                        direction="prev"
                        disabled={sqlPage === 0}
                        onClick={() => setSqlPage(Math.max(0, sqlPage - 1))}
                        testid="sql-prev"
                      />
                      <PagerButton
                        direction="next"
                        disabled={(sqlPage + 1) * PAGE_SIZE >= sqlRows.length}
                        onClick={() => setSqlPage(sqlPage + 1)}
                        testid="sql-next"
                      />
                    </div>
                  </div>
                </>
              )
            ) : null}
          </div>
        </TabsPanel>
      </Tabs>
    </div>
  );
}
