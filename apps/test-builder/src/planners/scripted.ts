// Scripted planner: replays a JSON list of intended steps, checking each against the live
// accessibility snapshot first. Lets everything run (and be tested) without an LLM.
import { readFileSync } from 'node:fs';
import { validateAction, describeAction, type Action, type ElementRef } from '../actions.ts';
import type { Planner, PlannerInput } from './types.ts';

export interface ScriptFile {
  goal: string;
  slug?: string;
  steps: unknown[];
}

/** Does the aria snapshot mention this role/name (or label/text)? Cheap pre-check, not the final word. */
export function snapshotMentions(snapshot: string, ref: ElementRef): boolean {
  if (ref.within && !snapshotMentions(snapshot, ref.within)) return false;
  if (ref.role && ref.name !== undefined) {
    // e.g. `- button "Sign in"` or `- textbox "Password"`
    return snapshot.split('\n').some((l) => l.includes(`${ref.role} "${ref.name}"`) || (l.includes(`- ${ref.role} `) && l.includes(ref.name!)));
  }
  if (ref.role) return snapshot.includes(`- ${ref.role}`);
  const needle = ref.label ?? ref.text ?? '';
  return snapshot.includes(needle);
}

export class ScriptedPlanner implements Planner {
  readonly name = 'scripted';
  readonly steps: Action[];
  private i = 0;
  private attempts = 0;
  private readonly maxAttempts: number;

  constructor(script: ScriptFile, maxAttempts = 3) {
    this.steps = script.steps.map((s, idx) => {
      try { return validateAction(s); } catch (e) { throw new Error(`script step ${idx + 1}: ${(e as Error).message}`); }
    });
    this.maxAttempts = maxAttempts;
  }

  static fromFile(file: string): { planner: ScriptedPlanner; script: ScriptFile } {
    const script = JSON.parse(readFileSync(file, 'utf8')) as ScriptFile;
    if (!script.goal || !Array.isArray(script.steps)) throw new Error(`${file}: needs "goal" and "steps"`);
    return { planner: new ScriptedPlanner(script), script };
  }

  private pending = false;

  async next(input: PlannerInput): Promise<Action> {
    if (this.pending) {
      // The last history entry is the outcome of the step we issued last time.
      this.pending = false;
      const prev = input.history.at(-1);
      if (prev?.outcome === 'error') {
        if (++this.attempts < this.maxAttempts) {
          this.pending = true;
          return this.steps[this.i - 1]!; // retry (page may still be settling)
        }
        return { action: 'done', success: false, reason: `script step ${this.i} failed ${this.maxAttempts}x: ${prev.message ?? ''}` };
      }
      // ok, or blocked by policy (never retried: policy said no) -> move on.
      this.attempts = 0;
    }
    if (this.i >= this.steps.length) return { action: 'done', success: true, reason: 'script complete' };
    const step = this.steps[this.i]!;
    this.i++;
    this.pending = true;
    if ('target' in step && step.target && !snapshotMentions(input.snapshot, step.target)) {
      // Not in the snapshot yet (e.g. rendered after an async fetch). The executor auto-waits and
      // resolves strictly, so we still issue it; a real miss comes back as an error and is retried.
      process.stderr.write(`  [scripted] note: ${describeAction(step)} not in current snapshot; relying on auto-wait\n`);
    }
    return step;
  }
}
