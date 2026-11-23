/**
 * "Ask the graph": the request is debounced, then sent to `/knowledge/understand` (lexical, no model call). Each
 * new text aborts the request in flight (React Query passes the signal); the last answer stays while the next loads.
 */
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { knowledgeApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { motionOff } from '@/shared/motion/testFlag'
import { kg } from '../kit/beats'
import type { KnowledgeScope } from '../lib/types'

/** Shorter than the builder's chips: a question about the graph is often two words. */
export const ASK_MIN_CHARS = 3

export function useAskGraph(scope: KnowledgeScope, text: string) {
  const [debounced, setDebounced] = useState(text)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(text), motionOff() ? 0 : kg.chips.debounceMs)
    return () => window.clearTimeout(t)
  }, [text])

  const wanted = debounced.trim()
  const enabled = wanted.length >= ASK_MIN_CHARS
  const q = useQuery({
    queryKey: qk.knowledgeAsk(scope, wanted),
    queryFn: ({ signal }) => knowledgeApi.understand(scope, wanted, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  })
  const active = text.trim().length >= ASK_MIN_CHARS
  return {
    /** null while the box is empty or too short, so clearing it clears the highlight at once */
    data: active && enabled ? (q.data ?? null) : null,
    pending: active && (text.trim() !== wanted || q.isFetching),
    error: active && enabled ? q.error : null,
  }
}
