/**
 * The build screen's pure parts: the SSE reducer (snapshot → step updates → logs → done), progress and elapsed time,
 * the primary build action, step links, the report's plan-check lines, and where "New app" goes.
 */
import { describe, expect, it } from 'vitest'
import {
  activeBuild,
  applyBuildEvent,
  buildAction,
  buildCounts,
  formatElapsed,
  flowPhase,
  isActive,
  missingLines,
  newAppTarget,
  pickAppKey,
  waitingStep,
  stepElapsed,
  stepLink,
} from './buildLogic'
import type { Build, BuildStep } from './buildTypes'

const step = (key: string, over: Partial<BuildStep> = {}): BuildStep => ({
  key,
  kind: key.startsWith('app:') ? 'app' : key.startsWith('verify:') ? 'verify' : key.startsWith('fix:') ? 'fix' : 'module',
  title: key,
  builder: key.startsWith('app:') ? 'ui' : key.startsWith('verify:') ? 'check' : 'backend',
  status: 'pending',
  attempt: 1,
  started_at: null,
  finished_at: null,
  summary: null,
  problems: [],
  ...over,
})

const build = (over: Partial<Build> = {}): Build => ({
  id: 'b1',
  project_id: 'p1',
  plan_version: 2,
  app_key: null,
  status: 'running',
  reason: null,
  created_at: '2026-10-20T10:00:00Z',
  started_at: '2026-10-20T10:00:00Z',
  finished_at: null,
  steps: [step('module:common'), step('module:runs'), step('app:workbench')],
  report: null,
  ...over,
})

describe('applyBuildEvent', () => {
  it('starts from the snapshot, then replaces steps by key, appends logs and ends with done', () => {
    let s = applyBuildEvent(null, { type: 'snapshot', build: build() })
    expect(s?.steps.map((x) => x.status)).toEqual(['pending', 'pending', 'pending'])

    s = applyBuildEvent(s, { type: 'step', step: step('module:common', { status: 'running', started_at: '2026-10-20T10:00:01Z' }) })
    expect(s?.steps[0].status).toBe('running')

    s = applyBuildEvent(s, { type: 'log', step_key: 'module:common', message: 'generating routes' })
    s = applyBuildEvent(s, { type: 'log', step_key: 'module:common', message: 'saving' })
    expect(s?.steps[0].log).toEqual(['generating routes', 'saving'])

    // a step update without a log keeps the lines already streamed
    s = applyBuildEvent(s, { type: 'step', step: step('module:common', { status: 'passed', summary: '4 routes, check passed' }) })
    expect(s?.steps[0]).toMatchObject({ status: 'passed', summary: '4 routes, check passed', log: ['generating routes', 'saving'] })

    // a step the snapshot did not have (a verify round) is appended
    s = applyBuildEvent(s, { type: 'step', step: step('verify:1', { status: 'running' }) })
    expect(s?.steps.map((x) => x.key)).toEqual(['module:common', 'module:runs', 'app:workbench', 'verify:1'])

    const done = build({ status: 'partial', steps: s!.steps.map((x) => ({ ...x, log: undefined })), finished_at: '2026-10-20T10:05:00Z' })
    s = applyBuildEvent(s, { type: 'done', build: done })
    expect(s?.status).toBe('partial')
    expect(s?.steps[0].log).toEqual(['generating routes', 'saving'])
  })

  it('caps the log at 50 lines and ignores events before the snapshot', () => {
    let s = applyBuildEvent(null, { type: 'snapshot', build: build() })
    for (let n = 0; n < 60; n++) s = applyBuildEvent(s, { type: 'log', step_key: 'module:runs', message: `line ${n}` })
    expect(s?.steps[1].log).toHaveLength(50)
    expect(s?.steps[1].log?.[49]).toBe('line 59')
    expect(applyBuildEvent(null, { type: 'step', step: step('module:x') })).toBeNull()
  })
})

describe('progress and time', () => {
  it('counts finished steps out of all', () => {
    const b = build({ steps: [step('a', { status: 'passed' }), step('b', { status: 'failed' }), step('c', { status: 'skipped' }), step('d', { status: 'running' }), step('e')] })
    expect(buildCounts(b)).toEqual({ finished: 3, total: 5, failed: 1 })
  })

  it('elapsed runs to now while running and stops at finished_at', () => {
    const now = Date.parse('2026-10-20T10:01:05Z')
    expect(stepElapsed(step('a'), now)).toBeNull()
    expect(stepElapsed(step('a', { started_at: '2026-10-20T10:00:00Z', status: 'running' }), now)).toBe(65_000)
    expect(stepElapsed(step('a', { started_at: '2026-10-20T10:00:00Z', finished_at: '2026-10-20T10:00:09Z' }), now)).toBe(9_000)
    expect(formatElapsed(9_000)).toBe('9s')
    expect(formatElapsed(65_000)).toBe('1m 05s')
    expect(formatElapsed(3_725_000)).toBe('1h 02m')
  })

  it('knows active builds and finds the running one', () => {
    expect(isActive('queued') && isActive('running')).toBe(true)
    expect(isActive('waiting_for_input')).toBe(true)
    expect(isActive('partial')).toBe(false)
    const list = [build({ id: 'b3', status: 'running' }), build({ id: 'b2', status: 'done' })]
    expect(activeBuild(list)?.id).toBe('b3')
    expect(activeBuild([build({ status: 'failed' })])).toBeNull()
  })
})

describe('the build action', () => {
  it('is Build it, Build remaining or Build again, and opens a running build instead', () => {
    expect(buildAction({ built: 0, total: 4 }, null)).toEqual({ kind: 'start', label: 'Build it' })
    expect(buildAction({ built: 2, total: 4 }, null)).toEqual({ kind: 'start', label: 'Build remaining' })
    expect(buildAction({ built: 4, total: 4 }, null)).toEqual({ kind: 'start', label: 'Build again' })
    expect(buildAction({ built: 1, total: 4 }, build({ id: 'b9' }))).toEqual({ kind: 'open', label: 'Open the build', id: 'b9' })
  })
})

describe('step links and the report', () => {
  it('links a module step to its module by key, an app step to its app', () => {
    expect(stepLink(step('module:runs'))).toEqual({ kind: 'module', name: 'runs' })
    expect(stepLink(step('fix:tests_library:1'))).toEqual({ kind: 'module', name: 'tests_library' })
    expect(stepLink(step('app:workbench', { app_id: 'a1' }))).toEqual({ kind: 'app', id: 'a1' })
    expect(stepLink(step('app:workbench'))).toBeNull()
    expect(stepLink(step('verify:1'))).toBeNull()
  })

  it('says what the plan check still lists in plain words', () => {
    expect(
      missingLines({
        routes: [{ route: 'POST /runs', module: 'runs' }, 'GET /tests'],
        screens: [{ app: 'workbench', screen: 'Triage' }],
        modules: [],
      }),
    ).toEqual([
      { kind: 'Routes', items: ['POST /runs', 'GET /tests'] },
      { kind: 'Screens', items: ['Triage'] },
    ])
  })
})

describe('New app', () => {
  it('describes without a plan, revises an approved one, continues an open draft', () => {
    expect(newAppTarget({ draft: null, approved: null })).toBe('describe')
    expect(newAppTarget({ draft: null, approved: { version: 1 } })).toBe('revise')
    expect(newAppTarget({ draft: { version: 2 }, approved: { version: 1 } })).toBe('draft')
  })
})

describe('the app builder flow', () => {
  const draft = (questions: number) => ({ questions: Array.from({ length: questions }, (_, n) => ({ id: `q${n}`, topic: 't', question: '?' })) })
  it('goes describe → drafting → questions → review → building → input → finished', () => {
    expect(flowPhase({ started: false, draft: null, build: null })).toBe('describe')
    expect(flowPhase({ started: true, draft: null, build: null })).toBe('drafting')
    expect(flowPhase({ started: true, draft: draft(2), build: null })).toBe('questions')
    expect(flowPhase({ started: true, draft: draft(0), build: null })).toBe('review')
    expect(flowPhase({ started: true, draft: null, build: build({ status: 'queued' }) })).toBe('building')
    expect(flowPhase({ started: true, draft: null, build: build({ status: 'waiting_for_input' }) })).toBe('input')
    expect(flowPhase({ started: true, draft: null, build: build({ status: 'partial' }) })).toBe('finished')
  })

  it('finds the step that waits for input', () => {
    const input = { secrets: [{ name: 'BANK_USER', purpose: 'bank login', set: false }], tables: [], message: 'Before the UI' }
    const b = build({ status: 'waiting_for_input', steps: [step('module:runs', { status: 'passed' }), step('input:1', { kind: 'input', status: 'waiting_for_input', input })] })
    expect(waitingStep(b)?.input?.secrets[0].name).toBe('BANK_USER')
    expect(waitingStep(build())).toBeNull()
  })

  it('builds the one app the plan has not built yet, else every app', () => {
    const app = (key: string, app_id: string | null) => ({ key, name: key, app_id, users: '', screens: [] })
    expect(pickAppKey([app('workbench', null)])).toBe('workbench')
    expect(pickAppKey([app('admin', 'a1'), app('workbench', null)])).toBe('workbench')
    expect(pickAppKey([app('a', null), app('b', null)])).toBeUndefined()
    expect(pickAppKey([])).toBeUndefined()
  })
})
