/**
 * What happened in this preview session (§5.2): observed `/api/p/` calls per route (a ✓ appears
 * only after an observed 2xx, review H6), the last call for the console strip,
 * plus the composer draft that "Use" writes into.
 */
import { create } from 'zustand'

export interface ObservedCall {
  method: string
  path: string
  status: number
  ms: number
  at: number
  routeId?: string
}

interface PreviewSessionState {
  appId: string | null
  /** route id → last observed call */
  byRoute: Record<string, ObservedCall>
  /** route id → an observed 2xx */
  ok: Record<string, ObservedCall>
  lastCall: ObservedCall | null
  composerDraft: { text: string; nonce: number } | null
  reset: (appId: string | null) => void
  record: (c: ObservedCall) => void
  setDraft: (text: string) => void
}

export const usePreviewSession = create<PreviewSessionState>()((set) => ({
  appId: null,
  byRoute: {},
  ok: {},
  lastCall: null,
  composerDraft: null,
  reset: (appId) => set({ appId, byRoute: {}, ok: {}, lastCall: null, composerDraft: null }),
  record: (c) =>
    set((s) => ({
      lastCall: c,
      byRoute: c.routeId ? { ...s.byRoute, [c.routeId]: c } : s.byRoute,
      ok: c.routeId && c.status >= 200 && c.status < 300 ? { ...s.ok, [c.routeId]: c } : s.ok,
    })),
  setDraft: (text) => set({ composerDraft: { text, nonce: Date.now() } }),
}))
