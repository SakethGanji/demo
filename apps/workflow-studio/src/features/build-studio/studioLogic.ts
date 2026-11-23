/**
 * The build studio's decisions (New app, `apps/new`): which view it is in, the app the build made, what the preview
 * pane shows, and which file the code pane shows.
 */
import type { FlowPhase } from '@/features/plan/buildLogic'
import type { Build, BuildStep } from '@/features/plan/buildTypes'

/** The centred prompt before anything has started; the split workspace from the first message on. */
export const studioView = (phase: FlowPhase): 'hero' | 'workspace' => (phase === 'describe' ? 'hero' : 'workspace')

/** The app the build is making, once its UI step has started. */
export function builtAppId(b: Build | null | undefined): string | null {
  return b?.steps.find((s) => s.kind === 'app' && s.app_id && s.status !== 'pending')?.app_id ?? null
}

/** The app to open when the build is done (partial and failed builds end on their report instead). */
export const readyApp = (b: Build | null | undefined): string | null => (b?.status === 'done' ? builtAppId(b) : null)

export type PreviewStage = 'waiting' | 'api' | 'skeleton' | 'app'

/** Nothing to show before a build; the routes as they are built until the app exists; a skeleton until its first files. */
export function previewStage(o: { build: Build | null | undefined; appId: string | null; files: number }): PreviewStage {
  if (o.appId) return o.files > 0 ? 'app' : 'skeleton'
  return o.build ? 'api' : 'waiting'
}

/** The file the code pane shows: the newest write while following, otherwise the one the user picked. */
export const followTarget = (follow: boolean, newest: string | null, picked: string | null): string | null => (follow && newest ? newest : picked)

export const stepOf = (b: Build | null | undefined, key: string | null | undefined): BuildStep | null =>
  (key && b?.steps.find((s) => s.key === key)) || null

export const EXAMPLES: Array<{ label: string; prompt: string }> = [
  {
    label: 'Support ticket desk',
    prompt: 'A support desk: customers file tickets, agents triage them by priority, reply, and close them; a dashboard of open tickets per agent.',
  },
  {
    label: 'Expense approvals',
    prompt: 'Expense approvals: employees submit expenses with a receipt and amount, managers approve or reject them, finance sees what is approved each month.',
  },
  {
    label: 'Inventory tracker',
    prompt: 'An inventory tracker for a small warehouse: items with stock levels and locations, stock in and out movements, and a low-stock list.',
  },
  {
    label: 'Event sign-ups',
    prompt: 'Event sign-ups: we list upcoming events with capacity, people register and get a waitlist when full, organisers check people in at the door.',
  },
]
