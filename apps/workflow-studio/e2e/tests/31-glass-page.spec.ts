/**
 * The glass editor's read side (BUILD-PLAN §1.5, W4a): glass is the default editor, the Page view reads the
 * refund-triage workflow as sentences, a failed run shows its path (the untaken branch folded), the run picker,
 * Definition and Side by side (hover-linked), token edits, + Add step drafts, and `?legacy=1`.
 * Each test seeds its own project and refund workflow; nothing shared is changed.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

function refundDef(name: string, url: string) {
  const raw = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../workflow-engine/tests/fixtures/workflows/refund_triage.json'), 'utf8')
  const def = JSON.parse(raw.replace('__REFUND_API_URL__', url))
  def.name = name
  def.nodes[0].parameters.path = `refund-${rand()}`
  return def
}

async function seedRefund(api: EngineApi, s: StackState, p: P, url = s.fake_api_ok_url) {
  return seedWorkflow(api, p, refundDef(`Refund triage ${rand(4)}`, url))
}

/** A run that fails at the risk API (amount 900 takes the "yes" branch). */
async function failedRun(api: EngineApi, wfId: string) {
  const run = await api.post<{ execution_id: string; status: string }>(`/workflows/${wfId}/run`, {
    input_data: { order_id: 'ord_31', amount: 900, reason: 'damaged' },
  })
  expect(run.status).toBe('failed')
  return run.execution_id
}

async function openGlass(page: Page, s: StackState, slug: string, wfId: string, search = '') {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('workflow-studio:glass:view', 'page')
    } catch {
      /* ignore */
    }
  })
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}${search}`)
  await expect(page.getByTestId('glass-editor')).toBeVisible()
  await expect(page.getByTestId('glass-page')).toBeVisible()
}

const step = (page: Page, name: string) => page.locator(`#gp-step-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`)

test.describe('glass editor · read side', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T31')
  })

  test('T31.1 glass is the default and the Page reads the workflow as sentences', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p)
    await openGlass(page, s, p.slug, wf.id)
    await expect(page.getByTestId('workflow-navbar')).toHaveCount(0)

    await expect(page.getByTestId('glass-title')).toHaveText(wf.name)
    await expect(page.getByTestId('glass-status')).toHaveText('Draft')
    await expect(page.getByTestId('glass-byline')).toContainText('When a POST request arrives at /refund-')

    await expect(step(page, 'Webhook')).toContainText('When a POST request arrives at')
    await expect(step(page, 'Amount over 500')).toContainText('Go one way depending on whether amount > 500.')
    await expect(step(page, 'Risk API')).toContainText('Call POST')
    await expect(step(page, 'Log approved')).toContainText('Add a row to the table refunds.')
    // sections: trigger, Intake, the decision (no headings in the main flow); numbered in page order, a case's
    // steps numbered under their choice
    await expect(page.locator('#sec-intake').locator('#gp-step-normalize')).toHaveCount(1)
    await expect(page.locator('#sec-amount-over-500').locator('#gp-step-amount-over-500')).toHaveCount(1)
    await expect(step(page, 'Webhook').locator('.gl-step-num')).toHaveText('1')
    await expect(step(page, 'Normalize').locator('.gl-step-num')).toHaveText('2')
    await expect(step(page, 'Amount over 500').locator('.gl-step-num').first()).toHaveText('3')
    await expect(step(page, 'Risk API').locator('.gl-step-num')).toHaveText('3.1')
    await expect(step(page, 'Log escalated').locator('.gl-step-num')).toHaveText('3.2')
    await expect(step(page, 'Log approved').locator('.gl-step-num')).toHaveText('3.1')
    // live: both cases open, no run card numbers
    await expect(page.locator('.gl-case[data-open]')).toHaveCount(2)
    await expect(page.getByTestId('glass-run-card')).toContainText('No run picked')

    // the outline lists the top-level steps; a click selects the step
    await expect(page.getByTestId('glass-outline-step')).toHaveCount(3)
    await page.getByTestId('glass-outline-step').filter({ hasText: 'Amount > 500?' }).click()
    await expect(step(page, 'Amount over 500')).toHaveAttribute('data-selected', '')
    // the shape map draws every step; clicking a nested one selects it on the page
    await expect(page.getByTestId('glass-map-node')).toHaveCount(6)
    await page.locator('[data-testid="glass-map-node"][data-node="Risk API"]').click()
    await expect(step(page, 'Risk API')).toHaveAttribute('data-selected', '')
  })

  test('T31.2 a failed run shows its path and folds the branch it did not take', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p, s.fake_api_fail_url)
    const runId = await failedRun(api, wf.id)
    await openGlass(page, s, p.slug, wf.id, `?execution=${runId}`)

    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', runId)
    await expect(step(page, 'Normalize')).toHaveAttribute('data-status', 'ok')
    await expect(step(page, 'Normalize')).toHaveAttribute('data-path', '')
    await expect(step(page, 'Risk API')).toHaveAttribute('data-status', 'fail')
    await expect(step(page, 'Risk API')).toContainText('Failed')

    const yes = page.locator('.gl-case[data-case="Amount over 500:true"]')
    const no = page.locator('.gl-case[data-case="Amount over 500:false"]')
    await expect(yes).toHaveAttribute('data-taken', '')
    await expect(yes).toHaveAttribute('data-open', '')
    await expect(no).not.toHaveAttribute('data-open', '')
    await expect(step(page, 'Log approved')).toHaveCount(0)
    await no.locator('.gl-case-head').click()
    await expect(step(page, 'Log approved')).toHaveAttribute('data-status', 'skip')
    await expect(step(page, 'Log approved')).toHaveAttribute('data-dim', '')

    const card = page.getByTestId('glass-run-card')
    await expect(page.getByTestId('glass-run-outcome')).toHaveText('Failed at Risk API')
    await expect(page.getByTestId('glass-run-steps')).toHaveText('4 of 6')
    await expect(card).toContainText('→ true')
    await expect(page.getByTestId('glass-run-retry')).toBeVisible()
  })

  test('T31.3 the run picker switches between a run and live', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p, s.fake_api_fail_url)
    const runId = await failedRun(api, wf.id)
    await openGlass(page, s, p.slug, wf.id)

    await page.getByTestId('glass-run-picker').click()
    const opt = page.locator(`[data-testid="glass-run-option"][data-run-id="${runId}"]`)
    await expect(opt).toBeVisible()
    await page.getByLabel('Search runs').fill('nothing-matches-this')
    await expect(opt).toHaveCount(0)
    await page.getByLabel('Search runs').fill('fail')
    await opt.click()
    await expect(page).toHaveURL(new RegExp(`execution=${runId}`))
    await expect(step(page, 'Risk API')).toHaveAttribute('data-status', 'fail')

    await page.getByTestId('glass-run-picker').click()
    await page.getByTestId('glass-run-live').click()
    await expect(page).not.toHaveURL(/execution=/)
    await expect(step(page, 'Risk API')).toHaveAttribute('data-status', 'pending')
  })

  test('T31.4 Definition and Side by side, linked on hover', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p)
    await openGlass(page, s, p.slug, wf.id)

    // two views (Page, Canvas); the definition lives in the ⋯ menu
    await expect(page.getByTestId('glass-view-switch').getByText('Definition')).toHaveCount(0)
    await page.getByTestId('glass-more').click()
    await page.getByTestId('glass-more-definition').click()
    await expect(page.getByTestId('glass-definition-title')).toContainText('6 nodes · 5 connections · read-only')
    await expect(page.getByTestId('glass-definition-node')).toHaveCount(6)
    const dl = page.waitForEvent('download')
    await page.getByTestId('glass-definition-download').click()
    expect((await dl).suggestedFilename()).toMatch(/\.json$/)

    await page.getByTestId('glass-more').click()
    await page.getByTestId('glass-view-side').click()
    await expect(page.getByTestId('glass-side-by-side')).toBeVisible()
    await step(page, 'Risk API').locator('.gl-step-sentence').hover()
    await expect(page.locator('[data-testid="glass-definition-node"][data-node="Risk API"]')).toHaveClass(/is-on/)
    await page.locator('[data-testid="glass-definition-node"][data-node="Normalize"]').hover()
    await expect(step(page, 'Normalize')).toHaveAttribute('data-selected', '')
  })

  test('T31.5 tokens edit the draft; + Add step drafts a fill-in-the-blank step', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p)
    await openGlass(page, s, p.slug, wf.id)

    await step(page, 'Log approved').locator('.vtoken[data-path="parameters.table"]').click()
    await page.getByRole('textbox', { name: 'table' }).fill('refunds_ok')
    await page.getByRole('button', { name: 'Apply' }).click()
    await expect(step(page, 'Log approved')).toContainText('Add a row to the table refunds_ok.')
    await expect(page.getByTestId('glass-save')).toBeVisible()

    await page.locator('#sec-amount-over-500').getByTestId('glass-add-step').click()
    await page.getByTestId('glass-add-code').click()
    const draft = page.getByTestId('glass-draft')
    await expect(draft).toContainText('draft · not saved')
    await expect(draft).toContainText('Run custom code: [describe what it does].')
    await draft.getByTestId('glass-draft-remove').click()
    await expect(draft).toHaveCount(0)

    // the schedule preset adds a Cron trigger to the draft
    await page.getByTestId('glass-schedule').click()
    await page.getByTestId('glass-schedule-menu').getByText('Weekdays at 07:00').click()
    await expect(page.locator('#sec-also-cron')).toContainText('weekdays at 07:00')
  })

  test('T31.6 ?legacy=1 keeps the old canvas editor', async ({ page, api, s }) => {
    const wf = await seedRefund(api, s, p)
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows/${wf.id}?legacy=1`)
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(page.getByTestId('glass-editor')).toHaveCount(0)
  })
})
