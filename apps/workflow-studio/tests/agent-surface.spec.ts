import { test, expect, goto } from './fixtures'

/**
 * THE AGENT SURFACE: /build (Script → Workflow) and /agents (fleet + runs),
 * in a real browser against the real workflow-engine API (:8000).
 *
 * /build drives POST /api/workflow-sdk/execute — the same sandboxed core the
 * agent's build_workflow tool calls. These tests prove the whole path: script
 * in → validated graph out → persisted workflow → visible in the engine API —
 * and that a broken script surfaces the SDK's teaching error verbatim.
 *
 * /agents proves the operator loop's chrome: create an agent (whose role verb
 * is DERIVED — an sdk-toolkit agent must read "builds"), trigger a run, and
 * watch it reach a terminal state with its evidence (error or response) on
 * screen. With no LLM credit the run fails fast with an auth error — the
 * detail panel must show that truth, not hide it.
 *
 * Engine-API prerequisite: uvicorn on :8000 with Postgres on :5433. The
 * beforeAll ping fails loudly with the start command, same philosophy as
 * global-setup's analytics ping.
 */

const ENGINE = 'http://localhost:8000/api'
const PREFIX = `uitest-agentsdk-${Date.now()}`

async function engine<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${ENGINE}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (r.status >= 300) {
    throw new Error(`${method} ${path} → ${r.status}: ${(await r.text()).slice(0, 300)}`)
  }
  return (await r.json()) as T
}

test.beforeAll(async () => {
  try {
    const r = await fetch(`${ENGINE.replace('/api', '')}/health`)
    if (!r.ok) throw new Error(`health ${r.status}`)
  } catch (e) {
    throw new Error(
      `workflow-engine API is not reachable at :8000 (${e}). Start it: ` +
        `cd apps/workflow-engine && venv/bin/python -m uvicorn src.main:app --port 8000 ` +
        `(Postgres: docker start workflow-engine-postgres-1 — host port 5433)`,
    )
  }
})

const BROKEN_SCRIPT = 's = Start()\nCron >> s\n'

test('/build runs the example script and renders the graph with line provenance', async ({ page }) => {
  await goto(page, '/build')
  await page.getByTestId('build-run').click()

  const ok = page.getByTestId('build-ok')
  await expect(ok).toBeVisible({ timeout: 20000 })
  // Denominated claim, computed by the backend — not a hardcoded pass.
  await expect(ok).toContainText(/valid — \d+ nodes · \d+ connections/)

  // The canvas rendered one labeled node per provenance chip.
  const chips = page.getByTestId('build-provenance').locator('span')
  const chipCount = await chips.count()
  expect(chipCount).toBeGreaterThanOrEqual(5)
  const canvasNodes = page.getByTestId('build-canvas').locator('svg rect[rx="12"]')
  expect(await canvasNodes.count()).toBeGreaterThanOrEqual(chipCount)

  // The for-loop provenance: three regional loaders all carrying the same line.
  await expect(page.getByTestId('build-provenance')).toContainText('Load US')
  await expect(page.getByTestId('build-provenance')).toContainText('Load APAC')
})

test('/build surfaces the SDK teaching error for a wired constructor', async ({ page }) => {
  await goto(page, '/build')
  await page.locator('.cm-content').click()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type(BROKEN_SCRIPT)
  await page.getByTestId('build-run').click()

  const error = page.getByTestId('build-error')
  await expect(error).toBeVisible({ timeout: 20000 })
  await expect(error).toContainText('line 2')
  await expect(error).toContainText('CONSTRUCTOR')
  // The partial graph is acknowledged, not hidden.
  await expect(error).toContainText('1 node built before it raised')
})

test('/build saves a workflow the engine API can fetch back', async ({ page }) => {
  const name = `${PREFIX}-saved`
  await goto(page, '/build')
  await page.getByTestId('build-run').click()
  await expect(page.getByTestId('build-ok')).toBeVisible({ timeout: 20000 })

  await page.getByTestId('build-name').fill(name)
  await page.getByTestId('build-save').click()

  const openLink = page.getByTestId('build-open-editor')
  await expect(openLink).toBeVisible({ timeout: 20000 })
  const href = await openLink.getAttribute('href')
  const workflowId = new URL(href!, 'http://x').searchParams.get('workflowId')!
  expect(workflowId).toBeTruthy()

  // Verify the write by re-reading from the engine, then clean up.
  try {
    const detail = await engine<{ name: string; definition: { nodes: unknown[] } }>(
      'GET',
      `/workflows/${workflowId}`,
    )
    expect(detail.name).toBe(name)
    expect(detail.definition.nodes.length).toBeGreaterThanOrEqual(5)
  } finally {
    await engine('DELETE', `/workflows/${workflowId}`)
  }
})

test('/agents creates a builds-verb agent and a triggered run reaches a terminal state on screen', async ({ page }) => {
  const agentName = `${PREFIX}-agent`
  await goto(page, '/agents')

  await page.getByTestId('new-agent-name').fill(agentName)
  await page.getByTestId('new-agent-create').click()

  // The fleet rail shows it, with the DERIVED verb: sdk toolkit ⇒ builds.
  const row = page.getByTestId('agent-row').filter({ hasText: agentName })
  await expect(row).toBeVisible({ timeout: 10000 })
  await expect(row).toContainText('builds')

  // Trigger a run against it.
  const agents = await engine<Array<{ id: string; name: string }>>('GET', '/agents')
  const agent = agents.find((a) => a.name === agentName)!
  expect(agent).toBeTruthy()
  await page.getByTestId('run-agent-select').selectOption(agent.id)
  await page
    .getByTestId('run-task')
    .fill('Build a workflow that fetches an export and loads it into Postgres')
  await page.getByTestId('run-trigger').click()

  // The detail panel opens and the run reaches a terminal state. Without LLM
  // credit that is `failed` with the auth error verbatim; with credit it may
  // be `success` with a response — both are honest terminal outcomes.
  const detail = page.getByTestId('run-detail')
  await expect(detail).toBeVisible({ timeout: 10000 })
  const status = page.getByTestId('run-status')
  await expect(status).toContainText(/failed|success|cancelled/, { timeout: 60000 })
  const failed = (await status.textContent())?.includes('failed')
  if (failed) {
    await expect(page.getByTestId('run-error')).toBeVisible()
    expect((await page.getByTestId('run-error').textContent())!.length).toBeGreaterThan(5)
  } else {
    await expect(page.getByTestId('run-response')).toBeVisible()
  }

  // The runs table lists it with the same task text.
  await expect(
    page.getByTestId('run-row').filter({ hasText: 'fetches an export' }).first(),
  ).toBeVisible()

  // Cleanup: archive/delete the agent (hard delete only if sessions allow).
  await engine('DELETE', `/agents/${agent.id}`)
})
