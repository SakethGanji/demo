import { clsx, type ClassValue } from 'clsx'

/**
 * Class join for the glass layer. Deliberately NOT `cn()`: tailwind-merge does not know the custom
 * utilities (`kind-label`, `vtoken`, `chip-ok`, `rail`...) and can drop one when it looks like it
 * conflicts with a Tailwind class. Glass components only join; callers that need merging do it
 * themselves on their own classes.
 */
export function cx(...inputs: ClassValue[]): string {
  return clsx(inputs)
}

/** Status of a step, a cell or a run node. One vocabulary for the editor's Page view and the Notebook. */
export type StepStatus = 'pending' | 'running' | 'ok' | 'need' | 'skip' | 'stale' | 'fail'

export const STEP_STATUSES: readonly StepStatus[] = ['pending', 'running', 'ok', 'need', 'skip', 'stale', 'fail']

export const STATUS_LABEL: Record<StepStatus, string> = {
  pending: 'Not run',
  running: 'Running',
  ok: 'Done',
  need: 'Needs you',
  skip: 'Skipped',
  stale: 'Stale',
  fail: 'Failed',
}

/** Tone vocabulary shared by Chip and the inline result pill. */
export type Tone = 'neutral' | 'ok' | 'warn' | 'prod' | 'ai' | 'brand'
