/**
 * The pure logic behind the requirements-first Plan page: where the project is in Describe → Questions → Review →
 * Approved → Building, how far a question round is, requirement statements from the plan, and the build progress.
 */
import { describe, expect, it } from 'vitest'
import {
  buildProgress,
  confirmSuggested,
  coversLabel,
  groupByTopic,
  humanize,
  issueWhere,
  planStep,
  questionProgress,
  requirementGroups,
  smokeProven,
  stepStates,
} from './planLogic'
import type { ModuleBuildState, PlanBody, PlanQuestion } from './planTypes'

const q = (id: string, topic: string, extra: Partial<PlanQuestion> = {}): PlanQuestion => ({ id, topic, question: `${id}?`, ...extra })

describe('plan step', () => {
  const none: Record<string, ModuleBuildState> = {}
  it('is Describe with nothing, Questions with open questions, Review without', () => {
    expect(planStep({ draft: null, approved: null, states: none })).toBe('describe')
    expect(planStep({ draft: { questions: [q('a', 'roles')] }, approved: null, states: none })).toBe('questions')
    expect(planStep({ draft: { questions: [] }, approved: null, states: none })).toBe('review')
  })
  it('a revision draft is back in Review even though a version is approved', () => {
    expect(planStep({ draft: { questions: [] }, approved: {}, states: { runs: 'built' } })).toBe('review')
  })
  it('is Approved until any module is built, then Building', () => {
    expect(planStep({ draft: null, approved: {}, states: { runs: 'unknown', triage: 'not built' } })).toBe('approved')
    expect(planStep({ draft: null, approved: {}, states: { runs: 'partial', triage: 'not built' } })).toBe('building')
    expect(planStep({ draft: null, approved: {}, states: { runs: 'stale' } })).toBe('building')
  })
  it('marks the steps before the current one done and the rest pending', () => {
    expect(stepStates('review').map((s) => `${s.key}:${s.state}`)).toEqual([
      'describe:done',
      'questions:done',
      'review:current',
      'approved:pending',
      'building:pending',
    ])
    expect(stepStates('building').at(-1)?.state).toBe('current')
    expect(stepStates('building', { buildDone: true }).every((s) => s.state === 'done')).toBe(true)
    expect(stepStates('describe')[0]).toMatchObject({ key: 'describe', label: 'Describe', state: 'current' })
  })
})

describe('question round', () => {
  const qs = [q('a', 'roles', { default: 'leads' }), q('b', 'safety', { options: ['dry run'] }), q('c', 'roles'), q('d', 'data_retention')]
  it('groups by topic in first-seen order with readable labels', () => {
    const g = groupByTopic(qs)
    expect(g.map((x) => x.label)).toEqual(['Roles', 'Safety', 'Data retention'])
    expect(g[0].questions.map((x) => x.id)).toEqual(['a', 'c'])
  })
  it('counts answered (confirmed by the user), suggested (prefilled, not yet confirmed) and empty', () => {
    const answers = { a: 'leads', b: 'dry run', c: 'testers', d: '' }
    expect(questionProgress(qs, answers, new Set(['c']))).toEqual({ total: 4, answered: 1, suggested: 2, empty: 1 })
    // a confirmed but emptied answer is not answered
    expect(questionProgress(qs, { ...answers, c: ' ' }, new Set(['c'])).answered).toBe(0)
  })
  it('"use suggested answers" confirms every prefilled answer and leaves empty ones open', () => {
    const answers = { a: 'leads', b: 'dry run', c: 'testers', d: '' }
    const next = confirmSuggested(qs, answers, new Set(['c']))
    expect([...next].sort()).toEqual(['a', 'b', 'c'])
    expect(questionProgress(qs, answers, next)).toEqual({ total: 4, answered: 3, suggested: 0, empty: 1 })
  })
})

describe('requirements', () => {
  const body = {
    modules: [
      { key: 'common', kind: 'shared', purpose: '' },
      { key: 'tests_library', kind: 'routes', purpose: 'the test library' },
      { key: 'runs', kind: 'routes', purpose: '' },
    ],
    routes: [
      { key: 'GET /tests', module: 'tests_library', purpose: 'list tests' },
      { key: 'POST /runs', module: 'runs', purpose: 'start a run' },
      { key: 'GET /runs/{id}', module: 'runs', purpose: '' },
      { key: 'GET /x', module: 'gone', purpose: 'stray' },
    ],
  } as unknown as PlanBody
  it('are the routes as sentences, grouped by area, skipping shared modules without routes', () => {
    const g = requirementGroups(body)
    expect(g.map((x) => x.title)).toEqual(['Tests library', 'Runs', 'Other'])
    expect(g[0]).toMatchObject({ purpose: 'The test library', items: [{ text: 'List tests', route: 'GET /tests' }] })
    expect(g[1].items.map((i) => i.text)).toEqual(['Start a run', 'GET /runs/{id}'])
    expect(g[2].items).toEqual([{ text: 'Stray', route: 'GET /x' }])
  })
  it('reads keys, covers and issue paths in plain words', () => {
    expect(humanize('tests_library')).toBe('Tests library')
    expect(coversLabel('route:POST /runs')).toBe('POST /runs')
    expect(coversLabel('screen:Library')).toBe('Library screen')
    expect(coversLabel('free')).toBe('free')
    expect(issueWhere('data.tables.runs.columns.status')).toBe('Table runs, column status')
    expect(issueWhere('data.tables.runs')).toBe('Table runs')
    expect(issueWhere('routes.POST /runs')).toBe('Route POST /runs')
    expect(issueWhere('apps.qa.screens.Library')).toBe('Screen Library')
    expect(issueWhere('secrets.BANK_USER')).toBe('Secret BANK_USER')
    expect(issueWhere('build_order')).toBe('Build order')
  })
})

describe('build progress', () => {
  it('counts built modules in the build order, apps aside', () => {
    expect(buildProgress(['common', 'runs', 'triage', 'app:qa'], { common: 'built', runs: 'partial', triage: 'not built' })).toEqual({
      built: 1,
      total: 3,
    })
    expect(buildProgress([], {})).toEqual({ built: 0, total: 0 })
  })
})

describe('smoke proof', () => {
  it('is proven only when every integration passed (per-integration map or a single status)', () => {
    expect(smokeProven(null, ['jobs'])).toBe(false)
    expect(smokeProven({ jobs: 'not_run' }, ['jobs'])).toBe(false)
    expect(smokeProven({ jobs: 'passed' }, ['jobs'])).toBe(true)
    expect(smokeProven({ jobs: 'passed' }, ['jobs', 'mail'])).toBe(false)
    expect(smokeProven({ jobs: { status: 'passed' } }, ['jobs'])).toBe(true)
    expect(smokeProven({ status: 'passed' }, ['jobs'])).toBe(true)
    expect(smokeProven(undefined, [])).toBe(true)
  })
})
