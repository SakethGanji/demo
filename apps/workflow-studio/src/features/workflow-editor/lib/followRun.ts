/**
 * Follow a run that started outside the canvas (a chat turn or form submit from the interface dock) on the canvas.
 * It reuses the saved-run view (`?execution=` / `loadExecutionData`): the run's detail is re-read on the interface
 * polling schedule while it is still running, so nodes light up as they finish. Following stops when the run
 * settles, when the author goes back to live or opens another run, or after ten minutes. One run is followed at a time.
 */
import { executionsApi } from '@/shared/lib/api';
import { pollDelayMs } from '@/features/interfaces/lib/polling';
import { useWorkflowStore } from '../stores/workflowStore';

const MAX_FOLLOW_MS = 10 * 60_000;
const MAX_MISSES = 5;

/** A run is still filling in while it is queued or running; anything else (success, failed, waiting …) has settled. */
export function stillRunning(status: string | undefined): boolean {
  return status === 'running' || status === 'queued' || status === 'pending' || status === 'new';
}

let current: (() => void) | null = null;

/** Starts following `executionId` (stopping any run followed before). Returns a stop function. */
export function followRunOnCanvas(executionId: string): () => void {
  current?.();
  let stopped = false;
  let loaded = false;
  let misses = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const startedAt = Date.now();
  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    if (current === stop) current = null;
  };
  current = stop;

  // once shown, the run stays followed only while the canvas is still showing it
  const leftRun = () => loaded && useWorkflowStore.getState().viewingExecution?.id !== executionId;

  const tick = async () => {
    if (stopped || leftRun()) return stop();
    try {
      const detail = await executionsApi.get(executionId);
      if (stopped || leftRun()) return stop();
      if (detail.workflow_id !== useWorkflowStore.getState().workflowId) return stop();
      useWorkflowStore.getState().loadExecutionData(detail);
      loaded = true;
      if (!stillRunning(detail.status)) return stop();
    } catch {
      // the run row can land a moment after the 202: retry a few times before giving up
      if (++misses > MAX_MISSES) return stop();
    }
    const elapsed = Date.now() - startedAt;
    if (elapsed > MAX_FOLLOW_MS) return stop();
    timer = setTimeout(() => void tick(), pollDelayMs(elapsed));
  };
  void tick();
  return stop;
}
