import { describe, expect, it } from 'vitest'
import {
  EMPTY_ONBOARDING,
  guessTarget,
  isProjectEmpty,
  nextSteps,
  normalizeOnboarding,
  originOf,
  successKind,
  successMessage,
  type ProjectFacts,
} from './nextSteps'

const facts = (over: Partial<ProjectFacts> = {}): ProjectFacts => ({
  start: null,
  target: null,
  workflows: 0,
  datasets: 0,
  tables: 0,
  modules: 0,
  routes: 0,
  apps: 0,
  realRoutes: 0,
  workflowRoutes: 0,
  activeWorkflows: 0,
  successes: [],
  released: false,
  reportInstalled: false,
  missingSecrets: [],
  ...over,
})

const ids = (f: ProjectFacts, prefs = EMPTY_ONBOARDING) => nextSteps(f, prefs).map((s) => s.id)

describe('isProjectEmpty', () => {
  it('is empty only with no artifact of any kind', () => {
    expect(isProjectEmpty({})).toBe(true)
    expect(isProjectEmpty({ workflows: 0, datasets: 0, tables: 0, modules: 0, routes: 0, apps: 0 })).toBe(true)
  })
  it.each(['workflows', 'datasets', 'tables', 'modules', 'routes', 'apps'] as const)('one %s makes it non-empty', (k) => {
    expect(isProjectEmpty({ [k]: 1 })).toBe(false)
  })
})

describe('nextSteps', () => {
  it('workflow start: run it, trigger or route, interface, release', () => {
    expect(ids(facts({ start: 'workflow', workflows: 1 }))).toEqual(['run-workflow', 'add-trigger', 'add-interface', 'release'])
  })
  it('dataset start: profile, schedule, share', () => {
    expect(ids(facts({ start: 'dataset', datasets: 1 }))).toEqual(['run-profile', 'schedule', 'share-report'])
  })
  it('backend start: test a route, add an interface', () => {
    expect(ids(facts({ start: 'backend', modules: 1 }))).toEqual(['test-route', 'add-interface'])
  })
  it('interface start: wire a route, add a workflow behind it', () => {
    expect(ids(facts({ start: 'interface', apps: 1 }))).toEqual(['wire-route', 'add-workflow'])
  })
  it('never proposes a secret step by default', () => {
    for (const start of ['workflow', 'dataset', 'backend', 'interface'] as const) expect(ids(facts({ start })).some((i) => i.startsWith('secret-'))).toBe(false)
  })
  it('adds one step per missing secret, first, deduplicated', () => {
    const steps = nextSteps(facts({ start: 'backend', missingSecrets: ['STRIPE_KEY', 'STRIPE_KEY', 'SLACK_TOKEN'] }))
    expect(steps.map((s) => s.id)).toEqual(['secret-STRIPE_KEY', 'secret-SLACK_TOKEN', 'test-route', 'add-interface'])
    expect(steps[0]).toMatchObject({ title: 'Needs STRIPE_KEY', secret: 'STRIPE_KEY', state: 'open' })
  })
  it('marks steps done from the facts', () => {
    const s = nextSteps(facts({ start: 'workflow', workflows: 1, successes: ['workflow'], activeWorkflows: 1, apps: 1, released: true }))
    expect(s.every((x) => x.state === 'done')).toBe(true)
    expect(nextSteps(facts({ start: 'backend', successes: ['route'] }))[0].state).toBe('done')
    expect(nextSteps(facts({ start: 'interface', realRoutes: 1 }))[0].state).toBe('done')
    expect(nextSteps(facts({ start: 'workflow', workflowRoutes: 1 })).find((x) => x.id === 'add-trigger')!.state).toBe('done')
  })
  it('a route test does not finish "run it" for a workflow', () => {
    expect(nextSteps(facts({ start: 'workflow', successes: ['route'] }))[0].state).toBe('open')
  })
  it('dismissed steps disappear; deferred open steps move to the end', () => {
    const prefs = { deferred: ['run-workflow'], dismissed: ['release'] }
    const s = nextSteps(facts({ start: 'workflow' }), prefs)
    expect(s.map((x) => x.id)).toEqual(['add-trigger', 'add-interface', 'run-workflow'])
    expect(s[2].state).toBe('deferred')
  })
  it('a deferred step that is done counts as done and keeps its place', () => {
    const s = nextSteps(facts({ start: 'backend', successes: ['route'] }), { deferred: ['test-route'], dismissed: [] })
    expect(s.map((x) => [x.id, x.state])).toEqual([['test-route', 'done'], ['add-interface', 'open']])
  })
})

describe('originOf', () => {
  it('an explicit start wins', () => {
    expect(originOf(facts({ start: 'interface', workflows: 3 }))).toBe('interface')
  })
  it('the AI card follows its target', () => {
    expect(originOf(facts({ start: 'ai', target: 'backend' }))).toBe('backend')
  })
  it('a template or no start reads the artifacts', () => {
    expect(originOf(facts({ start: 'template', workflows: 1 }))).toBe('workflow')
    expect(originOf(facts({ start: 'template', workflows: 1, datasets: 1 }))).toBe('dataset')
    expect(originOf(facts({ modules: 1, realRoutes: 2 }))).toBe('backend')
    expect(originOf(facts({ apps: 1 }))).toBe('interface')
    expect(originOf(facts())).toBe('backend')
  })
})

describe('successKind / successMessage', () => {
  it('classifies runs by trigger', () => {
    expect(successKind({ trigger: { type: 'route_test' } })).toBe('route')
    expect(successKind({ trigger: { type: 'draft_test' } })).toBe('route')
    expect(successKind({ trigger: { type: 'app_preview' } })).toBe('interface')
    expect(successKind({ trigger: { type: 'manual' } })).toBe('workflow')
    expect(successKind({ trigger: null, mode: 'manual' })).toBe('workflow')
    expect(successKind({ trigger: { type: 'public' }, route_id: 'r1' })).toBe('route')
  })
  it('writes the toast for what ran', () => {
    expect(successMessage('workflow')).toBe('Your workflow ran. Nice.')
    expect(successMessage('route')).toMatch(/route/)
    expect(successMessage('interface')).toMatch(/interface/)
  })
})

describe('guessTarget', () => {
  it('guesses from keywords', () => {
    expect(guessTarget('A dashboard page with a form for refunds')).toBe('interface')
    expect(guessTarget('A REST API with CRUD endpoints over a table')).toBe('backend')
    expect(guessTarget('POST /refunds stores one')).toBe('backend')
    expect(guessTarget('Every morning email me the new signups')).toBe('workflow')
  })
  it('defaults to a workflow', () => {
    expect(guessTarget('')).toBe('workflow')
    expect(guessTarget('something nice')).toBe('workflow')
  })
})

describe('normalizeOnboarding', () => {
  it('rejects non-objects', () => {
    expect(normalizeOnboarding(null)).toBeNull()
    expect(normalizeOnboarding('x')).toBeNull()
    expect(normalizeOnboarding([])).toBeNull()
  })
  it('fills defaults and drops junk', () => {
    expect(normalizeOnboarding({ start: 'nope', deferred: ['a', 3], dismissed: 'x', first_success_at: '' })).toEqual({
      start: null,
      deferred: ['a'],
      dismissed: [],
      first_success_at: null,
      target: null,
      template: null,
      closed_at: null,
    })
    expect(normalizeOnboarding({ start: 'dataset', target: 'backend', first_success_at: '2026-10-18T00:00:00Z' })).toMatchObject({
      start: 'dataset',
      target: 'backend',
      first_success_at: '2026-10-18T00:00:00Z',
    })
  })
})
