/**
 * Stage 3B (CONTRACTS-STAGE3B §6, PLAN-STAGE3B-EXECUTION step 7): AI fill gaps with keep / reject, the variations panel
 * finding a planted bug and "save as test", the drift banner / stale badge / workflows-list dot after the fake risk API
 * changes its reply shape, and live-check (smoke) settings with a result row. Real UI, real stack, fake LLM only.
 * Written from the contract, not from the code.
 */
import { test, expect, type EngineApi } from '../fixtures/test'
import { seedProject, type P } from '../fixtures/seed'
import { mkTest, openEditor, openTestsTab, rowByName, seedRefund } from '../fixtures/tests3a'
import { OwnFakeApi, putLlmRules, restoreLlmScript, shot } from '../fixtures/shots17'
import type { Page } from '@playwright/test'
import type { StackState } from '../global-setup'
import { legacyEditor } from '../fixtures/legacy'

// these specs guard the legacy canvas editor (the glass editor is the default)
test.beforeEach(async ({ page }) => legacyEditor(page))


type TestCase = Record<string, unknown> & { id: string; name: string; status?: string; locked?: boolean; source?: string; approved_by?: string; expect?: Array<Record<string, unknown>> }
type Wf = { definition: { tests: TestCase[]; nodes: Array<{ name: string }>; connections: Array<Record<string, string>> } }
const getWf = (api: EngineApi, id: string) => api.get<Wf>(`/workflows/${id}`)

const fake = new OwnFakeApi()
const own = (s: StackState): StackState => ({ ...s, fake_api_ok_url: `${fake.url}/ok` })
test.beforeAll(async () => {
  await fake.start()
})
test.afterAll(() => {
  fake.stop()
})

// mocks that describe today's (v1) reply of the fake risk API: drift basis = mock, no API spec
const V1_BODY = { ok: true, decision: 'review', received: '{}' }
const TABLE_MOCK = { items: [{ id: 1, order_id: 'o-1', amount: 900, reason: 'late', status: 'logged' }] }
const V1_MOCKS = {
  'Risk API': { http: { status: 200, body: V1_BODY } },
  'Log escalated': TABLE_MOCK,
  'Log approved': TABLE_MOCK,
}
const BIG_V1_EXPECT = [{ node: 'Risk API', path: 'body.decision', op: 'exists' }, { node: 'Risk API', op: 'status', value: 'success' }]

const BUGGY = // passes the webhook body through but trusts every field
  "b = items[0]['json']['body']\n" +
  "return [{'json': {'body': {'order_id': b['order_id'], 'amount': b['amount'] + 0, 'reason': b['reason'].strip()}}}]\n"

async function saveEditor(page: Page) {
  const save = page.getByTestId('editor-save')
  if (await save.isEnabled().catch(() => false)) await save.click()
}

test.describe('AI and live checks (stage 3B)', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T17')
    await restoreLlmScript(api)
  })
  test.afterAll(async ({ api }) => {
    await restoreLlmScript(api)
  })

  test('T17.1 AI fill gaps on the coverage grid: AI drafts show a badge; keep approves one, reject removes the other', async ({ page, api, s, net }) => {
    void net
    const aiRisk = {
      name: 'AI: Risk API is down', target: { kind: 'node', node: 'Risk API' }, category: 'ext_error',
      input: { items: [{ order_id: 'o-9', amount: 900, reason: 'late' }] },
      mocks: { 'Risk API': { error: { message: '503 Service Unavailable' } } },
      expect: [{ node: 'Risk API', op: 'status', value: 'error' }],
    }
    const aiNorm = {
      name: 'AI: Normalize without a reason', target: { kind: 'node', node: 'Normalize' }, category: 'missing',
      input: { items: [{ body: { order_id: 'o-9', amount: 100 } }] }, mocks: {},
      expect: [{ node: 'Normalize', op: 'status', value: 'success' }],
    }
    const ghost = { ...aiNorm, name: 'AI: invented step', target: { kind: 'node', node: 'Ghost Node' }, expect: [{ node: 'Ghost Node', op: 'status', value: 'success' }] }
    await putLlmRules(api, [
      { id: 'e2e-draft', when: { system_matches: '^TEST_AI:DRAFT_TESTS', nth: 1 }, respond: { text: JSON.stringify({ tests: [aiRisk, aiNorm, ghost] }) } },
      // the one repair round for the invented step: the model gives up on it
      { id: 'e2e-draft-repair', when: { system_matches: '^TEST_AI:DRAFT_TESTS' }, respond: { text: JSON.stringify({ tests: [] }) } },
    ])
    const { wf } = await seedRefund(api, own(s), p, {
      withSpec: false, mocks: V1_MOCKS,
      tests: [
        mkTest('Small refund is auto-approved', 'normal', 100, [{ node: 'Amount over 500', port: 'false', op: 'count', value: 1 }]),
        mkTest('Exactly 500 is not escalated', 'boundary', 500, [{ node: 'Amount over 500', port: 'false', op: 'count', value: 1 }], { field: 'amount', covers: ['Amount over 500'] }),
      ],
    })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await page.getByTestId('tests-view-grid').click()
    await expect(page.getByTestId('coverage-grid')).toBeVisible()
    await expect(page.locator('[data-testid="coverage-cell"][data-status="none"]').first()).toBeVisible()
    await shot(page, 't17-1-grid-before')

    await page.getByTestId('tests-ai-fill-grid').click()
    // two valid drafts show up in the list with the AI badge; the invented step was skipped
    const r1 = rowByName(page, 'AI: Risk API is down')
    const r2 = rowByName(page, 'AI: Normalize without a reason')
    await expect(r1).toBeVisible()
    await expect(r2).toBeVisible()
    await expect(rowByName(page, 'invented step')).toHaveCount(0)
    for (const r of [r1, r2]) {
      await expect(r.getByTestId('test-ai-badge')).toBeVisible()
      await expect(r.getByTestId('test-keep')).toBeVisible()
      await expect(r.getByTestId('test-reject')).toBeVisible()
      await expect(r).toHaveAttribute('data-source', 'ai')
    }
    await expect(page.getByTestId('tests-ai-busy')).toHaveCount(0)
    await expect(rowByName(page, 'Small refund')).toHaveCount(1)
    await expect(rowByName(page, 'Small refund').getByTestId('test-ai-badge')).toHaveCount(0)
    await shot(page, 't17-1-ai-drafts')

    // the fake LLM was asked once with the contract's first line (a repair round may add more)
    const calls = await api.llmCalls()
    expect(calls.some((c) => c.rule_id === 'e2e-draft')).toBe(true)

    // nothing but drafts: save, then re-read through the API
    await saveEditor(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.tests.length).toBe(4)
    let stored = (await getWf(api, wf.id)).definition.tests
    for (const name of ['AI: Risk API is down', 'AI: Normalize without a reason']) {
      expect(stored.find((t) => t.name === name)).toMatchObject({ source: 'ai', status: 'draft', locked: false })
    }
    expect(stored.find((t) => t.name === 'AI: invented step')).toBeUndefined()
    expect(stored.filter((t) => t.source === 'human').length).toBe(2)

    // keep one: opens the editor, approve it
    await r1.getByTestId('test-keep').click()
    const ed = page.getByTestId('test-editor')
    await expect(ed).toBeVisible()
    await ed.getByTestId('test-approved-by').fill('Priya')
    await ed.getByTestId('test-approve').click()
    await expect(ed.getByTestId('test-locked')).toContainText('Priya')
    await shot(page, 't17-1-kept-approved')
    await expect
      .poll(async () => (await getWf(api, wf.id)).definition.tests.find((t) => t.name === 'AI: Risk API is down'))
      .toMatchObject({ status: 'approved', locked: true, approved_by: 'Priya', source: 'ai' })

    // reject the other one
    await page.keyboard.press('Escape')
    await expect(ed).toBeHidden()
    await expect(rowByName(page, 'AI: Normalize without a reason')).toBeVisible()
    await rowByName(page, 'AI: Normalize without a reason').getByTestId('test-reject').click()
    await expect(rowByName(page, 'AI: Normalize without a reason')).toHaveCount(0)
    await saveEditor(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.tests.map((t) => t.name)).not.toContain('AI: Normalize without a reason')
    stored = (await getWf(api, wf.id)).definition.tests
    expect(stored.map((t) => t.name)).toContain('AI: Risk API is down')
    expect(stored.length).toBe(3)
    await shot(page, 't17-1-after-reject')
  })

  test('T17.2 AI failure: the studio shows an error and adds nothing', async ({ page, api, s, net }) => {
    await putLlmRules(api, [{ id: 'e2e-draft-fail', when: { system_matches: '^TEST_AI:DRAFT_TESTS' }, respond: { raise: 'fatal' } }])
    const { wf } = await seedRefund(api, own(s), p, { withSpec: false, mocks: V1_MOCKS, tests: [mkTest('Small refund is auto-approved', 'normal', 100, [{ node: 'Amount over 500', port: 'false', op: 'count', value: 1 }])] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await net.expect([{ method: 'POST', url: /\/tests\/ai-draft/, status: 502 }], async () => {
      await page.getByTestId('tests-ai-fill').click()
    })
    await expect(page.locator('[data-sonner-toast]').first()).toBeVisible()
    await expect(page.getByTestId('tests-ai-busy')).toHaveCount(0)
    await expect(page.getByTestId('test-ai-badge')).toHaveCount(0)
    await shot(page, 't17-2-ai-failed')
    expect((await getWf(api, wf.id)).definition.tests.length).toBe(1)
  })

  test('T17.3 variations panel finds the planted bug; "save as test" adds a test that fails until fixed', async ({ page, api, s }) => {
    const base = mkTest('Big refund reaches the risk API', 'normal', 900, BIG_V1_EXPECT)
    const { wf } = await seedRefund(api, own(s), p, { withSpec: false, mocks: V1_MOCKS, tests: [base] })
    // plant a bug: an Enrich step that trusts every field of the webhook body
    const d = (await getWf(api, wf.id)).definition as unknown as { nodes: Array<Record<string, unknown>>; connections: Array<Record<string, string>> }
    const nodes = [...d.nodes, { name: 'Enrich', type: 'Code', parameters: { code: BUGGY }, position: { x: 140, y: 160 } }]
    const conns = d.connections.filter((c) => !(c.source_node === 'Webhook' && c.target_node === 'Normalize'))
    conns.push({ source_node: 'Webhook', target_node: 'Enrich', source_output: 'main', target_input: 'main' })
    conns.push({ source_node: 'Enrich', target_node: 'Normalize', source_output: 'main', target_input: 'main' })
    await api.put(`/workflows/${wf.id}`, { nodes, connections: conns })

    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await page.getByTestId('tests-fuzz').click()
    await expect(page.getByTestId('fuzz-panel')).toBeVisible()
    await page.getByTestId('fuzz-count').fill('80')
    await expect(page.getByTestId('fuzz-ai')).toBeVisible()
    await page.getByTestId('fuzz-run').click()
    const finding = page.locator('[data-testid="fuzz-finding"][data-node="Enrich"][data-kind="node_error"]').first()
    await expect(finding).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('fuzz-summary')).toBeVisible()
    await expect(finding.getByTestId('fuzz-error')).not.toBeEmpty()
    await expect(finding.getByTestId('fuzz-input')).not.toBeEmpty()
    await shot(page, 't17-3-fuzz-finding')
    // the route saves nothing by itself
    expect((await getWf(api, wf.id)).definition.tests.length).toBe(1)

    await finding.getByTestId('fuzz-save').click()
    await expect(finding.getByTestId('fuzz-save')).toBeDisabled()
    await saveEditor(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.tests.length).toBe(2)
    const saved = (await getWf(api, wf.id)).definition.tests.find((t) => t.source === 'fuzz')!
    expect(saved).toMatchObject({ status: 'draft', locked: false, source: 'fuzz' })
    expect(saved.expect).toEqual([{ node: 'Enrich', op: 'status', value: 'success' }])

    // the saved test is a real failing test; the original still passes
    await page.getByTestId('tests-view-list').click()
    await page.getByTestId('tests-run-all').click()
    await expect(page.getByTestId('tests-run-all')).toBeEnabled()
    await expect(page.locator(`[data-testid="test-row"][data-test-id="${saved.id}"]`)).toHaveAttribute('data-status', /failed|error/)
    await expect(rowByName(page, 'Big refund reaches the risk API')).toHaveAttribute('data-status', 'passed')
    await shot(page, 't17-3-saved-test-fails')
  })

  test('T17.4 drift: banner, stale badge and workflows-list dot appear after the API changes shape, and clear on resolve', async ({ page, api, s }) => {
    await fake.shape('v1')
    const big = mkTest('Big refund reaches the risk API', 'normal', 900, BIG_V1_EXPECT)
    const { wf } = await seedRefund(api, own(s), p, { withSpec: false, mocks: V1_MOCKS, tests: [big], name: 'Refund triage (drift)' })
    const d = (await getWf(api, wf.id)).definition as unknown as { nodes: Array<{ name: string; parameters: Record<string, unknown> }> }
    expect(JSON.stringify(d.nodes)).toContain('/ok')
    const run = (amount = 900) => api.post<{ status: string }>(`/workflows/${wf.id}/run`, { input_data: { order_id: 'o-1', amount, reason: 'late' } })

    // v1: a real run is clean
    expect((await run()).status).toBe('success')
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('drift-banner')).toHaveCount(0)
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows`)
    await expect(page.getByTestId(`workflow-open-${wf.id}`)).toBeVisible()
    await expect(page.getByTestId('wf-drift-dot')).toHaveCount(0)

    // v2: the reply changed shape
    await fake.shape('v2')
    expect((await run()).status).toBe('success')
    const drift = await api.get<{ alerts: Array<{ id: string; node_name: string; status: string }>; stale_test_ids: string[] }>(`/workflows/${wf.id}/drift`)
    expect(drift.alerts.length).toBe(1)
    expect(drift.alerts[0]).toMatchObject({ node_name: 'Risk API', status: 'open' })
    expect(drift.stale_test_ids).toContain((await getWf(api, wf.id)).definition.tests[0].id)

    await openEditor(page, s, p.slug, wf.id)
    const banner = page.getByTestId('drift-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('Risk API reply changed')
    await page.getByTestId('drift-details').click()
    await expect(page.getByTestId('drift-detail-body')).toContainText('Risk API')
    await shot(page, 't17-4-drift-banner')
    await openTestsTab(page)
    await expect(rowByName(page, 'Big refund').getByTestId('test-stale-badge')).toBeVisible()
    await shot(page, 't17-4-stale-badge')

    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows`)
    const dot = page.locator(`[data-testid="wf-drift-dot"][data-workflow-id="${wf.id}"]`)
    await expect(dot).toBeVisible()
    await shot(page, 't17-4-list-dot')

    // resolve in the UI, then re-read through the API and the UI
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('drift-resolve').click()
    await expect(page.getByTestId('drift-banner')).toHaveCount(0)
    const after = await api.get<{ alerts: unknown[]; stale_test_ids: string[] }>(`/workflows/${wf.id}/drift`)
    expect(after).toEqual({ alerts: [], stale_test_ids: [] })
    const all = await api.get<{ alerts: Array<{ status: string }> }>(`/workflows/${wf.id}/drift?status=all`)
    expect(all.alerts.map((a) => a.status)).toEqual(['resolved'])
    await openTestsTab(page)
    await expect(page.getByTestId('test-stale-badge')).toHaveCount(0)
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows`)
    await expect(page.getByTestId(`workflow-open-${wf.id}`)).toBeVisible()
    await expect(page.getByTestId('wf-drift-dot')).toHaveCount(0)
    await fake.shape('v1')
  })

  test('T17.5 live checks: settings persist, a run passes against v1 and fails against v2, with result rows', async ({ page, api, s }) => {
    await fake.shape('v1')
    const big = mkTest('Big refund reaches the risk API', 'normal', 900, BIG_V1_EXPECT)
    const { wf } = await seedRefund(api, own(s), p, { withSpec: false, mocks: V1_MOCKS, tests: [big] })
    const testId = (await getWf(api, wf.id)).definition.tests[0].id
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    const sm = page.getByTestId('smoke-settings')
    await expect(sm).toBeVisible()
    await expect(sm.getByTestId('smoke-run-row')).toHaveCount(0)
    await sm.getByTestId('smoke-enabled').click()
    await sm.getByTestId(`smoke-test-${testId}`).click()
    await sm.getByTestId('smoke-live-Risk API').click()
    await sm.getByTestId('smoke-before-release').click()
    await shot(page, 't17-5-smoke-settings')
    await sm.getByTestId('smoke-save').click()
    await expect
      .poll(async () => api.get<Record<string, unknown>>(`/workflows/${wf.id}/smoke`))
      .toMatchObject({ enabled: true, test_ids: [testId], live_nodes: ['Risk API'], before_release: true, schedule: null })

    // run now against v1: passes, and the result row records the live call
    await sm.getByTestId('smoke-run').click()
    const row1 = sm.getByTestId('smoke-run-row').first()
    await expect(row1).toHaveAttribute('data-status', 'passed', { timeout: 60_000 })
    await expect(row1).toHaveAttribute('data-trigger', 'manual')
    await shot(page, 't17-5-smoke-passed')
    let runs = await api.get<Array<{ status: string; trigger: string; results: Array<{ test_id: string; live_calls: Array<{ node: string; status_code: number }> }> }>>(`/workflows/${wf.id}/smoke/runs`)
    expect(runs.length).toBe(1)
    expect(runs[0]).toMatchObject({ status: 'passed', trigger: 'manual' })
    expect(runs[0].results[0].test_id).toBe(testId)
    expect(runs[0].results[0].live_calls[0]).toMatchObject({ node: 'Risk API', status_code: 200 })

    // the live reply changes shape: body.decision moved, so the structural check fails
    await fake.shape('v2')
    await sm.getByTestId('smoke-run').click()
    await expect(sm.getByTestId('smoke-run-row')).toHaveCount(2, { timeout: 60_000 })
    await expect(sm.getByTestId('smoke-run-row').first()).toHaveAttribute('data-status', 'failed')
    await expect(sm.getByTestId('smoke-run-row').nth(1)).toHaveAttribute('data-status', 'passed')
    await shot(page, 't17-5-smoke-failed')
    runs = await api.get(`/workflows/${wf.id}/smoke/runs`)
    expect(runs.map((r) => r.status)).toEqual(['failed', 'passed'])
    await fake.shape('v1')

    // reload: the settings and both rows survive
    await page.reload()
    await openTestsTab(page)
    await expect(page.getByTestId('smoke-enabled')).toBeChecked()
    await expect(page.getByTestId('smoke-run-row')).toHaveCount(2)
  })
})
