/**
 * The Page view as a document (the branching + loops mockups): a switch with per-case counts and folds, "Show paths
 * not taken", parallel lanes and their rejoin, "If this fails" rules, Stop and "Not reached", for-each frames with
 * an item stepper and result squares, a loop inside a loop, repeat-until, the shape map and outline, the floating
 * test bar (latest run picked for you), "simulated in tests", the token picker and the add-step palette.
 * Fixtures from the engine's tests (switch_loop.json, parallel_error.json, loop_failures.json, poll_attempts.json) are
 * seeded with real runs; the engine keeps each loop item's result and each repeat-until try, and the page shows them.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedTable, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

const FIX = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../workflow-engine/tests/fixtures/workflows')

function def(file: string, s: StackState) {
  const d = JSON.parse(fs.readFileSync(path.join(FIX, file), 'utf8').replaceAll('__OK_URL__', s.fake_api_ok_url).replaceAll('__FAIL_URL__', s.fake_api_fail_url))
  d.name = `${d.name} ${rand(4)}`
  d.nodes[0].parameters.path = `${d.nodes[0].parameters.path}-${rand()}`
  return d
}

async function run(api: EngineApi, id: string, input: Record<string, unknown>, status: string) {
  const r = await api.post<{ execution_id: string; status: string }>(`/workflows/${id}/run`, { input_data: input })
  expect(r.status).toBe(status)
  return r.execution_id
}

const WIRES = [
  { id: 'w1', rail: 'fed', amount: 12000 },
  { id: 'w2', rail: 'fed', amount: 800 },
  { id: 'w3', rail: 'ach', amount: 300 },
  { id: 'w4', rail: 'fed', amount: 450 },
  { id: 'w5', rail: 'swift', amount: 99 },
]

async function seedWires(api: EngineApi, s: StackState, p: P) {
  const wf = await seedWorkflow(api, p, def('switch_loop.json', s))
  const runId = await run(api, wf.id, { wires: WIRES }, 'success')
  return { wf, runId }
}

async function seedPayments(api: EngineApi, s: StackState, p: P) {
  await seedTable(api, p, 'payments', [
    { name: 'payment_id', type: 'string', required: false },
    { name: 'amount', type: 'number', required: false },
    { name: 'status', type: 'string', required: false },
  ])
  const wf = await seedWorkflow(api, p, def('parallel_error.json', s))
  const review = await run(api, wf.id, { payment_id: 'pay_3', amount: 9000, fraud_url: s.fake_api_ok_url }, 'failed')
  const broken = await run(api, wf.id, { payment_id: 'pay_2', amount: 70, fraud_url: s.fake_api_fail_url }, 'failed')
  const cleared = await run(api, wf.id, { payment_id: 'pay_1', amount: 1200, fraud_url: s.fake_api_ok_url }, 'success')
  return { wf, review, broken, cleared }
}

async function openPage(page: Page, s: StackState, slug: string, wfId: string, search = '') {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('workflow-studio:glass:view', 'page')
    } catch {
      /* ignore */
    }
  })
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}${search}`)
  await expect(page.getByTestId('glass-page')).toBeVisible()
}

const step = (page: Page, name: string) => page.locator(`#gp-step-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`)
const kase = (page: Page, key: string) => page.locator(`[data-testid="glass-case"][data-case="${key}"]`)

test.describe('glass page · the document', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T35')
  })

  test('T35.1 a switch shows per-case counts, raises the taken cases, and the latest run is picked for you', async ({ page, api, s }) => {
    const { wf, runId } = await seedWires(api, s, p)
    await openPage(page, s, p.slug, wf.id)

    // no ?execution: the page follows the latest run on its own
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', runId)
    await expect(page.getByTestId('glass-rec')).toHaveCount(1)
    await expect(page.getByTestId('glass-rec').first()).toHaveAttribute('aria-pressed', 'true')

    await expect(step(page, 'Rail')).toContainText('Go one way depending on the rail.')
    await expect(step(page, 'Rail').locator('.gl-step-num').first()).toHaveText('3')
    const fed = kase(page, 'Rail:output0')
    const ach = kase(page, 'Rail:output1')
    const other = kase(page, 'Rail:fallback')
    await expect(fed.locator('.gp-cs').first()).toHaveText('Is')
    await expect(other.locator('.gp-cs').first()).toHaveText('Otherwise')
    await expect(fed.getByTestId('glass-case-count').first()).toHaveText('3 items')
    await expect(ach.getByTestId('glass-case-count').first()).toHaveText('1 item')
    await expect(other.getByTestId('glass-case-count').first()).toHaveText('1 item')
    for (const c of [fed, ach, other]) {
      await expect(c).toHaveAttribute('data-taken', '')
      await expect(c.locator('.gp-ch').first()).toContainText('This record')
    }
    await expect(step(page, 'Hold wire')).toHaveAttribute('data-status', 'ok')
    await expect(step(page, 'Hold wire')).toContainText('id w5')

    // the shape map draws every step; the outline lists the top-level ones with status dots
    // (a loop is drawn as a frame around its steps, so the three loops are frames, the other steps nodes)
    await expect(page.getByTestId('glass-map-frame')).toHaveCount(3)
    await expect(page.getByTestId('glass-map-node')).toHaveCount(wfNodeCount('switch_loop.json') - 3)
    await expect(page.locator('[data-testid="glass-map-frame"][data-node="Two checks"]')).toHaveCount(1)
    await expect(page.getByTestId('glass-outline-step')).toHaveCount(3)
    await expect(page.locator('[data-testid="glass-outline-step"] .gp-dot.on')).toHaveCount(3)
    await page.locator('[data-testid="glass-map-node"][data-node="Hold wire"]').click()
    await expect(step(page, 'Hold wire')).toHaveAttribute('data-selected', '')
  })

  test('T35.2 a for-each frame: repeat count, rules, squares, a stepper that the item line follows, and a loop inside a loop', async ({ page, api, s }) => {
    const { wf } = await seedWires(api, s, p)
    await openPage(page, s, p.slug, wf.id)

    const fedLoop = step(page, 'Each fed wire')
    const frame = fedLoop.getByTestId('glass-loop').first()
    await expect(fedLoop).toContainText('For each item, do the steps below.')
    await expect(frame.locator('.gp-lhead .gp-kind').first()).toHaveText('Repeats 3 times')
    await expect(frame.locator('.gp-lrules').first()).toContainText('at a time, one pass after another')
    await expect(frame.locator('.gp-lrules').first()).toContainText('If one fails: the run stops there')
    await expect(frame.locator('.gp-strip').first().locator('button')).toHaveCount(3)
    await expect(frame.locator('.gp-legend').first()).toContainText('3 went through') // intentional: per-item outcomes are kept now
    await expect(frame.locator('.gp-strip').first().locator('button[data-outcome="ok"]')).toHaveCount(3)
    const stepper = frame.getByTestId('glass-loop-stepper').first()
    await expect(stepper).toContainText('item 3 of 3')
    await expect(frame.getByTestId('glass-loop-item').first()).toContainText('id w4')
    // the steps inside show the item being followed (the engine keeps each item's result)
    await expect(step(page, 'Release wire')).toContainText('id w4')
    await stepper.getByRole('button', { name: 'Previous item' }).click()
    await expect(stepper).toContainText('item 2 of 3')
    await expect(frame.getByTestId('glass-loop-item').first()).toContainText('id w2')
    await expect(step(page, 'Release wire').getByTestId('glass-sample')).toContainText('id w2') // intentional: was "Kept for the last item only (item 3)"
    await frame.locator('.gp-strip').first().getByRole('button', { name: 'Item 1' }).click()
    await expect(stepper).toContainText('item 1 of 3')
    await expect(frame.getByTestId('glass-loop-item').first()).toContainText('id w1')
    await expect(step(page, 'Release wire').getByTestId('glass-sample')).toContainText('Not run for item 1.')
    await expect(frame.locator('.gp-lfoot').first()).toContainText('After all 3: one summary of 3 items goes to the next step.')
    // the choice inside the loop folds its untaken case
    await expect(kase(page, 'Large wire:true')).not.toHaveAttribute('data-open', '')
    await expect(kase(page, 'Large wire:false')).toHaveAttribute('data-taken', '')

    // a loop inside a loop is a frame inside a frame
    const achLoop = step(page, 'Each ACH wire')
    await expect(achLoop.getByTestId('glass-loop')).toHaveCount(2)
    await expect(step(page, 'Two checks')).toContainText('Repeat the steps below 2 times.')
    await expect(step(page, 'Two checks').getByTestId('glass-loop-stepper')).toContainText('item 2 of 2')
    await expect(step(page, 'Two checks').locator('.gp-kind').first()).toHaveText('Repeat, inside a loop')
  })

  test('T35.3 parallel lanes rejoin, "If this fails" reads in red, writes are simulated in tests, and repeat-until shows how it ended', async ({ page, api, s }) => {
    const { wf, cleared } = await seedPayments(api, s, p)
    await openPage(page, s, p.slug, wf.id)
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', cleared)

    const par = page.getByTestId('glass-parallel')
    await expect(par).toHaveCount(1)
    await expect(par).toContainText('Do both of these at the same time.')
    await expect(par).toContainText('2 paths run together')
    await expect(par.locator('.gp-lane')).toHaveCount(2)
    await expect(par.getByTestId('glass-join').last()).toContainText('Then continue when both finish.')
    await expect(step(page, 'Sanctions API').locator('.gl-step-num')).toHaveText('3a')
    await expect(step(page, 'Fraud score').locator('.gl-step-num')).toHaveText('3b')

    const sanctions = step(page, 'Sanctions API').getByTestId('glass-fail-rule')
    await expect(sanctions.locator('.gp-kind')).toHaveText('If this fails')
    await expect(sanctions).toContainText("Don't try again; carry on with the next step.")
    await expect(sanctions).toContainText('Not used by this record')
    const fraud = step(page, 'Fraud score').getByTestId('glass-fail-rule')
    await expect(fraud).toContainText('Try again 1 time, 200 ms apart. If it still fails, stop the run.')
    await expect(step(page, 'Sanctions API')).toContainText('simulated in tests')
    await expect(step(page, 'Log payment')).toContainText('simulated in tests')

    await expect(step(page, 'Wait for settlement')).toContainText('Check every 1 until true, for up to 30.')
    await expect(step(page, 'Wait for settlement').getByTestId('glass-repeat-until')).toContainText('condition met')
    await expect(kase(page, 'Wait for settlement:timeout')).not.toHaveAttribute('data-open', '')
    await expect(step(page, 'Log payment')).toHaveAttribute('data-status', 'ok')
    await expect(page.getByTestId('glass-end-chip')).toContainText('Ends at step 5')
  })

  test('T35.4 the test bar switches records: a failure stops the record and the steps after it say "Not reached"; a Stop ends its path', async ({ page, api, s }) => {
    const { wf, review, broken, cleared } = await seedPayments(api, s, p)
    await openPage(page, s, p.slug, wf.id)

    const recs = page.getByTestId('glass-rec')
    await expect(recs).toHaveCount(3)
    await expect(page.locator(`[data-testid="glass-rec"][data-run-id="${cleared}"]`)).toHaveAttribute('aria-pressed', 'true')

    await page.locator(`[data-testid="glass-rec"][data-run-id="${broken}"]`).click()
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', broken)
    await expect(step(page, 'Fraud score')).toHaveAttribute('data-status', 'fail')
    await expect(step(page, 'Fraud score').getByTestId('glass-fail-rule')).toContainText('Used:')
    await expect(step(page, 'Both checks')).toContainText('Not reached: this record stopped at step 3.')
    await expect(step(page, 'Cleared')).toContainText('Not reached: this record stopped at step 3.')

    await page.locator(`[data-testid="glass-rec"][data-run-id="${review}"]`).click()
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', review)
    const no = kase(page, 'Cleared:false')
    await expect(no).toHaveAttribute('data-taken', '')
    await expect(no.locator('.gp-cs').first()).toHaveText('Otherwise')
    await expect(step(page, 'Needs review')).toContainText('Stop the run with the error Payment needs a person.')
    await expect(step(page, 'Needs review')).toHaveClass(/stopb/)
    await expect(step(page, 'Needs review')).toContainText('The run ends here for records on this path.')
    // the path the record did not take is folded to one line, and the switch opens every fold
    const yes = kase(page, 'Cleared:true')
    await expect(yes).not.toHaveAttribute('data-open', '')
    await expect(yes.locator('.gp-fold')).toContainText('not taken by this record')
    await expect(step(page, 'Log payment')).toHaveCount(0)
    await page.getByTestId('glass-show-untaken').click()
    await expect(page.getByTestId('glass-show-untaken')).toHaveAttribute('aria-pressed', 'true')
    await expect(yes).toHaveAttribute('data-open', '')
    await expect(step(page, 'Log payment')).toHaveAttribute('data-dim', '')
    await page.getByTestId('glass-show-untaken').click()
    await expect(yes).not.toHaveAttribute('data-open', '')
    // one fold opens on its own
    await yes.locator('.gp-fold').getByRole('button', { name: 'Show' }).click()
    await expect(yes).toHaveAttribute('data-open', '')
  })

  test('T35.6 a loop that skips failed items: coloured squares, the rules it ran with, and each item\'s own result', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, def('loop_failures.json', s))
    const pays = [1, 2, 3, 4, 5].map((i) => ({ id: `p${i}`, amount: i * 100, check_url: i === 2 || i === 4 ? s.fake_api_fail_url : s.fake_api_ok_url }))
    const runId = await run(api, wf.id, { payments: pays }, 'success')
    const ex = await api.get<{ loop_runs: Record<string, { passes: Array<{ status: string }>; concurrency: number }> }>(`/executions/${runId}`)
    expect(ex.loop_runs['Each payment'].passes.map((x) => x.status)).toEqual(['ok', 'skipped', 'ok', 'skipped', 'ok'])
    await openPage(page, s, p.slug, wf.id)
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', runId)

    const frame = step(page, 'Each payment').getByTestId('glass-loop').first()
    const rules = frame.locator('.gp-lrules').first()
    await expect(rules).toContainText('Run 3 at a time')
    await expect(rules).toContainText('If one fails: skip it and keep going')
    await expect(rules).toContainText('Stop early after 5 failures in a row')
    await expect(frame.getByTestId('glass-loop-stop-early')).toContainText("didn't happen in this run · longest run of failures: 1")
    const squares = frame.locator('.gp-strip').first().locator('button')
    await expect(squares).toHaveCount(5)
    expect(await squares.evaluateAll((bs) => bs.map((b) => (b as HTMLElement).dataset.outcome))).toEqual(['ok', 'skipped', 'ok', 'skipped', 'ok'])
    await expect(squares.nth(1)).toHaveClass(/o-skip/)
    await expect(frame.getByTestId('glass-loop-legend')).toContainText('3 went through')
    await expect(frame.getByTestId('glass-loop-legend')).toContainText('2 failed, skipped')

    await frame.locator('.gp-strip').first().getByRole('button', { name: 'Item 2' }).click()
    await expect(step(page, 'Screen payment').getByTestId('glass-sample')).toContainText('Failed for item 2, skipped')
    await expect(step(page, 'Mark screened').getByTestId('glass-sample')).toContainText('Not run for item 2.')
    await frame.locator('.gp-strip').first().getByRole('button', { name: 'Item 3' }).click()
    await expect(step(page, 'Mark screened').getByTestId('glass-sample')).toContainText('id p3 · status screened')
  })

  test('T35.7 repeat-until lists each try with its time and result', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, def('poll_attempts.json', s))
    const runId = await run(api, wf.id, { transfer_id: 't1' }, 'success')
    await openPage(page, s, p.slug, wf.id)
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', runId)
    const until = step(page, 'Until settled')
    await expect(until).toContainText('Check every 1 until attempt is at least 3')
    const tries = until.getByTestId('glass-poll-try')
    await expect(tries).toHaveCount(4)
    await expect(tries.first()).toContainText('try 1')
    await expect(tries.first()).toContainText('not yet')
    await expect(tries.last()).toContainText('try 4')
    await expect(tries.last()).toContainText('✓ met')
    await expect(tries.last()).toContainText('attempt 3')
    await expect(until.getByTestId('glass-poll-tries')).toContainText('stopped repeating: condition met')
    await expect(step(page, 'Funds in')).toContainText('attempt 3')
  })

  test('T35.5 with no AI: a value token opens the picker with values from the steps above, and steps are added from a palette', async ({ page, api, s }) => {
    const { wf, cleared } = await seedPayments(api, s, p)
    await openPage(page, s, p.slug, wf.id)
    await expect(page.getByTestId('glass-page')).toHaveAttribute('data-run', cleared)

    await step(page, 'Normalize').locator('.vtoken[data-path="parameters.fields.1.value"]').click()
    const picker = page.getByTestId('glass-token-picker')
    await expect(picker).toBeVisible()
    await expect(picker).toContainText('Values from the steps above')
    await expect(picker.getByTestId('glass-token-option').filter({ hasText: 'amount' }).first()).toContainText('1200')
    await page.keyboard.press('Escape')
    await expect(picker).toHaveCount(0)

    await step(page, 'Fraud score').locator('.vtoken[data-path="on_error"]').click()
    await page.getByRole('option', { name: 'carry on with the next step' }).click()
    await expect(step(page, 'Fraud score').getByTestId('glass-fail-rule')).toContainText('If it still fails, carry on with the next step.')
    await expect(page.getByTestId('glass-save')).toBeVisible()

    await page.getByTestId('glass-add-step').last().click()
    const menu = page.getByTestId('glass-add-step-menu')
    await expect(menu).toContainText('Add a step here')
    await menu.getByTestId('glass-add-code').click()
    await expect(page.getByTestId('glass-draft')).toContainText('Run custom code: [describe what it does].')
  })
})

function wfNodeCount(file: string) {
  return (JSON.parse(fs.readFileSync(path.join(FIX, file), 'utf8')) as { nodes: unknown[] }).nodes.length
}
