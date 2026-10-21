// Waiting for a page to be "settled" before taking an accessibility snapshot.
// The legacy approach (domcontentloaded + up to 1.5 s of networkidle) returns too early on SPAs:
// a click starts a fetch, the router changes the URL after it resolves, and skeletons render in between.
// This waits for all of: no in-flight XHR/fetch/document requests (long-lived EventSource/websocket
// excluded), a stable URL, a quiet DOM, and no [aria-busy="true"] - bounded by a hard timeout.
import type { Page, Request } from '@playwright/test';

const tracked = new WeakMap<Page, Set<Request>>();

/** Start counting in-flight requests for a page. Call once, right after the page is created. */
export function trackRequests(page: Page): void {
  if (tracked.has(page)) return;
  const inflight = new Set<Request>();
  tracked.set(page, inflight);
  const isLongLived = (r: Request) =>
    r.resourceType() === 'eventsource' || r.resourceType() === 'websocket' ||
    /text\/event-stream/.test(r.headers()['accept'] ?? '');
  page.on('request', (r) => { if (!isLongLived(r)) inflight.add(r); });
  const done = (r: Request) => inflight.delete(r);
  page.on('requestfinished', done);
  page.on('requestfailed', done);
}

export interface SettleResult { ms: number; timedOut: boolean; reason: string }

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Wait until the page is quiet for `quietMs` in a row, or `timeoutMs` passes.
 * Returns what (if anything) was still pending at the timeout, for the session log.
 */
export async function settlePage(page: Page, opts: { quietMs?: number; timeoutMs?: number } = {}): Promise<SettleResult> {
  const quietMs = opts.quietMs ?? 300;
  const timeoutMs = opts.timeoutMs ?? 8000;
  const start = Date.now();
  if (process.env.TB_SETTLE === 'legacy') {
    await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 1500 }).catch(() => {});
    return { ms: Date.now() - start, timedOut: false, reason: 'legacy' };
  }
  await page.waitForLoadState('domcontentloaded', { timeout: timeoutMs }).catch(() => {});
  // A mutation counter in the page; reinstalled after every navigation (evaluate is a no-op if present).
  const installObserver = () => page.evaluate(() => {
    const w = window as unknown as { __tbMut?: number };
    if (w.__tbMut !== undefined) return;
    w.__tbMut = 0;
    new MutationObserver((m) => { w.__tbMut! += m.length; }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  }).catch(() => {});
  await installObserver();
  const inflight = tracked.get(page);
  let lastUrl = page.url();
  let lastMut = -1;
  let quietSince = Date.now();
  let reason = '';
  while (Date.now() - start < timeoutMs) {
    await sleep(50);
    await installObserver();
    const state = await page.evaluate(() => ({
      mut: (window as unknown as { __tbMut?: number }).__tbMut ?? 0,
      busy: document.querySelectorAll('[aria-busy="true"]').length,
      ready: document.readyState,
    })).catch(() => ({ mut: -2, busy: 1, ready: 'navigating' }));
    const url = page.url();
    const pending = inflight?.size ?? 0;
    const busyNow = pending > 0 || state.busy > 0 || state.ready === 'loading' || url !== lastUrl || state.mut !== lastMut;
    reason = pending ? `${pending} request(s) in flight` : state.busy ? `${state.busy} aria-busy element(s)` : url !== lastUrl ? 'URL changed' : state.mut !== lastMut ? 'DOM mutating' : state.ready;
    lastUrl = url;
    lastMut = state.mut;
    if (busyNow) { quietSince = Date.now(); continue; }
    if (Date.now() - quietSince >= quietMs) return { ms: Date.now() - start, timedOut: false, reason: 'quiet' };
  }
  return { ms: Date.now() - start, timedOut: true, reason };
}
