/**
 * The engine keeps no chat transcript, but every AI turn that changed the app saved a version carrying the prompt that
 * asked for it. After a reload the chat is rebuilt from those versions: the ask, then a one-line answer. A build's own
 * versions ("one-shot build") carry no request, so the plan the build came from supplies it: what was asked for, and a
 * one-line summary of the checklist answers. The request is the user's own first message when the plan kept it.
 */
import type { ApiAppVersion } from '@/shared/lib/api'
import type { AppChatMessage } from '../stores/appBuilderChatStore'

export interface RestoreContext {
  title?: string | null
  /** the plan's summary: the closest record of what the user asked for */
  summary?: string | null
  /** the user's first message, as typed: preferred over title + summary when the plan kept it */
  request?: string | null
  /** checklist answers, in order */
  answers?: Array<{ topic: string; answer: string }>
}

const MAX_ANSWERS = 4

export function checklistSummary(answers: RestoreContext['answers']): string | null {
  const picked = (answers ?? []).filter((a) => a.answer?.trim())
  if (!picked.length) return null
  const shown = picked
    .slice(0, MAX_ANSWERS)
    .map((a) => `${a.topic.trim() || 'Answer'}: ${a.answer.trim().replace(/\s+/g, ' ')}`)
    .join(' · ')
  const more = picked.length - MAX_ANSWERS
  return `Your answers (${picked.length}): ${shown}${more > 0 ? ` · +${more} more` : ''}`
}

export function restoreChatMessages(versions: ApiAppVersion[], ctx?: RestoreContext): AppChatMessage[] {
  const turns = versions
    .filter((v) => v.trigger === 'ai' && !!v.prompt?.trim())
    .sort((a, b) => a.version_number - b.version_number)
  const out: AppChatMessage[] = []
  const toDate = (iso: string) => {
    const at = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
    return Number.isNaN(at.getTime()) ? new Date() : at
  }
  if (turns.length) {
    const request = ctx?.request?.trim() || [ctx?.title?.trim(), ctx?.summary?.trim()].filter(Boolean).join(': ')
    const checklist = checklistSummary(ctx?.answers)
    const first = toDate(turns[0].created_at)
    if (request) out.push({ id: 'app_restored_request', role: 'user', content: request, timestamp: first })
    if (checklist) out.push({ id: 'app_restored_checklist', role: 'user', content: checklist, timestamp: first })
  }
  for (const v of turns) {
    const when = toDate(v.created_at)
    out.push({ id: `app_restored_u_${v.id}`, role: 'user', content: v.prompt!.trim(), timestamp: when })
    out.push({
      id: `app_restored_a_${v.id}`,
      role: 'assistant',
      content: v.message?.trim() || `Saved as v${v.version_number}.`,
      timestamp: when,
      prompt: v.prompt!.trim(),
    })
  }
  return out
}
