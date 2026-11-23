/**
 * Capability graph, Wave 2 (PLAN-CAPGRAPH-UI section 4): the AI builds a whole application, and a second team adapts it.
 * Team A asks for an operational-exceptions app: the app panel shows the plan (5 parts, 1 table), the parts are approved and
 * built one by one while the canvas follows, and Keep installs everything at once. Team B asks for a similar app in another
 * project: the plan says it was adapted from team A's app, shows what changed, and asks fewer questions.
 * Real UI, real API, the engine's fake LLM (apps/workflow-engine/tests/fixtures/fake_llm/capability_graph/*.json).
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
const SCRIPTS = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/fake_llm/capability_graph')

const TEAM_A =
  "We need an application to manage operational exceptions: anyone in payments ops can log an exception, each one gets an owner based on its category, if the owner hasn't resolved it within 48 hours escalate it to the head of operations, and every Monday send the ops managers a report of open and closed exceptions."

const TEAM_B =
  "Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate to the fraud lead if it isn't handled in 4 hours, and send fraud managers a daily summary."

const uniq = (text: string) => `${text} (${rand(8)})`

/** The two-team script when WP-F has landed it, else team A's alone. */
async function useScript(api: EngineApi) {
  const file = ['demo_two_teams.json', 'app_exceptions_team_a.json'].map((f) => path.join(SCRIPTS, f)).find((f) => fs.existsSync(f))!
  await api.testApi('PUT', 'fake-llm/script', { rules: JSON.parse(fs.readFileSync(file, 'utf8')).rules })
  await api.clearLlmCalls()
}

async function openBuild(page: Page, studioUrl: string, slug: string) {
  await page.goto(`${studioUrl}/p/${slug}/workflows`)
  await page.getByTestId('ai-build-open').click()
  await expect(page.getByTestId('ai-builder-panel')).toBeVisible()
  await expect(page.getByTestId('ai-request-input')).toBeVisible()
}

const appPlan = (page: Page) => page.getByTestId('ai-app-plan')

/** Answer one open, blocking question by what kind of control it has (same as spec 23). */
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
    await input.fill(kind === 'duration' ? '48h' : 'category')
    await input.press('Enter')
  } else {
    await card.getByRole('radio').first().click()
  }
  await expect(page.getByTestId(testid)).toHaveAttribute('data-state', 'answered')
}

async function answerAll(page: Page) {
  const open = page.locator('[data-testid^="ai-question-q_"][data-required="true"]:not([data-state="answered"])')
  for (let i = 0; i < 5 && (await open.count()) > 0; i++) await answer(page, open.first())
  // the optional "build it as an app?" question hides the app panel while it is open: answer it too
  const shape = page.getByTestId('ai-question-q_shape')
  if ((await shape.count()) && (await shape.getAttribute('data-state')) !== 'answered') {
    await shape.getByRole('radio').first().click()
    await expect(shape).toHaveAttribute('data-state', 'answered')
  }
  await page.getByTestId('ai-clarify-continue').click()
}

/** Ask for the app, answer what it asks, and wait for the plan. Returns how many questions it asked. */
async function planApp(page: Page, request: string): Promise<number> {
  await page.getByTestId('ai-request-input').fill(uniq(request))
  await page.getByTestId('ai-request-submit').click()
  let asked = 0
  const clarify = page.getByTestId('ai-clarify')
  // The questions can come twice: on the request's own proposal (the "build it as an app?" question hides the app panel while it is
  // open) or inside the app panel itself (data-step "answer") once the request is known to be an app. Answer whichever is on screen.
  for (let round = 0; round < 3; round++) {
    await expect(clarify.or(page.locator('[data-testid="ai-app-plan"][data-step="plan"]'))).toBeVisible({ timeout: 60_000 })
    if (!(await clarify.isVisible())) break
    asked = Math.max(asked, Number(await page.getByTestId('ai-clarify-progress').getAttribute('data-total')))
    await answerAll(page)
    await expect(clarify).toHaveCount(0, { timeout: 30_000 })
  }
  await expect(appPlan(page)).toHaveAttribute('data-step', 'plan', { timeout: 60_000 })
  return asked
}

/** Approve the plan, approve every part's plan, build every part, and wait until Keep is on. */
async function buildApp(page: Page) {
  await page.getByTestId('ai-app-approve-plan').click()
  await expect(appPlan(page)).toHaveAttribute('data-step', 'specs', { timeout: 120_000 })
  await page.getByTestId('ai-app-approve-all-specs').click()
  await expect(appPlan(page)).toHaveAttribute('data-step', 'build', { timeout: 60_000 })
  await page.getByTestId('ai-app-build').click()
  await expect(appPlan(page)).toHaveAttribute('data-step', 'keep', { timeout: 240_000 })
}

test.describe('AI builder: whole applications and two teams (capability graph, wave 2)', () => {
  let a: P
  let b: P
  test.beforeEach(async ({ api, context }) => {
    a = await seedProject(api, 'T24A')
    b = await seedProject(api, 'T24B')
    await context.addInitScript(() => {
      try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
    })
    await restoreLlmScript(api)
    await useScript(api)
  })
  test.afterAll(async ({ api }) => { await restoreLlmScript(api) })

  test('T24.1 team A: the plan, the parts, the build, and Keep installs the whole app', async ({ page, s }) => {
    await openBuild(page, s.studioUrl, a.slug)
    await planApp(page, TEAM_A)

    // the plan: 5 parts, 1 table, a map; nothing is adapted for the first team
    await expect(page.getByTestId('ai-app-summary')).toContainText('5 parts')
    await expect(page.getByTestId('ai-app-summary')).toContainText('1 table')
    await expect(page.getByTestId('ai-app-map')).toBeVisible()
    await expect(page.getByTestId('ai-app-adapted-from')).toHaveCount(0)
    for (const key of ['assign_owner', 'build_report', 'log_exception', 'weekly_report', 'report_view']) {
      await expect(page.getByTestId(`ai-app-part-${key}`)).toBeVisible()
    }
    await expect(page.getByTestId('ai-app-keep')).toBeDisabled() // nothing is built yet

    await page.getByTestId('ai-app-approve-plan').click()
    await expect(appPlan(page)).toHaveAttribute('data-step', 'specs', { timeout: 120_000 })
    await expect(page.getByTestId('ai-app-part-assign_owner')).toHaveAttribute('data-status', 'spec_draft')

    // opening a part puts its plan on the canvas
    await page.getByTestId('ai-app-part-open-log_exception').click()
    await expect(page.getByTestId('ai-app-part-log_exception')).toHaveAttribute('data-selected', 'true')

    await page.getByTestId('ai-app-approve-all-specs').click()
    await expect(appPlan(page)).toHaveAttribute('data-step', 'build', { timeout: 60_000 })
    await expect(page.getByTestId('ai-app-part-assign_owner')).toHaveAttribute('data-status', 'spec_approved')

    await page.getByTestId('ai-app-build').click()
    await expect(appPlan(page)).toHaveAttribute('data-step', 'keep', { timeout: 240_000 })
    for (const key of ['assign_owner', 'build_report', 'log_exception', 'weekly_report', 'report_view']) {
      await expect(page.getByTestId(`ai-app-part-${key}`)).toHaveAttribute('data-status', 'ready')
    }
    await expect(page.getByTestId('ai-app-progress')).toHaveAttribute('data-built', '5')
    await expect(page.getByTestId('ai-app-keep')).toBeEnabled()

    await page.getByTestId('ai-app-keep').click()
    await expect(page.getByTestId('ai-app-kept-links')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('ai-app-kept-links').getByRole('link').first()).toBeVisible()
    await expect(page.getByTestId('ai-app-kept-publish-note')).toContainText('publish to go live') // entries stay drafts after Keep
    await expect(page.getByTestId('ai-app-kept-approvals')).toBeVisible()
    await expect(page.getByTestId('ai-app-part-log_exception')).toHaveAttribute('data-status', 'kept')
  })

  test('T24.2 discard saves nothing', async ({ page, s, api }) => {
    await openBuild(page, s.studioUrl, a.slug)
    await planApp(page, TEAM_A)
    await page.getByTestId('ai-app-discard').click()
    await expect(page.getByTestId('ai-app-discarded')).toBeVisible()
    await expect(page.getByTestId('ai-app-kept-links')).toHaveCount(0)
    const wfs = await api.get<{ items?: unknown[] } | unknown[]>(`/workflows?project_id=${a.id}`)
    expect(Array.isArray(wfs) ? wfs.length : (wfs.items ?? []).length).toBe(0)
  })

  test('T24.3 team B adapts team A\'s app: the banner, the changes, fewer questions', async ({ page, s }) => {
    // team A keeps first (over the page, so the learned pattern exists)
    await openBuild(page, s.studioUrl, a.slug)
    const askedA = await planApp(page, TEAM_A)
    await buildApp(page)
    await page.getByTestId('ai-app-keep').click()
    await expect(page.getByTestId('ai-app-kept-links')).toBeVisible({ timeout: 60_000 })

    await openBuild(page, s.studioUrl, b.slug)
    const askedB = await planApp(page, TEAM_B)
    const banner = page.getByTestId('ai-app-adapted-from')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Adapted from')
    await expect(banner).toContainText('validated by')
    // what changed is struck out and replaced: 48h -> 4h, Mondays -> daily, the head of operations -> the fraud lead
    await expect(banner.locator('s').first()).toBeVisible()
    await expect(banner).toContainText('4h')
    await expect(banner).toContainText('daily 08:00')
    // A cold team is asked 2 questions (the engine's two-teams journey); an earlier test in the run may already have taught the graph
    // team A's pattern, so team A here can be asked fewer. Team B must be asked fewer than a cold team, and never more than team A.
    expect(askedB).toBeLessThan(2)
    expect(askedB).toBeLessThanOrEqual(askedA)
    if (askedB === 0) await expect(page.getByTestId('ai-questions-count')).toHaveCount(0)
  })
})
