/**
 * Calls for the project plan. Every call is JSON (`Accept: application/json`): the draft step waits for the whole model
 * round instead of streaming, which the Plan page shows as one pending state.
 */
import { isApiError, request } from '@/shared/api/http'
import type { PlanSource } from '@/features/connections/connectionsTypes'
import type {
  ApproveResult,
  DraftResult,
  PlanBody,
  PlanCheck,
  PlanDiffResult,
  PlanState,
  PlanVersion,
  PlanVersionSummary,
  ReviseResult,
} from './planTypes'

const enc = encodeURIComponent
const P = (p: string) => `/api/projects/${enc(p)}/plan`
const JSON_ONLY = { Accept: 'application/json' }

export const planApi = {
  get: (p: string) => request<PlanState>(P(p)),
  versions: (p: string) => request<PlanVersionSummary[]>(`${P(p)}/versions`),
  version: (p: string, v: number) => request<PlanVersion>(`${P(p)}/${v}`),
  /** Round 1: the model drafts the plan and asks its questions. */
  draftFromPrompt: (p: string, prompt: string, sources?: PlanSource[]) =>
    request<DraftResult>(`${P(p)}/draft`, {
      method: 'POST',
      body: sources?.length ? { prompt, sources } : { prompt },
      headers: JSON_ONLY,
    }),
  /** The next round: answers to every open question of draft `version`. */
  answer: (p: string, version: number, answers: Record<string, string>) =>
    request<DraftResult>(`${P(p)}/draft`, { method: 'POST', body: { version, answers }, headers: JSON_ONLY }),
  /** The next round with every suggested pick taken (the checklist's "Accept all suggestions"). */
  acceptSuggestions: (p: string, version: number) =>
    request<DraftResult>(`${P(p)}/draft`, { method: 'POST', body: { version, accept_suggestions: true }, headers: JSON_ONLY }),
  /** Save a hand-written body as a new draft (no model). */
  draftFromBody: (p: string, body: PlanBody) =>
    request<DraftResult>(`${P(p)}/draft`, { method: 'POST', body: { body }, headers: JSON_ONLY }),
  save: (p: string, version: number, body: PlanBody, baseUpdatedAt: string | null) =>
    request<PlanVersion>(`${P(p)}/${version}`, { method: 'PUT', body: { body, base_updated_at: baseUpdatedAt } }),
  approve: (p: string, version: number, acceptBreaking = false) =>
    request<ApproveResult>(`${P(p)}/${version}/approve`, { method: 'POST', body: { accept_breaking: acceptBreaking } }),
  /** A new draft from the approved plan: with a prompt the model revises it; without one it is a plain copy to edit. */
  revise: async (p: string, prompt?: string, sources?: PlanSource[]): Promise<ReviseResult> => {
    const text = (prompt ?? '').trim()
    if (!text) return request<ReviseResult>(`${P(p)}/revise`, { method: 'POST', body: {} })
    const out = await request<DraftResult>(`${P(p)}/revise`, {
      method: 'POST',
      body: sources?.length ? { prompt: text, sources } : { prompt: text },
      headers: JSON_ONLY,
    })
    return out.plan as ReviseResult
  },
  diff: (p: string, version: number, against?: number) =>
    request<PlanDiffResult & { version: number; against: number | null }>(`${P(p)}/${version}/diff`, {
      query: { against },
    }),
  /** The builder gates' conformance check. `null` while the endpoint does not exist (status "unknown"). */
  check: async (p: string): Promise<PlanCheck | null> => {
    try {
      return await request<PlanCheck>(`${P(p)}/check`, { method: 'POST', body: {} })
    } catch (e) {
      if (isApiError(e, undefined, 404) || isApiError(e, undefined, 405)) return null
      throw e
    }
  },
}
