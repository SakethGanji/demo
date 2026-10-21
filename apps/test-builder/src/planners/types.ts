import type { Action } from '../actions.ts';

/** One entry of explore history, as the planner sees it (already scrubbed of secrets). */
export interface HistoryEntry {
  step: number;
  action: Action;
  outcome: 'ok' | 'error' | 'blocked';
  message?: string;
  url?: string;
}

export interface PlannerInput {
  goal: string;
  url: string;
  /** Aria snapshot of <body>, secrets replaced by {{key}}. */
  snapshot: string;
  history: HistoryEntry[];
  /** Names (never values) of credentials the planner may reference as {{name}}. */
  credentialKeys: string[];
  /** Error from the previous step (policy block, resolution failure, invalid JSON...). */
  lastError?: string;
}

export interface Planner {
  readonly name: string;
  next(input: PlannerInput): Promise<Action>;
}
