/**
 * The QuerySpec builder's state machine.
 *
 * Everything the query page can express lives here as ONE piece of state, and
 * the compiled `QuerySpec` is derived from it. That direction matters: the JSON
 * on screen is not a second model kept in sync with the controls, it is the
 * controls, compiled. There is no way for the preview and the request to
 * disagree.
 *
 * Four backend facts shape this file, and each is enforced rather than
 * documented:
 *
 *  1. **The filter DSL has exactly 36 operators.** `OPERATORS` below is derived
 *     from `Filter.op` in `openapi.json`; `FilterOp` is that enum, so adding an
 *     operator the server does not have (or dropping one it does) is a compile
 *     error, not a 400 the user discovers. `istartswith`, `this_month` and
 *     `outlier` — which some design drafts show — do not exist.
 *
 *  2. **Operator/type pairing is a server rule, so it is mirrored exactly.**
 *     `app/shared/query/validate.py` refuses `STRING_OPS` (the six text matchers
 *     plus the six length ops) on a non-text column and `DATE_OPS` on a
 *     non-temporal one, with `400 operator-type-mismatch`. Everything else —
 *     including `gt`/`lt`, which DuckDB happily evaluates on VARCHAR, and the
 *     four rank operators — is allowed on ANY column. So the palette greys 16
 *     of 36 for a numeric column, 12 for a date column and 4 for text. Greying
 *     more than that would be inventing a refusal the API does not make.
 *
 *  3. **Filtering, sorting or searching a MASKED column is refused** with
 *     `400 sensitive-column-not-filterable`, because `total` is a COUNT over
 *     the same WHERE clause that masking never touches — a steerable count is a
 *     binary search over the hidden value. Those columns are removed from the
 *     filter and sort pickers here, and `search` is disabled outright when a
 *     masked column is text (search compiles to icontains over every text
 *     column, masked ones included). Projection stays allowed: the values come
 *     back masked.
 *
 *  4. **A cursor is bound to (version, spec).** Replaying it against a changed
 *     one returns `400 invalid-cursor`. Rather than let that happen silently,
 *     committing a changed spec while paged past the first page records a
 *     `conflict` and rewinds to page 1 — and if the server ever returns
 *     `invalid-cursor` anyway (a new version landing under a held cursor), the
 *     same record appears. Both carry the same two exits: undo the spec change
 *     and keep your place, or keep the change and restart.
 */

import { useCallback, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { AnalyticsApiError, analytics, errorText } from '@/shared/lib/analyticsClient';
import type { components } from '@/shared/lib/analyticsSchema';
import {
  useDatasetCatalog,
  useRows,
  useSheets,
  useVersions,
  type DatasetInfo,
  type QueryPage,
  type QuerySpec,
  type SheetSummary,
  type VersionSummary,
} from './useDatasets';

type FilterDto = components['schemas']['Filter'];
type FilterGroupDto = components['schemas']['FilterGroup'];
type SortDto = components['schemas']['Sort'];

/** The operator vocabulary, straight off `Filter.op`. Exactly 36 members. */
export type FilterOp = FilterDto['op'];

/* ------------------------------------------------------------- operators */

export type OpGroupId =
  | 'null'
  | 'comparison'
  | 'text'
  | 'length'
  | 'rank'
  | 'date'
  | 'cardinality';

/** What the server demands of the column's dtype. Mirrors `validate.py`. */
export type OpRequires = 'any' | 'text' | 'date';

/** How many operands the operator takes, and how they are typed. */
export type OpArity = 'none' | 'scalar' | 'pair' | 'list';

export interface OperatorMeta {
  op: FilterOp;
  group: OpGroupId;
  requires: OpRequires;
  arity: OpArity;
  /** Input mode for the value editor. */
  input: 'text' | 'number' | 'date';
  /** `case_sensitive` is read for these and ignored everywhere else. */
  caseSensitive: boolean;
  /** One footnote-register line explaining the operand. */
  hint: string;
}

/**
 * All 36. Ordered by group so the palette can render them in reading order
 * without a second sort.
 */
export const OPERATORS: readonly OperatorMeta[] = [
  // ---- null / blank (4) — value-free, legal on every dtype
  { op: 'is_null', group: 'null', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'no value stored' },
  { op: 'is_not_null', group: 'null', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'any value stored' },
  { op: 'is_empty', group: 'null', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'null OR the empty string' },
  { op: 'is_not_empty', group: 'null', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'not null AND not empty' },

  // ---- comparison / set / range (10)
  { op: 'eq', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'single value' },
  { op: 'neq', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'single value' },
  { op: 'gt', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'strictly greater' },
  { op: 'gte', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'greater or equal' },
  { op: 'lt', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'strictly less' },
  { op: 'lte', group: 'comparison', requires: 'any', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'less or equal' },
  { op: 'in', group: 'comparison', requires: 'any', arity: 'list', input: 'text', caseSensitive: false, hint: 'non-empty list' },
  { op: 'not_in', group: 'comparison', requires: 'any', arity: 'list', input: 'text', caseSensitive: false, hint: 'non-empty list' },
  { op: 'between', group: 'comparison', requires: 'any', arity: 'pair', input: 'text', caseSensitive: false, hint: '[low, high] · inclusive' },
  { op: 'not_between', group: 'comparison', requires: 'any', arity: 'pair', input: 'text', caseSensitive: false, hint: 'outside [low, high]' },

  // ---- text matching (6) — server: STRING_OPS require a text column
  { op: 'contains', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: true, hint: 'substring · LIKE %v%' },
  { op: 'icontains', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: false, hint: 'substring · always case-insensitive' },
  { op: 'not_contains', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: true, hint: 'substring absent' },
  { op: 'starts_with', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: true, hint: 'prefix' },
  { op: 'ends_with', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: true, hint: 'suffix' },
  { op: 'regex', group: 'text', requires: 'text', arity: 'scalar', input: 'text', caseSensitive: true, hint: 'regexp_matches — RE2 syntax' },

  // ---- length (6) — also STRING_OPS
  { op: 'len_eq', group: 'length', requires: 'text', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'character count' },
  { op: 'len_gt', group: 'length', requires: 'text', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'character count' },
  { op: 'len_gte', group: 'length', requires: 'text', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'character count' },
  { op: 'len_lt', group: 'length', requires: 'text', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'character count' },
  { op: 'len_lte', group: 'length', requires: 'text', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'character count' },
  { op: 'len_between', group: 'length', requires: 'text', arity: 'pair', input: 'number', caseSensitive: false, hint: '[min_len, max_len]' },

  // ---- rank (4) — allowed on any dtype; DuckDB orders VARCHAR too
  { op: 'top_n', group: 'rank', requires: 'any', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'n distinct values, descending' },
  { op: 'bottom_n', group: 'rank', requires: 'any', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'n distinct values, ascending' },
  { op: 'top_pct', group: 'rank', requires: 'any', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'fraction 0–1, e.g. 0.05' },
  { op: 'bottom_pct', group: 'rank', requires: 'any', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'fraction 0–1, e.g. 0.05' },

  // ---- date (4) — server: DATE_OPS require DATE/TIMESTAMP
  { op: 'date_before', group: 'date', requires: 'date', arity: 'scalar', input: 'date', caseSensitive: false, hint: 'exclusive' },
  { op: 'date_after', group: 'date', requires: 'date', arity: 'scalar', input: 'date', caseSensitive: false, hint: 'exclusive' },
  { op: 'date_between', group: 'date', requires: 'date', arity: 'pair', input: 'date', caseSensitive: false, hint: '[start, end] · inclusive' },
  { op: 'last_n_days', group: 'date', requires: 'date', arity: 'scalar', input: 'number', caseSensitive: false, hint: 'relative to CURRENT_DATE' },

  // ---- cardinality (2)
  { op: 'is_duplicate', group: 'cardinality', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'value occurs more than once' },
  { op: 'is_unique', group: 'cardinality', requires: 'any', arity: 'none', input: 'text', caseSensitive: false, hint: 'value occurs exactly once' },
];

/** Group order and their sentence-case titles. */
export const OP_GROUPS: readonly { id: OpGroupId; title: string }[] = [
  { id: 'null', title: 'Null and blank' },
  { id: 'comparison', title: 'Comparison, set and range' },
  { id: 'text', title: 'Text matching' },
  { id: 'length', title: 'Length' },
  { id: 'rank', title: 'Rank' },
  { id: 'date', title: 'Date' },
  { id: 'cardinality', title: 'Cardinality' },
];

const OP_BY_NAME = new Map(OPERATORS.map((o) => [o.op, o]));

export function operatorMeta(op: FilterOp): OperatorMeta {
  // Every FilterOp is in OPERATORS by construction; the fallback keeps this
  // total rather than asserting.
  return OP_BY_NAME.get(op) ?? OPERATORS[0];
}

/* ------------------------------------------------------------- columns */

export type ColumnKind = 'text' | 'number' | 'date' | 'bool' | 'unknown';

export interface QueryColumn {
  name: string;
  dtype: string | null;
  kind: ColumnKind;
  /** Masked for this seat, so it may be projected but never computed over. */
  masked: boolean;
}

/**
 * Classify a dtype the way the server does. `_is_text` matches VARCHAR/CHAR/
 * TEXT/STRING and `_is_temporal` matches DATE/TIMESTAMP — everything else is
 * neither, INCLUDING an absent dtype, which is why `unknown` is not treated as
 * permissive here. Being optimistic would show operators the server refuses.
 */
export function classifyDtype(dtype: string | null | undefined): ColumnKind {
  const d = (dtype ?? '').toUpperCase();
  if (!d) return 'unknown';
  if (d.startsWith('VARCHAR') || d.startsWith('CHAR') || d.startsWith('TEXT') || d.startsWith('STRING'))
    return 'text';
  if (d.startsWith('DATE') || d.startsWith('TIMESTAMP') || d.startsWith('TIME')) return 'date';
  if (d.startsWith('BOOL')) return 'bool';
  if (/^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|REAL|DECIMAL|NUMERIC)/.test(d))
    return 'number';
  return 'unknown';
}

/** Short kind label for a column chip — lowercase, mono, three or four chars. */
export function kindLabel(kind: ColumnKind): string {
  return kind === 'number' ? 'num' : kind;
}

/**
 * Whether the server will accept this operator on this column kind, and why
 * not when it will not. The reason is the dtype family the operator demands,
 * rendered as-is beside the greyed name — it has to be legible to be useful.
 */
export function operatorApplies(meta: OperatorMeta, kind: ColumnKind): boolean {
  if (meta.requires === 'any') return true;
  if (meta.requires === 'text') return kind === 'text';
  return kind === 'date';
}

/* --------------------------------------------------------------- the tree */

export interface ConditionNode {
  kind: 'condition';
  id: string;
  column: string;
  op: FilterOp;
  /** First operand, or the only one. Kept as text; coerced when compiled. */
  value: string;
  /** Second operand for `pair` operators. */
  value2: string;
  /** Operands for `list` operators. */
  values: string[];
  caseSensitive: boolean;
}

export interface GroupNode {
  kind: 'group';
  id: string;
  logic: 'and' | 'or';
  children: TreeNode[];
}

export type TreeNode = ConditionNode | GroupNode;

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${seq}`;
}

function newCondition(column: string, kind: ColumnKind): ConditionNode {
  // Seed with an operator the column can actually take, so a fresh row is
  // never born invalid.
  const op: FilterOp = kind === 'date' ? 'date_after' : 'eq';
  return {
    kind: 'condition',
    id: nextId('cond'),
    column,
    op,
    value: '',
    value2: '',
    values: [],
    caseSensitive: true,
  };
}

function newGroup(logic: 'and' | 'or' = 'or'): GroupNode {
  return { kind: 'group', id: nextId('grp'), logic, children: [] };
}

/** Structural edit helper — returns a new tree with `fn` applied to `id`. */
function mapNode(node: TreeNode, id: string, fn: (n: TreeNode) => TreeNode): TreeNode {
  if (node.id === id) return fn(node);
  if (node.kind !== 'group') return node;
  return { ...node, children: node.children.map((c) => mapNode(c, id, fn)) };
}

function removeFrom(node: GroupNode, id: string): GroupNode {
  return {
    ...node,
    children: node.children
      .filter((c) => c.id !== id)
      .map((c) => (c.kind === 'group' ? removeFrom(c, id) : c)),
  };
}

/** Splice a group's children into its parent, deleting the group itself. */
function ungroupIn(node: GroupNode, id: string): GroupNode {
  const children: TreeNode[] = [];
  for (const c of node.children) {
    if (c.kind === 'group' && c.id === id) children.push(...c.children);
    else if (c.kind === 'group') children.push(ungroupIn(c, id));
    else children.push(c);
  }
  return { ...node, children };
}

export interface TreeCounts {
  conditions: number;
  groups: number;
  depth: number;
  /** Conditions whose operands are not filled in — they are NOT compiled. */
  incomplete: number;
  /** Conditions naming a column this seat may only read masked. */
  masked: number;
}

function countTree(node: TreeNode, depth: number, masked: Set<string>, acc: TreeCounts): void {
  if (node.kind === 'group') {
    acc.groups += 1;
    acc.depth = Math.max(acc.depth, depth);
    node.children.forEach((c) => countTree(c, depth + 1, masked, acc));
    return;
  }
  acc.conditions += 1;
  if (!conditionIsComplete(node)) acc.incomplete += 1;
  if (masked.has(node.column)) acc.masked += 1;
}

/** A condition compiles only when its operator's operands are all present. */
export function conditionIsComplete(c: ConditionNode): boolean {
  const meta = operatorMeta(c.op);
  if (meta.arity === 'none') return Boolean(c.column);
  if (meta.arity === 'list') return Boolean(c.column) && c.values.length > 0;
  if (meta.arity === 'pair') return Boolean(c.column) && c.value !== '' && c.value2 !== '';
  return Boolean(c.column) && c.value !== '';
}

/**
 * Coerce a typed-in operand to the JSON type the server expects.
 *
 * `_num` on the server raises `400 invalid-filter-value` for the numeric
 * operators, and a comparison against a numeric column with a string operand
 * is a silent mismatch in DuckDB — so the coercion is decided by the operator
 * first and the column's dtype second, never left to the wire format of an
 * `<input>`.
 */
function coerce(raw: string, meta: OperatorMeta, kind: ColumnKind): string | number {
  if (meta.input === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (meta.input === 'date') return raw;
  if (kind === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) && raw.trim() !== '' ? n : raw;
  }
  return raw;
}

function compileCondition(c: ConditionNode, kindOf: (name: string) => ColumnKind): FilterDto | null {
  if (!conditionIsComplete(c)) return null;
  const meta = operatorMeta(c.op);
  const kind = kindOf(c.column);
  const base = { column: c.column, case_sensitive: meta.caseSensitive ? c.caseSensitive : true };
  if (meta.arity === 'none') return { ...base, op: c.op };
  if (meta.arity === 'list')
    return { ...base, op: c.op, value: c.values.map((v) => coerce(v, meta, kind)) };
  if (meta.arity === 'pair')
    return { ...base, op: c.op, value: [coerce(c.value, meta, kind), coerce(c.value2, meta, kind)] };
  return { ...base, op: c.op, value: coerce(c.value, meta, kind) };
}

function compileGroup(
  g: GroupNode,
  kindOf: (name: string) => ColumnKind,
): FilterGroupDto | null {
  const conditions: (FilterDto | FilterGroupDto)[] = [];
  for (const child of g.children) {
    const compiled =
      child.kind === 'group' ? compileGroup(child, kindOf) : compileCondition(child, kindOf);
    if (compiled) conditions.push(compiled);
  }
  // An empty group is legal on the wire but means "no filtering", so dropping
  // it is the same query with less noise in the preview.
  if (conditions.length === 0) return null;
  return { logic: g.logic, conditions };
}

/* ---------------------------------------------------------------- sorting */

export interface SortEntry {
  column: string;
  direction: 'asc' | 'desc';
}

/* --------------------------------------------------------------- conflicts */

export interface CursorConflict {
  /** `spec-change` was caught here; `invalid-cursor` came back from the API. */
  reason: 'spec-change' | 'invalid-cursor';
  /** What changed, in the spec's own vocabulary — `sort`, `filters`, … */
  changed: string;
  previousSpec: QuerySpec;
  previousCursors: (string | null)[];
  previousPageIndex: number;
}

/**
 * Which parts of a spec a cursor is actually bound to.
 *
 * Not a guess: `spec_hash` on the server is a sha256 over the spec's canonical
 * JSON **excluding `cursor` and `limit`**, and the cursor carries that hash
 * plus the version id. So page size is deliberately absent here — changing it
 * does not invalidate a cursor, and claiming otherwise would make the UI refuse
 * something the API accepts.
 */
function cursorBoundKeys(a: QuerySpec, b: QuerySpec): string[] {
  const keys: (keyof QuerySpec)[] = ['columns', 'filters', 'sort', 'search'];
  return keys
    .filter((k) => JSON.stringify(a[k] ?? null) !== JSON.stringify(b[k] ?? null))
    .map(String);
}

/* ------------------------------------------------------------------- hook */

export const PAGE_SIZES = [25, 50, 100] as const;

export interface SaveViewVars {
  name: string;
  description?: string | null;
  pinVersion: boolean;
}

export interface QueryBuilder {
  /* scope */
  datasets: DatasetInfo[];
  dataset: DatasetInfo | null;
  datasetId: string | null;
  selectDataset: (id: string) => void;
  versions: VersionSummary[];
  version: number | null;
  selectVersion: (v: number) => void;
  sheets: SheetSummary[];
  sheet: string | null;
  selectSheet: (s: string) => void;
  columns: QueryColumn[];
  maskedColumns: string[];

  /* filter tree */
  root: GroupNode;
  counts: TreeCounts;
  selectedConditionId: string | null;
  selectCondition: (id: string | null) => void;
  addCondition: (parentId: string) => void;
  addGroup: (parentId: string) => void;
  removeNode: (id: string) => void;
  ungroup: (id: string) => void;
  setLogic: (id: string, logic: 'and' | 'or') => void;
  updateCondition: (id: string, patch: Partial<Omit<ConditionNode, 'kind' | 'id'>>) => void;

  /* sort */
  sort: SortEntry[];
  addSort: (column: string) => void;
  removeSort: (index: number) => void;
  setSortDirection: (index: number, direction: 'asc' | 'desc') => void;
  moveSort: (index: number, delta: number) => void;

  /* projection */
  projection: string[];
  toggleProjection: (column: string) => void;
  selectAllColumns: () => void;
  clearColumns: () => void;

  /* search */
  search: string;
  setSearch: (v: string) => void;
  searchRefusedBy: string[];

  /* spec + run */
  draftSpec: QuerySpec;
  runSpec: QuerySpec;
  dirty: boolean;
  run: () => void;
  resetSpec: () => void;

  /* paging */
  limit: number;
  setLimit: (n: number) => void;
  pageIndex: number;
  rowOffset: number;
  nextPage: () => void;
  prevPage: () => void;
  canPrev: boolean;
  nextCursor: string | null;

  /* results */
  page: QueryPage | undefined;
  loading: boolean;
  errorMessage: string | null;
  errorCode: string | null;

  /* cursor recovery */
  conflict: CursorConflict | null;
  undoSpecChange: () => void;
  restartFromFirstPage: () => void;

  /* persist */
  saveView: (vars: SaveViewVars) => void;
  savingView: boolean;
}

export function useQuerySpec(initialDatasetId: string | null): QueryBuilder {
  const [pickedId, setPickedId] = useState<string | null>(initialDatasetId);
  const [pickedVersion, setPickedVersion] = useState<number | null>(null);
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);

  const [root, setRoot] = useState<GroupNode>(() => ({
    kind: 'group',
    id: 'root',
    logic: 'and',
    children: [],
  }));
  const [selectedConditionId, setSelectedConditionId] = useState<string | null>(null);
  const [sort, setSort] = useState<SortEntry[]>([]);
  // `null` means "every column" — the API's own encoding, kept rather than a
  // client-side "all selected" flag that would have to be synced with the list.
  const [projection, setProjection] = useState<string[] | null>(null);
  const [search, setSearch] = useState('');
  const [limit, setLimitState] = useState<number>(50);

  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [conflict, setConflict] = useState<CursorConflict | null>(null);

  /* ---- scope resolution (same shape as DatasetsPage: preferences, resolved) */

  const catalog = useDatasetCatalog({});
  const datasets = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const datasetId = pickedId ?? datasets[0]?.id ?? null;
  const dataset = datasets.find((d) => d.id === datasetId) ?? null;

  const versionsQuery = useVersions(datasetId);
  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data]);
  const version =
    pickedVersion != null && versions.some((v) => v.version_number === pickedVersion)
      ? pickedVersion
      : versions.length > 0
        ? Math.max(...versions.map((v) => v.version_number))
        : null;

  const sheetsQuery = useSheets(datasetId, version);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);
  const sheet =
    pickedSheet && sheets.some((s) => s.name === pickedSheet)
      ? pickedSheet
      : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;

  /* ---- the committed spec, and the draft being edited beside it */

  const [runSpec, setRunSpec] = useState<QuerySpec>({ limit: 50 });

  const rowsQuery = useRows(
    datasetId,
    version,
    sheet,
    runSpec,
    cursors[pageIndex] ?? null,
    Boolean(sheet),
  );

  const page = rowsQuery.data;
  const maskedColumns = useMemo(() => page?.masked_columns ?? [], [page]);
  const maskedSet = useMemo(() => new Set(maskedColumns), [maskedColumns]);

  const columns = useMemo<QueryColumn[]>(() => {
    const declared = sheetRow?.columns ?? [];
    const names = declared.length
      ? declared.map((c) => ({ name: c.name, dtype: c.dtype ?? null }))
      : Object.keys(page?.items?.[0] ?? {}).map((name) => ({ name, dtype: null }));
    return names.map(({ name, dtype }) => ({
      name,
      dtype,
      kind: classifyDtype(dtype),
      masked: maskedSet.has(name),
    }));
  }, [sheetRow, page, maskedSet]);

  const kindOf = useCallback(
    (name: string): ColumnKind => columns.find((c) => c.name === name)?.kind ?? 'unknown',
    [columns],
  );

  /**
   * `search` compiles to icontains over EVERY text column, masked ones
   * included, so a single masked text column poisons the whole search. Name
   * the columns rather than just disabling the box.
   */
  const searchRefusedBy = useMemo(
    () => columns.filter((c) => c.masked && c.kind === 'text').map((c) => c.name),
    [columns],
  );

  /* ---- compile */

  const counts = useMemo<TreeCounts>(() => {
    const acc: TreeCounts = { conditions: 0, groups: 0, depth: 0, incomplete: 0, masked: 0 };
    countTree(root, 0, maskedSet, acc);
    return acc;
  }, [root, maskedSet]);

  const draftSpec = useMemo<QuerySpec>(() => {
    const filters = compileGroup(root, kindOf);
    const spec: QuerySpec = { limit };
    if (projection && projection.length > 0) spec.columns = projection;
    if (filters) spec.filters = filters;
    if (sort.length > 0) spec.sort = sort.map((s): SortDto => ({ ...s }));
    const q = search.trim();
    if (q && searchRefusedBy.length === 0) spec.search = q;
    return spec;
  }, [root, kindOf, projection, sort, search, limit, searchRefusedBy]);

  const dirty = JSON.stringify(draftSpec) !== JSON.stringify(runSpec);

  /* ---- run + cursor recovery */

  const run = useCallback(() => {
    const changed = cursorBoundKeys(draftSpec, runSpec);
    if (changed.length === 0) {
      // Nothing cursor-bound moved; re-running in place is safe.
      void rowsQuery.refetch();
      return;
    }
    if (pageIndex > 0) {
      // The held cursor encodes the last row's sort key under the OLD spec.
      // Sending it would be `400 invalid-cursor`; rewind and say so, with both
      // exits offered rather than a silent jump back to page 1.
      setConflict({
        reason: 'spec-change',
        changed: changed.join(', '),
        previousSpec: runSpec,
        previousCursors: cursors,
        previousPageIndex: pageIndex,
      });
    }
    setRunSpec(draftSpec);
    setCursors([null]);
    setPageIndex(0);
  }, [draftSpec, runSpec, pageIndex, cursors, rowsQuery]);

  const undoSpecChange = useCallback(() => {
    if (!conflict) return;
    setRunSpec(conflict.previousSpec);
    setCursors(conflict.previousCursors);
    setPageIndex(conflict.previousPageIndex);
    setConflict(null);
  }, [conflict]);

  const restartFromFirstPage = useCallback(() => {
    setCursors([null]);
    setPageIndex(0);
    setConflict(null);
  }, []);

  /* ---- errors */

  const rawError = versionsQuery.error ?? sheetsQuery.error ?? rowsQuery.error;
  const errorCode = rawError instanceof AnalyticsApiError ? rawError.code : null;
  const errorMessage = rawError
    ? errorText(rawError, {
        notFound: 'This dataset has no readable version, or is not available to this seat.',
      })
    : null;

  // A server-side `invalid-cursor` — a new version landing under a held cursor
  // is the realistic path — gets the same recovery affordance.
  if (errorCode === 'invalid-cursor' && !conflict && pageIndex > 0) {
    setConflict({
      reason: 'invalid-cursor',
      changed: 'the version or spec this cursor was minted against',
      previousSpec: runSpec,
      previousCursors: cursors,
      previousPageIndex: pageIndex,
    });
  }

  /* ---- paging */

  const nextPage = useCallback(() => {
    const next = rowsQuery.data?.next_cursor;
    if (!next) return;
    setCursors((cs) => [...cs.slice(0, pageIndex + 1), next]);
    setPageIndex((i) => i + 1);
  }, [rowsQuery.data, pageIndex]);

  const prevPage = useCallback(() => setPageIndex((i) => Math.max(0, i - 1)), []);

  const resetPaging = useCallback(() => {
    setCursors([null]);
    setPageIndex(0);
    setConflict(null);
  }, []);

  /* ---- scope setters (every one invalidates the cursor) */

  const selectDataset = useCallback(
    (id: string) => {
      setPickedId(id);
      setPickedVersion(null);
      setPickedSheet(null);
      setRoot({ kind: 'group', id: 'root', logic: 'and', children: [] });
      setSort([]);
      setProjection(null);
      setSearch('');
      setSelectedConditionId(null);
      setRunSpec({ limit });
      resetPaging();
    },
    [limit, resetPaging],
  );

  const selectVersion = useCallback(
    (v: number) => {
      setPickedVersion(v);
      setPickedSheet(null);
      resetPaging();
    },
    [resetPaging],
  );

  const selectSheet = useCallback(
    (s: string) => {
      setPickedSheet(s);
      // Columns change with the sheet, so a filter tree written against the
      // previous one would be `400 unknown-column` on the next run.
      setRoot({ kind: 'group', id: 'root', logic: 'and', children: [] });
      setSort([]);
      setProjection(null);
      setSelectedConditionId(null);
      resetPaging();
    },
    [resetPaging],
  );

  /* ---- tree edits */

  const addCondition = useCallback(
    (parentId: string) => {
      const first = columns.find((c) => !c.masked);
      if (!first) return;
      const node = newCondition(first.name, first.kind);
      setRoot(
        (r) =>
          mapNode(r, parentId, (n) =>
            n.kind === 'group' ? { ...n, children: [...n.children, node] } : n,
          ) as GroupNode,
      );
      setSelectedConditionId(node.id);
    },
    [columns],
  );

  const addGroup = useCallback((parentId: string) => {
    const g = newGroup('or');
    setRoot(
      (r) =>
        mapNode(r, parentId, (n) =>
          n.kind === 'group' ? { ...n, children: [...n.children, g] } : n,
        ) as GroupNode,
    );
  }, []);

  const removeNode = useCallback((id: string) => {
    setRoot((r) => removeFrom(r, id));
    setSelectedConditionId((cur) => (cur === id ? null : cur));
  }, []);

  const ungroup = useCallback((id: string) => setRoot((r) => ungroupIn(r, id)), []);

  const setLogic = useCallback((id: string, logic: 'and' | 'or') => {
    setRoot(
      (r) => mapNode(r, id, (n) => (n.kind === 'group' ? { ...n, logic } : n)) as GroupNode,
    );
  }, []);

  const updateCondition = useCallback(
    (id: string, patch: Partial<Omit<ConditionNode, 'kind' | 'id'>>) => {
      setRoot(
        (r) =>
          mapNode(r, id, (n) => (n.kind === 'condition' ? { ...n, ...patch } : n)) as GroupNode,
      );
    },
    [],
  );

  /* ---- sort edits */

  const addSort = useCallback((column: string) => {
    setSort((s) => (s.some((e) => e.column === column) ? s : [...s, { column, direction: 'asc' }]));
  }, []);

  const removeSort = useCallback((index: number) => {
    setSort((s) => s.filter((_, i) => i !== index));
  }, []);

  const setSortDirection = useCallback((index: number, direction: 'asc' | 'desc') => {
    setSort((s) => s.map((e, i) => (i === index ? { ...e, direction } : e)));
  }, []);

  const moveSort = useCallback((index: number, delta: number) => {
    setSort((s) => {
      const to = index + delta;
      if (to < 0 || to >= s.length) return s;
      const copy = s.slice();
      const [item] = copy.splice(index, 1);
      copy.splice(to, 0, item);
      return copy;
    });
  }, []);

  /* ---- projection edits */

  const allNames = useMemo(() => columns.map((c) => c.name), [columns]);

  const toggleProjection = useCallback(
    (column: string) => {
      setProjection((p) => {
        const current = p ?? allNames;
        const next = current.includes(column)
          ? current.filter((c) => c !== column)
          : // Keep declared order rather than click order — the projection IS
            // the returned order, so a click must not silently reshuffle it.
            allNames.filter((c) => current.includes(c) || c === column);
        return next.length === allNames.length ? null : next;
      });
    },
    [allNames],
  );

  const selectAllColumns = useCallback(() => setProjection(null), []);
  // An empty projection would mean "every column" on the wire, so the floor is
  // one column and the UI keeps the first.
  const clearColumns = useCallback(() => setProjection(allNames.slice(0, 1)), [allNames]);

  /**
   * Page size is not part of the cursor's hash, so the API would happily accept
   * a new `limit` against a held cursor. The row-ordinal arithmetic on screen
   * assumes a constant page size, though, so this restarts the walk rather than
   * printing a range it cannot justify. It commits immediately: a pager control
   * that needs a separate "Run" is not a pager control.
   */
  const setLimit = useCallback(
    (n: number) => {
      setLimitState(n);
      setRunSpec((s) => ({ ...s, limit: n }));
      resetPaging();
    },
    [resetPaging],
  );

  const resetSpec = useCallback(() => {
    setRoot({ kind: 'group', id: 'root', logic: 'and', children: [] });
    setSort([]);
    setProjection(null);
    setSearch('');
    setSelectedConditionId(null);
    setLimitState(50);
    setRunSpec({ limit: 50 });
    resetPaging();
  }, [resetPaging]);

  /* ---- persist */

  const saveMutation = useMutation({
    mutationFn: (vars: SaveViewVars) =>
      analytics.post(`/datasets/${datasetId}/views`, {
        name: vars.name,
        description: vars.description ?? null,
        sheet,
        version_selector: vars.pinVersion
          ? { mode: 'version', version_number: version }
          : { mode: 'current' },
        // A view stores the spec and NEVER a cursor: every open starts at the
        // first page, which is the only thing a cursor bound to (version, spec)
        // could honestly mean once the version can move.
        query: { ...draftSpec, cursor: null },
      }),
    onSuccess: (_d, vars) => toast.success(`Saved view "${vars.name}".`),
    onError: (e) => toast.error(errorText(e)),
  });

  const saveView = useCallback(
    (vars: SaveViewVars) => {
      if (!datasetId || !sheet) return;
      saveMutation.mutate(vars);
    },
    [datasetId, sheet, saveMutation],
  );

  return {
    datasets,
    dataset,
    datasetId,
    selectDataset,
    versions,
    version,
    selectVersion,
    sheets,
    sheet,
    selectSheet,
    columns,
    maskedColumns,

    root,
    counts,
    selectedConditionId,
    selectCondition: setSelectedConditionId,
    addCondition,
    addGroup,
    removeNode,
    ungroup,
    setLogic,
    updateCondition,

    sort,
    addSort,
    removeSort,
    setSortDirection,
    moveSort,

    projection: projection ?? allNames,
    toggleProjection,
    selectAllColumns,
    clearColumns,

    search,
    setSearch,
    searchRefusedBy,

    draftSpec,
    runSpec,
    dirty,
    run,
    resetSpec,

    limit,
    setLimit,
    pageIndex,
    rowOffset: pageIndex * limit,
    nextPage,
    prevPage,
    canPrev: pageIndex > 0,
    nextCursor: page?.next_cursor ?? null,

    page,
    loading: rowsQuery.isFetching,
    errorMessage,
    errorCode,

    conflict,
    undoSpecChange,
    restartFromFirstPage,

    saveView,
    savingView: saveMutation.isPending,
  };
}
