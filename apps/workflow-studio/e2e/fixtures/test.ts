/**
 * E2E test fixture: the isolated stack, an engine API client, and the network / console /
 * layout policy of UI-DESIGN §12.3, enforced on every page of every scenario.
 *
 * - Network: any 4xx/5xx from the engine, the studio or a published app fails the test unless
 *   the current step declared it with `net.expect([...], async () => {...})`; a declared status
 *   that never happened also fails.
 * - Console: any console.error / console.warn / pageerror / "Warning:" fails the test (studio,
 *   preview iframe and published pages). The browser's own "Failed to load resource" line for a
 *   4xx/5xx response is owned by the network policy (declared → allowed, otherwise the response
 *   itself already fails the test), so it is not double-counted.
 * - Layout: `assertNoClipping(page)` runs automatically at the end of every UI scenario.
 */
import { test as base, expect, type BrowserContext, type Page, type Response } from '@playwright/test'
import fs from 'node:fs'
import { CONSOLE_ALLOW } from '../console-allow'
import { STATE_FILE, type StackState } from '../global-setup'
import { assertNoClipping } from './layout'

export { expect }

export function stack(): StackState {
  return JSON.parse(fs.readFileSync(process.env.E2E_STATE ?? STATE_FILE, 'utf8')) as StackState
}

// ── engine API client (Node side; used for seeding and for backend assertions) ─────────────
export interface ApiResult<T = unknown> {
  status: number
  body: T
  headers: Headers
  text: string
}

export class EngineApi {
  readonly base: string
  readonly token: string
  constructor(s: StackState) {
    this.base = s.engineUrl
    this.token = s.token
  }
  async raw<T = unknown>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<ApiResult<T>> {
    const url = path.startsWith('http') ? path : `${this.base}${path.startsWith('/api') || path.startsWith('/a/') ? '' : '/api'}${path}`
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', ...headers },
      body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
    })
    const text = await res.text()
    let parsed: unknown = text
    try {
      parsed = text ? JSON.parse(text) : undefined
    } catch {
      /* text */
    }
    return { status: res.status, body: parsed as T, headers: res.headers, text }
  }
  /** Like `raw` but throws unless the status is 2xx (seeding must not silently fail). */
  async ok<T = unknown>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    const r = await this.raw<T>(method, path, body, headers)
    if (r.status < 200 || r.status >= 300) throw new Error(`${method} ${path} → ${r.status}: ${r.text.slice(0, 500)}`)
    return r.body
  }
  get<T = unknown>(path: string) {
    return this.ok<T>('GET', path)
  }
  post<T = unknown>(path: string, body?: unknown) {
    return this.ok<T>('POST', path, body ?? {})
  }
  put<T = unknown>(path: string, body?: unknown) {
    return this.ok<T>('PUT', path, body)
  }
  del<T = unknown>(path: string) {
    return this.ok<T>('DELETE', path)
  }
  /** test-only endpoints (`/api/_test/*`, token-gated) */
  testApi<T = unknown>(method: string, path: string, body?: unknown) {
    return this.ok<T>(method, `/api/_test/${path}`, body, { 'X-Test-Token': this.token })
  }
  async llmCalls(): Promise<Array<{ seq: number; rule_id: string | null; messages: Array<{ role: string; content: unknown }>; tools: unknown[]; model: string }>> {
    return this.testApi('GET', 'fake-llm/calls')
  }
  clearLlmCalls() {
    return this.testApi('DELETE', 'fake-llm/calls')
  }
}

// ── network + console policy ───────────────────────────────────────────────────────────────
export interface Expectation {
  method?: string
  url: RegExp
  status: number
}

interface Seen {
  method: string
  url: string
  status: number
  at: number
}

export class Policy {
  private allowed: Array<Expectation & { seen: number }> = []
  readonly violations: string[] = []
  readonly responses: Seen[] = []
  readonly bodies: Array<{ url: string; text: string }> = []
  captureBodies = false
  private origins: string[]
  constructor(origins: string[]) {
    this.origins = origins
  }

  watch(context: BrowserContext) {
    context.on('response', (r) => void this.onResponse(r))
    const hookPage = (p: Page) => {
      p.on('console', (m) => {
        const t = m.type()
        const text = m.text()
        if (t !== 'error' && t !== 'warning' && !/^Warning:/.test(text)) return
        if (/^Failed to load resource: the server responded with a status of \d+/.test(text)) return
        if (CONSOLE_ALLOW.some((re) => re.test(text))) return
        this.violations.push(`console.${t} on ${p.url()}: ${text.slice(0, 600)}`)
      })
      p.on('pageerror', (e) => this.violations.push(`pageerror on ${p.url()}: ${e.message}`))
      // fonts: answered locally with empty CSS, the fallback system font is accepted (§12.2)
      void p.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.fulfill({ status: 200, contentType: 'text/css', body: '' }))
    }
    context.pages().forEach(hookPage)
    context.on('page', hookPage)
  }

  private async onResponse(r: Response) {
    const url = r.url()
    if (!this.origins.some((o) => url.startsWith(o))) return
    const method = r.request().method()
    const status = r.status()
    this.responses.push({ method, url, status, at: Date.now() })
    if (this.captureBodies) {
      try {
        this.bodies.push({ url, text: await r.text() })
      } catch {
        /* streaming or redirect bodies */
      }
    }
    if (status < 400) return
    const a = this.allowed.find((x) => x.status === status && x.url.test(url) && (!x.method || x.method === method))
    if (a) {
      a.seen++
      return
    }
    this.violations.push(`unexpected HTTP ${status} ${method} ${url}`)
  }

  /** Declare the 4xx/5xx a step expects; each must happen (within `waitMs` after the step). */
  async expect<T>(exps: Expectation[], step: () => Promise<T>, waitMs = 10_000): Promise<T> {
    const entries = exps.map((e) => ({ ...e, seen: 0 }))
    this.allowed.push(...entries)
    try {
      const out = await step()
      const deadline = Date.now() + waitMs
      while (entries.some((e) => e.seen === 0) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 100))
      for (const e of entries)
        if (e.seen === 0) this.violations.push(`declared HTTP ${e.status} ${e.method ?? '*'} ${e.url} never happened`)
      return out
    } finally {
      this.allowed = this.allowed.filter((x) => !entries.includes(x))
    }
  }

  assertClean() {
    expect(this.violations, 'network/console policy violations').toEqual([])
  }
}

// ── fixtures ───────────────────────────────────────────────────────────────────────────────
interface Fixtures {
  s: StackState
  api: EngineApi
  net: Policy
  /** open a studio path in the page (base = the isolated Vite) */
  open: (path: string, page?: Page) => Promise<void>
  /** set to false in a test that ends on a non-studio page */
  layoutCheck: { enabled: boolean }
}

export const test = base.extend<Fixtures>({
  s: async ({}, use) => {
    await use(stack())
  },
  api: async ({ s }, use) => {
    await use(new EngineApi(s))
  },
  layoutCheck: async ({}, use) => {
    await use({ enabled: true })
  },
  net: [
    async ({ context, s }, use) => {
      const p = new Policy([s.engineUrl, s.studioUrl])
      p.watch(context)
      await use(p)
    },
    { auto: true },
  ],
  open: async ({ page, s }, use) => {
    await use(async (path: string, pg?: Page) => {
      await (pg ?? page).goto(`${s.studioUrl}${path}`)
    })
  },
  page: async ({ page, net, layoutCheck, s }, use, testInfo) => {
    await use(page)
    // end-of-scenario checks: layout first (needs the page), then the policy
    if (layoutCheck.enabled && testInfo.status === testInfo.expectedStatus && !page.isClosed() && page.url().startsWith(s.studioUrl)) {
      await page.waitForTimeout(150)
      await assertNoClipping(page)
    }
    net.assertClean()
  },
})

export function rand(n = 6) {
  return Math.random().toString(36).slice(2, 2 + n)
}
