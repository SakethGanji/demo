/**
 * Typed client for the Stage 6 AI builder (DESIGN-STAGE6-AI-BUILDER §4), over the shared HTTP core.
 *
 * Every mutating call names its actor in the body. Proposal routes take `project_id` as a query parameter so a
 * proposal of another project answers 404. `describeAiError` turns any thrown value into plain English for the
 * screens: raw engine text, JSON and stack traces never reach the user.
 */
import { ApiError, request } from '@/shared/api/http';
import type { ApiRefItem } from './lib/catalog';
import type {
  AiEventList, BuilderConfig, KeepResult, NodeAiExplain, NodeAiTask, Proposal, ProposalList, ProposalStatus, Spec, SpecIssue, Understanding,
} from './types';

// ---------------------------------------------------------------- actor (who is asking)

const ACTOR_KEY = 'studio.actorName';
export const MAX_ACTOR_CHARS = 80;

/** The remembered name, or '' (storage can be missing or blocked: never throws). */
export function getActorName(): string {
  try {
    return (localStorage.getItem(ACTOR_KEY) ?? memoryActor).trim().slice(0, MAX_ACTOR_CHARS);
  } catch {
    return memoryActor;
  }
}

let memoryActor = '';
const actorListeners = new Set<() => void>();

export function setActorName(name: string): string {
  const clean = name.trim().slice(0, MAX_ACTOR_CHARS);
  memoryActor = clean;
  try {
    if (clean) localStorage.setItem(ACTOR_KEY, clean);
    else localStorage.removeItem(ACTOR_KEY);
  } catch {
    /* private window or blocked storage: the name lasts for this page only */
  }
  actorListeners.forEach((l) => l());
  return clean;
}

/** For `useSyncExternalStore`: re-render when the remembered name changes. */
export function subscribeActorName(listener: () => void): () => void {
  actorListeners.add(listener);
  return () => { actorListeners.delete(listener); };
}

// ---------------------------------------------------------------- errors in plain English

export type AiErrorCode =
  | 'actor_required' | 'spec_invalid' | 'request_invalid' | 'no_failed_run'
  | 'model_not_allowed' | 'ai_not_configured' | 'ai_failed'
  | 'bad_state' | 'answer_invalid' | 'draft_moved' | 'locked_tests' | 'tests_failing' | 'builder_busy'
  | 'not_found' | 'network' | 'unknown';

export interface AiUiError {
  code: AiErrorCode;
  /** plain English, safe to show */
  message: string;
  status: number | null;
  /** spec_invalid: the issues the engine listed */
  issues: SpecIssue[];
  /** locked_tests: the approved tests involved */
  testIds: string[];
  /** bad_state: the status the proposal is in */
  proposalStatus: ProposalStatus | null;
  /** answer_invalid: the question the engine refused (its plain message is `message`) */
  qid?: string | null;
}

const KNOWN_CODES: ReadonlySet<string> = new Set([
  'actor_required', 'spec_invalid', 'request_invalid', 'no_failed_run', 'model_not_allowed', 'ai_not_configured', 'ai_failed',
  'bad_state', 'answer_invalid', 'draft_moved', 'locked_tests', 'tests_failing', 'builder_busy', 'not_found',
]);

const STATE_WORDS: Partial<Record<ProposalStatus, string>> = {
  clarify: 'This request is still waiting for answers.',
  spec_draft: 'The plan has not been approved yet.',
  spec_approved: 'The plan is approved but not built yet.',
  building: 'The workflow is still being built.',
  ready: 'This change is ready to review.',
  failed: 'This build did not finish.',
  kept: 'This change was already kept.',
  undone: 'This change was already undone.',
  discarded: 'This change was already discarded.',
  answered: 'This question was already answered.',
};

/** What went wrong inside the AI run (`ai_failed` carries the engine's finer reason as `ai_code`). */
export function aiFailedMessage(aiCode: unknown): string {
  switch (aiCode) {
    case 'deadline': return 'The AI ran out of time before it finished. Nothing was saved. Try a smaller request.';
    case 'call_budget': return 'The AI used up the number of attempts allowed for one request. Nothing was saved.';
    case 'tests_changed': return 'The AI kept trying to change tests you had approved, so its work was thrown away. Nothing was saved.';
    case 'bad_json': return 'The AI answered in a form we could not read. Nothing was saved. Please try again.';
    case 'internal_error': return 'The build stopped unexpectedly. Nothing was saved. Please try again.';
    default: return 'The AI could not finish this request. Nothing was saved. Try again, or describe it a little differently.';
  }
}

/** Why a build ended without a usable workflow (the proposal's `error_code`). */
export function failedBuildMessage(code: string | null): string {
  if (code === 'checker_errors') return 'The workflow still has mistakes the AI could not fix. Nothing was saved.';
  if (code === 'no_workflow') return 'The AI did not add any steps. Nothing was saved.';
  return aiFailedMessage(code);
}

function messageFor(code: AiErrorCode, details: Record<string, unknown>): string {
  switch (code) {
    case 'actor_required': return 'Enter your name first, so this change can be recorded as yours.';
    case 'spec_invalid': return 'The plan still has problems. Fix the items marked below, then approve again.';
    case 'request_invalid': return 'That request cannot be used. Describe what the workflow should do in a few sentences.';
    case 'no_failed_run': return 'There is no failed run of this step to fix yet. Run the workflow first, then ask again.';
    case 'model_not_allowed': return 'The AI model is not approved for use here. Ask an administrator which models are allowed.';
    case 'ai_not_configured': return 'AI is not set up on this system yet. Ask an administrator to connect it.';
    case 'ai_failed': return aiFailedMessage(details.ai_code);
    case 'answer_invalid': return 'That answer does not fit this question. Please check it and try again.';
    case 'bad_state': return STATE_WORDS[details.status as ProposalStatus] ?? 'That cannot be done at this stage of the change.';
    case 'draft_moved': return 'This workflow was changed by someone after the AI started. Nothing was overwritten. Ask again from the latest version.';
    case 'locked_tests': return 'This would change tests that were already approved. Only a person can change approved tests.';
    case 'tests_failing': return 'Some approved tests are failing. Look at them first, or choose to keep the change anyway.';
    case 'builder_busy': return 'You already have a build running in this project. Wait for it to finish, then try again.';
    case 'not_found': return 'We could not find that change. It may have been removed.';
    case 'network': return 'We could not reach the server. Check your connection and try again.';
    default: return 'Something went wrong. Nothing was saved. Please try again.';
  }
}

/** Any thrown value -> a plain-English error the screens can show. */
export function describeAiError(e: unknown): AiUiError {
  if (e instanceof ApiError) {
    const code: AiErrorCode = e.code && KNOWN_CODES.has(e.code) ? (e.code as AiErrorCode) : e.status === 404 ? 'not_found' : 'unknown';
    const d = e.details;
    return {
      code,
      // answer_invalid: the engine's own sentence is written for people ("Pick one of the roles listed"), and belongs to one question
      message: code === 'answer_invalid' && e.message.trim() && e.message !== code ? e.message.trim() : messageFor(code, d),
      qid: typeof d.qid === 'string' ? d.qid : null,
      status: e.status,
      issues: Array.isArray(d.issues) ? (d.issues as SpecIssue[]) : [],
      testIds: Array.isArray(d.test_ids) ? (d.test_ids as string[]) : [],
      proposalStatus: typeof d.status === 'string' ? (d.status as ProposalStatus) : null,
    };
  }
  return { code: 'network', message: messageFor('network', {}), status: null, issues: [], testIds: [], proposalStatus: null };
}

/** An error the engine reported inside a stream (`{code, message}` of an SSE `error` event), in plain English. */
export function aiErrorFromCode(code: unknown, details: Record<string, unknown> = {}): AiUiError {
  const c: AiErrorCode = typeof code === 'string' && KNOWN_CODES.has(code) ? (code as AiErrorCode) : 'unknown';
  return { code: c, message: messageFor(c, details), status: null, issues: [], testIds: [], proposalStatus: null };
}

export function actorRequiredError(): AiUiError {
  return { code: 'actor_required', message: messageFor('actor_required', {}), status: null, issues: [], testIds: [], proposalStatus: null };
}

// ---------------------------------------------------------------- calls

const scope = (projectId?: string) => (projectId ? { project_id: projectId } : undefined);

export const aiBuilderApi = {
  config: (): Promise<BuilderConfig> => request('/ai/builder/config'),

  /** The project's `api_ref` strings, named by the engine (`GET /ai/builder/api-refs`). */
  apiRefs: (projectId: string): Promise<{ project_id: string; api_refs: string[]; items: ApiRefItem[] }> =>
    request('/ai/builder/api-refs', { query: { project_id: projectId } }),

  /** §4.1. 201 `{proposal}`. `workflowId` asks for a change to an existing workflow. */
  draftSpec: async (b: {
    projectId: string; request: string; actor: string; workflowId?: string | null;
    /** ground the plan in the knowledge graph and ask what is missing (default: yes) */
    graph?: boolean;
  }): Promise<Proposal> => {
    const graph = b.graph ?? true;
    const r = await request<{ proposal: Proposal }>('/ai/builder/spec', {
      method: 'POST',
      body: { project_id: b.projectId, request: b.request, actor: b.actor, workflow_id: b.workflowId ?? null, use_graph: graph, clarify: graph },
    });
    return r.proposal;
  },

  /** Answers to the questions of a `clarify` proposal; 200 with the proposal at `spec_draft`. 422 `answer_invalid` names the question. */
  answers: (id: string, answers: Record<string, unknown>, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/answers`, { method: 'POST', query: scope(projectId), body: { answers, actor } }),

  /** The live chips while typing: lexical only, no model call, nothing saved (`POST /knowledge/understand`). */
  understandLive: (projectId: string, text: string, signal?: AbortSignal): Promise<Understanding> =>
    request('/knowledge/understand', { method: 'POST', body: { project_id: projectId, request: text.slice(0, 4000) }, signal }),

  /** §4.2. `issues` and `warnings` are server-stamped and never sent back. */
  putSpec: (id: string, spec: Spec, actor: string, projectId?: string): Promise<Proposal> => {
    const { issues: _i, warnings: _w, ...clean } = spec;
    void _i; void _w;
    return request(`/ai/proposals/${id}/spec`, { method: 'PUT', query: scope(projectId), body: { spec: clean, actor } });
  },

  approveSpec: (id: string, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/spec/approve`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** §4.4. Synchronous: it returns when the build ends. Poll `events` meanwhile. */
  build: (id: string, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/build`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** §4.5. 201: a new child proposal, already built. */
  revise: (id: string, instruction: string, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/revise`, { method: 'POST', query: scope(projectId), body: { instruction, actor } }),

  get: (id: string, projectId?: string): Promise<Proposal> => request(`/ai/proposals/${id}`, { query: scope(projectId) }),

  list: (q: { projectId: string; workflowId?: string; status?: ProposalStatus; limit?: number; cursor?: string }): Promise<ProposalList> =>
    request('/ai/proposals', {
      query: { project_id: q.projectId, workflow_id: q.workflowId, status: q.status, limit: q.limit, cursor: q.cursor },
    }),

  events: (id: string, after = 0, projectId?: string): Promise<AiEventList> =>
    request(`/ai/proposals/${id}/events`, { query: { after, ...scope(projectId) } }),

  keep: (id: string, b: { actor: string; acceptFailing?: boolean; name?: string }, projectId?: string): Promise<KeepResult> =>
    request(`/ai/proposals/${id}/keep`, {
      method: 'POST', query: scope(projectId),
      body: { actor: b.actor, accept_failing: !!b.acceptFailing, ...(b.name ? { name: b.name } : {}) },
    }),

  discard: (id: string, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/discard`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** The engine answers the proposal itself (not wrapped). */
  undo: (id: string, actor: string, projectId?: string): Promise<Proposal> =>
    request(`/ai/proposals/${id}/undo`, { method: 'POST', query: scope(projectId), body: { actor } }),

  /** §4.11. `explain` answers 200 `{answer, proposal_id}`; every other task answers 201 with a proposal. */
  nodeAssist: (workflowId: string, node: string, b: {
    task: NodeAiTask; actor: string; field?: string; instruction?: string; executionId?: string;
  }): Promise<NodeAiExplain | Proposal> =>
    request(`/workflows/${encodeURIComponent(workflowId)}/nodes/${encodeURIComponent(node)}/ai`, {
      method: 'POST',
      body: { task: b.task, actor: b.actor, field: b.field, instruction: b.instruction, execution_id: b.executionId },
    }),
};
