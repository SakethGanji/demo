/**
 * The query + filter builder — the most-used control in the product.
 *
 * Three zones, grouped by elevation rather than by borders (rule 3):
 *
 *   spec rail (bg-card)   |   filter tree + results (bg-background)   |   operator dock (bg-card)
 *
 * The page owns its own scrolling. `html, body, #root` are all `overflow:
 * hidden` and the studio shell already spends 46px on its header, so the root
 * here is `flex min-h-0 flex-1 flex-col` and every scrollable region below it
 * is explicitly `min-h-0 overflow-auto`. Anything that forgets is silently
 * clipped rather than visibly broken, which is the worst failure mode.
 *
 * ACCENT BUDGET (rule 1). The shell has already spent one `--sig` on the active
 * route. This screen spends two more, and no others:
 *   1. the as-of version marker in the spec bar — "what is this resolved against";
 *   2. the SELECTED condition, and the operator palette entry scoped to it —
 *      one meaning ("the thing you are editing"), rendered in two places.
 * Everything else that is repeated state (page-size segments, and/or segments,
 * projection checkboxes, sort ranks) takes value + elevation instead.
 *
 * WHAT THE API MAKES POSSIBLE, AND WHAT IT DOES NOT:
 *   - 36 operators, no more (see `useQuerySpec.ts`). The palette renders every
 *     one and greys the ones the server would refuse for the selected column,
 *     with the dtype family it demands legible beside it.
 *   - Cursor paging. `total` is returned, so "51–62 of 41,908" is honest — but a
 *     numbered pager is unbuildable, and this page says so rather than drawing
 *     one. `PagerButton` is deliberately a STEP.
 *   - Masked columns can be projected and never computed over. The filter and
 *     sort pickers simply do not offer them, and the reason is stated where the
 *     user would otherwise go looking for them.
 */

import { useMemo, useState, type ReactNode } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Copy,
  CornerUpLeft,
  Download,
  Lock,
  Plus,
  Search,
  X,
} from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { compact, formatBytes, num } from '@/shared/lib/format';
import { errorText } from '@/shared/lib/analyticsClient';
import {
  Eyebrow,
  Figure,
  Footnote,
  Identifier,
  SectionTitle,
} from '@/shared/components/instrument/Typography';
import { Status } from '@/shared/components/instrument/Status';
import { Severity } from '@/shared/components/instrument/Severity';
import { Stat } from '@/shared/components/instrument/Stat';
import { Guard } from '@/shared/components/instrument/Guard';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { complete, coverage } from '@/shared/components/instrument/coverage';
import { middleTruncate } from '@/shared/components/instrument/shape';
import { Checkbox } from '@/shared/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table';
import {
  OPERATORS,
  OP_GROUPS,
  PAGE_SIZES,
  conditionIsComplete,
  kindLabel,
  operatorApplies,
  operatorMeta,
  useQuerySpec,
  type ColumnKind,
  type ConditionNode,
  type FilterOp,
  type GroupNode,
  type OperatorMeta,
  type QueryBuilder,
  type QueryColumn,
  type TreeNode,
} from '../hooks/useQuerySpec';
import {
  downloadPath,
  errorCodeOf,
  useDatasetDownload,
  type DownloadFormat,
} from '../hooks/useJoins';
import { isRestricted } from '../hooks/useAnalysis';
import { controlClass, fieldClass } from './fieldStyles';
import { LensRestricted } from './lenses/primitives';
import { PagerButton } from './PagerButton';

/* ------------------------------------------------------------------ atoms */

/** A dense button. Value + elevation, never the accent — rule 1. */
const BTN =
  'inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-small text-foreground ' +
  'ring-1 ring-border transition-colors hover:bg-accent disabled:cursor-not-allowed ' +
  'disabled:text-muted-foreground disabled:hover:bg-transparent';

/** Rule 2 — the primary action wins on VALUE, which preserves the accent. */
const BTN_PRIMARY =
  'inline-flex h-7 items-center gap-2 rounded-md bg-primary px-3 text-body font-semibold ' +
  'text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40';

const GHOST = 'inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-small ' +
  'text-muted-foreground transition-colors hover:bg-accent hover:text-foreground';

/** The refusal surface: tinted, code chip, explanation in the footnote register. */
function Refusal({
  code,
  children,
  testid,
}: {
  code: string;
  children: ReactNode;
  testid?: string;
}) {
  return (
    <div
      data-testid={testid}
      className="flex items-start gap-2 rounded-md bg-destructive/8 px-2 py-1.5 ring-1 ring-destructive/25"
    >
      <span className="mt-px size-[7px] shrink-0 rounded-[1px] bg-destructive" aria-hidden="true" />
      <div className="min-w-0">
        <Identifier className="rounded bg-black/30 px-1 text-footnote font-semibold text-destructive ring-1 ring-destructive/35">
          {code}
        </Identifier>
        <Footnote className="mt-1">{children}</Footnote>
      </div>
    </div>
  );
}

/** An informational note — a left rule and an indent, never a box (rule 8). */
function Note({ children }: { children: ReactNode }) {
  return (
    <Footnote className="mt-1.5 pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">{children}</Footnote>
  );
}

function SectionHead({
  title,
  path,
  right,
}: {
  title: string;
  path?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <SectionTitle className="text-small">{title}</SectionTitle>
      {path && <Identifier className="text-footnote text-muted-foreground">{path}</Identifier>}
      <span className="h-px min-w-2 flex-1 bg-[var(--r1)]" />
      {right}
    </div>
  );
}

/** Segmented control. Repeated active state → value + elevation, no accent. */
function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  testid,
  label,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  testid?: string;
  label: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex gap-0.5 rounded-md bg-black/25 p-0.5 shadow-[inset_0_1px_2px_rgba(0,0,0,.35)]"
    >
      {options.map((o) => (
        <button
          key={String(o)}
          role="radio"
          aria-checked={value === o}
          data-testid={testid ? `${testid}-${o}` : undefined}
          onClick={() => onChange(o)}
          className={cn(
            'h-5 rounded px-2 font-mono text-micro tabular-nums transition-colors',
            value === o
              ? 'bg-secondary font-semibold text-foreground shadow-[inset_0_1px_0_rgba(255,255,255,.045)]'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- filter tree */

function ConditionRow({
  b,
  node,
  columns,
}: {
  b: QueryBuilder;
  node: ConditionNode;
  columns: QueryColumn[];
}) {
  const meta = operatorMeta(node.op);
  const kind: ColumnKind = columns.find((c) => c.name === node.column)?.kind ?? 'unknown';
  const selected = b.selectedConditionId === node.id;
  const complete = conditionIsComplete(node);

  return (
    <div
      data-testid="condition-row"
      data-op={node.op}
      data-complete={complete ? '' : undefined}
      onClick={() => b.selectCondition(node.id)}
      className={cn(
        // A condition earns a SURFACE, not a border (rule 8).
        'flex min-h-[30px] flex-wrap items-center gap-1.5 rounded-lg px-2 py-1 shadow-[inset_0_1px_0_rgba(255,255,255,.045)]',
        selected ? 'bg-secondary ring-1 ring-[var(--sig-line)]' : 'bg-muted',
      )}
    >
      <select
        aria-label="Column"
        data-testid="condition-column"
        value={node.column}
        onChange={(e) => {
          const nextKind = columns.find((c) => c.name === e.target.value)?.kind ?? 'unknown';
          // An operator that no longer applies would be `400
          // operator-type-mismatch`; fall back rather than carry it across.
          const keep = operatorApplies(meta, nextKind);
          b.updateCondition(node.id, {
            column: e.target.value,
            op: keep ? node.op : ('eq' as FilterOp),
          });
        }}
        className={cn(fieldClass, 'w-[150px] font-mono')}
      >
        {columns
          .filter((c) => !c.masked)
          .map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
      </select>

      <Identifier className="text-footnote text-muted-foreground">{kindLabel(kind)}</Identifier>

      <select
        aria-label="Operator"
        data-testid="condition-op"
        value={node.op}
        onChange={(e) => b.updateCondition(node.id, { op: e.target.value as FilterOp })}
        className={cn(fieldClass, 'w-[124px] font-mono')}
      >
        {OP_GROUPS.map((g) => (
          <optgroup key={g.id} label={g.title}>
            {OPERATORS.filter((o) => o.group === g.id).map((o) => (
              <option key={o.op} value={o.op} disabled={!operatorApplies(o, kind)}>
                {o.op}
                {operatorApplies(o, kind) ? '' : ` — needs ${o.requires}`}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      <ValueEditor b={b} node={node} meta={meta} dense />

      <button
        aria-label="Remove condition"
        data-testid="remove-node"
        onClick={(e) => {
          e.stopPropagation();
          b.removeNode(node.id);
        }}
        className="ml-auto grid size-6 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

/** The operand inputs, which are entirely a function of the operator's arity. */
function ValueEditor({
  b,
  node,
  meta,
  dense,
}: {
  b: QueryBuilder;
  node: ConditionNode;
  meta: OperatorMeta;
  dense?: boolean;
}) {
  const [draft, setDraft] = useState('');
  const inputType = meta.input === 'number' ? 'number' : meta.input === 'date' ? 'date' : 'text';
  const cls = cn(fieldClass, 'font-mono', dense ? 'w-[110px]' : 'w-full');

  return (
    <div className="flex flex-1 flex-wrap items-center gap-1.5">
      {meta.arity === 'none' && <Footnote>{meta.hint}</Footnote>}

      {meta.arity === 'scalar' && (
        <>
          <input
            aria-label="Value"
            data-testid="condition-value"
            type={inputType}
            value={node.value}
            onChange={(e) => b.updateCondition(node.id, { value: e.target.value })}
            className={cls}
          />
          <Footnote>{meta.hint}</Footnote>
        </>
      )}

      {meta.arity === 'pair' && (
        <>
          <input
            aria-label="Low"
            data-testid="condition-value"
            type={inputType}
            value={node.value}
            onChange={(e) => b.updateCondition(node.id, { value: e.target.value })}
            className={cls}
          />
          <Identifier className="text-footnote text-muted-foreground">and</Identifier>
          <input
            aria-label="High"
            data-testid="condition-value2"
            type={inputType}
            value={node.value2}
            onChange={(e) => b.updateCondition(node.id, { value2: e.target.value })}
            className={cls}
          />
          <Footnote>{meta.hint}</Footnote>
        </>
      )}

      {meta.arity === 'list' && (
        <>
          {node.values.map((v, i) => (
            <span
              key={`${v}-${i}`}
              data-testid="value-chip"
              className="inline-flex h-5 items-center gap-1.5 rounded px-1.5 font-mono text-micro text-foreground shadow-[inset_0_0_0_1px_var(--r2)]"
            >
              {v}
              <button
                aria-label={`Remove ${v}`}
                onClick={(e) => {
                  e.stopPropagation();
                  b.updateCondition(node.id, {
                    values: node.values.filter((_, j) => j !== i),
                  });
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="size-2.5" />
              </button>
            </span>
          ))}
          <input
            aria-label="Add value"
            data-testid="condition-value"
            placeholder="value ⏎"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' || draft.trim() === '') return;
              e.preventDefault();
              b.updateCondition(node.id, { values: [...node.values, draft.trim()] });
              setDraft('');
            }}
            className={cn(fieldClass, 'w-[104px] font-mono')}
          />
          <Footnote>
            {node.values.length === 0 ? 'non-empty list required' : `${node.values.length} values`}
          </Footnote>
        </>
      )}

      {meta.caseSensitive && (
        <label
          className="inline-flex items-center gap-1.5 font-mono text-footnote text-muted-foreground"
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            checked={node.caseSensitive}
            onCheckedChange={(v: boolean) => b.updateCondition(node.id, { caseSensitive: v })}
            className="size-3.5"
          />
          case_sensitive
        </label>
      )}
    </div>
  );
}

function GroupBlock({
  b,
  node,
  columns,
  depth,
}: {
  b: QueryBuilder;
  node: GroupNode;
  columns: QueryColumn[];
  depth: number;
}) {
  const isRoot = depth === 0;
  return (
    <div
      data-testid={isRoot ? 'filter-tree' : 'filter-group'}
      className={cn(
        // A nested group is a LEFT-RULED INDENT, not a box inside a box (rule 8).
        !isRoot && 'rounded-lg bg-white/[.022] px-2 py-1.5 shadow-[inset_2px_0_0_var(--r2)]',
      )}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <Segmented
          label="Group logic"
          options={['and', 'or'] as const}
          value={node.logic}
          onChange={(v) => b.setLogic(node.id, v)}
          testid={isRoot ? 'root-logic' : `group-logic-${node.id}`}
        />
        <Footnote>
          {node.logic === 'and' ? 'match all' : 'match any'} · {node.children.length}{' '}
          {node.children.length === 1 ? 'child' : 'children'}
        </Footnote>
        <span className="ml-auto flex items-center gap-1">
          <button
            className={GHOST}
            data-testid={isRoot ? 'add-filter' : 'add-filter-nested'}
            onClick={() => b.addCondition(node.id)}
          >
            <Plus className="size-3" />
            filter
          </button>
          <button
            className={GHOST}
            data-testid={isRoot ? 'add-group' : 'add-group-nested'}
            onClick={() => b.addGroup(node.id)}
          >
            <Plus className="size-3" />
            group
          </button>
          {!isRoot && (
            <>
              <button
                aria-label="Ungroup"
                title="Splice these conditions into the parent"
                data-testid="ungroup"
                onClick={() => b.ungroup(node.id)}
                className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <CornerUpLeft className="size-3" />
              </button>
              <button
                aria-label="Delete group"
                onClick={() => b.removeNode(node.id)}
                className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            </>
          )}
        </span>
      </div>

      <div className="flex flex-col gap-1">
        {node.children.map((child: TreeNode, i) => (
          <div key={child.id} className="grid grid-cols-[44px_1fr] items-start gap-2">
            <Identifier
              className={cn(
                'pt-2 text-right text-footnote',
                i === 0 ? 'text-muted-foreground' : 'font-semibold text-foreground',
              )}
            >
              {i === 0 ? 'where' : node.logic.toUpperCase()}
            </Identifier>
            {child.kind === 'group' ? (
              <GroupBlock b={b} node={child} columns={columns} depth={depth + 1} />
            ) : (
              <ConditionRow b={b} node={child} columns={columns} />
            )}
          </div>
        ))}
        {node.children.length === 0 && (
          <Footnote className="pl-[52px]">
            No conditions — every row matches. Add one, or leave it empty and page the whole sheet.
          </Footnote>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- operator dock */

function OperatorPalette({ b }: { b: QueryBuilder }) {
  const [q, setQ] = useState('');
  const selected = useMemo(() => findCondition(b.root, b.selectedConditionId), [b.root, b.selectedConditionId]);
  const kind: ColumnKind = selected
    ? (b.columns.find((c) => c.name === selected.column)?.kind ?? 'unknown')
    : 'unknown';

  const needle = q.trim().toLowerCase();
  const matching = OPERATORS.filter((o) => !needle || o.op.includes(needle));
  const greyed = selected ? matching.filter((o) => !operatorApplies(o, kind)).length : 0;

  return (
    <>
      <div className="px-3 py-2">
        <div className={cn(controlClass, 'flex items-center gap-2')}>
          <Search className="size-3 shrink-0 text-muted-foreground" />
          <input
            data-testid="op-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter 36 operators…"
            className="min-w-0 flex-1 bg-transparent text-small outline-none placeholder:text-muted-foreground"
          />
        </div>
      </div>

      <div className="flex items-center gap-2 px-3 py-1.5 shadow-[inset_0_1px_0_var(--r1),inset_0_-1px_0_var(--r1)]">
        {selected ? (
          <>
            <Footnote>applies to</Footnote>
            <Identifier className="text-small text-foreground">{selected.column}</Identifier>
            <Identifier className="text-footnote text-muted-foreground">
              {kindLabel(kind)}
            </Identifier>
            <Footnote className="ml-auto tabular-nums">
              {matching.length - greyed} of {matching.length} available
            </Footnote>
          </>
        ) : (
          <Footnote>Select a condition to see which operators its column can take.</Footnote>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2" data-testid="operator-palette">
        {OP_GROUPS.map((g) => {
          const ops = matching.filter((o) => o.group === g.id);
          if (ops.length === 0) return null;
          return (
            <div key={g.id} className="mb-2">
              <div className="mb-1 flex items-center gap-2">
                <SectionTitle className="text-small">{g.title}</SectionTitle>
                <Identifier className="text-footnote text-muted-foreground">
                  {ops.length}
                </Identifier>
                <span className="h-px min-w-2 flex-1 bg-[var(--r1)]" />
              </div>
              <div className="grid grid-cols-2 gap-x-2">
                {ops.map((o) => {
                  const ok = !selected || operatorApplies(o, kind);
                  const active = selected?.op === o.op;
                  return (
                    <button
                      key={o.op}
                      data-testid={`operator-${o.op}`}
                      data-available={ok ? '' : undefined}
                      disabled={!ok || !selected}
                      title={
                        ok
                          ? o.hint
                          : `400 operator-type-mismatch — '${o.op}' requires a ${o.requires} column`
                      }
                      onClick={() => selected && b.updateCondition(selected.id, { op: o.op })}
                      className={cn(
                        'flex h-5 items-center gap-1.5 rounded px-1.5 text-left',
                        ok && 'hover:bg-accent',
                        !ok && 'cursor-not-allowed',
                        // SIGNAL — the operator the draft row is scoped to.
                        active &&
                          'bg-secondary shadow-[inset_0_1px_0_rgba(255,255,255,.045),inset_0_0_0_1px_var(--sig-line)]',
                      )}
                    >
                      <Identifier
                        className={cn(
                          'min-w-0 flex-1 truncate text-micro',
                          ok ? 'text-foreground' : 'text-muted-foreground line-through decoration-[var(--t4)]',
                          active && 'font-semibold',
                        )}
                      >
                        {o.op}
                      </Identifier>
                      {!ok && (
                        <Identifier className="shrink-0 text-footnote text-muted-foreground">
                          {o.requires}
                        </Identifier>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3 px-3 py-1.5 shadow-[inset_0_1px_0_var(--r1)]">
        <Footnote>
          36 operators · struck through = the server would answer{' '}
          <Identifier className="text-foreground">400 operator-type-mismatch</Identifier>
        </Footnote>
      </div>

      {selected && (
        <div className="shrink-0 px-3 py-2 shadow-[inset_0_1px_0_var(--r1)]">
          <SectionHead title="Value" path={`spec.filters · ${selected.op}`} />
          <ValueEditor b={b} node={selected} meta={operatorMeta(selected.op)} />
          <Footnote className="mt-2">
            Sent as{' '}
            <Identifier className="text-foreground">
              {JSON.stringify({
                column: selected.column,
                op: selected.op,
                ...(operatorMeta(selected.op).arity === 'none' ? {} : { value: '…' }),
              })}
            </Identifier>
          </Footnote>
        </div>
      )}
    </>
  );
}

function findCondition(node: TreeNode, id: string | null): ConditionNode | null {
  if (!id) return null;
  if (node.kind === 'condition') return node.id === id ? node : null;
  for (const c of node.children) {
    const hit = findCondition(c, id);
    if (hit) return hit;
  }
  return null;
}

/* ---------------------------------------------------------------- download */

const DOWNLOAD_FORMATS = ['csv', 'parquet', 'xlsx'] as const;

/**
 * The download panel — the one RAW EGRESS path on this page.
 *
 * Everything above this point is a *read through the explorer*, which masks.
 * `GET /download` hands over the file, so masking would be theatre if it were
 * not gated: `ensure_raw_access` refuses a viewer or an editor outright on any
 * dataset that declares a sensitive column, with `403
 * sensitive-data-restricted`. It does NOT write a masked file. That refusal is
 * a first-class state here (`isRestricted` → `LensRestricted`), not an error.
 *
 * Three things this panel says BEFORE the click, because afterwards the file
 * exists and the reader owns it:
 *
 *  1. WHICH COLUMNS, and whether they are raw. A masked column on screen means
 *     this seat has no raw access, which means this download will be refused —
 *     and dropping the column from `columns` does not unlock it, because the
 *     gate is on the dataset, not the column.
 *  2. WHICH ROWS. The filter tree, the search box and the sort stack are not
 *     sent. `filter_expr` is a SQL WHERE evaluated by DuckDB — a different
 *     language from QuerySpec, where `top_n` and `is_duplicate` have no
 *     spelling — and the route has no ORDER BY, so rows arrive in storage
 *     order. Claiming the file matches the grid would be the silent-wrong-
 *     answer class in file form.
 *  3. WHICH ROUTE. Pinning an older version switches to
 *     `/versions/{n}/download`, which accepts format and sheet ONLY. The
 *     column subset, the row limit and the filter are not merely ignored —
 *     they are not sent, and the panel greys them and says why.
 */
function DownloadPanel({ b }: { b: QueryBuilder }) {
  const [format, setFormat] = useState<DownloadFormat>('csv');
  const [subset, setSubset] = useState<'projection' | 'all'>('projection');
  const [limitText, setLimitText] = useState('');
  const [filterExpr, setFilterExpr] = useState('');
  const download = useDatasetDownload();

  const currentVersion =
    b.versions.length > 0 ? Math.max(...b.versions.map((v) => v.version_number)) : null;
  // Only the current-version route takes the extra four parameters, so which
  // version is selected decides which controls exist at all.
  const pinned = b.version != null && currentVersion != null && b.version !== currentVersion;
  const versionNumber = pinned ? b.version : null;
  const path = downloadPath(b.datasetId ?? '{dataset_id}', versionNumber);

  // A masked column is never NAMED in `columns`. It would not come back masked
  // — the file has no masking layer — so asking for it is asking for the raw
  // value, which is precisely what the gate refuses.
  const sendable = b.projection.filter((name) => !b.maskedColumns.includes(name));
  // An empty `columns` is not "no columns" on the wire — it is the parameter
  // omitted, which means EVERY column. So an all-masked projection falls back
  // to the honest reading rather than promising a subset it cannot ask for.
  const columns = !pinned && subset === 'projection' && sendable.length > 0 ? sendable : null;
  const columnCount = columns ? columns.length : b.columns.length;

  const trimmedLimit = limitText.trim();
  const parsedLimit = /^\d+$/.test(trimmedLimit) ? Number(trimmedLimit) : null;
  const rowLimit = pinned ? null : parsedLimit;
  const expr = pinned ? '' : filterExpr.trim();

  const sheetRow = b.sheets.find((s) => s.name === b.sheet) ?? null;
  const sheetRows = sheetRow?.row_count ?? null;
  const rowsIncluded =
    sheetRows == null ? null : rowLimit == null ? sheetRows : Math.min(rowLimit, sheetRows);

  const paramCount =
    1 + (b.sheet ? 1 : 0) + (columns ? 1 : 0) + (rowLimit != null ? 1 : 0) + (expr ? 1 : 0);

  return (
    <section className="min-w-0 overflow-auto" data-testid="download-panel">
      <SectionHead
        title="Download"
        path={path}
        right={
          <Identifier className="text-footnote text-muted-foreground tabular-nums">
            {paramCount} params
          </Identifier>
        }
      />

      <div className="grid grid-cols-[58px_1fr] items-center gap-x-2.5 gap-y-1.5">
        <Identifier className="text-right text-footnote text-muted-foreground">format</Identifier>
        <div className="flex items-center gap-2">
          <Segmented
            label="Download format"
            options={DOWNLOAD_FORMATS}
            value={format}
            onChange={setFormat}
            testid="download-format"
          />
          <Footnote>
            {format === 'xlsx' && !b.sheet
              ? 'a whole multi-sheet workbook'
              : `one sheet · ${b.sheet ?? 'none selected'}`}
          </Footnote>
        </div>

        <Identifier className="text-right text-footnote text-muted-foreground">columns</Identifier>
        <div className={cn('flex items-center gap-2', pinned && 'opacity-45')}>
          <Segmented
            label="Column subset"
            options={['projection', 'all'] as const}
            value={pinned ? 'all' : subset}
            onChange={(v) => !pinned && setSubset(v)}
            testid="download-columns"
          />
          <Footnote className="tabular-nums">
            {columns
              ? `${num(columns.length)} named of ${num(b.columns.length)}`
              : 'columns omitted — every column'}
          </Footnote>
        </div>

        <Identifier className="text-right text-footnote text-muted-foreground">limit</Identifier>
        <div className={cn('flex items-center gap-2', pinned && 'opacity-45')}>
          <input
            aria-label="Row limit"
            data-testid="download-limit"
            inputMode="numeric"
            disabled={pinned}
            value={limitText}
            onChange={(e) => setLimitText(e.target.value)}
            placeholder="every row"
            className={cn(fieldClass, 'w-[92px] font-mono')}
          />
          <Footnote>
            {trimmedLimit !== '' && parsedLimit == null
              ? 'whole rows only — anything else is not sent'
              : 'rows, from the top of the sheet'}
          </Footnote>
        </div>

        <Identifier className="text-right text-footnote text-muted-foreground">
          filter_expr
        </Identifier>
        <input
          aria-label="Filter expression"
          data-testid="download-filter"
          disabled={pinned}
          value={filterExpr}
          onChange={(e) => setFilterExpr(e.target.value)}
          placeholder="SQL WHERE — plan in ('pro','team')"
          className={cn(fieldClass, 'font-mono', pinned && 'opacity-45')}
        />
      </div>

      {/* What the file will contain, for THIS seat, stated before the click. */}
      <div className="mt-2 flex flex-col gap-1" data-testid="download-manifest">
        <Stat
          name="columns"
          value={num(columnCount)}
          coverage={coverage(columnCount, b.columns.length, 'columns')}
        />
        {sheetRows == null ? (
          <Footnote>
            This sheet reports no row count, so how many rows the file carries is not knowable
            until it arrives.
          </Footnote>
        ) : expr ? (
          <Footnote className="tabular-nums">
            at most <b className="font-semibold text-foreground">{num(rowsIncluded)}</b> of{' '}
            {num(sheetRows)} rows — <Identifier>filter_expr</Identifier> is applied by DuckDB, so
            how many survive it is not known until the file arrives.
          </Footnote>
        ) : (
          <Stat
            name="rows"
            value={num(rowsIncluded)}
            coverage={
              rowLimit == null
                ? complete(sheetRows)
                : coverage(rowsIncluded ?? 0, sheetRows)
            }
          />
        )}
      </div>

      {pinned && (
        <Guard className="mt-1.5">
          <b className="font-semibold text-foreground">v{b.version}</b> is not the current version,
          so this is the per-version route — it accepts{' '}
          <Identifier className="text-foreground">format</Identifier> and{' '}
          <Identifier className="text-foreground">sheet</Identifier> only. The subset, the limit and
          the filter are not sent: the file is the whole sheet at that version.
        </Guard>
      )}

      {b.maskedColumns.length > 0 ? (
        <Guard tone="warning" className="mt-1.5" data-testid="download-gate">
          This seat reads{' '}
          <Identifier className="text-foreground">{b.maskedColumns.join(', ')}</Identifier> masked,
          which means it does not hold raw access here — and a download IS the raw file.{' '}
          <Identifier>ensure_raw_access</Identifier> refuses the whole request rather than writing a
          partly-masked file, and leaving those columns out of{' '}
          <Identifier>columns</Identifier> does not unlock it: the gate is on the dataset, not the
          column.
        </Guard>
      ) : (
        <Guard className="mt-1.5" data-testid="download-gate">
          Nothing is masked for this seat on this sheet, so every column above arrives with its real
          values. A dataset that declares a sensitive column on a sheet you are not looking at is
          still refused — the gate is per dataset.
        </Guard>
      )}

      <Guard className="mt-1">
        The filter tree, the search box and the sort stack are not sent.{' '}
        <Identifier>filter_expr</Identifier> is a SQL <Identifier>WHERE</Identifier> evaluated by
        DuckDB — a different language from QuerySpec — and the route has no{' '}
        <Identifier>ORDER BY</Identifier>, so rows arrive in storage order, not in the order on
        screen.
      </Guard>

      <div className="mt-2 flex items-center gap-2">
        <button
          className={BTN_PRIMARY}
          data-testid="download-run"
          disabled={!b.datasetId || download.isPending}
          onClick={() =>
            b.datasetId &&
            download.mutate({
              datasetId: b.datasetId,
              versionNumber,
              format,
              sheet: b.sheet,
              columns,
              limit: rowLimit,
              filterExpr: expr || null,
            })
          }
        >
          <Download className="size-3" />
          {download.isPending ? 'Preparing…' : `Download ${format}`}
        </button>
        <Footnote>
          {download.isPending
            ? 'The seat travels as a header, so the file comes through the app and is held in memory until the browser saves it.'
            : `v${versionNumber ?? b.version ?? '—'} · ${b.sheet ?? 'no sheet'}`}
        </Footnote>
      </div>

      {isRestricted(download.error) ? (
        <div className="mt-2" data-testid="download-restricted">
          <LensRestricted what="Downloading the raw file" />
        </div>
      ) : download.error ? (
        <div className="mt-2">
          <Refusal code={errorCodeOf(download.error)} testid="download-error">
            {errorText(download.error, {
              notFound: 'No such dataset, version or sheet for this seat.',
            })}
          </Refusal>
        </div>
      ) : null}

      {download.data && !download.isPending && (
        <Footnote className="mt-2" data-testid="download-result">
          Delivered{' '}
          <Identifier className="text-foreground">
            {middleTruncate(download.data.filename, 34)}
          </Identifier>{' '}
          · {formatBytes(download.data.bytes)} · measured off the file, not estimated.
        </Footnote>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------- page */

export function QueryPage({ initialDatasetId = null }: { initialDatasetId?: string | null }) {
  const b = useQuerySpec(initialDatasetId);
  const [viewName, setViewName] = useState('');
  const [pinVersion, setPinVersion] = useState(false);

  // Memoised because `gridColumns` depends on it: a fresh `[]` on every render
  // would rebuild the column list on every keystroke in the builder.
  const rows = useMemo(() => b.page?.items ?? [], [b.page]);
  const matched = b.page?.total ?? null;
  const sheetRow = b.sheets.find((s) => s.name === b.sheet) ?? null;
  const sheetRows = sheetRow?.row_count ?? null;
  const maskedText = b.searchRefusedBy;

  const sortRank = useMemo(() => {
    const m = new Map<string, { rank: number; direction: 'asc' | 'desc' }>();
    b.sort.forEach((s, i) => m.set(s.column, { rank: i + 1, direction: s.direction }));
    return m;
  }, [b.sort]);

  const projected = b.projection;
  const gridColumns = useMemo(() => {
    const declared = projected.filter((name) => b.columns.some((c) => c.name === name));
    if (declared.length > 0) return declared;
    return Object.keys(rows[0] ?? {});
  }, [projected, b.columns, rows]);

  const unsortedCandidates = b.columns.filter(
    (c) => !c.masked && !b.sort.some((s) => s.column === c.name),
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="query-page">
      {/* ---------------------------------------------------------- spec bar */}
      <header className="flex h-14 shrink-0 items-center gap-5 bg-card px-4 shadow-[inset_0_1px_0_rgba(255,255,255,.045),0_1px_0_rgba(0,0,0,.35)]">
        <div className="flex min-w-0 items-center gap-2">
          {b.maskedColumns.length > 0 && (
            <Lock className="size-3 shrink-0 text-[var(--st-warn)]" aria-hidden="true" />
          )}
          <select
            aria-label="Dataset"
            data-testid="dataset-select"
            value={b.datasetId ?? ''}
            onChange={(e) => b.selectDataset(e.target.value)}
            className={cn(controlClass, 'w-[190px]')}
          >
            {b.datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Version"
            data-testid="version-select"
            value={b.version ?? ''}
            onChange={(e) => b.selectVersion(Number(e.target.value))}
            className={cn(controlClass, 'w-[92px]')}
          >
            {b.versions.map((v) => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number}
              </option>
            ))}
          </select>
          <select
            aria-label="Sheet"
            data-testid="sheet-select"
            value={b.sheet ?? ''}
            onChange={(e) => b.selectSheet(e.target.value)}
            className={cn(controlClass, 'w-[140px]')}
          >
            {b.sheets.map((s) => (
              <option key={s.sheet_key} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div className="min-w-0">
          <Eyebrow>Rows in sheet</Eyebrow>
          <Figure size="figure" className="leading-tight">
            {sheetRows == null ? '—' : compact(sheetRows)}
          </Figure>
        </div>

        <div className="min-w-0">
          <Eyebrow>Query</Eyebrow>
          <div className="mt-0.5 flex items-center gap-2.5">
            <Identifier className="text-small font-semibold text-foreground">QuerySpec</Identifier>
            {/* SIGNAL — the version the whole spec is resolved against. */}
            <span
              className="inline-flex items-center gap-1.5 font-mono text-small text-foreground"
              title="Projection, sort keys and every cursor are resolved against this version."
              data-testid="as-of"
            >
              <span
                aria-hidden="true"
                className="size-[5px] rounded-full bg-[var(--sig)] shadow-[0_0_8px_-1px_var(--sig)]"
              />
              as-of <b className="font-semibold">v{b.version ?? '—'}</b>
            </span>
            {b.counts.incomplete > 0 ? (
              <Status kind="warning">{b.counts.incomplete} incomplete</Status>
            ) : (
              <Status kind="good">valid</Status>
            )}
            {b.dirty && <Footnote>unrun changes</Footnote>}
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button className={GHOST} data-testid="reset-spec" onClick={b.resetSpec}>
            Reset
          </button>
          <button
            className={BTN_PRIMARY}
            data-testid="run-query"
            onClick={b.run}
            disabled={!b.sheet}
          >
            Run query
          </button>
        </div>
      </header>

      {/* -------------------------------------------------------- workspace */}
      <div className="flex min-h-0 flex-1">
        {/* ============================== RAIL: search / sort / projection */}
        <aside className="flex w-[300px] shrink-0 flex-col bg-card">
          <div className="flex h-8 shrink-0 items-center gap-2 px-3.5">
            <SectionTitle className="text-small">Query spec</SectionTitle>
            <Identifier className="text-footnote text-muted-foreground">
              {b.counts.conditions + b.sort.length} clauses
            </Identifier>
            <Identifier className="ml-auto text-footnote text-muted-foreground">
              POST /query
            </Identifier>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {/* ---- free-text search */}
            <section className="px-3.5 pt-2 pb-3">
              <SectionHead title="Search" path="spec.search" />
              <div
                className={cn(
                  controlClass,
                  'flex items-center gap-2',
                  maskedText.length > 0 && 'opacity-60',
                )}
              >
                <Search className="size-3 shrink-0 text-muted-foreground" />
                <input
                  data-testid="search-input"
                  value={b.search}
                  disabled={maskedText.length > 0}
                  onChange={(e) => b.setSearch(e.target.value)}
                  placeholder="Substring across all text columns…"
                  className="min-w-0 flex-1 bg-transparent text-small outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
                />
              </div>
              <Footnote className="mt-1.5">
                Case-insensitive substring, OR&apos;d across every text column. Never numbers or
                dates.
              </Footnote>
              {maskedText.length > 0 && (
                <div className="mt-1.5">
                  <Refusal code="400 sensitive-column-not-filterable" testid="search-refusal">
                    Search compiles to <Identifier>icontains</Identifier> over every text column,{' '}
                    <Identifier className="text-foreground">{maskedText.join(', ')}</Identifier>{' '}
                    included — and a matched count is an oracle for the masked value. Disabled here
                    rather than refused after you type.
                  </Refusal>
                </div>
              )}
            </section>

            {/* ---- sort stack */}
            <section className="px-3.5 py-3 shadow-[inset_0_1px_0_var(--r1)]">
              <SectionHead
                title="Sort stack"
                path="spec.sort[]"
                right={
                  <Identifier className="text-footnote text-muted-foreground">
                    {b.sort.length}
                  </Identifier>
                }
              />
              <div className="flex flex-col gap-1" data-testid="sort-stack">
                {b.sort.map((s, i) => (
                  <div
                    key={s.column}
                    data-testid="sort-row"
                    className="flex h-7 items-center gap-1.5 rounded-md bg-muted px-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,.045)]"
                  >
                    <Identifier className="w-3 shrink-0 text-micro font-semibold text-foreground">
                      {i + 1}
                    </Identifier>
                    <Identifier className="min-w-0 flex-1 truncate text-small">
                      {s.column}
                    </Identifier>
                    <Segmented
                      label={`Direction for ${s.column}`}
                      options={['asc', 'desc'] as const}
                      value={s.direction}
                      onChange={(v) => b.setSortDirection(i, v)}
                      testid={`sort-dir-${i}`}
                    />
                    <button
                      aria-label={`Move ${s.column} up`}
                      disabled={i === 0}
                      onClick={() => b.moveSort(i, -1)}
                      className="grid size-5 place-items-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowUp className="size-2.5" />
                    </button>
                    <button
                      aria-label={`Move ${s.column} down`}
                      disabled={i === b.sort.length - 1}
                      onClick={() => b.moveSort(i, 1)}
                      className="grid size-5 place-items-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowDown className="size-2.5" />
                    </button>
                    <button
                      aria-label={`Remove ${s.column} from sort`}
                      data-testid="sort-remove"
                      onClick={() => b.removeSort(i)}
                      className="grid size-5 place-items-center rounded text-muted-foreground hover:text-foreground"
                    >
                      <X className="size-2.5" />
                    </button>
                  </div>
                ))}

                <select
                  aria-label="Add sort column"
                  data-testid="sort-add"
                  value=""
                  onChange={(e) => e.target.value && b.addSort(e.target.value)}
                  className={cn(fieldClass, 'text-muted-foreground')}
                >
                  <option value="">+ add sort column</option>
                  {unsortedCandidates.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} · {kindLabel(c.kind)}
                    </option>
                  ))}
                </select>
              </div>
              <Note>
                An <b className="font-semibold text-foreground">array</b>, applied 1 → n, not one
                header arrow. The server then appends <b className="font-semibold text-foreground">
                  every remaining column ascending</b>, so the order is total — which is what stops
                cursor pages from skipping or duplicating tied rows.
              </Note>
              {b.maskedColumns.length > 0 && (
                <div className="mt-1.5">
                  <Refusal code="400 sensitive-column-not-filterable" testid="sort-refusal">
                    <Identifier className="text-foreground">
                      {b.maskedColumns.join(', ')}
                    </Identifier>{' '}
                    {b.maskedColumns.length === 1 ? 'is' : 'are'} absent from this list and from the
                    filter tree&apos;s column picker. Ordering leaks row identity, so the API
                    refuses it — you can still project the column and read it masked.
                  </Refusal>
                </div>
              )}
            </section>

            {/* ---- projection */}
            <section className="px-3.5 py-3 shadow-[inset_0_1px_0_var(--r1)]">
              <SectionHead
                title="Projection"
                path="spec.columns[]"
                right={
                  <Identifier className="text-footnote text-muted-foreground tabular-nums">
                    {projected.length} / {b.columns.length}
                  </Identifier>
                }
              />
              <div className="mb-1.5 flex items-center gap-1.5">
                <button className={BTN} data-testid="projection-all" onClick={b.selectAllColumns}>
                  Select all
                </button>
                <button
                  className={BTN}
                  data-testid="projection-none"
                  title="An empty `columns` means EVERY column on the wire, so the floor is one."
                  onClick={b.clearColumns}
                >
                  Minimum
                </button>
              </div>
              <div className="flex flex-col gap-px" data-testid="projection-list">
                {b.columns.map((c) => {
                  const on = projected.includes(c.name);
                  return (
                    <label
                      key={c.name}
                      className="flex h-5 cursor-pointer items-center gap-2 rounded px-1 text-small hover:bg-white/[.04]"
                    >
                      <Checkbox
                        checked={on}
                        onCheckedChange={() => b.toggleProjection(c.name)}
                        className="size-3.5"
                        data-testid={`projection-${c.name}`}
                      />
                      <Identifier
                        className={cn(
                          'min-w-0 flex-1 truncate',
                          on ? 'text-foreground' : 'text-muted-foreground',
                        )}
                      >
                        {c.name}
                      </Identifier>
                      {c.masked && <Lock className="size-2.5 shrink-0 text-[var(--st-warn)]" />}
                      <Identifier className="shrink-0 text-footnote text-muted-foreground">
                        {c.masked ? 'masked' : kindLabel(c.kind)}
                      </Identifier>
                      <Identifier className="w-3 shrink-0 text-right text-footnote text-muted-foreground">
                        {on ? projected.indexOf(c.name) + 1 : '—'}
                      </Identifier>
                    </label>
                  );
                })}
              </div>
              <Note>
                This order is the returned order. Filtering an{' '}
                <b className="font-semibold text-foreground">un-projected</b> column is allowed — a
                column can be filtered without being returned.
              </Note>
            </section>

            {/* ---- compiled spec */}
            <section className="px-3.5 py-3 shadow-[inset_0_1px_0_var(--r1)]">
              <SectionHead title="Compiled" path="request body" />
              <pre
                data-testid="compiled-spec"
                className="max-h-56 overflow-auto rounded-md bg-[var(--s0)] p-2 font-mono text-footnote leading-relaxed text-muted-foreground shadow-[inset_0_2px_4px_-2px_rgba(0,0,0,.6)]"
              >
                {JSON.stringify(b.draftSpec, null, 2)}
              </pre>
              {b.counts.incomplete > 0 && (
                <Footnote className="mt-1.5">
                  <Severity level="warning">
                    {b.counts.incomplete} condition{b.counts.incomplete === 1 ? '' : 's'} with no
                    operand
                  </Severity>{' '}
                  — not compiled, so they are not silently matching everything.
                </Footnote>
              )}
            </section>
          </div>
        </aside>

        {/* ============================== CENTRE: tree, results, persistence */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex h-8 shrink-0 items-center gap-2.5 px-4 shadow-[inset_0_-1px_0_var(--r1)]">
            <SectionTitle className="text-small">Filter tree</SectionTitle>
            <Identifier className="text-footnote text-muted-foreground">
              spec.filters · FilterGroup
            </Identifier>
            <Identifier
              className="text-footnote text-muted-foreground tabular-nums"
              data-testid="tree-counts"
            >
              {b.counts.conditions} conditions · {Math.max(0, b.counts.groups - 1)} nested groups ·
              depth {b.counts.depth}
            </Identifier>
          </div>

          <div className="h-[272px] shrink-0 overflow-auto px-4 py-2 shadow-[inset_0_-1px_0_var(--r2)]">
            <GroupBlock b={b} node={b.root} columns={b.columns} depth={0} />
          </div>

          {/* ---- results meta */}
          <div className="flex h-8 shrink-0 items-center gap-3 px-4 text-micro text-muted-foreground shadow-[inset_0_1px_0_var(--r1)]">
            <span className="tabular-nums">
              matched{' '}
              <b className="font-semibold text-foreground" data-testid="matched-total">
                {matched == null ? '—' : matched.toLocaleString()}
              </b>
              {sheetRows != null && <span className="font-mono"> / {sheetRows.toLocaleString()}</span>}
            </span>
            {matched != null && sheetRows != null && sheetRows > 0 && (
              <MagnitudeBar of={coverage(matched, sheetRows)} className="w-20" />
            )}
            <Identifier className="text-footnote">
              sort {b.sort.length} {b.sort.length === 1 ? 'key' : 'keys'} + full-row tiebreak
            </Identifier>
            <Identifier className="text-footnote">
              {projected.length} / {b.columns.length} cols
            </Identifier>
            {b.maskedColumns.length > 0 && (
              <span className="ml-auto flex items-center gap-1.5">
                <Lock className="size-2.5" />
                <Identifier className="text-footnote">
                  {b.maskedColumns.join(', ')} masked for this seat
                </Identifier>
              </span>
            )}
          </div>

          {/* ---- grid */}
          {b.errorMessage && b.errorCode !== 'invalid-cursor' ? (
            <div className="flex min-h-0 flex-1 items-start p-4">
              <Refusal code={b.errorCode ?? 'error'} testid="query-error">
                {b.errorMessage}
              </Refusal>
            </div>
          ) : gridColumns.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center">
              <Footnote>No columns to show yet.</Footnote>
            </div>
          ) : (
            <Table
              containerClassName={cn(b.loading && 'opacity-60')}
              className="w-max min-w-full"
              data-testid="result-grid"
            >
              <TableHeader>
                <TableRow>
                  <TableHead className="sticky left-0 z-20 w-12 bg-card text-right">#</TableHead>
                  {gridColumns.map((name) => {
                    const rank = sortRank.get(name);
                    return (
                      <TableHead key={name} data-column={name} className="whitespace-nowrap">
                        <span className="flex items-center gap-1">
                          {name}
                          {b.maskedColumns.includes(name) && (
                            <Lock className="size-2.5 text-muted-foreground/70" />
                          )}
                          {rank && (
                            <Identifier
                              className="text-footnote text-foreground"
                              title={`sort key ${rank.rank}, ${rank.direction}ending`}
                            >
                              {rank.rank}
                              {rank.direction === 'asc' ? '▲' : '▼'}
                            </Identifier>
                          )}
                        </span>
                      </TableHead>
                    );
                  })}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, i) => (
                  <TableRow key={i}>
                    <TableCell className="sticky left-0 z-10 w-12 bg-background text-right text-micro text-muted-foreground tabular-nums">
                      {b.rowOffset + i + 1}
                    </TableCell>
                    {gridColumns.map((name) => {
                      const v = (row as Record<string, unknown>)[name];
                      const isMasked = b.maskedColumns.includes(name);
                      const numeric = typeof v === 'number';
                      const text = v === null || v === undefined ? null : String(v);
                      return (
                        <TableCell
                          key={name}
                          data-testid="cell"
                          title={text ?? undefined}
                          className={cn(
                            'h-7 max-w-[280px] truncate whitespace-nowrap',
                            numeric && 'text-right font-mono tabular-nums text-foreground',
                          )}
                        >
                          {text === null ? (
                            <span className="text-muted-foreground/50">—</span>
                          ) : isMasked ? (
                            // Masked PII gets the recessed-well treatment the
                            // material calls for — it reads as withheld, not as
                            // a value that happens to look like dots.
                            <span className="rounded bg-black/35 px-1.5 py-0.5 font-mono tracking-[.06em] text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,.5)]">
                              {text}
                            </span>
                          ) : numeric ? (
                            num(v)
                          ) : (
                            middleTruncate(text, 44)
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {/* ---- cursor conflict recovery */}
          {b.conflict && (
            <div
              data-testid="cursor-conflict"
              className="flex shrink-0 items-start gap-2.5 bg-destructive/8 px-4 py-1.5 shadow-[inset_0_1px_0_rgba(220,80,80,.28),inset_0_-1px_0_rgba(220,80,80,.28)]"
            >
              <span
                className="mt-1 size-[7px] shrink-0 rounded-[1px] bg-destructive"
                aria-hidden="true"
              />
              <Identifier className="mt-px shrink-0 rounded bg-black/30 px-1.5 text-micro font-semibold text-destructive ring-1 ring-destructive/35">
                400 invalid-cursor
              </Identifier>
              <Footnote className="min-w-0">
                {b.conflict.reason === 'spec-change' ? (
                  <>
                    Spec changed mid-page —{' '}
                    <Identifier className="text-foreground">{b.conflict.changed}</Identifier>. A
                    cursor is signed against the version and a hash of the spec, so it cannot be
                    replayed against a new one. Page{' '}
                    <b className="font-semibold text-foreground">
                      {b.conflict.previousPageIndex + 1}
                    </b>{' '}
                    was released to avoid the refusal.
                  </>
                ) : (
                  <>
                    The API refused this cursor — {b.conflict.changed} no longer matches what it was
                    signed against.
                  </>
                )}
              </Footnote>
              <span className="ml-auto flex shrink-0 items-center gap-1.5">
                {b.conflict.reason === 'spec-change' && (
                  <button className={BTN} data-testid="conflict-undo" onClick={b.undoSpecChange}>
                    Undo spec change
                  </button>
                )}
                <button
                  className={cn(BTN, 'bg-primary font-semibold text-primary-foreground ring-0')}
                  data-testid="conflict-restart"
                  onClick={b.restartFromFirstPage}
                >
                  Restart from first page
                </button>
              </span>
            </div>
          )}

          {/* ---- the honest pager */}
          <div className="shrink-0 px-4 py-1.5 shadow-[inset_0_1px_0_var(--r1)]">
            <div className="flex items-center gap-2.5 text-small text-muted-foreground">
              <PagerButton
                direction="prev"
                onClick={b.prevPage}
                disabled={!b.canPrev}
                testid="pager-prev"
              />
              <Footnote>
                back stack <b className="font-semibold text-foreground">{b.pageIndex}</b>
              </Footnote>
              <span className="h-3.5 w-px bg-[var(--r2)]" />
              <span className="tabular-nums" data-testid="pager-range">
                rows{' '}
                <b className="font-semibold text-foreground">
                  {rows.length === 0
                    ? '0'
                    : `${(b.rowOffset + 1).toLocaleString()}–${(b.rowOffset + rows.length).toLocaleString()}`}
                </b>{' '}
                of{' '}
                <b className="font-semibold text-foreground">
                  {matched == null ? '—' : matched.toLocaleString()}
                </b>
              </span>
              <Footnote>
                — <Identifier className="text-foreground">total</Identifier> is returned, so the
                count is honest
              </Footnote>
              <span className="ml-auto flex items-center gap-2">
                <Segmented
                  label="Page size"
                  options={PAGE_SIZES as readonly number[]}
                  value={b.limit}
                  onChange={b.setLimit}
                  testid="page-size"
                />
                <PagerButton
                  direction="next"
                  onClick={b.nextPage}
                  disabled={!b.nextCursor}
                  testid="pager-next"
                />
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2.5">
              <Footnote className="font-mono">next_cursor</Footnote>
              <Identifier
                data-testid="next-cursor"
                title="Opaque and server-signed: it carries the version id and a sha256 of the spec."
                className="w-[260px] truncate rounded bg-[var(--s0)] px-1.5 py-0.5 text-footnote text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,.4)]"
              >
                {b.nextCursor ?? 'null — last page'}
              </Identifier>
              <button
                aria-label="Copy cursor"
                disabled={!b.nextCursor}
                onClick={() => b.nextCursor && void navigator.clipboard?.writeText(b.nextCursor)}
                className="grid size-5 place-items-center rounded text-muted-foreground hover:text-foreground disabled:opacity-30"
              >
                <Copy className="size-2.5" />
              </button>
              <Footnote className="ml-auto">
                no page jumps —{' '}
                <span className="line-through decoration-[var(--t4)]">‹ 1 2 3 … 2,329 ›</span> is
                unbuildable on a cursor API
              </Footnote>
            </div>
          </div>

          {/* ---- persist: save as view, and the one raw-egress path.
               Fixed height with each half scrolling itself, so the download
               panel can say everything it has to say without eating the grid. */}
          <div className="grid h-[214px] shrink-0 grid-cols-2 gap-7 bg-card px-4 py-2.5 shadow-[inset_0_1px_0_var(--r2)]">
            <section className="min-w-0 overflow-auto">
              <SectionHead title="Save as view" path="POST /datasets/{id}/views" />
              <div className="flex items-center gap-2">
                <input
                  aria-label="View name"
                  data-testid="view-name"
                  value={viewName}
                  onChange={(e) => setViewName(e.target.value)}
                  placeholder="e.g. Paid signups — active or recent"
                  className={cn(controlClass, 'max-w-[340px] flex-1')}
                />
                <Segmented
                  label="Version selector"
                  options={['latest', 'pinned'] as const}
                  value={pinVersion ? 'pinned' : 'latest'}
                  onChange={(v) => setPinVersion(v === 'pinned')}
                  testid="view-version"
                />
                <button
                  className={BTN_PRIMARY}
                  data-testid="save-view"
                  disabled={!viewName.trim() || !b.datasetId || !b.sheet || b.savingView}
                  onClick={() =>
                    b.saveView({ name: viewName.trim(), description: null, pinVersion })
                  }
                >
                  {b.savingView ? 'Saving…' : 'Save view'}
                </button>
              </div>
              <Note>
                A view stores the whole{' '}
                <b className="font-semibold text-foreground">QuerySpec</b> — tree, sort array,
                projection, search — and{' '}
                <b className="font-semibold text-foreground">no cursor</b>: every open starts at the
                first page.{' '}
                {pinVersion
                  ? `Pinned to v${b.version ?? '—'}, so data and spec are both frozen.`
                  : 'Always latest re-runs against whichever version is current.'}
              </Note>
            </section>

            <DownloadPanel b={b} />
          </div>
        </main>

        {/* ============================== DOCK: the 36-operator palette */}
        <aside className="flex w-[368px] shrink-0 flex-col bg-card">
          <div className="flex h-8 shrink-0 items-center gap-2 px-3.5">
            <SectionTitle className="text-small">Operators</SectionTitle>
            <Identifier className="text-footnote text-muted-foreground">
              {OPERATORS.length} in the DSL
            </Identifier>
            <Identifier className="ml-auto text-footnote text-muted-foreground">
              Filter.op
            </Identifier>
          </div>
          <OperatorPalette b={b} />
        </aside>
      </div>
    </div>
  );
}
