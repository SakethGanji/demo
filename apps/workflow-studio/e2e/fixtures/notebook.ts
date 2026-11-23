/**
 * Notebook spec helpers (specs 40–44). The bank DB and its connections (`bank` read-only, `bank_rw` writable) live
 * in proj_default (tests/e2e_stack.py nbseed), so notebooks that read or write it are created there with unique
 * titles; nothing shared is changed except bank.wires W4 (spec 42 releases it, idempotently).
 */
import { execFileSync } from 'node:child_process'
import type { Page } from '@playwright/test'
import { expect, rand, type EngineApi, type Expectation } from './test'
import type { StackState } from '../global-setup'

export interface NbP { id: string; slug: string }
export interface NbCell { id: string; name: string; kind: string; position: number; stale: boolean; latest: { status: string; output: Record<string, unknown> | null } | null; children: NbCell[] | null }
export interface Nb { id: string; title: string; status: string; active_run_id: string | null; cells: NbCell[]; runs: Array<{ id: string; n: number; status: string; stop: { reason: string; cell_id: string | null } | null }> }

export async function bankProject(api: EngineApi): Promise<NbP> {
  const p = await api.get<{ id: string; slug: string }>('/projects/proj_default')
  return { id: p.id, slug: p.slug }
}

export function newNotebook(api: EngineApi, pid: string, body: Record<string, unknown> = {}) {
  return api.post<Nb>(`/projects/${pid}/notebooks`, { title: `e2e nb ${rand()}`, created_by: 'you', ...body })
}

export function addCell(api: EngineApi, nbId: string, kind: string, spec: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return api.post<NbCell>(`/notebooks/${nbId}/cells`, { kind, spec, created_by: 'you', ...extra })
}

export function getNotebook(api: EngineApi, nbId: string) {
  return api.get<Nb>(`/notebooks/${nbId}`)
}

/** Wait (API side) until the notebook holds no run, or its latest run needs you. */
export async function settled(api: EngineApi, nbId: string, ms = 30_000): Promise<Nb> {
  const deadline = Date.now() + ms
  for (;;) {
    const nb = await getNotebook(api, nbId)
    if (!nb.active_run_id || nb.runs[0]?.status === 'needs_you') return nb
    if (Date.now() > deadline) throw new Error(`notebook ${nbId} still running: ${JSON.stringify(nb.runs[0])}`)
    await new Promise((r) => setTimeout(r, 150))
  }
}

export async function runAndSettle(api: EngineApi, nbId: string, body: Record<string, unknown> = {}) {
  await api.post(`/notebooks/${nbId}/runs`, { triggered_by: 'you', ...body })
  return settled(api, nbId)
}

/**
 * Routes the page calls on load that may not exist on an older engine (501): probe them here and return the
 * expectations to declare, so a spec passes on both.
 */
export async function notBuilt(api: EngineApi, probes: Array<{ method?: string; path: string; url: RegExp; body?: unknown }>): Promise<Expectation[]> {
  const out: Expectation[] = []
  for (const p of probes) {
    const r = await api.raw(p.method ?? 'GET', p.path, p.body)
    if (r.status === 501) out.push({ method: p.method ?? 'GET', url: p.url, status: 501 })
  }
  return out
}

export const CAPS_PROBE = { path: '/notebooks/capabilities', url: /\/api\/notebooks\/capabilities$/ }
export const TEMPLATES_PROBE = { path: '/notebook-templates', url: /\/api\/notebook-templates$/ }

export async function openNotebook(page: Page, s: StackState, slug: string, nbId: string, search = '') {
  await page.goto(`${s.studioUrl}/p/${slug}/notebooks/${nbId}${search}`)
  await expect(page.getByTestId('notebook-page')).toBeVisible()
  await expect(page.getByTestId('nb-cells')).toBeVisible()
}

export const cellEl = (page: Page, name: string) => page.locator(`[data-testid="nb-cell"][data-cell="${name}"]`)

/**
 * Put a cell of a removed kind (approval / person) into a notebook the way an older notebook stored it. The API
 * refuses to create one (kind_removed), so this writes the row in the throwaway e2e database only.
 */
export function insertRemovedCell(s: StackState, nbId: string, kind: 'approval' | 'person', position: number, name: string, spec: Record<string, unknown>) {
  if (!/^workflows_test_e2e_[0-9a-f]+$/.test(s.dbName)) throw new Error(`refusing to touch database ${s.dbName}`)
  if (!/^[\w-]+$/.test(nbId) || !/^c\d+$/.test(name) || !Number.isInteger(position)) throw new Error('unexpected id')
  const json = JSON.stringify(spec).replace(/'/g, "''")
  const sql = `INSERT INTO "workflow-app".notebook_cells (id, notebook_id, position, name, kind, title, spec, created_via) ` +
    `VALUES ('cell_e2e${rand().replace(/[^\w]/g, '')}', '${nbId}', ${position}, '${name}', '${kind}', 'An old ${kind} step', '${json}'::jsonb, 'user')`
  execFileSync('docker', ['exec', 'workflow-engine-postgres-1', 'psql', '-q', '-U', 'workflow', '-d', s.dbName, '-c', sql], { stdio: 'pipe' })
}
