/** Stage 3B actions: AI fill gaps, variations, drift resolve and smoke. Plain functions over the stores. */
import { toast } from 'sonner';
import { driftApi, smokeApi, testsApi } from '@/shared/lib/api';
import { useWorkflowStore } from '../stores/workflowStore';
import { useTestStore } from '../stores/testStore';
import { flushPendingEdits } from './pendingEdits';
import { describeActionError } from './nodeActions';
import { draftDefinition, openTestsTab } from './testActions';
import type { Category } from './tests';
import {
  aiDraftsToTests, aiErrorInfo, aiRunsById, findingToTest, type Finding, type SmokeConfig, type SmokeRun,
} from './testsAi';

function aiFail(e: unknown) {
  const m = describeActionError(e);
  const info = aiErrorInfo(m.status, m.message);
  toast.error(info.title, info.description ? { description: info.description } : undefined);
}

/** Ask the model for tests that fill the grid gaps (or the given cells) and insert them as AI drafts. */
export async function aiFillGaps(cells?: Array<{ node: string; category: Category }>) {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) {
    toast.info('Save the workflow first');
    return;
  }
  flushPendingEdits();
  useTestStore.getState().setAiBusy(true);
  try {
    const res = await testsApi.aiDraft(workflowId, { definition: draftDefinition(), cells });
    const ws = useWorkflowStore.getState();
    const tests = aiDraftsToTests(res.drafts, ws.tests.map((t) => t.id));
    tests.forEach((t) => ws.upsertTest(t));
    useTestStore.getState().addAiRuns(aiRunsById(res.drafts, tests));
    if (tests.length > 0) {
      useTestStore.getState().setView('list');
      toast.success(`Added ${tests.length} AI ${tests.length === 1 ? 'draft' : 'drafts'}`, {
        description: res.rejected.length > 0 ? `${res.rejected.length} the AI proposed didn't fit and were skipped.` : 'Keep the ones you like, reject the rest.',
      });
    } else {
      toast.info('The AI had nothing to add', res.rejected[0] ? { description: res.rejected[0].reason } : undefined);
    }
  } catch (e) {
    aiFail(e);
  } finally {
    useTestStore.getState().setAiBusy(false);
  }
}

/** Reject an AI draft: it is removed from the workflow's tests. */
export function rejectAiDraft(testId: string) {
  useWorkflowStore.getState().removeTest(testId);
  if (useTestStore.getState().selectedTestId === testId) useTestStore.getState().selectTest(null);
  toast.info('Draft rejected');
}

export function openFuzzPanel() {
  openTestsTab();
  useTestStore.getState().setView('fuzz');
}

export async function runFuzz(opts: { count: number; ai: boolean }) {
  const { workflowId } = useWorkflowStore.getState();
  if (!workflowId) {
    toast.info('Save the workflow first');
    return;
  }
  flushPendingEdits();
  useTestStore.getState().setFuzzRunning(true);
  try {
    const res = await testsApi.fuzz(workflowId, { definition: draftDefinition(), target: { kind: 'workflow' }, count: opts.count, seed: 1, ai: opts.ai });
    useTestStore.getState().setFuzzResult(res);
    if (res.findings.length === 0) toast.success('No problems found', { description: `${res.inputs_tried} inputs tried.` });
  } catch (e) {
    if (opts.ai) aiFail(e);
    else toast.error("Couldn't try variations", { description: describeActionError(e).message });
  } finally {
    useTestStore.getState().setFuzzRunning(false);
  }
}

/** "Save as test": add the finding's ready draft to the workflow. */
export function saveFindingAsTest(f: Finding) {
  const ws = useWorkflowStore.getState();
  const t = findingToTest(f, ws.tests.map((x) => x.id));
  ws.upsertTest(t);
  useTestStore.getState().markFindingSaved(f.id);
  toast.success('Saved as a draft test');
}

function approverName(): string {
  try {
    return localStorage.getItem('contract-approved-by') || 'studio';
  } catch {
    return 'studio';
  }
}

export async function resolveDrift(workflowId: string, driftId: string): Promise<boolean> {
  try {
    await driftApi.resolve(workflowId, driftId, approverName());
    toast.success('Marked as resolved');
    return true;
  } catch (e) {
    toast.error("Couldn't resolve the alert", { description: describeActionError(e).message });
    return false;
  }
}

/** Smoke acts on the stored workflow (test ids, nodes), so save the draft first. */
async function saveDraft(workflowId: string) {
  const { updateWorkflowDraft } = await import('./nodeActions');
  flushPendingEdits();
  await updateWorkflowDraft(workflowId);
}

export async function saveSmoke(workflowId: string, body: SmokeConfig): Promise<SmokeConfig | null> {
  try {
    await saveDraft(workflowId);
    const saved = await smokeApi.put(workflowId, body);
    toast.success('Live check settings saved');
    return saved;
  } catch (e) {
    toast.error("Couldn't save the live check settings", { description: describeActionError(e).message });
    return null;
  }
}

export async function runSmoke(workflowId: string): Promise<SmokeRun | null> {
  try {
    return await smokeApi.run(workflowId);
  } catch (e) {
    const m = describeActionError(e);
    toast.error(m.status === 409 ? 'Pick at least one test and one live step first' : "Couldn't run the live check", {
      description: m.message,
    });
    return null;
  }
}
