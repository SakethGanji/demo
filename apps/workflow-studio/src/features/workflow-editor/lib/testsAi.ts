/**
 * Stage 3B helpers: AI drafts, variation findings, drift alerts and smoke settings.
 * Pure functions only; the actions that call the API live in testAiActions.ts.
 */
import { newTestId, normalizeDraft, type Category, type TestCase } from './tests';

export interface AiRun {
  status: 'passed' | 'failed' | 'error';
  failures: Array<{ kind: string; node?: string | null; message?: string }>;
}
export type AIDraft = TestCase & { ai_run?: AiRun };
export interface AiDraftResponse {
  drafts: AIDraft[];
  rejected: Array<{ reason: string; test?: unknown }>;
  cells: Array<{ node: string; category: Category }>;
  model: string;
}

export type FindingKind = 'node_error' | 'contract' | 'crash';
export interface Finding {
  id: string;
  kind: FindingKind;
  node: string | null;
  field: string | null;
  input: { items: unknown[] };
  error: { message: string; code?: string; path?: string };
  count: number;
  test: TestCase;
}
export interface FuzzResponse {
  runs: number;
  seed: number;
  findings: Finding[];
  inputs_tried: number;
  ai_inputs: number;
}

export interface DriftDiff {
  path: string;
  kind: 'missing' | 'type' | 'extra' | 'status';
  expected?: unknown;
  actual?: unknown;
}
export interface DriftAlert {
  id: string;
  workflow_id: string;
  node_name: string;
  basis: 'spec' | 'contract' | 'mock';
  status_code: number | null;
  diffs: DriftDiff[];
  count: number;
  status: 'open' | 'resolved';
  title: string;
  summary: string;
  last_seen: string | null;
}
export interface WorkflowDrift {
  alerts: DriftAlert[];
  stale_test_ids: string[];
}

export interface SmokeConfig {
  enabled: boolean;
  test_ids: string[];
  live_nodes: string[];
  schedule: string | null;
  before_release: boolean;
}
export interface SmokeResult {
  test_id: string;
  name: string;
  status: string;
  failures?: Array<{ kind?: string; message?: string }>;
  live_calls?: Array<{ node: string; status_code: number | null; ms: number }>;
}
export interface SmokeRun {
  id: string;
  trigger: 'manual' | 'schedule' | 'release';
  status: 'passed' | 'failed' | 'error';
  started_at: string | null;
  finished_at: string | null;
  results: SmokeResult[];
}

export const EMPTY_SMOKE: SmokeConfig = { enabled: false, test_ids: [], live_nodes: [], schedule: null, before_release: false };

/** An AI draft still waiting for a human: it shows keep / reject. */
export function isAiDraft(t: Pick<TestCase, 'source' | 'status'>): boolean {
  return t.source === 'ai' && t.status === 'draft';
}

/** Turn the model's drafts into tests to insert: always drafts, never colliding with an existing id. */
export function aiDraftsToTests(drafts: AIDraft[], existingIds: Iterable<string>): TestCase[] {
  const used = new Set(existingIds);
  const out: TestCase[] = [];
  for (const d of drafts) {
    const { ai_run: _run, ...rest } = d;
    void _run;
    const t = normalizeDraft({ ...rest, source: 'ai' });
    if (used.has(t.id)) t.id = newTestId();
    used.add(t.id);
    out.push(t);
  }
  return out;
}

/** The dry-run result of each AI draft by test id (for the row hint). */
export function aiRunsById(drafts: AIDraft[], tests: TestCase[]): Record<string, AiRun> {
  const out: Record<string, AiRun> = {};
  drafts.forEach((d, i) => {
    if (d.ai_run && tests[i]) out[tests[i].id] = d.ai_run;
  });
  return out;
}

/** A fuzz finding's ready-made test as a new draft (fresh id if the model's id is taken). */
export function findingToTest(f: Finding, existingIds: Iterable<string>): TestCase {
  const t = normalizeDraft({ ...f.test, source: f.test.source === 'ai' ? 'ai' : 'fuzz' });
  if (new Set(existingIds).has(t.id)) t.id = newTestId();
  return t;
}

/** One-line description of where a finding hit. */
export function findingField(f: Finding): string {
  return f.field ?? f.error.path ?? '';
}

export function inputPreview(f: Finding, max = 80): string {
  let s = '';
  try {
    s = JSON.stringify(f.input.items[0] ?? {});
  } catch {
    s = String(f.input.items[0]);
  }
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function staleSet(drift: WorkflowDrift | undefined | null): Set<string> {
  return new Set(drift?.stale_test_ids ?? []);
}

/** Workflow ids that have an open drift alert (from the project drift list). */
export function driftWorkflowIds(alerts: Array<Pick<DriftAlert, 'workflow_id' | 'status'>> | undefined | null): Set<string> {
  return new Set((alerts ?? []).filter((a) => a.status === 'open').map((a) => a.workflow_id));
}

/** Smoke form state from what the server stored (or defaults). */
export function smokeForm(c: SmokeConfig | undefined | null): SmokeConfig {
  return { ...EMPTY_SMOKE, ...(c ?? {}), test_ids: [...(c?.test_ids ?? [])], live_nodes: [...(c?.live_nodes ?? [])] };
}

export function toggleIn(list: string[], value: string, on: boolean): string[] {
  const has = list.includes(value);
  if (on && !has) return [...list, value];
  if (!on && has) return list.filter((v) => v !== value);
  return list;
}

/** Drop ids / nodes that no longer exist and normalise the cron box (empty -> null). */
export function smokeBody(form: SmokeConfig, testIds: string[], nodeNames: string[]): SmokeConfig {
  const tests = new Set(testIds);
  const nodes = new Set(nodeNames);
  return {
    enabled: form.enabled,
    test_ids: form.test_ids.filter((t) => tests.has(t)),
    live_nodes: form.live_nodes.filter((n) => nodes.has(n)),
    schedule: form.schedule?.trim() ? form.schedule.trim() : null,
    before_release: form.before_release,
  };
}

export function sameSmoke(a: SmokeConfig, b: SmokeConfig): boolean {
  return JSON.stringify(smokeBody(a, a.test_ids, a.live_nodes)) === JSON.stringify(smokeBody(b, b.test_ids, b.live_nodes));
}

export interface AiErrorInfo { title: string; description?: string }

/** The toast for an AI call that failed: 503 not set up, 502 the model's message. */
export function aiErrorInfo(status: number | undefined, message: string): AiErrorInfo {
  if (status === 503) return { title: "AI isn't set up" };
  if (status === 502) return { title: "AI couldn't do that", description: message };
  return { title: "Couldn't reach the AI", description: message };
}
