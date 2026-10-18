/**
 * Public (anonymous) interface client: `/api/i/{project_slug}/{slug}/...` (DESIGN-STAGE5-UIS §7.2).
 * Nothing here sends studio credentials. Internal endpoints live in `shared/api/endpoints.ts`.
 */
import { request } from '@/shared/api/http'
import type { ChatPage, PublicSchema, RunView, SubmitResponse } from '../lib/types'

const enc = encodeURIComponent
const base = (project: string, slug: string) => `/api/i/${enc(project)}/${enc(slug)}`
const bearer = (t: string) => ({ Authorization: `Bearer ${t}` })

export const publicInterfaceApi = {
  schema: (project: string, slug: string) => request<PublicSchema>(`${base(project, slug)}/schema`),

  submit: (project: string, slug: string, fields: Record<string, unknown>, idempotencyKey: string) =>
    request<SubmitResponse>(`${base(project, slug)}/submit`, {
      method: 'POST',
      body: { fields },
      headers: { 'Idempotency-Key': idempotencyKey },
    }),

  run: (project: string, slug: string, executionId: string, runToken: string) =>
    request<RunView>(`${base(project, slug)}/runs/${enc(executionId)}`, { headers: bearer(runToken) }),

  createChatSession: (project: string, slug: string, userLabel?: string) =>
    request<{ session_id: string; session_token: string }>(`${base(project, slug)}/chat/sessions`, {
      method: 'POST',
      body: userLabel ? { user_label: userLabel } : {},
    }),

  chatMessages: (project: string, slug: string, sessionId: string, token: string, after = 0, limit = 50) =>
    request<ChatPage>(`${base(project, slug)}/chat/sessions/${enc(sessionId)}/messages`, {
      query: { after, limit },
      headers: bearer(token),
    }),

  sendChat: (project: string, slug: string, sessionId: string, token: string, content: string, clientMessageId: string) =>
    request<{ seq: number; execution_id: string }>(`${base(project, slug)}/chat/sessions/${enc(sessionId)}/messages`, {
      method: 'POST',
      body: { content, client_message_id: clientMessageId },
      headers: bearer(token),
    }),
}
