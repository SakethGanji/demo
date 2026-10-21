// Pause / resume / step for running UI cases. Honoured between steps: a step already in flight always finishes.
import { emit, isFinal, type Job } from './store.ts';

export type ControlAction = 'pause' | 'resume' | 'step';
export const CONTROL_ACTIONS: ControlAction[] = ['pause', 'resume', 'step'];

export class ControlError extends Error { status = 409; }

/** Apply one control action; emits a `log` event (data.control = paused | resumed | step) and returns the new state. */
export function applyControl(job: Job, action: ControlAction): { paused: boolean; step_credits: number } {
  if (isFinal(job.status)) throw new ControlError(`job is ${job.status}; nothing to control`);
  const c = job.control;
  if (action === 'pause') {
    c.paused = true;
    c.stepCredits = 0;
  } else if (action === 'resume') {
    c.paused = false;
    c.stepCredits = 0;
  } else {
    // step: run exactly one more step, then stay paused (also pauses a free-running job)
    c.paused = true;
    c.stepCredits += 1;
  }
  job.progress.paused = c.paused;
  const word = action === 'pause' ? 'paused' : action === 'resume' ? 'resumed' : 'step';
  emit(job, 'log', job.progress.current_case_id, action === 'step' ? 'step requested: running one step, then paused' : `run ${word}`, { control: word, paused: c.paused });
  return { paused: c.paused, step_credits: c.stepCredits };
}

/** Called by a UI case before each step: returns when the step may run (or the signal aborted). */
export async function gate(job: Job, caseId: string, signal: AbortSignal): Promise<void> {
  const c = job.control;
  if (!c.paused) return;
  if (c.stepCredits > 0) {
    c.stepCredits--;
    emit(job, 'log', caseId, 'stepping: running one step', { control: 'stepping' });
    return;
  }
  emit(job, 'log', caseId, 'waiting (paused) before the next step', { control: 'waiting' });
  while (c.paused && c.stepCredits === 0 && !signal.aborted) await new Promise((r) => setTimeout(r, 100));
  if (c.paused && c.stepCredits > 0 && !signal.aborted) {
    c.stepCredits--;
    emit(job, 'log', caseId, 'stepping: running one step', { control: 'stepping' });
  }
}
