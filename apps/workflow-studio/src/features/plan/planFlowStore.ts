/**
 * The app builder's plan-and-build conversation, per project: what the user asked for, the build it started and the
 * app that build made. It outlives a page change (New app at `apps/new` moves to `apps/<id>` once the build has made
 * the app) but not a reload; after a reload the conversation is rebuilt from the server (the open draft, the active
 * build).
 */
import { create } from 'zustand'

export interface PlanFlow {
  prompt: string
  buildId: string | null
  appId: string | null
}

interface FlowState {
  flows: Record<string, PlanFlow>
  start: (pid: string, prompt: string) => void
  setBuild: (pid: string, buildId: string) => void
  setApp: (pid: string, appId: string) => void
  end: (pid: string) => void
}

export const usePlanFlowStore = create<FlowState>()((set) => ({
  flows: {},
  start: (pid, prompt) => set((s) => ({ flows: { ...s.flows, [pid]: { prompt, buildId: null, appId: null } } })),
  setBuild: (pid, buildId) =>
    set((s) => ({ flows: { ...s.flows, [pid]: { prompt: s.flows[pid]?.prompt ?? '', appId: s.flows[pid]?.appId ?? null, buildId } } })),
  setApp: (pid, appId) =>
    set((s) => ({ flows: { ...s.flows, [pid]: { prompt: s.flows[pid]?.prompt ?? '', buildId: s.flows[pid]?.buildId ?? null, appId } } })),
  end: (pid) =>
    set((s) => {
      const flows = { ...s.flows }
      delete flows[pid]
      return { flows }
    }),
}))
