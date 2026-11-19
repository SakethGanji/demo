import type { ApiStatus } from '@/shared/api/types'
export type Tool = { id: string; name: string; verb: string; desc: string; meta: string; route: string }

/** The toolkit. Every tool is a verb; the workbench, toolbelt and command palette all read this list. */
export const TOOLS: Tool[] = [
  { id: 'quick', name: 'Build', verb: 'Build', desc: 'Upload a table: Smart Intake finds what each column is for and what you can build, pre-filled.', meta: 'AutoGluon · leaderboard · ensemble', route: 'build' },
  { id: 'autopilot', name: 'Vela Autopilot', verb: 'Delegate', desc: 'Give Vela a dataset and a goal. It trains and fixes the data, and only asks you what is true.', meta: 'asks on ground truth only', route: 'autopilot' },
  { id: 'train', name: 'Training Gym', verb: 'Train', desc: 'Every AutoGluon fit() argument, validated by the server, with the exact call and a fit-time estimate.', meta: 'step 07 of 10 · Draft', route: 'gym' },
  { id: 'compare', name: 'String Plot', verb: 'Compare', desc: 'Thread every run across its parameters and metrics; click a string to select it.', meta: '9 runs · 3 selected', route: 'experiments' },
  { id: 'register', name: 'Model Registry', verb: 'Register', desc: 'Promote a run to a version and trace its lineage to production.', meta: 'v3.0.0-rc1 awaiting approval', route: 'registry' },
  { id: 'vela', name: 'Vela', verb: 'Ask', desc: 'Get a finding with a number and one patch you can apply.', meta: '2 patches ready', route: 'vela' },
]

/** The footer line of each tool: real counts from GET /api/status in live mode, the scripted line in mock mode.
 */
export function liveMeta(t: Tool, status: ApiStatus | null): string {
  if (!status) return t.meta
  const r = status.registry
  switch (t.id) {
    case 'quick': return `AutoGluon ${status.engine.version ?? ''} · ${status.runs} run${status.runs === 1 ? '' : 's'}`
    case 'compare': return `${status.runs} run${status.runs === 1 ? '' : 's'} to compare`
    case 'register': return r ? `${r.stages.Production} live · ${r.approvals.pending} pending` : 'registry'
    case 'train': return 'every fit() argument'
    case 'vela': return `computed from ${status.runs} run${status.runs === 1 ? '' : 's'}`
    case 'autopilot': return 'real runs · cleanlab inbox · requirements'
    default: return t.meta
  }
}
