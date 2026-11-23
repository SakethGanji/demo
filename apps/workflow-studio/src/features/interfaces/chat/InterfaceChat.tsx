/**
 * Chat surface (DESIGN-STAGE5-UIS §6.2, §11.2). The calls come from a `ChatTransport`, so the same component is the
 * public page's chat and the editor's draft chat. Session id and bearer live in sessionStorage (under the
 * transport's key) so the history survives a reload. One turn in flight per session: the input is disabled while the server says `busy`,
 * and a 409 CHAT_BUSY is shown inline. Polling: 1 s while busy, else every 10 s while the tab is visible.
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, SendHorizontal } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import { ChatPanel, ChatPanelFooter } from '@/shared/components/chat'
import { ApiError, errorMessage, isApiError } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'
import { CHAT_MAX_CHARS, clearSession, lastSeq, loadSession, mergeMessages, saveSession, type StoredSession } from '../lib/chat'
import { chatMessagesKey, chatSessionKey, type ChatTransport } from '../lib/chatTransport'
import { newIdempotencyKey } from '../lib/keys'
import { chatRefetchInterval } from '../lib/polling'
import type { ChatMessage } from '../lib/types'

interface ChatState {
  messages: ChatMessage[]
  busy: boolean
}

export function InterfaceChat({
  transport, onTurn, variant = 'tile',
}: {
  transport: ChatTransport
  /** a sent turn started this execution (the editor follows it on the canvas) */
  onTurn?: (executionId: string) => void
  /** `tile`: its own 520px card (the public page); `bare`: fills its parent, which is the surface (the editor dock) */
  variant?: 'tile' | 'bare'
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const sendLock = useRef(false)

  const sessionKey = chatSessionKey(transport)
  const session = useQuery({
    queryKey: sessionKey,
    queryFn: async (): Promise<StoredSession> => {
      const stored = loadSession(transport.storageKey)
      if (stored) return stored
      const created = await transport.createSession()
      saveSession(transport.storageKey, created)
      return created
    },
    staleTime: Infinity,
    gcTime: Infinity,
    retry: 1,
  })
  const stored = session.data
  const sid = stored?.session_id
  const token = stored?.session_token

  const key = chatMessagesKey(transport, sid ?? '')
  const chat = useQuery({
    queryKey: key,
    enabled: !!stored,
    queryFn: async (): Promise<ChatState> => {
      const prev = qc.getQueryData<ChatState>(key)
      let messages = prev?.messages ?? []
      let busy = prev?.busy ?? false
      try {
        for (let i = 0; i < 20; i++) {
          const page = await transport.messages(stored as StoredSession, lastSeq(messages))
          messages = mergeMessages(messages, page.messages)
          busy = page.busy
          if (page.next_after == null || page.messages.length === 0) break
        }
      } catch (e) {
        if (isApiError(e, undefined, 404)) {
          // the stored session is gone (404 hides expired sessions): start a fresh one
          clearSession(transport.storageKey)
          await qc.invalidateQueries({ queryKey: sessionKey })
        }
        throw e
      }
      return { messages, busy }
    },
    refetchInterval: (q) => chatRefetchInterval(q.state.data?.busy),
    retry: false,
  })

  const send = useMutation({
    mutationFn: (content: string) => transport.send(stored as StoredSession, content, newIdempotencyKey()),
    onSuccess: async (res) => {
      if (res.execution_id) onTurn?.(res.execution_id)
      setDraft('')
      setNotice(null)
      qc.setQueryData<ChatState>(key, (s) => (s ? { ...s, busy: true } : s))
      await qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'CHAT_BUSY') setNotice('Still answering your last message. Try again in a moment.')
      else setNotice(errorMessage(e))
    },
  })

  const busy = chat.data?.busy === true || send.isPending
  const messages = chat.data?.messages ?? []
  const canSend = !!sid && !!token && !busy && draft.trim().length > 0

  const submit = () => {
    if (!canSend || sendLock.current) return
    sendLock.current = true
    send.mutate(draft.trim().slice(0, CHAT_MAX_CHARS), { onSettled: () => { sendLock.current = false } })
  }

  return (
    <div className={cn('flex flex-col', variant === 'tile' ? 'tile h-[520px]' : 'h-full min-h-0 flex-1')} data-testid="iface-chat">
      <ChatPanel>
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-4" role="log" aria-live="polite">
          {session.isError && <div className="text-[12.5px] text-destructive" role="alert">Could not start the chat.</div>}
          {messages.length === 0 && !chat.isPending && <div className="m-auto text-[12.5px] text-muted-foreground">Say hello to start.</div>}
          {messages.map((m) => (
            <div
              key={m.seq}
              data-testid="iface-chat-message"
              data-role={m.role}
              data-seq={m.seq}
              className={cn(
                'max-w-[85%] whitespace-pre-wrap break-words rounded-panel px-3 py-2 text-[13px]',
                m.role === 'user' ? 'self-end bg-primary text-primary-foreground' : m.role === 'error' ? 'self-start bg-destructive-soft text-destructive' : 'self-start bg-recess text-foreground',
              )}
            >
              {m.content}
            </div>
          ))}
          {busy && (
            <div className="flex items-center gap-1.5 self-start text-[12px] text-muted-foreground" data-testid="iface-chat-busy">
              <Loader2 className="size-3 animate-spin" /> Thinking…
            </div>
          )}
        </div>
        <ChatPanelFooter>
          {notice && <div className="mb-2 text-[12px] text-destructive" role="alert" data-testid="iface-chat-notice">{notice}</div>}
          <div className="flex items-end gap-2">
            <Textarea
              rows={2}
              value={draft}
              maxLength={CHAT_MAX_CHARS}
              placeholder="Type a message…"
              disabled={!sid || busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  submit()
                }
              }}
              className="field-sizing-fixed"
              data-testid="iface-chat-input"
            />
            <Button size="icon" onClick={submit} disabled={!canSend} aria-label="Send" data-testid="iface-chat-send">
              <SendHorizontal />
            </Button>
          </div>
        </ChatPanelFooter>
      </ChatPanel>
    </div>
  )
}
