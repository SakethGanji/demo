/**
 * Phase 0 stale-base race (ARCHITECTURE §6.2): pending visual edits are saved before an AI turn is sent, the turn carries the
 * resulting head as `base_version_id`, a failed save blocks the send, and a 409 `stale_base` with nothing unsaved locally
 * rebases onto the server's head and sends once more. The hook runs outside React (hooks stubbed); the network is faked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

const qc = new QueryClient()
vi.hoisted(() => {
  // the api config reads the page's hostname at import
  const g = globalThis as { window?: unknown }
  g.window ??= { location: { hostname: 'localhost', origin: 'http://localhost' } }
})

vi.mock('react', async (orig) => {
  const actual = await orig<typeof import('react')>()
  return {
    ...actual,
    useCallback: <T,>(f: T) => f,
    useRef: <T,>(v: T) => ({ current: v }),
    useEffect: (f: () => void) => {
      f()
    },
  }
})
vi.mock('@tanstack/react-query', async (orig) => ({ ...(await orig<object>()), useQueryClient: () => qc }))
vi.mock('../stores/appBuilderChatStore', async (orig) => {
  const m = await orig<typeof import('../stores/appBuilderChatStore')>()
  const real = m.useAppBuilderChatStore
  const hook = Object.assign((sel: (s: ReturnType<typeof real.getState>) => unknown) => sel(real.getState()), real)
  return { ...m, useAppBuilderChatStore: hook }
})
const getApp = vi.fn()
const updateApp = vi.fn()
vi.mock('@/shared/lib/api', async (orig) => {
  const m = await orig<typeof import('@/shared/lib/api')>()
  return { ...m, appsApi: { ...m.appsApi, get: (...a: unknown[]) => getApp(...a), update: (...a: unknown[]) => updateApp(...a) } }
})

import { useAppBuilderChat } from './useAppBuilderChat'
import { createVisualSaver } from './visualSave'
import { useAppDocumentStore } from '../stores'
import { useAppBuilderChatStore } from '../stores/appBuilderChatStore'
import { ApiError } from '@/shared/api/http'
import type { ApiAppVersion } from '@/shared/lib/api'

const version = (id: number) => ({ id, version_number: id }) as unknown as ApiAppVersion

function sse(): Response {
  return new Response('data: {"type":"done"}\n\n', { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const log: string[] = []
const bodies: Array<Record<string, unknown>> = []
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  log.length = 0
  bodies.length = 0
  useAppDocumentStore.getState().reset()
  useAppBuilderChatStore.getState().clearHistory()
  useAppDocumentStore.getState().setCurrentVersion(version(1))
  fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    log.push('send')
    bodies.push(JSON.parse(String(init.body)))
    return sse()
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('chat turn flushes visual edits first', () => {
  it('awaits the flush before sending, and sends the flushed head as the base', async () => {
    const beforeSend = vi.fn(async () => {
      log.push('flush:start')
      await new Promise((r) => setTimeout(r, 20))
      useAppDocumentStore.getState().setCurrentVersion(version(2)) // the visual save landed as v2
      log.push('flush:done')
      return true as const
    })
    const chat = useAppBuilderChat({ appId: 'a1', pid: 'p1', beforeSend })
    await chat.sendMessage('make it blue')
    expect(log).toEqual(['flush:start', 'flush:done', 'send'])
    expect(bodies[0].base_version_id).toBe(2)
    expect(bodies[0].current_version_id).toBe(2)
  })

  it('blocks the send with a visible error when the flush fails', async () => {
    const chat = useAppBuilderChat({ appId: 'a1', pid: 'p1', beforeSend: async () => 'network down' })
    await chat.sendMessage('make it blue')
    expect(fetchMock).not.toHaveBeenCalled()
    const last = useAppBuilderChatStore.getState().messages.at(-1)
    expect(last?.error?.code).toBe('unsaved_edits')
    expect(last?.error?.message).toMatch(/visual edits aren't saved/)
    expect(useAppBuilderChatStore.getState().isStreaming).toBe(false)
  })

  it('on 409 stale_base with nothing unsaved, takes the server head and sends once more', async () => {
    getApp.mockResolvedValue({ id: 'a1', files: [{ path: 'App.tsx', content: 'theirs' }], current_version: version(7) })
    let n = 0
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)))
      if (n++ === 0)
        return new Response(JSON.stringify({ detail: { code: 'stale_base', message: 'changed' } }), { status: 409 })
      return sse()
    })
    const chat = useAppBuilderChat({ appId: 'a1', pid: 'p1', beforeSend: async () => true })
    await chat.sendMessage('make it blue')
    expect(bodies.map((b) => b.base_version_id)).toEqual([1, 7])
    expect(useAppDocumentStore.getState().files?.[0].content).toBe('theirs')
    const msgs = useAppBuilderChatStore.getState().messages
    expect(msgs.filter((m) => m.role === 'user')).toHaveLength(1)
    expect(msgs.at(-1)?.error ?? null).toBeNull()
  })

  it('on 409 with unsaved local edits, does not overwrite them and says so', async () => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ detail: { code: 'stale_base', message: 'x' } }), { status: 409 }))
    useAppDocumentStore.getState().setFiles([{ path: 'App.tsx', content: 'mine' }])
    useAppDocumentStore.getState().setDirty(true)
    const chat = useAppBuilderChat({ appId: 'a1', pid: 'p1', beforeSend: async () => true })
    await chat.sendMessage('make it blue')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(useAppDocumentStore.getState().files?.[0].content).toBe('mine')
    expect(useAppBuilderChatStore.getState().messages.at(-1)?.error?.code).toBe('stale_base')
  })
})

describe('visual saver', () => {
  it('flush cancels the debounce and saves now', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async () => {})
    const s = createVisualSaver(save, () => {}, 1200)
    s.schedule()
    expect(s.hasPending()).toBe(true)
    await expect(s.flush()).resolves.toBe(true)
    expect(save).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(save).toHaveBeenCalledTimes(1) // the debounce was cancelled
    expect(s.hasPending()).toBe(false)
  })

  it('flush waits for a save in flight and saves edits made during it', async () => {
    vi.useFakeTimers()
    let release: () => void = () => {}
    const save = vi.fn(() => (save.mock.calls.length === 1 ? new Promise<void>((r) => (release = r)) : Promise.resolve()))
    const s = createVisualSaver(save, () => {}, 100)
    s.schedule()
    await vi.advanceTimersByTimeAsync(100) // first save starts, hangs
    expect(s.isSaving()).toBe(true)
    s.schedule() // another edit while it is in flight
    const flushed = s.flush()
    release()
    await expect(flushed).resolves.toBe(true)
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('a failed save stays pending; flush retries it and reports a second failure', async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(undefined)
    const onError = vi.fn()
    const s = createVisualSaver(save, onError, 1200)
    s.schedule()
    await expect(s.flush()).resolves.toBe('boom')
    expect(onError).toHaveBeenCalledTimes(1)
    expect(s.hasPending()).toBe(true)
    await expect(s.flush()).resolves.toBe(true)
    expect(s.hasPending()).toBe(false)
    s.dispose()
  })

  it('flush with nothing pending does not save', async () => {
    const save = vi.fn(async () => {})
    await expect(createVisualSaver(save, () => {}).flush()).resolves.toBe(true)
    expect(save).not.toHaveBeenCalled()
  })

  it('a stale-base 409 is reported to onError', async () => {
    const err = new ApiError(409, 'stale_base', 'changed', null)
    const onError = vi.fn()
    const s = createVisualSaver(() => Promise.reject(err), onError)
    s.schedule()
    expect(typeof (await s.flush())).toBe('string')
    expect(onError).toHaveBeenCalledWith(err)
    s.dispose()
  })
})
