import type { ApiStatus } from '@/shared/api/types'
export type Tool = { id: string; name: string; verb: string; desc: string; meta: string; route: string }

/** The toolkit. Every tool is a verb; the workbench, toolbelt and command palette all read this list. */
export const TOOLS: Tool[] = [
  { id: 'quick', name: 'New Model', verb: 'Train', desc: 'Upload a table, say what to predict, and watch AutoGluon race and stack its models.', meta: 'AutoGluon · leaderboard · ensemble', route: 'quick-train' },
  { id: 'autopilot', name: 'Vela Autopilot', verb: 'Delegate', desc: 'Give Vela a dataset and a goal. It trains and fixes the data, and only asks you what is true.', meta: 'asks on ground truth only', route: 'autopilot' },
  { id: 'train', name: 'Training Gym', verb: 'Train', desc: 'String a pipeline end to end and fit it, step by step.', meta: 'step 07 of 10 · Draft', route: 'gym' },
  { id: 'compare', name: 'String Plot', verb: 'Compare', desc: 'Thread every run across its parameters and metrics; click a string to select it.', meta: '9 runs · 3 selected', route: 'experiments' },
  { id: 'register', name: 'Model Registry', verb: 'Register', desc: 'Promote a run to a version and trace its lineage to production.', meta: 'v3.0.0-rc1 awaiting approval', route: 'registry' },
  { id: 'vela', name: 'Vela', verb: 'Ask', desc: 'Get a finding with a number and one patch you can apply.', meta: '2 patches ready', route: 'vela' },
]

/** The footer line of each tool: real counts from GET /api/status in live mode, the scripted line in mock mode.
 *  Tools that are still prototypes (Gym, Autopilot, Vela) say so instead of showing made-up numbers. */
export function liveMeta(t: Tool, status: ApiStatus | null): string {
  if (!status) return t.meta
  const r = status.registry
  switch (t.id) {
    case 'quick': return `AutoGluon ${status.engine.version ?? ''} · ${status.runs} run${status.runs === 1 ? '' : 's'}`
    case 'compare': return `${status.runs} run${status.runs === 1 ? '' : 's'} to compare`
    case 'register': return r ? `${r.stages.Production} in production · ${r.approvals.pending} pending review` : 'registry'
    default: return 'prototype · simulated'
  }
}
