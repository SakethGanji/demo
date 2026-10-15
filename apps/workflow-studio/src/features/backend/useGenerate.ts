/**
 * Client for `POST /api/projects/{P}/modules/generate` (UI-DESIGN §10.1): SSE events
 * `status | draft | analysis | self_test`, then exactly one terminal `result | error`, then `done`.
 * Pre-stream failures are JSON (`llm_unavailable` 503, `generation_in_progress` 409, 422s).
 * Stop aborts the fetch; the server cancels the LLM call and sandbox jobs.
 */
import { useCallback, useRef, useState } from 'react'
import { createParser, type EventSourceMessage } from 'eventsource-parser'
import { apiUrl, parseError } from '@/shared/api/http'
import type { GenerateAnalysis, GenerateRequest, GenerateResult, SelfTestResult } from '@/shared/api/types'

export type GenPhase = 'context' | 'generating' | 'analysing' | 'repairing' | 'testing'

export interface GenStatus {
  phase: GenPhase
  attempt: number
  message: string
  route?: string
}

export type GenState =
  | { kind: 'idle' }
  | { kind: 'streaming'; status: GenStatus | null; phases: GenPhase[]; startedAt: number; draftModule: string | null }
  | { kind: 'error'; code: string; message: string; status?: number }
  | { kind: 'unavailable'; message: string }
  | { kind: 'busy'; message: string }

export interface GenInvalid {
  code: 'generation_invalid'
  message: string
  draft: { module_name: string; source: string }
  analysis: GenerateAnalysis | null
}

export function useGenerate(pid: string) {
  const [state, setState] = useState<GenState>({ kind: 'idle' })
  const abortRef = useRef<AbortController | null>(null)

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setState({ kind: 'idle' })
  }, [])

  /**
   * Resolves with the terminal payload: a result, an invalid draft, or null (stopped/failed;
   * `state` then holds the error).
   */
  const generate = useCallback(
    async (
      body: GenerateRequest,
      hooks: { onSelfTest?: (r: SelfTestResult[]) => void; onAnalysis?: (a: GenerateAnalysis) => void } = {},
    ): Promise<{ result: GenerateResult } | { invalid: GenInvalid } | null> => {
      abortRef.current?.abort()
      const ctl = new AbortController()
      abortRef.current = ctl
      const phases: GenPhase[] = []
      setState({ kind: 'streaming', status: null, phases, startedAt: Date.now(), draftModule: null })
      let res: Response
      try {
        res = await fetch(apiUrl(`/api/projects/${encodeURIComponent(pid)}/modules/generate`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify(body),
          signal: ctl.signal,
        })
      } catch (e) {
        if ((e as Error).name === 'AbortError') return null
        setState({ kind: 'error', code: 'network', message: (e as Error).message })
        return null
      }
      if (!res.ok) {
        let data: unknown = undefined
        try {
          data = await res.json()
        } catch {
          /* empty */
        }
        const err = parseError(res.status, data)
        if (err.code === 'llm_unavailable') setState({ kind: 'unavailable', message: err.message })
        else if (err.code === 'generation_in_progress') setState({ kind: 'busy', message: 'Another generation for this project is running' })
        else setState({ kind: 'error', code: err.code ?? `http_${res.status}`, message: err.message, status: res.status })
        return null
      }
      if (!res.body) {
        setState({ kind: 'error', code: 'internal', message: 'Empty response' })
        return null
      }

      type Terminal = { result: GenerateResult } | { invalid: GenInvalid } | { error: { code: string; message: string } }
      const box: { t: Terminal | null } = { t: null }
      let sawDone = false
      const parser = createParser({
        onEvent(ev: EventSourceMessage) {
          if (ev.event === 'done') {
            sawDone = true
            return
          }
          let data: Record<string, unknown>
          try {
            data = JSON.parse(ev.data)
          } catch {
            return
          }
          const type = data.type as string
          if (type === 'status') {
            const st = data as unknown as GenStatus
            if (!phases.includes(st.phase)) phases.push(st.phase)
            setState((s) => (s.kind === 'streaming' ? { ...s, status: st, phases: [...phases] } : s))
          } else if (type === 'draft') {
            const mn = (data.module_name as string) ?? null
            setState((s) => (s.kind === 'streaming' ? { ...s, draftModule: mn } : s))
          } else if (type === 'analysis') {
            hooks.onAnalysis?.(data as unknown as GenerateAnalysis)
          } else if (type === 'self_test') {
            hooks.onSelfTest?.((data.results as SelfTestResult[]) ?? [])
          } else if (type === 'result') {
            box.t = { result: data as unknown as GenerateResult }
          } else if (type === 'error') {
            const code = String(data.code ?? 'internal')
            if (code === 'generation_invalid' && data.draft)
              box.t = {
                invalid: {
                  code: 'generation_invalid',
                  message: String(data.message ?? 'The draft still has problems'),
                  draft: data.draft as GenInvalid['draft'],
                  analysis: (data.analysis as GenerateAnalysis) ?? null,
                },
              }
            else box.t = { error: { code, message: String(data.message ?? code) } }
          }
        },
      })
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          parser.feed(dec.decode(value, { stream: true }))
        }
      } catch (e) {
        if ((e as Error).name === 'AbortError') return null
        setState({ kind: 'error', code: 'network', message: (e as Error).message })
        return null
      }
      if (abortRef.current !== ctl) return null
      abortRef.current = null
      const t = box.t
      if (!t || !sawDone) {
        setState({ kind: 'error', code: 'stream_closed', message: 'The generator stopped unexpectedly' })
        return null
      }
      if ('error' in t) {
        setState({ kind: 'error', code: t.error.code, message: t.error.message })
        return null
      }
      setState({ kind: 'idle' })
      return t
    },
    [pid],
  )

  return { state, generate, stop, setState }
}

