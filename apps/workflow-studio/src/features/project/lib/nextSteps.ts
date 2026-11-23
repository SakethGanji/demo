/**
 * Project onboarding, pure (no React): what counts as an empty project, how a project started, and the 2–4 next
 * steps that follow from that start. The guide (`SetupGuide`), the slim `NextSteps` strip, the outline count and
 * the home cards all read these, so the rules live in one place.
 *
 * Nothing is ever locked: a step is open, done, deferred ("Later") or dismissed ("Not for this project").
 * Secrets are never a default step; a `secret-<KEY>` step appears only when a key is missing.
 */

export const START_KINDS = ['interface', 'backend', 'workflow', 'dataset', 'template', 'ai'] as const
export type StartKind = (typeof START_KINDS)[number]

/** what the AI card builds */
export type AiTarget = 'workflow' | 'backend' | 'interface'
export const AI_TARGETS: AiTarget[] = ['workflow', 'backend', 'interface']

/** the shape the next steps follow (a template or the AI card resolves to one of these) */
export type Origin = 'interface' | 'backend' | 'workflow' | 'dataset'

/** `project.settings.onboarding` */
export interface Onboarding {
  start: StartKind | null
  deferred: string[]
  dismissed: string[]
  first_success_at: string | null
  /** the AI card's target chip */
  target?: AiTarget | null
  /** an engine template chosen on the home page, opened in the guide's template chooser */
  template?: string | null
  /** "Go to the Overview" in the guide: stop taking over before the first success */
  closed_at?: string | null
}

export const EMPTY_ONBOARDING: Onboarding = { start: null, deferred: [], dismissed: [], first_success_at: null }

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/** Reads whatever is stored (possibly partial, possibly from an older build) into a full `Onboarding`, or null. */
export function normalizeOnboarding(v: unknown): Onboarding | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const o = v as Record<string, unknown>
  const start = (START_KINDS as readonly string[]).includes(o.start as string) ? (o.start as StartKind) : null
  const target = (AI_TARGETS as string[]).includes(o.target as string) ? (o.target as AiTarget) : null
  return {
    start,
    deferred: strings(o.deferred),
    dismissed: strings(o.dismissed),
    first_success_at: strOrNull(o.first_success_at),
    target,
    template: strOrNull(o.template),
    closed_at: strOrNull(o.closed_at),
  }
}

export interface ArtifactCounts {
  workflows: number
  datasets: number
  tables: number
  modules: number
  routes: number
  apps: number
}

/** Empty means no artifact of any kind: a workflow, dataset, table, module, route or app each makes it non-empty. */
export function isProjectEmpty(c: Partial<ArtifactCounts>): boolean {
  return !((c.workflows ?? 0) || (c.datasets ?? 0) || (c.tables ?? 0) || (c.modules ?? 0) || (c.routes ?? 0) || (c.apps ?? 0))
}

/** What kind of first success a run is. */
export type SuccessKind = 'route' | 'workflow' | 'interface'

/** The kind of a successful run, from its trigger (route tests and draft tests are routes; app calls are the interface). */
export function successKind(run: { trigger?: { type?: string } | null; route_id?: string | null; mode?: string | null }): SuccessKind {
  const t = run.trigger?.type ?? run.mode ?? ''
  if (t === 'app' || t === 'app_preview') return 'interface'
  if (t === 'route_test' || t === 'draft_test' || t === 'generate_self_test') return 'route'
  if (run.route_id) return 'route'
  return 'workflow'
}

export function successMessage(kind: SuccessKind): string {
  if (kind === 'interface') return 'Your interface made its first call. Nice.'
  if (kind === 'route') return 'Your route answered. Nice.'
  return 'Your workflow ran. Nice.'
}

export interface ProjectFacts extends ArtifactCounts {
  start: StartKind | null
  target?: AiTarget | null
  /** routes with a real handler (python or workflow), not stubs */
  realRoutes: number
  /** routes handled by a workflow */
  workflowRoutes: number
  /** workflows whose trigger is on */
  activeWorkflows: number
  /** the kinds of successful runs seen */
  successes: SuccessKind[]
  /** a release is active in some environment */
  released: boolean
  /** the dataset report template is installed */
  reportInstalled: boolean
  /** secret keys something in the project needs and no environment has */
  missingSecrets: string[]
}

/** The shape the next steps follow. An explicit start wins; a template, the AI card without a target, or no start reads the artifacts. */
export function originOf(f: Pick<ProjectFacts, 'start' | 'target' | 'workflows' | 'datasets' | 'modules' | 'realRoutes' | 'routes' | 'apps'>): Origin {
  if (f.start === 'interface' || f.start === 'backend' || f.start === 'workflow' || f.start === 'dataset') return f.start
  if (f.start === 'ai' && f.target) return f.target
  if (f.datasets > 0 && f.modules === 0) return 'dataset'
  if (f.workflows > 0) return 'workflow'
  if (f.modules > 0 || f.realRoutes > 0) return 'backend'
  if (f.apps > 0 || f.routes > 0) return 'interface'
  return f.start === 'ai' ? 'workflow' : 'backend'
}

export type StepId =
  | 'run-workflow'
  | 'add-trigger'
  | 'add-interface'
  | 'release'
  | 'run-profile'
  | 'schedule'
  | 'share-report'
  | 'test-route'
  | 'wire-route'
  | 'add-workflow'
  | `secret-${string}`

export type StepState = 'open' | 'done' | 'deferred'

export interface NextStep {
  id: StepId
  title: string
  body: string
  state: StepState
  /** set on `secret-<KEY>` steps */
  secret?: string
}

type Core = Omit<NextStep, 'state'> & { done: boolean }

function coreSteps(origin: Origin, f: ProjectFacts): Core[] {
  const ran = (k: SuccessKind) => f.successes.includes(k)
  const addInterface: Core = {
    id: 'add-interface',
    title: 'Add an interface',
    body: 'A page people can use. Routes it calls that do not exist yet become stubs you fill in later.',
    done: f.apps > 0,
  }
  switch (origin) {
    case 'workflow':
      return [
        { id: 'run-workflow', title: 'Run it', body: 'Run the workflow once in the editor and see what every step returned.', done: ran('workflow') },
        {
          id: 'add-trigger',
          title: 'Give it a trigger or a route',
          body: 'A schedule, a webhook, or an HTTP route so it runs without you.',
          done: f.activeWorkflows > 0 || f.workflowRoutes > 0,
        },
        addInterface,
        { id: 'release', title: 'Release it', body: 'Freeze this version and promote it to the live environment.', done: f.released },
      ]
    case 'dataset':
      return [
        { id: 'run-profile', title: 'Run the profile', body: 'Size, duplicates and missing values for the file you imported.', done: ran('workflow') },
        { id: 'schedule', title: 'Schedule it', body: 'Turn on a schedule so the profile reruns on its own.', done: f.activeWorkflows > 0 },
        { id: 'share-report', title: 'Share the report', body: 'Email a written report of the dataset to whoever needs it.', done: f.reportInstalled },
      ]
    case 'interface':
      return [
        {
          id: 'wire-route',
          title: 'Wire a route',
          body: 'Give the stub routes your interface calls a real handler, in Python or a workflow.',
          done: f.realRoutes > 0,
        },
        { id: 'add-workflow', title: 'Add a workflow behind it', body: 'Let a route start a workflow: approvals, emails, anything with steps.', done: f.workflows > 0 },
      ]
    case 'backend':
      return [
        { id: 'test-route', title: 'Test a route', body: 'Send a request and see the response plus every node call it made.', done: ran('route') },
        addInterface,
      ]
  }
}

/**
 * The next steps for a project: one `secret-<KEY>` step per missing secret, then 2–4 steps for how it started.
 * Dismissed steps are gone; deferred ones that are not done move to the end. Done steps keep their place.
 */
export function nextSteps(f: ProjectFacts, prefs: Pick<Onboarding, 'deferred' | 'dismissed'> = EMPTY_ONBOARDING): NextStep[] {
  const dismissed = new Set(prefs.dismissed)
  const deferred = new Set(prefs.deferred)
  const secrets: Core[] = [...new Set(f.missingSecrets)].map((key) => ({
    id: `secret-${key}`,
    title: `Needs ${key}`,
    body: 'Something in this project reads this secret and no environment has it yet. Add it when you have it; nothing waits on it.',
    secret: key,
    done: false,
  }))
  const all = [...secrets, ...coreSteps(originOf(f), f)].filter((s) => !dismissed.has(s.id))
  const withState = all.map(({ done, ...s }): NextStep => ({ ...s, state: done ? 'done' : deferred.has(s.id) ? 'deferred' : 'open' }))
  return [...withState.filter((s) => s.state !== 'deferred'), ...withState.filter((s) => s.state === 'deferred')]
}

/** Keywords that point the AI card at a target. */
const TARGET_WORDS: Record<AiTarget, RegExp> = {
  interface: /\b(ui|interface|page|pages|dashboard|form|forms|screen|screens|frontend|front-end|portal|app|button|chart|admin panel)\b/g,
  backend: /\b(api|apis|endpoint|endpoints|route|routes|rest|crud|backend|database|table|tables|json)\b|\b(?:get|post|put|patch|delete)\s+\//g,
  workflow: /\b(when|whenever|every|daily|weekly|hourly|schedule|scheduled|email|emails|notify|notification|slack|then|workflow|automate|automation|approval|approve|remind|sync)\b/g,
}

/** The AI card's target, guessed from the prompt; a workflow when nothing points elsewhere. Ties prefer workflow, then backend. */
export function guessTarget(text: string): AiTarget {
  const t = text.toLowerCase()
  const score = (k: AiTarget) => (t.match(TARGET_WORDS[k]) ?? []).length
  const order: AiTarget[] = ['workflow', 'backend', 'interface']
  let best: AiTarget = 'workflow'
  for (const k of order) if (score(k) > score(best)) best = k
  return best
}
