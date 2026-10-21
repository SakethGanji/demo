// UI cases: one fresh browser context per case, test-builder's policy + executor, live frames, trace + video.
import { mkdirSync, renameSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { chromium, type Browser, type Page } from '@playwright/test';
import { execute, type ExecResult } from '../../test-builder/src/explore.ts';
import { installNetworkPolicy, type PolicyBlock } from '../../test-builder/src/policy.ts';
import { trackRequests, settlePage } from '../../test-builder/src/settle.ts';
import { describeAction } from '../../test-builder/src/actions.ts';
import { scrub } from '../../test-builder/src/secrets.ts';
import type { Target } from '../../test-builder/src/config.ts';
import { emit, saveBaseline, type PassBaseline, type CaseResult, type FailureContext, type Job } from './store.ts';
import { gate } from './control.ts';
import { refOf, type UiStep } from './spec.ts';

let browserP: Promise<Browser> | null = null;
export function sharedBrowser(): Promise<Browser> {
  if (!browserP) {
    browserP = chromium.launch({ headless: process.env.JOBS_HEADED !== '1' });
    browserP.then((b) => b.on('disconnected', () => { browserP = null; })).catch(() => { browserP = null; });
  }
  return browserP;
}
export async function closeBrowser(): Promise<void> {
  if (browserP) await (await browserP).close().catch(() => {});
}

/** Non-empty lines of the page's visible text (scrubbed), for a text diff. */
async function visibleLines(page: Page, creds: Record<string, string>): Promise<string[]> {
  const t = await page.locator('body').innerText({ timeout: 2000 }).catch(() => '');
  return scrub(t, creds).split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 200);
}

export const describeStep = (s: UiStep) => (s.action === 'expectUrl' ? `expect URL to contain "${s.url}"` : describeAction(s));

// ---------------------------------------------------------------- similarity (for triage candidates)
export function levenshtein(a: string, b: string): number {
  const d = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let prev = d[0]!;
    d[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = d[j]!;
      d[j] = Math.min(d[j]! + 1, d[j - 1]! + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return d[b.length]!;
}
export function similarity(a: string, b: string): number {
  const x = a.trim().toLowerCase(), y = b.trim().toLowerCase();
  if (!x || !y) return 0;
  if (x === y) return 1;
  const lev = 1 - levenshtein(x, y) / Math.max(x.length, y.length);
  const contains = x.includes(y) || y.includes(x) ? 0.75 : 0;
  const tx = new Set(x.split(/\W+/).filter(Boolean)), ty = new Set(y.split(/\W+/).filter(Boolean));
  const inter = [...tx].filter((t) => ty.has(t)).length;
  const jac = inter / (new Set([...tx, ...ty]).size || 1);
  return Math.round(Math.max(lev, contains, jac) * 100) / 100;
}

/** Every `- role "name"` pair in an aria snapshot. */
export function ariaPairs(snapshot: string): { role: string; name: string }[] {
  const out: { role: string; name: string }[] = [];
  for (const l of snapshot.split('\n')) {
    const m = /^\s*-\s+([\w-]+)\s+"((?:[^"\\]|\\.)*)"/.exec(l);
    if (m) out.push({ role: m[1]!, name: m[2]!.replace(/\\"/g, '"') });
  }
  return out;
}

// ---------------------------------------------------------------- live frame
async function startFrames(job: Job, caseId: string, page: Page): Promise<() => Promise<void>> {
  const live = job.live.get(caseId)!;
  let lastCast = 0;
  let stopped = false;
  const setFrame = (buf: Buffer) => { if (buf.length) { job.frame = buf; job.frameAt = Date.now(); } };
  try {
    const cdp = await page.context().newCDPSession(page);
    live.cdp = cdp; // kept addressable for a future "take over" (POST /jobs/{id}/control)
    cdp.on('Page.screencastFrame', (f: { data: string; sessionId: number }) => {
      lastCast = Date.now();
      setFrame(Buffer.from(f.data, 'base64'));
      cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
    });
    await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 1280, maxHeight: 800, everyNthFrame: 1 });
  } catch { /* fall back to periodic screenshots only */ }
  // Fallback: the screencast only emits on paint; when it has been quiet, take a screenshot (~every 700 ms).
  let busy = false;
  const timer = setInterval(() => {
    if (stopped || busy || Date.now() - lastCast < 1500 || page.isClosed()) return;
    busy = true;
    page.screenshot({ type: 'jpeg', quality: 60, timeout: 2000 }).then(setFrame, () => {}).finally(() => { busy = false; });
  }, 700);
  return async () => {
    stopped = true;
    clearInterval(timer);
    await live.cdp?.send('Page.stopScreencast').catch(() => {});
    await live.cdp?.detach().catch(() => {});
  };
}

// ---------------------------------------------------------------- runner
export interface UiCaseInput { job: Job; target: Target; caseId: string; steps: UiStep[]; caseDir: string; signal: AbortSignal }
type UiOutcome = Pick<CaseResult, 'status' | 'failed_step' | 'error'> & { files: string[] };

const STEP_TIMEOUT = 5000;

async function expectUrl(page: Page, want: string): Promise<void> {
  const re = /^\/(.*)\/([a-z]*)$/s.exec(want);
  const test = (u: string) => (re ? new RegExp(re[1]!, re[2]).test(u) : u.includes(want));
  const end = Date.now() + STEP_TIMEOUT;
  while (Date.now() < end) {
    if (test(page.url())) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`expected URL to ${re ? 'match' : 'contain'} "${want}", got "${page.url()}"`);
}

export async function runUiCase(i: UiCaseInput): Promise<UiOutcome> {
  const { job, target, caseId, steps } = i;
  const files: string[] = [];
  const blocks: PolicyBlock[] = [];
  const network: NonNullable<FailureContext['network']> = [];
  const downloads = join(i.caseDir, 'downloads');
  mkdirSync(downloads, { recursive: true });

  const browser = await sharedBrowser();
  const context = await browser.newContext({
    baseURL: target.baseUrl,
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: i.caseDir, size: { width: 1280, height: 800 } },
  });
  await installNetworkPolicy(context, target, (b) => {
    blocks.push(b);
    emit(job, 'blocked', caseId, `ABORTED ${b.method} ${b.url} (${b.reason}${b.rule ? ` ${b.rule}` : ''})`, b);
  });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  trackRequests(page);
  job.live.set(caseId, { page, context });
  const onAbort = () => { context.close().catch(() => {}); };
  i.signal.addEventListener('abort', onAbort);

  page.on('response', (r) => {
    const req = r.request();
    const rt = req.resourceType();
    if (rt !== 'xhr' && rt !== 'fetch' && rt !== 'document') return;
    const u = new URL(req.url());
    network.push({ method: req.method(), url: req.url(), status: r.status() });
    emit(job, 'request', caseId, `${req.method()} ${u.pathname}${u.search} -> ${r.status()}`, { method: req.method(), url: req.url(), status: r.status(), resource_type: rt });
  });
  page.on('requestfailed', (req) => {
    const err = req.failure()?.errorText ?? 'failed';
    if (err.includes('BLOCKED_BY_CLIENT')) return; // already reported as a policy block
    const rt = req.resourceType();
    if (rt !== 'xhr' && rt !== 'fetch' && rt !== 'document') return;
    network.push({ method: req.method(), url: req.url(), status: err });
    emit(job, 'request', caseId, `${req.method()} ${new URL(req.url()).pathname} -> ${err}`, { method: req.method(), url: req.url(), status: 0, error: err });
  });

  const stopFrames = await startFrames(job, caseId, page);
  let outcome: UiOutcome = { status: 'passed', files };
  const shot = async (name: string) => {
    const f = join(i.caseDir, `${caseId}.${name}.png`);
    if (await page.screenshot({ path: f, timeout: 3000 }).then(() => true, () => false)) files.push(f);
  };

  try {
    // Every case starts at the target's base URL unless its first step is a goto.
    const all: UiStep[] = steps[0]?.action === 'goto' ? steps : [{ action: 'goto', url: '/', group: 'Open the app' }, ...steps];
    const offset = all.length - steps.length;
    const before: NonNullable<PassBaseline['before']> = {};
    const netCount: Record<string, number> = {};
    const stateNow = async () => ({ aria_snapshot: scrub(await page.locator('body').ariaSnapshot({ timeout: 2000 }).catch(() => ''), job.creds).slice(0, 8000), text_lines: await visibleLines(page, job.creds) });
    for (const [n, step] of all.entries()) {
      const idx = n - offset; // index into the case's own steps (-1 = implicit open)
      if (i.signal.aborted) { outcome = { status: 'cancelled', files }; break; }
      await gate(job, caseId, i.signal); // pause/step: honoured between steps
      if (i.signal.aborted) { outcome = { status: 'cancelled', files }; break; }
      before[String(idx)] = await stateNow();
      const desc = describeStep(step);
      job.progress.current_step = desc;
      emit(job, 'step_start', caseId, desc, { index: idx, step });
      const t0 = Date.now();
      let r: ExecResult;
      try {
        if (step.action === 'expectUrl') { await settlePage(page, { timeoutMs: 3000 }); await expectUrl(page, step.url); r = { outcome: 'ok' }; }
        else r = await execute(page, step, target, job.creds, blocks, downloads);
      } catch (e) {
        r = { outcome: 'error', message: (e as Error).message };
      }
      const ms = Date.now() - t0;
      const msg = r.message ? scrub(r.message.replace(/\u001b\[[0-9;]*m/g, '').split('\n').slice(0, 3).join(' ').slice(0, 600), job.creds) : undefined;
      if (i.signal.aborted) { outcome = { status: 'cancelled', files }; break; }
      if (r.outcome === 'ok') { netCount[String(idx)] = network.length; emit(job, 'step_pass', caseId, desc, { index: idx, ms }); continue; }
      if (r.outcome === 'blocked') {
        const last = blocks.at(-1);
        emit(job, 'blocked', caseId, msg ?? `BLOCKED: ${desc}`, { index: idx, step, ...(last?.kind === 'action' ? { rule: last.rule, role: last.role, name: last.name } : {}) });
        await shot('blocked');
        outcome = { status: 'blocked', failed_step: { index: idx, step, description: desc }, error: msg, files };
        break;
      }
      emit(job, 'step_fail', caseId, `${desc}: ${msg}`, { index: idx, ms, error: msg });
      await shot('failure');
      // Evidence for triage: what the page actually offers, near the target.
      const snap = scrub(await page.locator('body').ariaSnapshot({ timeout: 3000 }).catch(() => ''), job.creds);
      const ref = refOf(step);
      const pairs = ariaPairs(snap);
      const candidates = ref?.name !== undefined
        ? pairs.filter((p) => !ref.role || p.role === ref.role).map((p) => ({ ...p, similarity: similarity(p.name, ref.name!) })).sort((a, b) => b.similarity - a.similarity).slice(0, 8)
        : ref?.label || ref?.text
          ? pairs.map((p) => ({ ...p, similarity: similarity(p.name, (ref.label ?? ref.text)!) })).sort((a, b) => b.similarity - a.similarity).slice(0, 8)
          : [];
      job.failures[caseId] = {
        case_id: caseId, kind: 'ui', steps, failed_index: idx, failed_step: step, error: msg ?? 'step failed',
        url: page.url(), title: await page.title().catch(() => ''), candidates, aria_snapshot: snap.slice(0, 6000), network: network.slice(-25),
        job_id: job.job_id, full_network: network.slice(0, 200), text_lines: await visibleLines(page, job.creds), screenshot: `${caseId}.failure.png`,
      };
      outcome = { status: 'failed', failed_step: { index: idx, step, description: desc }, error: msg, files };
      break;
    }
    if (outcome.status === 'passed') {
      await shot('final');
      // What a pass looks like, so a later failure of this case can be diffed against it.
      saveBaseline({
        case_id: caseId, job_id: job.job_id, at: new Date().toISOString(), url: page.url(),
        aria_snapshot: scrub(await page.locator('body').ariaSnapshot({ timeout: 3000 }).catch(() => ''), job.creds).slice(0, 12000),
        text_lines: await visibleLines(page, job.creds), network: network.slice(0, 200), screenshot: `${caseId}.final.png`, before, net_count: netCount, steps,
      });
    }
  } catch (e) {
    outcome = { status: i.signal.aborted ? 'cancelled' : 'error', error: scrub((e as Error).message.slice(0, 600), job.creds), files };
  } finally {
    i.signal.removeEventListener('abort', onAbort);
    await stopFrames();
    const video = page.video();
    const tracePath = join(i.caseDir, `${caseId}.trace.zip`);
    if (await context.tracing.stop({ path: tracePath }).then(() => true, () => false)) files.push(tracePath);
    await context.close().catch(() => {});
    const vp = await video?.path().catch(() => null);
    if (vp && existsSync(vp)) {
      const dest = join(i.caseDir, `${caseId}.video.webm`);
      renameSync(vp, dest);
      files.push(dest);
    }
    job.live.delete(caseId);
  }
  return outcome;
}
