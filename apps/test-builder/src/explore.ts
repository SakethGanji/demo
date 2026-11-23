// The explore loop: snapshot -> planner -> policy check -> execute -> record. Emits a spec + session log.
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { chromium, expect, type Page } from '@playwright/test';
import { toLocator, describeAction, type Action, type ElementRef } from './actions.ts';
import { credentialValues, type Target } from './config.ts';
import { installNetworkPolicy, checkElement, checkRequest, type PolicyBlock, type NetworkBlock } from './policy.ts';
import { scrub, scrubDeep } from './secrets.ts';
import { generateSpec } from './codegen.ts';
import { trackRequests, settlePage } from './settle.ts';
import { totp } from './totp.ts';
import { extractText } from './downloads.ts';
import { resolve as resolvePath, isAbsolute } from 'node:path';
import type { Planner, HistoryEntry } from './planners/types.ts';

export interface ExploreOptions {
  target: Target;
  goal: string;
  planner: Planner;
  slug: string;
  outDir: string;      // where the spec goes (tests/generated)
  runsDir: string;     // where session logs go (runs/)
  rootDir: string;     // package root, for relative paths in output
  headed?: boolean;
  maxConsecutiveErrors?: number;
}

export interface SessionStep {
  step: number;
  at: string;
  planner: string;
  action: Action;
  description: string;
  outcome: 'ok' | 'error' | 'blocked';
  message?: string;
  url: string;
  ms: number;
}

export interface ExploreResult {
  status: 'done' | 'done-failed' | 'budget-steps' | 'budget-time' | 'too-many-errors' | 'planner-error';
  specPath: string;
  sessionPath: string;
  steps: SessionStep[];
  blocks: PolicyBlock[];
}

const STEP_TIMEOUT = 5000;

export const ts = () => new Date().toISOString().replace(/[:.]/g, '-');

export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'goal';
}

function resolveValue(v: string, creds: Record<string, string>): string {
  return v
    .replace(/\{\{totp:(\w+)\}\}/g, (all, k: string) => (creds[k] ? totp(creds[k]!) : all))
    .replace(/\{\{(\w+)\}\}/g, (all, k: string) => creds[k] ?? all);
}

async function settle(page: Page): Promise<void> {
  await settlePage(page);
}

async function resolveOne(page: Page, ref: ElementRef) {
  const loc = toLocator(page, ref);
  await loc.first().waitFor({ state: 'attached', timeout: STEP_TIMEOUT });
  const n = await loc.count();
  if (n > 1) throw new Error(`ambiguous: ${n} elements match; add "within" or a more specific name`);
  return loc;
}

export interface ExecResult { outcome: 'ok' | 'error' | 'blocked'; message?: string }

const FOUND_TEXT_MAX = 120;
const firstLine = (e: unknown) => String((e as Error)?.message ?? e).replace(/\u001b\[[0-9;]*m/g, '').split('\n')[0]!.trim();

/** What a failed expectText actually saw: `found: '<text>'` (whitespace collapsed, capped), or how many matched. */
export async function foundText(loc: ReturnType<typeof toLocator>): Promise<string> {
  const n = await loc.count().catch(() => -1);
  if (n === 0) return 'found no matching element';
  if (n < 0) return 'could not read the element';
  const raw = await loc.first().innerText({ timeout: 1000 }).catch(() => null);
  if (raw === null) return `found ${n} matching element(s), text unreadable`;
  const t = raw.replace(/\s+/g, ' ').trim();
  const shown = t.length > FOUND_TEXT_MAX ? `${t.slice(0, FOUND_TEXT_MAX - 1)}…` : t;
  return `${n > 1 ? `found ${n} matching elements; the first has` : 'found'}: '${shown}'`;
}

export async function execute(
  page: Page, a: Action, t: Target, creds: Record<string, string>, blocks: PolicyBlock[], downloadsDir: string,
): Promise<ExecResult> {
  const netBefore = blocks.filter((b) => b.kind === 'network').length;
  const guardClick = async (ref: ElementRef, verb: string) => {
    const loc = await resolveOne(page, ref);
    const c = await checkElement(t, loc);
    if (c.rule) {
      blocks.push({ kind: 'action', at: new Date().toISOString(), role: c.role, name: c.name, rule: c.rule, action: verb });
      return { loc, blocked: `BLOCKED by policy: ${verb} on ${c.role} "${c.name}" matches forbiddenActions ${c.rule}. Do not retry.` };
    }
    return { loc, blocked: null as string | null };
  };

  switch (a.action) {
    case 'goto': {
      const url = new URL(a.url, t.baseUrl + '/').toString();
      const v = checkRequest(t, 'GET', url);
      if (v) {
        blocks.push({ kind: 'network', at: new Date().toISOString(), method: 'GET', url, ...v });
        return { outcome: 'blocked', message: `BLOCKED by policy: navigation to ${url} (${v.reason})` };
      }
      await page.goto(url, { timeout: STEP_TIMEOUT * 2 });
      break;
    }
    case 'click': {
      const g = await guardClick(a.target, 'click');
      if (g.blocked) return { outcome: 'blocked', message: g.blocked };
      await g.loc.click({ timeout: STEP_TIMEOUT });
      break;
    }
    case 'press': {
      if (a.target) {
        // Enter/Space on a control activates it, so it gets the same guard as a click.
        const g = /^(Enter|Space| )$/.test(a.key) ? await guardClick(a.target, `press ${a.key}`) : { loc: await resolveOne(page, a.target), blocked: null };
        if (g.blocked) return { outcome: 'blocked', message: g.blocked };
        await g.loc.press(a.key, { timeout: STEP_TIMEOUT });
      } else {
        await page.keyboard.press(a.key);
      }
      break;
    }
    case 'fill': {
      const loc = await resolveOne(page, a.target);
      await loc.fill(resolveValue(a.value, creds), { timeout: STEP_TIMEOUT });
      break;
    }
    case 'select': {
      const loc = await resolveOne(page, a.target);
      await loc.selectOption({ label: a.option }, { timeout: STEP_TIMEOUT });
      break;
    }
    case 'hover': {
      const loc = await resolveOne(page, a.target);
      await loc.hover({ timeout: STEP_TIMEOUT });
      break;
    }
    case 'upload': {
      const loc = await resolveOne(page, a.target);
      const file = isAbsolute(a.file) ? a.file : resolvePath(dirname(t.path), a.file);
      await loc.setInputFiles(file, { timeout: STEP_TIMEOUT });
      break;
    }
    case 'otp': {
      // One-time-code UIs are N single-character boxes. Fill the resolved code one box per character.
      const scope = a.within ? toLocator(page, a.within) : page.getByRole('textbox');
      const boxes = a.within ? scope.getByRole('textbox') : page.getByRole('textbox');
      const code = resolveValue(a.value, creds);
      const n = await boxes.count();
      if (n >= code.length) {
        for (let i = 0; i < code.length; i++) await boxes.nth(i).fill(code[i]!, { timeout: STEP_TIMEOUT });
      } else if (n === 1) {
        await boxes.first().fill(code, { timeout: STEP_TIMEOUT });
      } else {
        throw new Error(`otp: found ${n} code field(s) but the code has ${code.length} digits`);
      }
      break;
    }
    case 'waitFor': {
      const loc = await resolveOne(page, a.target);
      await loc.waitFor({ state: a.state ?? 'visible', timeout: STEP_TIMEOUT });
      break;
    }
    case 'download': {
      const loc = await resolveOne(page, a.target);
      const dl = await Promise.all([
        page.waitForEvent('download', { timeout: STEP_TIMEOUT * 2 }),
        loc.click({ timeout: STEP_TIMEOUT }),
      ]).then(([d]) => d);
      const dest = join(downloadsDir, dl.suggestedFilename());
      await dl.saveAs(dest);
      if (a.contains?.length) {
        const text = extractText(dest);
        const missing = a.contains.filter((s) => !text.includes(s));
        if (missing.length) return { outcome: 'error', message: `downloaded ${dl.suggestedFilename()} is missing expected content: ${JSON.stringify(missing)}` };
      }
      break;
    }
    case 'expectVisible':
      await expect(toLocator(page, a.target)).toBeVisible({ timeout: STEP_TIMEOUT });
      break;
    case 'expectText': {
      const loc = toLocator(page, a.target);
      try {
        await expect(loc).toContainText(a.text, { timeout: STEP_TIMEOUT });
      } catch (e) {
        throw new Error(`${await foundText(loc)}; ${firstLine(e)}`);
      }
      break;
    }
    case 'expectCount': {
      const loc = toLocator(page, a.target);
      try {
        await expect(loc).toHaveCount(a.count, { timeout: STEP_TIMEOUT });
      } catch (e) {
        const n = await loc.count().catch(() => -1);
        throw new Error(`${n >= 0 ? `found ${n}` : 'could not count the matches'} (expected ${a.count}); ${firstLine(e)}`);
      }
      break;
    }
    case 'done':
      return { outcome: 'ok' };
  }
  await settle(page);
  const newNet = blocks.filter((b): b is NetworkBlock => b.kind === 'network').slice(netBefore);
  if (newNet.length) {
    return {
      outcome: 'blocked',
      message: `BLOCKED by policy: this step triggered ${newNet.length} aborted request(s): ` +
        newNet.map((b) => `${b.method} ${b.url} (${b.reason}${b.rule ? ` ${b.rule}` : ''})`).join('; ') +
        '. The step is excluded from the generated test.',
    };
  }
  return { outcome: 'ok' };
}

/**
 * Drive a login script once and save the resulting Playwright storageState, so UI specs reuse a
 * pre-authenticated session instead of logging in every time. Login itself (SSO/MFA) is a setup
 * concern, executed here and never repeated per-test. Uses the credential placeholders + {{totp:}}.
 */
export async function loginAndSaveState(t: Target, steps: Action[], outPath: string, headed = false): Promise<void> {
  const creds = credentialValues(t, true);
  const browser = await chromium.launch({ headless: !headed });
  const context = await browser.newContext({ baseURL: t.baseUrl });
  const page = await context.newPage();
  trackRequests(page);
  const downloadsDir = join(dirname(outPath), 'login-downloads');
  mkdirSync(downloadsDir, { recursive: true });
  try {
    const open: Action = { action: 'goto', url: '/' };
    await execute(page, open, t, creds, [], downloadsDir);
    await settlePage(page);
    for (const step of steps) {
      const r = await execute(page, step, t, creds, [], downloadsDir);
      if (r.outcome === 'error') throw new Error(`login step failed (${describeAction(step)}): ${r.message}`);
      await settlePage(page);
    }
    mkdirSync(dirname(outPath), { recursive: true });
    await context.storageState({ path: outPath });
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

export async function explore(o: ExploreOptions): Promise<ExploreResult> {
  const t = o.target;
  const creds = credentialValues(t, true);
  const credKeys = Object.keys(creds);
  const started = Date.now();
  const deadline = started + t.budget.maxSeconds * 1000;
  const sessionDir = join(o.runsDir, `explore-${ts()}-${o.slug}`);
  mkdirSync(join(sessionDir, 'snapshots'), { recursive: true });
  const downloadsDir = join(sessionDir, 'downloads');
  mkdirSync(downloadsDir, { recursive: true });

  const blocks: PolicyBlock[] = [];
  const steps: SessionStep[] = [];
  const history: HistoryEntry[] = [];
  const recorded: Action[] = [];
  let status: ExploreResult['status'] = 'budget-steps';
  let lastError: string | undefined;
  let consecutiveErrors = 0;
  const maxErr = o.maxConsecutiveErrors ?? 6;

  const browser = await chromium.launch({ headless: !o.headed });
  const statePath = t.auth?.storageState ? resolvePath(dirname(t.path), t.auth.storageState) : undefined;
  const context = await browser.newContext({
    baseURL: t.baseUrl,
    ...(statePath && existsSync(statePath) ? { storageState: statePath } : {}),
  });
  await installNetworkPolicy(context, t, (b) => {
    blocks.push(b);
    process.stderr.write(`  [policy] ABORTED ${b.method} ${b.url} (${b.reason}${b.rule ? ` ${b.rule}` : ''})\n`);
  });
  const page = await context.newPage();
  trackRequests(page);

  const record = (a: Action, r: ExecResult, ms: number, plannerName: string) => {
    const stepNo = steps.length + 1;
    const safeAction = scrubDeep(a, creds); // values were placeholders already; belt and braces
    const msg = r.message ? scrub(r.message.split('\n')[0]!.slice(0, 500), creds) : undefined;
    steps.push({ step: stepNo, at: new Date().toISOString(), planner: plannerName, action: safeAction, description: describeAction(a), outcome: r.outcome, message: msg, url: page.url(), ms });
    history.push({ step: stepNo, action: safeAction, outcome: r.outcome, message: msg, url: page.url() });
    const tag = r.outcome === 'ok' ? 'ok     ' : r.outcome === 'blocked' ? 'BLOCKED' : 'error  ';
    process.stderr.write(`  ${String(stepNo).padStart(2)}. [${tag}] ${describeAction(a)}${msg ? `  -- ${msg}` : ''}\n`);
    if (r.outcome === 'ok' && a.action !== 'done') recorded.push(a);
    lastError = r.outcome === 'ok' ? undefined : msg;
    consecutiveErrors = r.outcome === 'error' ? consecutiveErrors + 1 : 0;
  };

  try {
    // Step 0 is fixed, not planned: open the app at its base URL.
    const open: Action = { action: 'goto', url: '/', group: 'Open the app' };
    const t0 = Date.now();
    record(open, await execute(page, open, t, creds, blocks, downloadsDir).catch((e: Error) => ({ outcome: 'error' as const, message: e.message })), Date.now() - t0, 'builder');

    while (true) {
      if (steps.length >= t.budget.maxSteps) { status = 'budget-steps'; break; }
      if (Date.now() > deadline) { status = 'budget-time'; break; }
      if (consecutiveErrors >= maxErr) { status = 'too-many-errors'; break; }

      await settlePage(page); // a step can trigger work that outlives its own settle (timers, redirects)
      const rawSnap = await page.locator('body').ariaSnapshot({ timeout: STEP_TIMEOUT }).catch((e: Error) => `(snapshot failed: ${e.message.split('\n')[0]})`);
      const snapshot = scrub(rawSnap, creds);
      writeFileSync(join(sessionDir, 'snapshots', `step-${String(steps.length + 1).padStart(3, '0')}.yaml`), snapshot);

      let action: Action;
      try {
        action = await o.planner.next({ goal: o.goal, url: page.url(), snapshot, history: [...history], credentialKeys: credKeys, lastError });
      } catch (e) {
        status = 'planner-error';
        steps.push({ step: steps.length + 1, at: new Date().toISOString(), planner: o.planner.name, action: { action: 'done', success: false }, description: 'planner error', outcome: 'error', message: scrub((e as Error).message, creds).slice(0, 800), url: page.url(), ms: 0 });
        process.stderr.write(`  planner error: ${scrub((e as Error).message, creds).slice(0, 400)}\n`);
        break;
      }
      const t1 = Date.now();
      if (action.action === 'done') {
        record(action, { outcome: 'ok' }, 0, o.planner.name);
        status = action.success === false ? 'done-failed' : 'done';
        break;
      }
      const remaining = deadline - Date.now();
      const r = await Promise.race([
        execute(page, action, t, creds, blocks, downloadsDir).catch((e: Error) => ({ outcome: 'error' as const, message: e.message })),
        new Promise<ExecResult>((res) => setTimeout(() => res({ outcome: 'error', message: 'time budget exhausted mid-step' }), Math.max(remaining, 0))),
      ]);
      record(action, r, Date.now() - t1, o.planner.name);
    }
    await page.screenshot({ path: join(sessionDir, 'final.png'), fullPage: true }).catch(() => {});
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }

  mkdirSync(o.outDir, { recursive: true });
  // Only a successful exploration yields a runnable spec; anything else is a draft for review.
  const specPath = join(o.outDir, status === 'done' ? `${o.slug}.spec.ts` : `${o.slug}.draft.ts`);
  const spec = generateSpec({
    goal: o.goal,
    planner: o.planner.name,
    targetName: t.name,
    targetPath: relative(o.rootDir, t.path),
    steps: recorded,
    blocks,
    credentialKeys: credKeys,
    fixturesImport: relative(dirname(specPath), join(o.rootDir, 'src', 'fixtures.ts')).replace(/^(?!\.)/, './'),
    generatedAt: new Date().toISOString(),
  });
  writeFileSync(specPath, spec);

  const sessionPath = join(sessionDir, 'session.json');
  const session = {
    goal: o.goal,
    planner: o.planner.name,
    target: { name: t.name, baseUrl: t.baseUrl, config: relative(o.rootDir, t.path) },
    policy: {
      allowedHosts: t.allowedHosts,
      forbiddenActions: t.forbiddenActions.map(String),
      forbiddenRequests: t.forbiddenRequests.map((r) => r.raw),
      budget: t.budget,
    },
    status,
    startedAt: new Date(started).toISOString(),
    endedAt: new Date().toISOString(),
    durationMs: Date.now() - started,
    counts: {
      steps: steps.length,
      ok: steps.filter((s) => s.outcome === 'ok').length,
      errors: steps.filter((s) => s.outcome === 'error').length,
      blockedSteps: steps.filter((s) => s.outcome === 'blocked').length,
      actionBlocks: blocks.filter((b) => b.kind === 'action').length,
      networkBlocks: blocks.filter((b) => b.kind === 'network').length,
      recordedActions: recorded.length,
    },
    steps,
    blocks,
    spec: relative(o.rootDir, specPath),
  };
  writeFileSync(sessionPath, JSON.stringify(scrubDeep(session, creds), null, 2));
  return { status, specPath, sessionPath, steps, blocks };
}
