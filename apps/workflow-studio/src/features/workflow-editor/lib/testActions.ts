/**
 * Test actions over the stores (run, score, suggest, approve, unlock, mock fixes). Plain functions so the
 * Tests tab, the editor drawer, the navbar and the live-run hook share one path.
 */
import { getEditorAction } from '../stores/editorActionsStore';
import { toast } from 'sonner';
import type { Node } from '@xyflow/react';
import { testsApi } from '@/shared/lib/api';
import { useWorkflowStore } from '../stores/workflowStore';
import { useTestStore } from '../stores/testStore';
import { useEditorLayoutStore } from '../stores/editorLayoutStore';
import { useNDVStore } from '../stores/ndvStore';
import { toBackendWorkflow } from './workflowTransform';
import { flushPendingEdits } from './pendingEdits';
import { describeActionError } from './nodeActions';
import { normalizeDraft, type Mock, type TestCase } from './tests';
import type { WorkflowNodeData } from '../types/workflow';

/** The unsaved draft in the shape `tests/run` and `score` take as `definition`. */
export function draftDefinition() {
  const s = useWorkflowStore.getState();
  const d = toBackendWorkflow(s.nodes as Node<WorkflowNodeData>[], s.edges, s.workflowName, s.workflowId);
  return { nodes: d.nodes, connections: d.connections, tests: s.tests, test_defaults: s.testDefaults };
}

/** Fingerprint of everything a test run depends on (positions excluded). */
export function testFingerprint(): string {
  const d = draftDefinition();
  return JSON.stringify({ n: d.nodes.map((n) => ({ ...n, position: undefined })), c: d.connections, t: d.tests, d: d.test_defaults });
}

export function openTestsTab() {
  useEditorLayoutStore.getState().showBottomTab('tests');
}

let runSeq = 0;

/** Runs the tests against the draft (never saves). `silent` keeps failures out of toasts (live runs). */
export async function runTests(opts: { ids?: string[]; silent?: boolean } = {}) {
  const { workflowId } = useWorkflowStore.getState();
  const store = useTestStore.getState();
  if (!workflowId) {
    if (!opts.silent) toast.info('Save the workflow first', { description: 'Tests run against a saved workflow.' });
    return null;
  }
  flushPendingEdits();
  const mine = ++runSeq;
  store.setRunning(true);
  try {
    const report = await testsApi.run(workflowId, { ids: opts.ids, definition: draftDefinition() });
    if (mine === runSeq && useWorkflowStore.getState().workflowId === workflowId) {
      if (opts.ids && store.report) {
        // a partial run merges into the last full report
        const byId = new Map(report.results.map((r) => [r.test_id, r]));
        const merged = store.report.results.map((r) => byId.get(r.test_id) ?? r);
        useTestStore.getState().setReport({ ...report, results: merged });
      } else {
        useTestStore.getState().setReport(report);
      }
    }
    return report;
  } catch (e) {
    console.warn('[tests] run failed', e);
    if (!opts.silent) toast.error("Couldn't run the tests", { description: describeActionError(e).message });
    return null;
  } finally {
    if (mine === runSeq) useTestStore.getState().setRunning(false);
  }
}

export async function scoreWorkflow() {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) {
    toast.info('Save the workflow first');
    return;
  }
  flushPendingEdits();
  useTestStore.getState().setScoring(true);
  try {
    const sc = await testsApi.score(workflowId, { definition: draftDefinition(), mutations: true });
    useTestStore.getState().setScorecard(sc);
    useTestStore.getState().setView('score');
  } catch (e) {
    toast.error("Couldn't score the tests", { description: describeActionError(e).message });
  } finally {
    useTestStore.getState().setScoring(false);
  }
}

export async function suggestTests(node?: string) {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) {
    toast.info('Save the workflow first');
    return;
  }
  useTestStore.getState().setSuggesting(true);
  try {
    const drafts = await testsApi.suggest(workflowId, node ? { node } : {});
    const have = new Set(useWorkflowStore.getState().tests.map((t) => t.id));
    const list = drafts.map((d) => normalizeDraft({ ...d, source: 'suggested' })).filter((d) => !have.has(d.id));
    useTestStore.getState().setSuggestions(list);
    if (list.length === 0) toast.info('No new test suggestions');
  } catch (e) {
    toast.error("Couldn't suggest tests", { description: describeActionError(e).message });
  } finally {
    useTestStore.getState().setSuggesting(false);
  }
}

/** Adds a suggested draft to the workflow as a draft test. */
export function acceptSuggestion(test: TestCase) {
  useWorkflowStore.getState().upsertTest({ ...test, status: 'draft', locked: false, source: 'suggested' });
  useTestStore.getState().dismissSuggestion(test.id);
}

/** Save the draft first (approve/unlock act on the stored workflow), then reload tests from the answer. */
async function viaServer(
  call: (id: string) => Promise<{ definition: { tests?: TestCase[] } }>,
  failTitle: string,
): Promise<boolean> {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) return false;
  try {
    const { updateWorkflowDraft } = await import('./nodeActions');
    flushPendingEdits();
    await updateWorkflowDraft(workflowId);
    const res = await call(workflowId);
    useWorkflowStore.setState({ tests: res.definition.tests ?? [] });
    useWorkflowStore.getState().markAsSaved();
    return true;
  } catch (e) {
    toast.error(failTitle, { description: describeActionError(e).message });
    return false;
  }
}

export async function approveTest(testId: string, approvedBy: string): Promise<boolean> {
  const ok = await viaServer((id) => testsApi.approve(id, testId, approvedBy), "Couldn't approve the test");
  if (ok) toast.success('Test approved and locked');
  return ok;
}

export async function unlockTest(testId: string, by: string): Promise<boolean> {
  const ok = await viaServer((id) => testsApi.unlock(id, testId, by), "Couldn't unlock the test");
  if (ok) toast.success('Test unlocked');
  return ok;
}

/** The two fixes on an unmocked failure: generate a mock (from the spec or contract) or reuse the last real reply. */
export async function fixUnmocked(node: string, fix: 'generate' | 'last_real') {
  const { workflowId, nodes } = useWorkflowStore.getState();
  if (!workflowId) return;
  const data = nodes.find((n) => (n.data as { name?: string }).name === node)?.data as WorkflowNodeData | undefined;
  const hasSpec = !!(data?.parameters as { api_ref?: unknown } | undefined)?.api_ref;
  const kind = fix === 'last_real' ? 'last_real' : hasSpec ? 'from_spec' : 'from_contract';
  try {
    // The engine builds the mock from the stored workflow, so save the draft first (new nodes, api_ref).
    const { updateWorkflowDraft } = await import('./nodeActions');
    flushPendingEdits();
    await updateWorkflowDraft(workflowId);
    const { mock } = await testsApi.generateMock(workflowId, node, { kind });
    useWorkflowStore.getState().setTestDefaultMock(node, mock as Mock);
    toast.success(`Mock added for ${node}`);
    await runTests({ silent: true });
  } catch (e) {
    const m = describeActionError(e);
    const hint = (e as { body?: { detail?: { hint?: string } } } | null)?.body?.detail?.hint;
    toast.error(`Couldn't make a mock for ${node}`, { description: hint ? `${m.message}. Try: ${hint}.` : m.message });
  }
}

/** Publish blocked by failing approved tests (422 `detail.failed_tests`). */
export function showPublishBlocked(failed: Array<{ test_id: string; name: string }>) {
  const names = failed.slice(0, 3).map((t) => t.name).join(', ');
  const more = failed.length > 3 ? ` and ${failed.length - 3} more` : '';
  toast.dismiss();
  toast.error(`Can't publish: ${failed.length} approved ${failed.length === 1 ? 'test fails' : 'tests fail'}`, {
    description: `${names}${more}`,
    id: 'publish-blocked',
  });
  openTestsTab();
  useTestStore.getState().setView('list');
  void runTests({ silent: true });
}

/** Select a node the way a canvas click does (React Flow `selected` state + store) and pan it into view. */
function selectCanvasNode(id: string) {
  const ws = useWorkflowStore.getState();
  ws.setNodes(ws.nodes.map((n) => (!!n.selected === (n.id === id) ? n : { ...n, selected: n.id === id })));
  ws.setSelectedNode(id);
  getEditorAction('focus-node')?.(id);
}

/** Select a node on the canvas by its name and open its panel. */
export function openNodeByName(name: string) {
  const node = useWorkflowStore.getState().nodes.find((n) => (n.data as { name?: string }).name === name);
  if (!node) return;
  selectCanvasNode(node.id);
  useNDVStore.getState().openNDV(node.id);
}

/** Select a node on the canvas by its name (no panel). */
export function selectNodeByName(name: string) {
  const node = useWorkflowStore.getState().nodes.find((n) => (n.data as { name?: string }).name === name);
  if (node) selectCanvasNode(node.id);
}

/** "Save as test": turn a recorded run into a draft test, add it to the workflow and open it in the editor. */
export async function saveExecutionAsTest(executionId: string) {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) {
    toast.info('Save the workflow first');
    return null;
  }
  useTestStore.getState().setSavingFromRun(true);
  try {
    const draft = await testsApi.fromExecution(workflowId, { execution_id: executionId });
    const test = normalizeDraft({ ...draft, name: draft.name.replace(/exec_([0-9a-f]{8})[0-9a-f-]*/i, 'exec_$1'), source: 'replay' });
    useWorkflowStore.getState().upsertTest(test);
    openTestsTab();
    useTestStore.getState().openEditor(test, false);
    toast.success('Saved this run as a draft test');
    return test;
  } catch (e) {
    toast.error("Couldn't save this run as a test", { description: describeActionError(e).message });
    return null;
  } finally {
    useTestStore.getState().setSavingFromRun(false);
  }
}
