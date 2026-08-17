import { test as base, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * The harness.
 *
 * Four rules, carried over from the suite this replaces. They are here because
 * each one was learned from a bug that shipped:
 *
 *  1. **Never hardcode a pass.** Compute every assertion from the DOM or from a
 *     fresh API read. A test that always passes is worse than no test.
 *  2. **Verify writes by re-reading.** A toast is not evidence. Ten delete call
 *     sites once reported failure while succeeding.
 *  3. **Seed your own fixture** with known answers. Two tests once depended on
 *     ambient state and passed only because earlier work had left it behind.
 *  4. **Scope selectors.** A bare `Delete` matched the page-level delete before
 *     the row's, and the first draft deleted its own fixture.
 *
 * To which this suite adds a fifth: **console errors fail the test.** The
 * previous harness collected them and never asserted on them, so "zero console
 * errors" was a claim verified by hand exactly once.
 */

export const API_BASE = process.env.API_BASE || 'http://localhost:8001/api/v1'
export const ADMIN = process.env.ADMIN_USER_ID || '00000000-0000-0000-0000-000000000001'
export const DEFAULT_TEAM = '00000000-0000-0000-0000-000000000001'

/**
 * Dismiss any visible toasts before interacting with what is under them.
 *
 * The Toaster sits bottom-right, which is exactly where the lens dock's action
 * buttons are. A toast is a real element with real pointer events (it carries a
 * close button), so while one is up it genuinely intercepts clicks on the
 * control beneath — Playwright reports
 * `<li data-sonner-toast> … subtree intercepts pointer events` and retries
 * until the test times out. Under full-suite load there are more toasts in
 * flight and they overlap more actions, which is why this only ever failed in
 * a full run.
 *
 * This is not papering over a flake: a human hitting the same overlap has the
 * same problem, and the honest fix on the product side is for toasts not to
 * cover the dock. Until then, tests clear them deliberately rather than racing
 * the auto-dismiss.
 */
export async function dismissToasts(page: Page): Promise<void> {
  const toasts = page.locator('[data-sonner-toast]')
  for (let guard = 0; guard < 10; guard++) {
    if ((await toasts.count()) === 0) return
    const close = toasts.first().getByRole('button', { name: /close/i })
    if (await close.count()) await close.first().click({ timeout: 2_000 }).catch(() => {})
    else await page.mouse.click(4, 4)
    await page.waitForTimeout(150)
  }
}

/**
 * Drive a `ScopePicker` — the custom listbox behind Version, Sheet and Acting
 * seat in the app chrome.
 *
 * Those three are no longer native `<select>` elements (an OS widget in the
 * middle of custom chrome was the loudest remaining visual tell), so
 * `selectOption()` does not drive them. This is the replacement: open the
 * trigger by its accessible name, then click the option by its visible text.
 *
 * The FORM selects inside panels and dialogs are still native and still take
 * `selectOption()` — that line is deliberate and is documented in
 * `ScopePicker.tsx`.
 */
export async function pickScope(
  page: Page,
  label: string,
  option: string | RegExp,
): Promise<void> {
  await page.getByLabel(label).click()

  // Scoped to the open listbox on purpose. A native `<select><option>` also
  // carries `role=option`, and the datasets dock has several — an unscoped
  // `getByRole('option')` can match one of those and click a control the test
  // never meant to touch, or make the unmount assertion below never settle.
  const popup = page.getByRole('listbox')
  await popup.getByRole('option', { name: option }).first().click()

  // The listbox unmounts on select; waiting for that keeps the next action
  // from racing a popup that is still capturing pointer events.
  await expect(popup).toHaveCount(0)
}

/** The localStorage key the studio's identity store reads at module init. */
const IDENTITY_KEY = 'studio.identity'

export interface ApiResult<T = any> {
  status: number
  body: T
}

/** Raw call — returns the status instead of throwing, so tests can assert on it. */
export async function api<T = any>(
  method: string,
  path: string,
  opts: { body?: unknown; userId?: string } = {},
): Promise<ApiResult<T>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'X-User-Id': opts.userId || ADMIN,
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  // 204 carries no body but still arrives with a JSON content-type.
  const text = res.status === 204 ? '' : await res.text()
  let parsed: any = null
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = text
  }
  return { status: res.status, body: parsed }
}

/** Call that must succeed; failures carry the body so the message is useful. */
export async function apiOk<T = any>(
  method: string,
  path: string,
  opts: { body?: unknown; userId?: string } = {},
): Promise<T> {
  const r = await api<T>(method, path, opts)
  if (r.status >= 300) {
    throw new Error(`${method} ${path} → ${r.status}: ${JSON.stringify(r.body).slice(0, 400)}`)
  }
  return r.body
}

/** CSV, because upload names the dataset after the filename — which we control. */
function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return ''
  const cols = Object.keys(rows[0])
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n')
}

/**
 * Every fixture this suite creates is named with this prefix, which is how the
 * sweep in `sweep.ts` finds them without any per-test bookkeeping.
 */
/**
 * Every fixture this suite creates is named with this prefix, which is how the
 * sweep finds them without any per-test bookkeeping.
 *
 * `UITEST_PREFIX` namespaces a run. The sweep deletes everything matching the
 * prefix at BOTH ends of a run, so two concurrent runs sharing one prefix
 * destroy each other's fixtures mid-test — which is exactly what happens when
 * several people (or several agents) drive this suite against one API at once.
 * Giving a run its own namespace makes concurrent runs safe:
 *
 *     UITEST_PREFIX=uitest-query- npx playwright test query.spec.ts
 *
 * Default is unchanged, so a normal `npm run test:ui` behaves as before.
 */
export const TEST_PREFIX = process.env.UITEST_PREFIX || 'uitest-'

let counter = 0
/** Unique per run AND per call, so parallel workers never collide on a name. */
export function uniqueName(prefix: string): string {
  counter += 1
  return `${TEST_PREFIX}${prefix}-${Date.now().toString(36)}-${counter}`
}

/**
 * Path to the analytics-service virtualenv, used only to write a real .xlsx.
 *
 * Multi-sheet behaviour (`sheet-selection-required`, per-sheet queries, whole
 * *version* tags) cannot be exercised with a CSV — a single-file upload always
 * lands one sheet called `data`. Rather than add a JS spreadsheet dependency to
 * ship a test fixture, borrow the openpyxl that is already installed next door.
 */
const VENV_PYTHON =
  process.env.ANALYTICS_PYTHON ||
  join(
    // The package is ESM ("type": "module"), so there is no __dirname.
    dirname(fileURLToPath(import.meta.url)),
    '..',
    '..',
    'analytics-service',
    'venv',
    'bin',
    'python',
  )

/** Build a real multi-sheet workbook on disk and return its bytes. */
function buildWorkbook(sheets: Record<string, Record<string, unknown>[]>): Buffer {
  const out = join(tmpdir(), `uitest-${Date.now().toString(36)}-${counter++}.xlsx`)
  const script = `
import json, sys
from openpyxl import Workbook
sheets = json.loads(sys.argv[1])
wb = Workbook()
wb.remove(wb.active)
for name, rows in sheets.items():
    ws = wb.create_sheet(title=name)
    if rows:
        cols = list(rows[0].keys())
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
wb.save(sys.argv[2])
`
  execFileSync(VENV_PYTHON, ['-c', script, JSON.stringify(sheets), out], { stdio: 'pipe' })
  const bytes = readFileSync(out)
  rmSync(out, { force: true })
  return bytes
}

export interface SeededDataset {
  id: string
  name: string
  /** A single-file CSV always lands its sheet under this name. */
  sheet: string
}

export interface Harness {
  /** Create a dataset from rows. Cleaned up automatically after the test. */
  seed: (rows: Record<string, unknown>[], prefix?: string) => Promise<SeededDataset>
  /** Create a real multi-sheet .xlsx dataset. Cleaned up automatically. */
  seedWorkbook: (
    sheets: Record<string, Record<string, unknown>[]>,
    prefix?: string,
  ) => Promise<{ id: string; name: string; sheets: string[] }>
  /** Upload another file to an existing dataset, creating a new version. */
  addVersion: (datasetId: string, rows: Record<string, unknown>[]) => Promise<void>
  /** Mark a column sensitive — this is what turns masking on. */
  markSensitive: (datasetId: string, column: string, sheet?: string) => Promise<void>
  /** A viewer seat in the Default team, created if the install lacks one. */
  viewer: () => Promise<{ user_id: string; name: string; role: string }>
  /** Load the app as a given seat. Must be called before the first navigation. */
  asSeat: (page: Page, userId: string, label?: string) => Promise<void>
  /** Console errors / page errors / HTTP 5xx seen so far. */
  errors: string[]
  /** Permit an expected error (e.g. a deliberate 403 logged by a dependency). */
  allowError: (pattern: RegExp) => void
}

export const test = base.extend<{ h: Harness }>({
  h: async ({ page }, use) => {
    const errors: string[] = []
    const allowed: RegExp[] = []

    // Chromium's console message for a failed request is just "Failed to load
    // resource: the server responded with a status of 404" — no URL. That makes
    // an intermittent failure nearly undiagnosable, so record every 4xx/5xx
    // separately with its method and URL and attach the list to the failure.
    const requestFailures: string[] = []

    /**
     * Cut the browser off from the public internet.
     *
     * This used to exist because `index.html` pulled Inter and JetBrains Mono
     * from Google Fonts, so every run depended on fonts.gstatic.com being
     * reachable and a CDN hiccup failed whichever test happened to be running.
     * The app is on system fonts now (INSTRUMENT mandates zero network
     * resources), so nothing should reach the public internet at all — this
     * route is kept as an assertion of that rather than as a workaround. If it
     * ever fires, a network dependency has crept back in.
     */
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) =>
      route.abort('blockedbyclient'),
    )

    page.on('pageerror', (e) => errors.push(`[pageerror] ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(`[console.error] ${m.text()}`)
    })
    page.on('response', (r) => {
      if (r.status() >= 400) requestFailures.push(`${r.status()} ${r.request().method()} ${r.url()}`)
      if (r.status() >= 500) errors.push(`[HTTP ${r.status()}] ${r.url()}`)
    })
    page.on('requestfailed', (r) => {
      requestFailures.push(`FAILED ${r.method()} ${r.url()} — ${r.failure()?.errorText}`)
    })

    const harness: Harness = {
      errors,
      allowError: (p) => allowed.push(p),

      seed: async (rows, prefix = 'ds') => {
        const name = uniqueName(prefix)
        const fd = new FormData()
        fd.append('file', new Blob([rowsToCsv(rows)], { type: 'text/csv' }), `${name}.csv`)
        const res = await fetch(`${API_BASE}/upload?sync=true`, {
          method: 'POST',
          headers: { 'X-User-Id': ADMIN },
          body: fd,
        })
        if (!res.ok) {
          throw new Error(`seed failed: ${res.status} ${(await res.text()).slice(0, 300)}`)
        }
        const body = await res.json()
        return { id: body.dataset_id, name: `${name}.csv`, sheet: 'data' }
      },

      seedWorkbook: async (sheets, prefix = 'wb') => {
        const name = uniqueName(prefix)
        const fd = new FormData()
        fd.append(
          'file',
          new Blob([new Uint8Array(buildWorkbook(sheets))], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          }),
          `${name}.xlsx`,
        )
        const res = await fetch(`${API_BASE}/upload?sync=true`, {
          method: 'POST',
          headers: { 'X-User-Id': ADMIN },
          body: fd,
        })
        if (!res.ok) {
          throw new Error(`seedWorkbook failed: ${res.status} ${(await res.text()).slice(0, 300)}`)
        }
        const body = await res.json()
        return { id: body.dataset_id, name: `${name}.xlsx`, sheets: Object.keys(sheets) }
      },

      addVersion: async (datasetId, rows) => {
        const fd = new FormData()
        fd.append('file', new Blob([rowsToCsv(rows)], { type: 'text/csv' }), `${uniqueName('v')}.csv`)
        fd.append('dataset_id', datasetId)
        const res = await fetch(`${API_BASE}/upload?sync=true`, {
          method: 'POST',
          headers: { 'X-User-Id': ADMIN },
          body: fd,
        })
        if (!res.ok) {
          throw new Error(`addVersion failed: ${res.status} ${(await res.text()).slice(0, 300)}`)
        }
      },

      markSensitive: async (datasetId, column, sheet = 'data') => {
        await apiOk('PUT', `/datasets/${datasetId}/sheet-metadata/${sheet}/columns/${column}`, {
          body: { sensitivity: 'confidential', semantic_type: 'pii' },
        })
      },

      viewer: async () => {
        const members = await apiOk<{ items: any[] }>(`GET`, `/teams/${DEFAULT_TEAM}/members`)
        const existing = members.items.find((m) => m.role === 'viewer')
        if (existing) return existing

        // No viewer out of the box — the migrations seed only the System owner.
        const email = `${uniqueName('viewer')}@example.com`
        const user = await apiOk<any>('POST', '/auth/users', {
          body: { email, name: 'UI Test Viewer' },
        })
        await apiOk('POST', `/teams/${DEFAULT_TEAM}/members`, {
          body: { email, role: 'viewer' },
        })
        // The create may already place them; make the role explicit either way.
        await apiOk('PATCH', `/teams/${DEFAULT_TEAM}/members/${user.id}`, {
          body: { role: 'viewer' },
        })
        const after = await apiOk<{ items: any[] }>('GET', `/teams/${DEFAULT_TEAM}/members`)
        const seat = after.items.find((m) => m.user_id === user.id)
        if (!seat) throw new Error(`viewer seat ${user.id} not present after creation`)
        return seat
      },

      asSeat: async (p, userId, label = 'Test seat') => {
        await p.addInitScript(
          ([k, v]) => window.localStorage.setItem(k as string, v as string),
          [IDENTITY_KEY, JSON.stringify({ userId, label })] as const,
        )
      },
    }

    await use(harness)

    // NOTE: fixtures are deliberately NOT deleted here. Deleting mid-run meant
    // one worker destroyed datasets while another worker's page had them
    // listed, which surfaced as an unrelated 404. Cleanup is a prefix sweep at
    // both ends of the run instead — see `sweep.ts`.
    const unexpected = errors.filter((e) => !allowed.some((p) => p.test(e)))
    const detail =
      `unexpected console/page errors:\n${unexpected.join('\n')}` +
      (requestFailures.length ? `\n\nfailing requests seen:\n${requestFailures.join('\n')}` : '')
    expect(unexpected, detail).toEqual([])
  },
})

export { expect }

/**
 * Navigate and wait for the app to have settled.
 *
 * `networkidle` alone is not enough: the studio fires its analytics queries
 * after hydration, so the first idle can precede the data arriving.
 */
export async function goto(page: Page, path: string) {
  await page.goto(path)
  await page.waitForLoadState('networkidle')
}
