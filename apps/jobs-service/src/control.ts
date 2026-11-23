// Pause / resume / step for running UI cases. Honoured between steps: a step already in flight always finishes.
// Take over: the run pauses and the user drives the live page (click / type / key / scroll relayed by relayInput);
// handback resumes the scripted steps from where they stopped.
import { emit, isFinal, type Job } from './store.ts';

export type ControlAction = 'pause' | 'resume' | 'step' | 'takeover' | 'handback';
export const CONTROL_ACTIONS: ControlAction[] = ['pause', 'resume', 'step', 'takeover', 'handback'];

export class ControlError extends Error { status = 409; }
export class InputError extends Error { status = 400; }

/** Apply one control action; emits a `log` event (data.control = paused | resumed | step) and returns the new state. */
export function applyControl(job: Job, action: ControlAction): { paused: boolean; step_credits: number; taken_over: boolean } {
  if (isFinal(job.status)) throw new ControlError(`job is ${job.status}; nothing to control`);
  const c = job.control;
  if (action === 'takeover') {
    if (!job.live || job.live.size === 0) throw new ControlError('no live page to take over (the browser has not opened, or the case finished)');
    c.paused = true;
    c.stepCredits = 0;
    c.takenOver = true;
    job.progress.paused = true;
    // stop recording before any input reaches the page: a trace must never hold take-over input or typed DOM values
    for (const l of job.live.values()) if (l.onTakeover) l.cut ??= l.onTakeover().catch(() => {});
    emit(job, 'log', job.progress.current_case_id, 'taken over: the scripted steps wait while you use the page', { control: 'taken_over', paused: true });
    return { paused: true, step_credits: 0, taken_over: true };
  }
  if (action === 'handback') {
    if (!c.takenOver) throw new ControlError('the page is not taken over');
    c.paused = false;
    c.stepCredits = 0;
    c.takenOver = false;
    job.progress.paused = false;
    emit(job, 'log', job.progress.current_case_id, 'handed back: the scripted steps continue', { control: 'handed_back', paused: false });
    return { paused: false, step_credits: 0, taken_over: false };
  }
  if (c.takenOver && action !== 'resume') throw new ControlError('the page is taken over; hand it back first');
  if (action === 'resume') c.takenOver = false;
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
  return { paused: c.paused, step_credits: c.stepCredits, taken_over: !!c.takenOver };
}

/** One input relayed to the live page while it is taken over. click x/y are fractions (0..1) of the viewport. */
export type RelayedInput =
  | { type: 'click'; x: number; y: number }
  | { type: 'type'; text: string }
  | { type: 'key'; key: string }
  | { type: 'scroll'; dx: number; dy: number };

/** Keys a user may press on the taken-over page (no OS / browser chords). */
export const RELAY_KEYS = new Set([
  'Enter', 'Tab', 'Shift+Tab', 'Escape', 'Backspace', 'Delete', 'Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'Home', 'End', 'PageUp', 'PageDown',
]);
const MAX_TYPED = 2000;
const fin = (v: unknown, lo: number, hi: number) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;

export async function relayInput(job: Job, input: RelayedInput): Promise<{ ok: true; type: string }> {
  if (isFinal(job.status)) throw new ControlError(`job is ${job.status}; nothing to control`);
  if (!job.control.takenOver) throw new ControlError('take over the page first');
  const caseId = job.progress.current_case_id;
  const live = (caseId ? job.live.get(caseId) : undefined) ?? [...job.live.values()][0];
  if (!live) throw new ControlError('no live page (the case finished)');
  await live.cut;
  const page = live.page;
  const i = (input ?? {}) as Record<string, unknown>;
  if (i.type === 'click') {
    if (!fin(i.x, 0, 1) || !fin(i.y, 0, 1)) throw new InputError('click needs x and y between 0 and 1');
    const vp = page.viewportSize() ?? { width: 1280, height: 800 };
    const x = Math.round((i.x as number) * vp.width), y = Math.round((i.y as number) * vp.height);
    await page.mouse.click(x, y);
    emit(job, 'log', caseId, `you clicked at ${x},${y}`, { control: 'input', input: 'click', x, y });
  } else if (i.type === 'type') {
    if (typeof i.text !== 'string' || !i.text.length || i.text.length > MAX_TYPED) throw new InputError(`type needs text (1..${MAX_TYPED} characters)`);
    (job.control.typed ??= []).push(i.text);
    await page.keyboard.type(i.text);
    // never the text itself: it may be a password
    emit(job, 'log', caseId, `you typed ${i.text.length} characters`, { control: 'input', input: 'type', length: i.text.length });
  } else if (i.type === 'key') {
    if (typeof i.key !== 'string' || !RELAY_KEYS.has(i.key)) throw new InputError(`key must be one of ${[...RELAY_KEYS].join(', ')}`);
    await page.keyboard.press(i.key === 'Space' ? ' ' : i.key);
    emit(job, 'log', caseId, `you pressed ${i.key}`, { control: 'input', input: 'key', key: i.key });
  } else if (i.type === 'scroll') {
    if (!fin(i.dx, -5000, 5000) || !fin(i.dy, -5000, 5000)) throw new InputError('scroll needs dx and dy between -5000 and 5000');
    await page.mouse.wheel(i.dx as number, i.dy as number);
    emit(job, 'log', caseId, `you scrolled ${i.dy}px`, { control: 'input', input: 'scroll' });
  } else {
    throw new InputError('input type must be click, type, key or scroll');
  }
  return { ok: true, type: String(i.type) };
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
