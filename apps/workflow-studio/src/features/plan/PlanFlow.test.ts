/**
 * The app builder's plan-and-build cards, rendered to static markup (the unit suite runs in node, without a DOM): the
 * composer hint for each phase, the review card's counts and Approve gate, the input card a paused build shows
 * (secrets still to set, tables to seed, Continue), and the build timeline and report.
 */
import { createElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/api/http', () => ({
  ApiError: class ApiError extends Error {},
  errorMessage: (e: unknown) => String(e),
  request: vi.fn(),
  isApiError: () => false,
  apiUrl: (p: string) => p,
  parseError: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, className }: { children?: ReactNode; className?: string }) => createElement('a', { className }, children),
}))
vi.mock('@/features/project/ProjectContext', () => ({
  useProjectCtx: () => ({ pid: 'p1', slug: 'demo', project: { environments: [{ name: 'dev' }, { name: 'prod' }] } }),
}))
vi.mock('@/features/secrets/useSecretMutations', () => ({ useCreateSecrets: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('./useBuild', () => ({
  useBuildMutations: () => ({ proceed: { mutate: vi.fn(), isPending: false }, start: {}, cancel: {} }),
  useBuilds: () => ({ data: [] }),
  useLiveBuild: () => ({ build: null }),
  useNow: () => 0,
}))

const { BuildInputCard, FlowReview } = await import('./PlanFlow')
const { composerHint } = await import('./usePlanFlow')
const { BuildReportCard, BuildTimeline } = await import('./BuildTimeline')
import type { Build, BuildStep } from './buildTypes'
import type { PlanBody, PlanIssue, PlanVersion } from './planTypes'

const step = (over: Partial<BuildStep>): BuildStep => ({
  key: 'module:orders',
  kind: 'module',
  title: 'Build orders',
  builder: 'backend',
  status: 'passed',
  attempt: 1,
  started_at: '2026-10-20T10:00:00Z',
  finished_at: '2026-10-20T10:01:00Z',
  summary: '',
  problems: [],
  ...over,
})

const build = (over: Partial<Build>): Build => ({
  id: 'b1',
  project_id: 'p1',
  plan_version: 3,
  app_key: 'shop',
  status: 'running',
  reason: null,
  created_at: '2026-10-20T10:00:00Z',
  started_at: '2026-10-20T10:00:00Z',
  finished_at: null,
  steps: [],
  report: null,
  ...over,
})

const body = {
  title: 'Shop',
  summary: 'Orders and a storefront.',
  requirements: { answers: [], assumptions: [], limitations: [], acceptance: [] },
  roles_note: '',
  environments: [],
  secrets: [],
  integrations: [],
  data: { conventions: {}, tables: [] },
  modules: [{ key: 'orders', kind: 'domain', purpose: 'Takes orders' }],
  routes: [
    { key: 'POST /orders', module: 'orders', purpose: 'place an order' },
    { key: 'GET /orders', module: 'orders', purpose: 'list orders' },
  ],
  apps: [{ key: 'shop', title: 'Shop', screens: [{ key: 'home' }, { key: 'orders' }] }],
  nfr: {},
  build_order: ['orders'],
  migrations: [],
} as unknown as PlanBody

const draft = (issues: PlanIssue[] = []): PlanVersion => ({
  id: 'v4',
  project_id: 'p1',
  version: 4,
  status: 'draft',
  phase: 'review',
  base_version: null,
  body,
  questions: [],
  round: 1,
  created_by: null,
  approved_by: null,
  approved_at: null,
  created_at: '',
  updated_at: '',
  issues,
})

const button = (html: string, id: string) => html.match(new RegExp(`<button[^>]*data-testid="${id}"[^>]*>`))?.[0] ?? ''

describe('composer hint', () => {
  it('follows the phase', () => {
    expect(composerHint({ phase: 'describe', target: 'draft', approved: null })).toBe('Describe the app you need…')
    expect(composerHint({ phase: 'describe', target: 'revise', approved: null })).toMatch(/added to your plan/)
    expect(composerHint({ phase: 'questions', target: 'draft', approved: null })).toMatch(/Answer the questions/)
    expect(composerHint({ phase: 'review', target: 'draft', approved: null })).toMatch(/Approve the plan/)
    expect(composerHint({ phase: 'input', target: 'draft', approved: null })).toMatch(/needs your input/)
    expect(composerHint({ phase: 'finished', target: 'draft', approved: null })).toBe('Describe another app…')
  })
})

describe('review card', () => {
  it('counts what the plan builds and approves with no errors', () => {
    const html = renderToStaticMarkup(createElement(FlowReview, { draft: draft(), slug: 'demo', approving: false, onApprove: () => {} }))
    expect(html).toContain('Shop')
    expect(html).toContain('2 requirements')
    expect(html).toContain('1 module')
    expect(html).toContain('1 app · 2 screens')
    expect(html).toContain('View full plan')
    expect(button(html, 'flow-approve')).not.toMatch(/\sdisabled=""/)
  })

  it('holds Approve while the plan has errors, and says why', () => {
    const issue: PlanIssue = { code: 'PLAN003', path: 'routes.GET /x', message: 'no module', severity: 'error', needs_input: false }
    const html = renderToStaticMarkup(createElement(FlowReview, { draft: draft([issue]), slug: 'demo', approving: false, onApprove: () => {} }))
    expect(html).toContain('1 error')
    expect(html).toContain('Fix 1 error first')
    expect(button(html, 'flow-approve')).toMatch(/\sdisabled=""/)
  })
})

describe('input card', () => {
  const waiting = step({
    key: 'input:app',
    kind: 'input',
    builder: 'check',
    title: 'Your input',
    status: 'waiting_for_input',
    input: {
      message: 'The app needs a payment key and a few products.',
      secrets: [
        { name: 'STRIPE_KEY', purpose: 'charges cards', set: false },
        { name: 'MAIL_KEY', purpose: 'sends receipts', set: true },
      ],
      tables: [{ name: 'products', columns: ['name', 'price'], rows: 0, suggested_min: 3 }],
    },
  })

  it('asks for unset secrets, shows set ones as set, offers rows per column and Continue', () => {
    const html = renderToStaticMarkup(createElement(BuildInputCard, { build: build({ status: 'waiting_for_input', steps: [waiting] }), step: waiting }))
    expect(html).toContain('The app needs a payment key and a few products.')
    expect(html).toMatch(/data-testid="flow-input-secret-STRIPE_KEY"[\s\S]*type="password"/)
    expect(html).toMatch(/data-testid="flow-input-secret-MAIL_KEY"[\s\S]*set/)
    expect(html).toContain('0 rows · 3 suggested')
    expect(html).toContain('placeholder="name"')
    expect(html).toContain('placeholder="price"')
    expect(button(html, 'flow-continue')).not.toMatch(/\sdisabled=""/)
    expect(html).toContain('Anything left empty is skipped')
  })
})

describe('timeline and report', () => {
  it('lists the steps with their state, backend first, and the running step', () => {
    const b = build({
      steps: [
        step({}),
        step({ key: 'app:shop', kind: 'app', builder: 'ui', title: 'Build the Shop app', status: 'running', finished_at: null, attempt: 2, log: ['writing Home.tsx'] }),
        step({ key: 'verify:plan', kind: 'verify', builder: 'check', title: 'Check against the plan', status: 'pending', started_at: null, finished_at: null }),
      ],
    })
    const html = renderToStaticMarkup(createElement(BuildTimeline, { build: b, slug: 'demo', now: Date.parse('2026-10-20T10:02:00Z') }))
    expect(html.indexOf('Build orders')).toBeLessThan(html.indexOf('Build the Shop app'))
    expect(html).toContain('try 2')
    expect(html).toContain('writing Home.tsx')
    expect(html).toContain('Check against the plan')
  })

  it('reports what was built and what the plan still misses', () => {
    const b = build({
      status: 'partial',
      finished_at: '2026-10-20T10:05:00Z',
      report: { built: ['orders'], failed: ['payments'], plan_check: { ok: false, missing: { routes: ['POST /refunds'] } }, app_ids: ['app_1'] },
    })
    const html = renderToStaticMarkup(createElement(BuildReportCard, { build: b, slug: 'demo' }))
    expect(html).toContain('Mostly built from plan v3')
    expect(html).toContain('orders')
    expect(html).toContain('payments')
    expect(html).toContain('POST /refunds')
    expect(html).toContain('Open the app')
  })
})
