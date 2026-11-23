/**
 * Capability graph, Wave 1 (PLAN-CAPGRAPH-UI section 3): the Build sidebar understands the request.
 * Live concept chips while typing, the Understanding card, the clarify questions (answer, Continue, the plan), a refused
 * answer shown on its own question, and a request with nothing to ask going straight to the plan. Real UI, real API, the
 * engine's fake LLM (scripted spec replies from apps/workflow-engine/tests/fixtures/fake_llm/ai_builder/*.json).
 * Written from the plan; not run by its author (testing was on hold).
 */
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, type P } from '../fixtures/seed'
import { restoreLlmScript } from '../fixtures/shots17'
import type { Locator, Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPTS = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/fake_llm/ai_builder')

// "sign off" is the Approval concept with no approver named (its required slot stays empty, so Understand has to ask) and
// "within 48 hours" is the SLA concept. Same request as the engine's test_ai_builder_graph_http.py.
const NEEDS_ANSWERS = 'Expense claims need sign off, and if nobody decides within 48 hours escalate it to the head of operations.'
const NOTHING_TO_ASK = 'Reply to every webhook call with the text hello.'
const uniq = (text: string) => `${text} (${rand(8)})`

const GRAPH_SCRIPTS = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/fake_llm/capability_graph')

async function use(api: EngineApi, name: string) {
  const dir = name === 'single_approval_escalation' ? GRAPH_SCRIPTS : SCRIPTS
  const rules = JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), 'utf8')).rules
  await api.testApi('PUT', 'fake-llm/script', { rules })
  await api.clearLlmCalls()
}

async function openBuild(page: Page, studioUrl: string, slug: string) {
  await page.goto(`${studioUrl}/p/${slug}/workflows`)
  await page.getByTestId('ai-build-open').click()
  await expect(page.getByTestId('ai-builder-panel')).toBeVisible()
  await expect(page.getByTestId('ai-request-input')).toBeVisible()
}

/**
 * The plan is longer than the sidebar. The layout check flags a control cut by the bottom edge of the scroller, wherever the scroll
 * happens to rest, so end on the last step: the plan's whole step list is then reachable and nothing sits across the fold.
 */
async function settlePlan(page: Page) {
  await page.getByTestId('ai-spec-panel').evaluate((el) => {
    let a: HTMLElement | null = el.parentElement
    while (a && !/(auto|scroll)/.test(getComputedStyle(a).overflowY)) a = a.parentElement
    a?.scrollTo({ top: a.scrollHeight })
  })
  await expect(page.getByTestId('ai-spec-step-type-0')).toBeAttached()
}

const panel = (page: Page) => page.getByTestId('ai-builder-panel')

/** Answer one open, blocking question by what kind of control it has. */
async function answer(page: Page, card: Locator) {
  const testid = (await card.getAttribute('data-testid'))!
  const qid = testid.replace('ai-question-', '')
  const kind = await card.getAttribute('data-kind')
  const byId = (id: string) => page.getByTestId(`${id}-${qid}`)
  const select = byId('ai-question-select')
  const input = byId('ai-question-input')
  const done = byId('ai-question-done')
  if (await select.count()) {
    await select.click()
    await page.getByRole('option').first().click()
  } else if (await done.count()) {
    await card.getByRole('checkbox').first().click()
    await done.click()
  } else if (kind === 'duration' || kind === 'text') {
    await input.fill(kind === 'duration' ? '48h' : 'refund amount')
    await input.press('Enter')
  } else {
    await card.getByRole('radio').first().click()
  }
  await expect(page.getByTestId(testid)).toHaveAttribute('data-state', 'answered')
}

async function answerAll(page: Page) {
  const open = page.locator('[data-testid^="ai-question-q_"][data-required="true"]:not([data-state="answered"])')
  for (let i = 0; i < 5 && (await open.count()) > 0; i++) await answer(page, open.first())
}

test.describe('AI builder understanding and clarify (capability graph, wave 1)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T23')
    await context.addInitScript(() => {
      try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
    })
    await restoreLlmScript(api)
  })
  test.afterAll(async ({ api }) => { await restoreLlmScript(api) })

  test('T23.1 live chips: the concepts it hears appear under the request while typing, and nothing is saved', async ({ page, s }) => {
    await openBuild(page, s.studioUrl, p.slug)
    await expect(page.getByTestId('ai-live-concepts')).toHaveCount(0) // hidden until there is something to show
    await page.getByTestId('ai-request-input').fill(NEEDS_ANSWERS)
    await expect(page.getByTestId('ai-live-concepts')).toBeVisible()
    await expect(page.getByTestId('ai-live-concept-approval')).toBeVisible()
    await expect(page.getByTestId('ai-live-concept-sla')).toBeVisible()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'ask') // typing only suggests; no proposal exists
    // clearing the text hides the row again
    await page.getByTestId('ai-request-input').fill('hi')
    await expect(page.getByTestId('ai-live-concepts')).toHaveCount(0)
  })

  test('T23.2 clarify: questions, answers, Continue, then the plan with the understanding still above it', async ({ page, api, s }) => {
    await use(api, 'single_approval_escalation')
    await openBuild(page, s.studioUrl, p.slug)
    await page.getByTestId('ai-request-input').fill(uniq(NEEDS_ANSWERS))
    await page.getByTestId('ai-request-submit').click()

    // the AI stops and asks
    await expect(panel(page)).toHaveAttribute('data-build-state', 'clarify')
    await expect(page.getByTestId('ai-phases')).toHaveAttribute('data-phase', 'understand')
    await expect(page.getByTestId('ai-understanding')).toBeVisible()
    await expect(page.getByTestId('ai-understanding')).toHaveAttribute('data-compact', 'false')
    await expect(page.getByTestId('ai-understood-chip-approval')).toBeVisible()
    await expect(page.getByTestId('ai-reusing')).toBeVisible()
    await expect(page.getByTestId('ai-clarify')).toBeVisible()
    const total = Number(await page.getByTestId('ai-clarify-progress').getAttribute('data-total'))
    expect(total).toBeGreaterThan(0)
    expect(total).toBeLessThanOrEqual(4)
    await expect(page.getByTestId('ai-questions-count')).toHaveText(String(total))
    await expect(page.getByTestId('ai-clarify-continue')).toBeDisabled()

    // no plan has been written yet
    await expect(page.getByTestId('ai-spec-panel')).toHaveCount(0)

    await answerAll(page)
    await expect(page.getByTestId('ai-clarify-progress')).toHaveAttribute('data-done', String(total))
    await expect(page.getByTestId('ai-clarify-continue')).toBeEnabled()
    await page.getByTestId('ai-clarify-continue').click()

    // the plan arrives; the understanding stays above it, now saying "Answered"
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'plan')
    await expect(page.getByTestId('ai-understanding')).toBeVisible()
    await expect(page.getByTestId('ai-questions-row')).toHaveAttribute('data-answered', 'true')
    await expect(page.getByTestId('ai-clarify')).toHaveCount(0)
    await settlePlan(page)
  })

  test('T23.3 a refused answer shows on its own question, the answers are kept, and a retry goes through', async ({ page, api, s, net }) => {
    await use(api, 'single_approval_escalation')
    await openBuild(page, s.studioUrl, p.slug)
    await page.getByTestId('ai-request-input').fill(uniq(NEEDS_ANSWERS))
    await page.getByTestId('ai-request-submit').click()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'clarify')
    await answerAll(page)

    const first = (await page.locator('[data-testid^="ai-question-q_"][data-required="true"]').first().getAttribute('data-testid'))!
    const qid = first.replace('ai-question-', '')
    let refused = false
    await page.route(/\/ai\/proposals\/[^/]+\/answers/, async (route) => {
      if (refused) return route.continue()
      refused = true
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: { code: 'answer_invalid', message: 'Pick one of the roles listed.', qid } }),
      })
    })
    await net.expect([{ method: 'POST', url: /\/ai\/proposals\/[^/]+\/answers/, status: 422 }], async () => {
      await page.getByTestId('ai-clarify-continue').click()
      await expect(page.getByTestId(`ai-question-error-${qid}`)).toBeVisible()
    })

    // back in clarify, that one question open with the engine's sentence under it
    await expect(page.getByTestId(`ai-question-error-${qid}`)).toHaveText('Pick one of the roles listed.')
    await expect(panel(page)).toHaveAttribute('data-build-state', 'clarify')
    await expect(page.getByTestId(`ai-question-${qid}`)).not.toHaveAttribute('data-state', 'answered')

    // answer it again; the others were kept; Continue now goes through
    await answerAll(page)
    await expect(page.getByTestId('ai-clarify-continue')).toBeEnabled()
    await page.getByTestId('ai-clarify-continue').click()
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    await settlePlan(page)
  })

  test('T23.4 nothing to ask: the plan comes straight back and there is no Understand step', async ({ page, api, s }) => {
    await use(api, 'repairable')
    await openBuild(page, s.studioUrl, p.slug)
    await page.getByTestId('ai-request-input').fill(uniq(NOTHING_TO_ASK))
    await page.getByTestId('ai-request-submit').click()
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'plan')
    await expect(page.getByTestId('ai-clarify')).toHaveCount(0)
    await expect(page.getByTestId('ai-phases')).toHaveAttribute('data-phase', 'plan')
    await expect(page.getByTestId('ai-phases')).not.toContainText('Understand')
    await settlePlan(page)
  })

  test('T23.5 Start a new request in clarify discards it and returns to the request box', async ({ page, api, s }) => {
    await use(api, 'single_approval_escalation')
    await openBuild(page, s.studioUrl, p.slug)
    await page.getByTestId('ai-request-input').fill(uniq(NEEDS_ANSWERS))
    await page.getByTestId('ai-request-submit').click()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'clarify')
    const id = new URL(page.url()).searchParams.get('proposal')!
    await page.getByTestId('ai-clarify-start-over').click()
    await expect(panel(page)).toHaveAttribute('data-build-state', 'ask')
    await expect(page.getByTestId('ai-request-input')).toBeVisible()
    expect((await api.get<{ status: string }>(`/ai/proposals/${id}`)).status).toBe('discarded')
  })
})
