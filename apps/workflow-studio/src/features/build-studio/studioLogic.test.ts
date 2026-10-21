import { describe, expect, it } from 'vitest'
import type { Build, BuildStep } from '@/features/plan/buildTypes'
import { builtAppId, EXAMPLES, followTarget, previewStage, readyApp, stepOf, studioView } from './studioLogic'

const step = (over: Partial<BuildStep>): BuildStep => ({ key: 'module:runs', kind: 'module', title: 'Backend · Runs', status: 'pending', ...over }) as BuildStep
const build = (status: Build['status'], steps: BuildStep[]): Build =>
  ({ id: 'b1', project_id: 'p', plan_version: 1, app_key: null, status, reason: null, created_at: '', started_at: null, finished_at: null, steps, report: null }) as Build

describe('studioView', () => {
  it('is the centred prompt only before anything has started; every later phase is the workspace', () => {
    expect(studioView('describe')).toBe('hero')
    for (const p of ['drafting', 'questions', 'review', 'building', 'input', 'finished'] as const) expect(studioView(p)).toBe('workspace')
  })
})

describe('builtAppId and readyApp', () => {
  const app = step({ key: 'app:w', kind: 'app', title: 'UI · Workbench', status: 'running', app_id: 'app_1' })
  it('the app the build made, once its UI step has started', () => {
    expect(builtAppId(null)).toBeNull()
    expect(builtAppId(build('running', [step({ status: 'passed' })]))).toBeNull()
    expect(builtAppId(build('running', [step({ status: 'passed' }), { ...app, status: 'pending' }]))).toBeNull()
    expect(builtAppId(build('running', [step({ status: 'passed' }), app]))).toBe('app_1')
  })
  it('ready only when the build is done and made an app (partial and failed keep the report)', () => {
    expect(readyApp(build('done', [step({ status: 'passed' }), { ...app, status: 'passed' }]))).toBe('app_1')
    expect(readyApp(build('partial', [{ ...app, status: 'partial' }]))).toBeNull()
    expect(readyApp(build('running', [app]))).toBeNull()
    expect(readyApp(build('done', [step({ status: 'passed' })]))).toBeNull()
  })
})

describe('previewStage', () => {
  it('waits for a build, shows the API panel until the app exists, a skeleton until its first files, then the app', () => {
    expect(previewStage({ build: null, appId: null, files: 0 })).toBe('waiting')
    expect(previewStage({ build: build('running', []), appId: null, files: 0 })).toBe('api')
    expect(previewStage({ build: build('running', []), appId: 'a', files: 0 })).toBe('skeleton')
    expect(previewStage({ build: build('running', []), appId: 'a', files: 3 })).toBe('app')
    // a finished conversation reopened later still shows its app
    expect(previewStage({ build: null, appId: 'a', files: 3 })).toBe('app')
  })
})

describe('followTarget', () => {
  it('follows the newest write while on; otherwise (or before any write) the picked file', () => {
    expect(followTarget(true, 'backend/runs.py', 'PLAN.md')).toBe('backend/runs.py')
    expect(followTarget(false, 'backend/runs.py', 'PLAN.md')).toBe('PLAN.md')
    expect(followTarget(true, null, 'PLAN.md')).toBe('PLAN.md')
    expect(followTarget(true, null, null)).toBeNull()
  })
})

describe('stepOf', () => {
  it('finds a file’s step by key', () => {
    const b = build('running', [step({}), step({ key: 'app:w', kind: 'app', title: 'UI · W' })])
    expect(stepOf(b, 'app:w')?.title).toBe('UI · W')
    expect(stepOf(b, null)).toBeNull()
    expect(stepOf(null, 'app:w')).toBeNull()
  })
})

describe('EXAMPLES', () => {
  it('offers a few short starting prompts', () => {
    expect(EXAMPLES.length).toBeGreaterThanOrEqual(3)
    for (const e of EXAMPLES) {
      expect(e.label.length).toBeLessThan(32)
      expect(e.prompt.length).toBeGreaterThan(e.label.length)
    }
  })
})
