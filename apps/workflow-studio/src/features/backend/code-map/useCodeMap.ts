import { useEffect, useRef, useState } from 'react'
import { modulesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import type { CodeMap } from './types'

/** Source equality is checked during render as well as after the debounce/request. */
export function useCodeMap(pid: string, moduleId: string | null, source: string, enabled: boolean) {
  const [last, setLast] = useState<{ model: CodeMap; source: string } | null>(null)
  const [status, setStatus] = useState<{ source: string; state: 'loading' | 'synced' | 'invalid' | 'failed'; error?: string }>({
    source: '',
    state: 'loading',
  })
  const [retry, setRetry] = useState(0)
  const sequence = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const revision = ++sequence.current
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setStatus({ source, state: 'loading' })
      modulesApi
        .codeMap(pid, source, moduleId, controller.signal)
        .then((result) => {
          if (controller.signal.aborted || revision !== sequence.current) return
          if (result.valid) {
            setLast({ model: result, source })
            setStatus({ source, state: 'synced' })
          } else
            setStatus({
              source,
              state: 'invalid',
              error: `${result.error.message}${result.error.line ? ` · line ${result.error.line}` : ''}`,
            })
        })
        .catch((error) => {
          if (!controller.signal.aborted && revision === sequence.current)
            setStatus({ source, state: 'failed', error: errorMessage(error) })
        })
    }, 400)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [pid, moduleId, source, enabled, retry])
  const state = status.source === source ? status.state : 'loading'
  return {
    model: last?.model ?? null,
    synced: enabled && state === 'synced' && last?.source === source,
    state,
    error: status.source === source ? status.error : undefined,
    retry: () => setRetry((v) => v + 1),
  }
}
