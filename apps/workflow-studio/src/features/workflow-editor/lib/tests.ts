/**
 * Test types and pure helpers (CONTRACTS-STAGE3A §1, §2, §4, §5, §6, §7). Mirrors the engine's test models.
 */
import type { Shape } from './shapes';

export type Category =
  | 'normal' | 'boundary' | 'missing' | 'wrong_type' | 'unusual'
  | 'ext_success' | 'ext_error' | 'ext_timeout' | 'ext_empty';

export const ALL_CATEGORIES: Category[] = [
  'normal', 'boundary', 'missing', 'wrong_type', 'unusual', 'ext_success', 'ext_error', 'ext_timeout', 'ext_empty',
];

export const CATEGORY_LABELS: Record<Category, string> = {
  normal: 'Normal',
  boundary: 'Boundary',
  missing: 'Missing',
  wrong_type: 'Wrong type',
  unusual: 'Unusual',
  ext_success: 'Reply OK',
  ext_error: 'Reply error',
  ext_timeout: 'Timeout',
  ext_empty: 'Empty reply',
};

export type Mock =
  | { items: unknown[]; port?: string }
  /** decision-only Approval mock: the run emits each real incoming item plus `approval`, on the decision's port */
  | { approval: { decision: string; comment?: string; form_data?: Record<string, unknown>; decided_by?: string }; port?: string; items?: undefined }
  | { http: { status: number; body?: unknown; headers?: Record<string, string> } }
  | { error: { message: string; code?: string } }
  | { timeout: true }
  | { rules: Array<{ when: { path: string; op: AssertionOp; value?: unknown }; then: Mock }>; default?: Mock };

export type AssertionOp =
  | 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'matches' | 'exists' | 'not_exists'
  | 'count' | 'type' | 'one_of' | 'range' | 'status' | 'shape';

export const ASSERTION_OPS: AssertionOp[] = [
  'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'contains', 'matches', 'exists', 'not_exists',
  'count', 'type', 'one_of', 'range', 'status', 'shape',
];

export interface Assertion {
  id?: string;
  subject?: 'output' | 'request' | 'node';
  node: string;
  port?: string;
  item?: number | 'all' | 'any';
  path?: string;
  op: AssertionOp;
  value?: unknown;
}

export type TestTarget =
  | { kind: 'workflow' }
  | { kind: 'node'; node: string }
  | { kind: 'range'; from: string; to: string };

export type TestStatus = 'draft' | 'approved';
export type TestSource = 'human' | 'suggested' | 'replay' | 'ai' | 'fuzz';

export interface TestCase {
  id: string;
  name: string;
  description?: string;
  target: TestTarget;
  input: { items: unknown[] };
  mocks: Record<string, Mock>;
  expect: Assertion[];
  category: Category;
  field?: string;
  covers?: string[];
  status: TestStatus;
  locked: boolean;
  approved_by?: string;
  approved_at?: string;
  source: TestSource;
  now?: string;
  seed?: number;
}

export interface TestDefaults {
  mocks: Record<string, Mock>;
  now?: string;
  seed?: number;
}

export interface AssertionResult {
  passed: boolean;
  node: string;
  port?: string;
  path?: string;
  op: string;
  expected?: unknown;
  actual?: unknown;
  message: string;
  note?: string;
}

export type FailureKind = 'assertion' | 'contract' | 'unmocked' | 'node_error' | 'setup';

export interface Failure {
  kind: FailureKind;
  node: string | null;
  port?: string;
  path?: string;
  expected?: unknown;
  actual?: unknown;
  message: string;
  fixes?: string[];
  direction?: 'input' | 'output';
}

export type TestResultStatus = 'passed' | 'failed' | 'error';

export interface TestResult {
  test_id: string;
  name: string;
  status: TestResultStatus;
  duration_ms: number;
  execution_id: string | null;
  assertions: AssertionResult[];
  failures: Failure[];
  warnings: Failure[];
  node_status: Record<string, 'success' | 'error' | 'no_output' | 'not_run'>;
  ports_taken: Record<string, string[]>;
  requests: Record<string, unknown[]>;
  outputs: Record<string, Record<string, unknown[]>>;
}

export interface TestSummary {
  total: number;
  passed: number;
  failed: number;
  errored: number;
  approved_total: number;
  approved_failed: number;
}

export type CellStatus = 'pass' | 'fail' | 'none' | 'na';

export interface Grid {
  categories: Category[];
  rows: Array<{ node: string; categories: Partial<Record<Category, { status: CellStatus; tests: string[] }>> }>;
}

export interface TestRunReport {
  results: TestResult[];
  summary: TestSummary;
  badges: Record<string, 'pass' | 'fail'>;
  grid: Grid;
}

/**
 * The test badge a node shows. While an AI review is open, its test result is the latest result the person is
 * looking at, so the canvas follows it; otherwise the live run decides.
 */
export function nodeTestBadge(
  name: string,
  live: Pick<TestRunReport, 'badges'> | null | undefined,
  reviewing: Pick<TestRunReport, 'badges'> | null | undefined,
): 'pass' | 'fail' | undefined {
  return (reviewing ?? live)?.badges?.[name];
}

export interface Scorecard {
  checker: { errors: number; warnings: number };
  tests: TestSummary;
  coverage: {
    branches_total: number;
    branches_taken: number;
    untaken: Array<{ node: string; port: string }>;
    grid_gaps: number;
  };
  mutation: {
    total: number;
    caught: number;
    survived: Array<{ id: string; operator: string; node: string; description: string }>;
  } | null;
  repeatability: { runs: number; identical: boolean; differing_tests: string[] } | null;
  gaps: Array<{ node: string; category: string; reason: string }>;
  bar: string;
}

// ---- API catalog (§6) ----

export interface ApiSpecSummary {
  id: string;
  name: string;
  base_url: string;
  operation_count: number;
  source: 'paste' | 'upload' | 'url' | 'example';
}

export interface OperationResponse {
  description: string;
  shape: Shape | null;
  example: unknown;
}

export interface ApiOperation {
  operation_id: string;
  method: string;
  path: string;
  summary: string;
  path_params: string[];
  request_shape: Shape | null;
  responses: Record<string, OperationResponse>;
}

export interface ApiSpecDetail {
  id: string;
  name: string;
  base_url: string;
  source: string;
  operations: ApiOperation[];
}

export interface MockOption {
  label: string;
  category: Category;
  mock: Mock;
}

// ---- pure helpers ----

const HEX = '0123456789abcdef';

/** "t_" + 8 lowercase hex characters. */
export function newTestId(): string {
  let out = 't_';
  for (let i = 0; i < 8; i++) out += HEX[Math.floor(Math.random() * 16)];
  return out;
}

export function newTestCase(node: string | null, category: Category = 'normal'): TestCase {
  const label = CATEGORY_LABELS[category];
  return {
    id: newTestId(),
    name: node ? `${node}: ${label.toLowerCase()}` : `Workflow: ${label.toLowerCase()}`,
    target: node ? { kind: 'node', node } : { kind: 'workflow' },
    input: { items: [{}] },
    mocks: {},
    expect: [],
    category,
    covers: node ? [node] : undefined,
    status: 'draft',
    locked: false,
    source: 'human',
  };
}

/** Ensure a suggested/unsaved draft carries an id, status and lock flag as the contract wants. */
export function normalizeDraft(t: Partial<TestCase> & { name: string }): TestCase {
  return {
    id: t.id ?? newTestId(),
    name: t.name,
    description: t.description,
    target: t.target ?? { kind: 'workflow' },
    input: t.input ?? { items: [{}] },
    mocks: t.mocks ?? {},
    expect: t.expect ?? [],
    category: t.category ?? 'normal',
    field: t.field,
    covers: t.covers,
    status: 'draft',
    locked: false,
    source: t.source ?? 'suggested',
    now: t.now,
    seed: t.seed,
  };
}

export type TestRowStatus = 'passed' | 'failed' | 'error' | 'draft-unrun';

/** What a row shows: the last result when there is one, else "draft-unrun". */
export function rowStatus(testId: string, report: TestRunReport | null): TestRowStatus {
  const r = report?.results.find((x) => x.test_id === testId);
  return r ? r.status : 'draft-unrun';
}

export function resultFor(testId: string, report: TestRunReport | null): TestResult | undefined {
  return report?.results.find((x) => x.test_id === testId);
}

/** "N/M passing" from a summary. */
export function summaryText(s: TestSummary): string {
  return `${s.passed}/${s.total} passing`;
}

/** The bar text: the scorecard's own `bar` once scored, else "N/M passing". */
export function barText(report: TestRunReport | null, scorecard: Scorecard | null): string {
  if (scorecard?.bar) return scorecard.bar;
  if (!report) return '';
  return summaryText(report.summary);
}

/** Short form for the navbar: "3/4" or "3/4 · 11/19 bugs caught". The full text stays in the tooltip and the Tests tab. */
export function compactBarText(report: TestRunReport | null, scorecard: Scorecard | null): string {
  const t = scorecard?.tests ?? report?.summary;
  if (!t) return '';
  const parts = [`${t.passed}/${t.total}`];
  const m = scorecard?.mutation;
  if (m) parts.push(`${m.caught}/${m.total} bugs caught`);
  return parts.join(' · ');
}

export type BarState = 'pass' | 'fail' | 'none';

export function barState(report: TestRunReport | null): BarState {
  if (!report || report.summary.total === 0) return 'none';
  return report.summary.failed + report.summary.errored > 0 ? 'fail' : 'pass';
}

/**
 * Compose a bar line locally from a scorecard ("14/14 passing · 1 gap · 19/20 planted bugs caught").
 * Used only when the server omitted `bar`.
 */
export function composeBar(sc: Scorecard): string {
  const parts = [`${sc.tests.passed}/${sc.tests.total} passing`];
  const gaps = sc.gaps.length;
  parts.push(`${gaps} ${gaps === 1 ? 'gap' : 'gaps'}`);
  if (sc.mutation) parts.push(`${sc.mutation.caught}/${sc.mutation.total} planted bugs caught`);
  return parts.join(' · ');
}

// ---- grid helpers ----

export function gridCell(grid: Grid | null | undefined, node: string, category: Category) {
  return grid?.rows.find((r) => r.node === node)?.categories[category];
}

/** Categories that actually apply somewhere (a column that is "na" in every row is hidden). */
export function visibleCategories(grid: Grid): Category[] {
  return grid.categories.filter((c) => grid.rows.some((r) => (r.categories[c]?.status ?? 'na') !== 'na'));
}

export function gridGapCount(grid: Grid | null | undefined): number {
  if (!grid) return 0;
  let n = 0;
  for (const row of grid.rows) for (const c of grid.categories) if (row.categories[c]?.status === 'none') n++;
  return n;
}

// ---- failures grouped for the Tests tab ----

export function failureCountsByNode(report: TestRunReport | null): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of report?.results ?? []) for (const f of r.failures) if (f.node) out[f.node] = (out[f.node] ?? 0) + 1;
  return out;
}

// ---- rename rewrite (mirror of the server's rename_node_in_tests) ----

function renameInValue(v: unknown, oldName: string, newName: string): unknown {
  if (typeof v === 'string') {
    return v
      .split(`$node["${oldName}"]`).join(`$node["${newName}"]`)
      .split(`$node['${oldName}']`).join(`$node['${newName}']`);
  }
  if (Array.isArray(v)) return v.map((x) => renameInValue(x, oldName, newName));
  if (v && typeof v === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) out[k] = renameInValue(val, oldName, newName);
    return out;
  }
  return v;
}

function renameKeys<T>(m: Record<string, T>, oldName: string, newName: string): Record<string, T> {
  const out: Record<string, T> = {};
  for (const [k, v] of Object.entries(m)) out[k === oldName ? newName : k] = v;
  return out;
}

export function renameNodeInTest(t: TestCase, oldName: string, newName: string): TestCase {
  const sw = (n: string) => (n === oldName ? newName : n);
  let target = t.target;
  if (target.kind === 'node') target = { kind: 'node', node: sw(target.node) };
  else if (target.kind === 'range') target = { kind: 'range', from: sw(target.from), to: sw(target.to) };
  return {
    ...t,
    target,
    mocks: renameInValue(renameKeys(t.mocks ?? {}, oldName, newName), oldName, newName) as Record<string, Mock>,
    expect: (t.expect ?? []).map((a) => ({ ...a, node: sw(a.node) })),
    covers: t.covers?.map(sw),
  };
}

export function renameNodeInTests(
  tests: TestCase[],
  defaults: TestDefaults,
  oldName: string,
  newName: string,
): { tests: TestCase[]; defaults: TestDefaults } {
  return {
    tests: tests.map((t) => renameNodeInTest(t, oldName, newName)),
    defaults: {
      ...defaults,
      mocks: renameInValue(renameKeys(defaults.mocks ?? {}, oldName, newName), oldName, newName) as Record<string, Mock>,
    },
  };
}

/**
 * Collapse a chain of renames recorded since the last save (a->b then b->c becomes a->c; a->b then b->a drops out).
 */
export function recordRename(renames: Record<string, string>, oldName: string, newName: string): Record<string, string> {
  if (oldName === newName) return renames;
  const out = { ...renames };
  const origin = Object.keys(out).find((k) => out[k] === oldName);
  if (origin !== undefined) {
    if (origin === newName) delete out[origin];
    else out[origin] = newName;
  } else {
    out[oldName] = newName;
  }
  return out;
}

export const EMPTY_DEFAULTS: TestDefaults = { mocks: {} };

/** Default mock for a MockOption list: the 2xx `ext_success`, else the first option. */
export function successMock(options: MockOption[]): Mock | null {
  return (options.find((o) => o.category === 'ext_success') ?? options[0])?.mock ?? null;
}

// ---- display helpers ----

function compact(v: unknown): string {
  if (v === undefined) return '';
  let s: string;
  try {
    s = JSON.stringify(v) ?? String(v);
  } catch {
    s = String(v);
  }
  return s.length > 60 ? `${s.slice(0, 57)}...` : s;
}

/**
 * "expected X, got Y" for a failure, only when the message does not already say it (the engine's messages
 * usually do). Empty string when there is nothing to add.
 */
export function failureDetail(f: Pick<Failure, 'kind' | 'message' | 'expected' | 'actual'>): string {
  if (f.kind !== 'assertion' && f.kind !== 'contract') return '';
  const msg = f.message ?? '';
  const parts: string[] = [];
  if (f.expected !== undefined && !msg.includes(compact(f.expected).replace(/^"|"$/g, ''))) parts.push(`expected ${compact(f.expected)}`);
  if (f.actual !== undefined && !msg.includes(compact(f.actual).replace(/^"|"$/g, ''))) parts.push(`got ${compact(f.actual)}`);
  return parts.join(', ');
}

/** Node name -> the label the canvas shows (name when there is no label). */
export function nodeLabelMap(nodes: Array<{ data: unknown }>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const n of nodes) {
    const d = n.data as { name?: string; label?: string };
    if (d?.name) out[d.name] = d.label || d.name;
  }
  return out;
}

/** The message without a leading "<node>: " (the row already shows the node). */
export function failureMessage(f: { node: string | null; message: string }): string {
  const m = f.message ?? '';
  if (f.node && m.startsWith(`${f.node}: `)) return m.slice(f.node.length + 2);
  return m;
}
