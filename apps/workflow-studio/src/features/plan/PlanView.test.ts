/**
 * The Plan page's gates, rendered to static markup (the unit suite runs in node, without a DOM): Approve is disabled while
 * the plan has errors (or open questions, or unsaved edits), the breaking-change confirmation names what it affects, and
 * the build order states and Build link follow the conformance check.
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/api/http', () => ({
  ApiError: class ApiError extends Error {},
  errorMessage: (e: unknown) => String(e),
  request: vi.fn(),
  isApiError: () => false,
}))

const { ApproveBar, BreakingConfirmBody } = await import('./PlanView')
const { approveGate, breakingSummary, buildHref, buildModule, buildSearch, initialAnswers, issuesAt, moduleStates, unanswered } = await import('./planLogic')
import type { PlanBody, PlanDiffResult, PlanIssue } from './planTypes'

const err = (path: string): PlanIssue => ({ code: 'PLAN003', path, message: 'route belongs to no planned module', severity: 'error', needs_input: false })
const warn = (path: string): PlanIssue => ({ code: 'PLAN009', path, message: 'no errors declared', severity: 'warning', needs_input: false })

function bar(gate: ReturnType<typeof approveGate>, errors: number) {
  return renderToStaticMarkup(
    createElement(ApproveBar, { gate, dirty: false, saving: false, approving: false, errors, onSave: () => {}, onApprove: () => {} }),
  )
}

const approveButton = (html: string) => html.match(/<button[^>]*data-testid="plan-approve"[^>]*>/)?.[0] ?? ''

describe('Approve', () => {
  it('is disabled while there are errors, and says why', () => {
    const issues = [err('routes.GET /x'), warn('routes.POST /y')]
    const gate = approveGate({ status: 'draft', questions: [], issues, dirty: false })
    expect(gate).toEqual({ allowed: false, reason: 'Fix 1 error first' })
    const html = bar(gate, 1)
    expect(approveButton(html)).toMatch(/\sdisabled=""/)
    expect(html).toContain('1 error')
    expect(html).toContain('Fix 1 error first')
  })

  it('is enabled with warnings only', () => {
    const gate = approveGate({ status: 'draft', questions: [], issues: [warn('routes.POST /y')], dirty: false })
    expect(gate.allowed).toBe(true)
    expect(approveButton(bar(gate, 0))).not.toMatch(/\sdisabled=""/)
  })

  it('waits for open questions, unsaved edits and refuses non-drafts', () => {
    expect(approveGate({ status: 'draft', questions: [{ id: 'q', topic: 'roles', question: '?' }], issues: [], dirty: false }).allowed).toBe(false)
    expect(approveGate({ status: 'draft', questions: [], issues: [], dirty: true }).reason).toBe('Save your edits first')
    expect(approveGate({ status: 'approved', questions: [], issues: [], dirty: false }).allowed).toBe(false)
  })
})

describe('breaking confirmation', () => {
  const diff: PlanDiffResult = {
    breaking: true,
    changes: [
      { kind: 'route', key: 'POST /triage/{id}/retry', change: 'removed', breaking: true },
      { kind: 'column', key: 'tests.owner', change: 'added', breaking: false },
    ],
    affected: { modules: ['triage'], apps: ['qa_workbench'], tables: [] },
  }
  it('lists the breaking changes and what they affect', () => {
    expect(breakingSummary(diff)).toBe('1 breaking change; affects modules triage and apps qa_workbench')
    const html = renderToStaticMarkup(createElement(BreakingConfirmBody, { diff }))
    expect(html).toContain('POST /triage/{id}/retry')
    expect(html).not.toContain('tests.owner')
    expect(html).toContain('affects modules triage and apps qa_workbench')
  })
})

describe('build order', () => {
  const body = { modules: [{ key: 'common' }, { key: 'runs' }, { key: 'triage' }, { key: 'tests_library' }] } as unknown as PlanBody
  it('is unknown without a check, else built / partial / stale / not built', () => {
    expect(moduleStates(body, null)).toEqual({ common: 'unknown', runs: 'unknown', triage: 'unknown', tests_library: 'unknown' })
    const check = {
      has_plan: true,
      ok: false,
      missing: { modules: ['triage'], routes: [{ route: 'GET /runs', module: 'runs', reason: 'missing' }] },
      stale: [{ module: 'tests_library', built_against: 1, approved: 2, affected_by: '' }],
    }
    expect(moduleStates(body, check)).toEqual({ common: 'built', runs: 'partial', triage: 'not built', tests_library: 'stale' })
  })
  it('Build opens a new backend module generated from the plan', () => {
    expect(buildSearch('runs')).toEqual({ prompt: 'Build the runs module from the approved plan [plan:runs]', generate: 1 })
    expect(buildHref('qa', 'runs')).toBe(
      '/p/qa/backend/~new?prompt=Build%20the%20runs%20module%20from%20the%20approved%20plan%20%5Bplan%3Aruns%5D&generate=1',
    )
  })
  it('Rebuild opens the module that exists, so it is saved over instead of copied', () => {
    expect(buildModule('runs', 'not built')).toBe('~new')
    expect(buildModule('runs', 'unknown')).toBe('~new')
    expect(buildModule('runs', 'built')).toBe('runs')
    expect(buildModule('runs', 'partial')).toBe('runs')
    expect(buildModule('runs', 'stale')).toBe('runs')
  })
})

describe('questions and issues', () => {
  it('start from defaults and need every answer', () => {
    const qs = [
      { id: 'a', topic: 'roles', question: '?', default: 'leads' },
      { id: 'b', topic: 'safety', question: '?', options: ['dry run first'] },
      { id: 'c', topic: 'other', question: '?' },
    ]
    const a = initialAnswers(qs)
    expect(a).toEqual({ a: 'leads', b: 'dry run first', c: '' })
    expect(unanswered(qs, a)).toEqual(['c'])
  })
  it('are found under their item', () => {
    const issues = [err('data.tables.runs.columns.status'), err('data.tables.runs_archive'), warn('environments')]
    expect(issuesAt(issues, 'data.tables.runs').map((i) => i.path)).toEqual(['data.tables.runs.columns.status'])
    expect(issuesAt(issues, 'environments', true)).toHaveLength(1)
  })
})
