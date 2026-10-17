/** Helpers shared by the design-feature specs (11a–11c): theme matrix, theme assertion, rows API. */
import { expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import type { StackState } from '../global-setup'
import type { EngineApi } from './test'
import type { P } from './seed'

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

/** The page follows `prefers-color-scheme` (theme "system"); assert the html class and the canvas token. */
export async function expectTheme(page: Page, theme: Theme) {
  if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
  else await expect(page.locator('html')).not.toHaveClass(/dark/)
  await expect
    .poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe(theme === 'dark' ? 'rgb(8, 9, 10)' : 'rgb(255, 255, 255)')
}

export interface ApiRow {
  id: number
  data: Record<string, unknown>
  created_at: string
}

/** Every row of a table through the REST API (pages of 100). */
export async function apiRows(api: EngineApi, p: P, table: string): Promise<ApiRow[]> {
  const base = await api.tablePath(p.id, table)
  const out: ApiRow[] = []
  let cursor: string | null = null
  for (let i = 0; i < 50; i++) {
    const page: { rows: ApiRow[]; next_cursor?: string | null } = await api.get(`${base}/rows?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
    out.push(...page.rows)
    cursor = page.next_cursor ?? null
    if (!cursor) break
  }
  return out
}

/**
 * Move runs back in time (the API cannot backdate): shifts start/end of the given executions by `hours`
 * in the throwaway e2e database only.
 */
export function backdateRuns(s: StackState, ids: string[], hours: number) {
  if (!/^workflows_test_e2e_[0-9a-f]+$/.test(s.dbName)) throw new Error(`refusing to touch database ${s.dbName}`)
  if (!ids.length) return
  if (!ids.every((i) => /^[\w-]+$/.test(i))) throw new Error('unexpected run id')
  const list = ids.map((i) => `'${i}'`).join(',')
  const sql = `UPDATE "workflow-app".executions SET start_time = start_time - interval '${hours} hours', end_time = end_time - interval '${hours} hours' WHERE id IN (${list})`
  execFileSync('docker', ['exec', 'workflow-engine-postgres-1', 'psql', '-q', '-U', 'workflow', '-d', s.dbName, '-c', sql], { stdio: 'pipe' })
}
