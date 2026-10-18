/**
 * Typed client for the AI app composer (CONTRACTS-CAPGRAPH §5.1), over the shared HTTP core.
 *
 * Every mutating call names its actor in the body and scopes to the project with `project_id` (an app of another project is 404).
 * `describeAppError` adds the app-only error codes to the builder's plain-English mapper.
 */
import { ApiError, request } from '@/shared/api/http';
import { describeAiError, type AiUiError } from '../api';
import { humanise } from '../lib/understanding';
import type { AppBuildView, AppListItem, AppPlan, BuildNextResult, KeepResult } from './appTypes';

// ---------------------------------------------------------------- errors in plain English

const APP_MESSAGES: Record<string, string> = {
  plan_invalid: 'The plan has a problem the AI cannot build from. Check the items marked in the plan and try again.',
  parts_not_ready: 'Some parts are not built yet. Build them first, then keep the app.',
  spec_not_approved: 'A part still needs its plan approved before it can be built.',
  bundle_invalid: 'The finished parts do not fit together as an app yet. Nothing was saved.',
  install_failed: 'The app could not be installed. Nothing was saved.',
  tests_failing: 'Some approved tests are failing. Look at them first, or keep the app anyway.',
};

/** Any thrown value -> a plain-English error. App codes get their own sentence; everything else is the builder's. */
export function describeAppError(e: unknown): AiUiError {
  const base = describeAiError(e);
  if (e instanceof ApiError && e.code && APP_MESSAGES[e.code]) return { ...base, message: appMessage(e.code, e.details) };
  if (e instanceof ApiError && e.code === 'bad_state') {
    const st = typeof e.details.status === 'string' ? e.details.status : '';
    const word = STATE_WORDS[st];
    if (word) return { ...base, message: word };
  }
  return base;
}

/** The sentence for an app code; names the part or parts when the engine says which (`part_key`, `parts`). */
function appMessage(code: string, d: Record<string, unknown>): string {
  const plain = APP_MESSAGES[code];
  if (code === 'spec_not_approved' && typeof d.part_key === 'string' && d.part_key) {
    return `The plan of '${humanise(d.part_key)}' still needs approving before it can be built.`;
  }
  if (code === 'parts_not_ready' && Array.isArray(d.parts)) {
    const names = d.parts.map((p) => (p && typeof p === 'object' && typeof (p as { part_key?: unknown }).part_key === 'string' ? humanise((p as { part_key: string }).part_key) : '')).filter(Boolean);
    if (names.length) return `${plain} Not ready: ${names.join(', ')}.`;
  }
  if (code === 'tests_failing' && Array.isArray(d.parts) && d.parts.length) {
    return `Some approved tests are failing (${d.parts.map((k) => humanise(String(k))).join(', ')}). Look at them first, or keep the app anyway.`;
  }
  return plain;
}

const STATE_WORDS: Record<string, string> = {
  clarify: 'This app is still waiting for answers.',
  planned: 'The plan is ready but not approved yet.',
  specs: 'The plans of the parts are being checked. Build comes next.',
  building: 'A part is being built right now. Wait for it to finish.',
  ready: 'The app is built and ready to keep.',
  kept: 'This app was already kept.',
  discarded: 'This app was already discarded.',
};

// ---------------------------------------------------------------- calls

const scope = (projectId: string) => ({ project_id: projectId });

export const appApi = {
  /** 201. `clarify` while a required slot is open, else `planned` with the plan. */
  create: (b: { projectId: string; request: string; actor: string; answers?: Record<string, unknown> }): Promise<AppBuildView> =>
    request('/ai/apps', {
      method: 'POST',
      body: { project_id: b.projectId, request: b.request, actor: b.actor, ...(b.answers ? { answers: b.answers } : {}) },
    }),

  get: (id: string, projectId: string): Promise<AppBuildView> => request(`/ai/apps/${id}`, { query: scope(projectId) }),

  list: (projectId: string, q: { status?: string; limit?: number } = {}): Promise<{ apps: AppListItem[]; next_cursor: string | null }> =>
    request('/ai/apps', { query: { project_id: projectId, status: q.status, limit: q.limit } }),

  /** 200, `planned`. 422 `answer_invalid` names the question (`qid`). */
  answers: (id: string, answers: Record<string, unknown>, actor: string, projectId: string): Promise<AppBuildView> =>
    request(`/ai/apps/${id}/answers`, { method: 'POST', query: scope(projectId), body: { answers, actor } }),

  /** The whole plan again; re-checked by the engine, no model call. */
  putPlan: (id: string, plan: AppPlan, actor: string, projectId: string): Promise<AppBuildView> =>
    request(`/ai/apps/${id}/plan`, { method: 'PUT', query: scope(projectId), body: { plan, actor } }),

  /** One draft plan per part, children first. Long on a real model: one call writes every part's plan. */
  approvePlan: (id: string, actor: string, projectId: string): Promise<AppBuildView> =>
    request(`/ai/apps/${id}/approve-plan`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** Builds the next part in dependency order and answers when it ends. 409 `spec_not_approved` when its plan is not approved. */
  buildNext: (id: string, actor: string, projectId: string): Promise<BuildNextResult> =>
    request(`/ai/apps/${id}/build-next`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** 409 `parts_not_ready` / `tests_failing` (unless `acceptFailing`). */
  keep: (id: string, b: { actor: string; acceptFailing?: boolean }, projectId: string): Promise<KeepResult> =>
    request(`/ai/apps/${id}/keep`, { method: 'POST', query: scope(projectId), body: { actor: b.actor, accept_failing: !!b.acceptFailing } }),

  discard: (id: string, actor: string, projectId: string): Promise<AppBuildView> =>
    request(`/ai/apps/${id}/discard`, { method: 'POST', query: scope(projectId), body: { actor } }),
};
