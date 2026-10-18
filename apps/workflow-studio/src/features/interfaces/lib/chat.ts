/** Chat helpers (DESIGN-STAGE5-UIS §6.2): merging polled pages and remembering the session across reloads. */
import type { ChatMessage } from './types'

/** Merge by `seq` (the server's order); later copies win; result is ascending. */
export function mergeMessages(prev: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const bySeq = new Map<number, ChatMessage>()
  for (const m of prev) bySeq.set(m.seq, m)
  for (const m of incoming) bySeq.set(m.seq, m)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

export function lastSeq(messages: ChatMessage[]): number {
  return messages.reduce((n, m) => Math.max(n, m.seq), 0)
}

export const CHAT_MAX_CHARS = 16_000

const KEY = (project: string, slug: string) => `workflow-studio:iface-chat:${project}/${slug}`

export interface StoredSession {
  session_id: string
  session_token: string
}

export function loadSession(project: string, slug: string): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(KEY(project, slug))
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<StoredSession>
    return typeof v.session_id === 'string' && typeof v.session_token === 'string' ? (v as StoredSession) : null
  } catch {
    return null
  }
}

export function saveSession(project: string, slug: string, s: StoredSession): void {
  try {
    sessionStorage.setItem(KEY(project, slug), JSON.stringify(s))
  } catch {
    /* best effort */
  }
}

export function clearSession(project: string, slug: string): void {
  try {
    sessionStorage.removeItem(KEY(project, slug))
  } catch {
    /* best effort */
  }
}
