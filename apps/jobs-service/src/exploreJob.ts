// explore jobs: wrap test-builder's explore loop + LLM planner to propose UI steps for a goal.
// Without an LLM, a deterministic fallback opens the app and proposes steps from its accessibility snapshot.
import { join } from 'node:path';
import { readFileSync, existsSync } from 'node:fs';
import { explore, execute } from '../../test-builder/src/explore.ts';
import { LlmPlanner } from '../../test-builder/src/planners/llm.ts';
import { scrub, scrubDeep } from '../../test-builder/src/secrets.ts';
import { describeAction, type Action } from '../../test-builder/src/actions.ts';
import type { Target } from '../../test-builder/src/config.ts';
import { llmConfig } from './llm.ts';
import { ariaPairs, sharedBrowser } from './uiCase.ts';
import { emit, jobDir, type Job } from './store.ts';
import { installNetworkPolicy, checkAction, type PolicyBlock } from '../../test-builder/src/policy.ts';
import { settlePage, trackRequests } from '../../test-builder/src/settle.ts';

export interface ExploreOutput { steps: unknown[]; notes: string; source: string; status?: string; blocked?: unknown[] }

export async function runExplore(job: Job, target: Target, goal: string, signal: AbortSignal): Promise<ExploreOutput> {
  const cfg = llmConfig();
  if (!cfg) return heuristicExplore(job, target, goal, signal);
  // test-builder resolves credentials from env by name; give this job private env names and remove them afterwards.
  const envNames: string[] = [];
  const credentials: Target['credentials'] = {};
  for (const [k, v] of Object.entries(job.creds)) {
    const env = `JOBS_CRED_${job.job_id}_${k}`.replace(/\W/g, '_').toUpperCase();
    process.env[env] = v;
    envNames.push(env);
    credentials[k] = { env, secret: true };
  }
  const dir = join(jobDir(job.job_id), 'explore');
  emit(job, 'log', null, `exploring with ${cfg.provider}:${cfg.model}: ${goal}`);
  try {
    const planner = new LlmPlanner(cfg);
    let r: Awaited<ReturnType<typeof explore>>;
    try {
      r = await Promise.race([
      explore({ target: { ...target, credentials, budget: { maxSteps: 25, maxSeconds: 180 } }, goal, planner, slug: 'proposal', outDir: dir, runsDir: dir, rootDir: dir }),
      new Promise<never>((_, rej) => signal.addEventListener('abort', () => rej(new Error('cancelled')))),
      ]);
    } catch (e) {
      if (signal.aborted) throw e;
      emit(job, 'log', null, `LLM explore failed (${scrub((e as Error).message, job.creds).slice(0, 160)}); falling back to the deterministic walk`);
      return heuristicExplore(job, target, goal, signal);
    }
    if (r.status === 'planner-error' || !r.steps.some((s) => s.outcome === 'ok' && s.action.action !== 'done')) {
      emit(job, 'log', null, `LLM explore ended ${r.status} (planner unavailable or no usable step); falling back to the deterministic walk`);
      return heuristicExplore(job, target, goal, signal);
    }
    for (const s of r.steps) {
      emit(job, s.outcome === 'ok' ? 'step_pass' : s.outcome === 'blocked' ? 'blocked' : 'step_fail', null, `${s.description}${s.message ? ` -- ${s.message}` : ''}`, { planner: s.planner, outcome: s.outcome });
    }
    const steps = r.steps.filter((s) => s.outcome === 'ok' && s.action.action !== 'done').map((s) => s.action);
    const spec = existsSync(r.specPath) ? readFileSync(r.specPath, 'utf8') : '';
    return scrubDeep({
      steps, source: planner.name, status: r.status,
      notes: `explore ${r.status}: ${r.steps.length} step(s) tried, ${steps.length} kept, ${r.blocks.length} policy block(s). Only successful steps are proposed; review before saving as a case.`,
      blocked: r.blocks, spec_preview: spec.slice(0, 4000),
    }, job.creds);
  } finally {
    for (const e of envNames) delete process.env[e];
  }
}

const STOP = new Set(['the', 'and', 'for', 'with', 'that', 'this', 'from', 'into', 'then', 'verify', 'check', 'open', 'page', 'user', 'log', 'sign', 'can', 'see', 'should', 'cases', 'case', 'generate', 'test', 'tests']);
const stem = (w: string) => w.replace(/(ing|es|s)$/, '');
const tokens = (t: string) => [...new Set(t.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && !STOP.has(w)).map(stem))];
const NAV_ROLES = new Set(['link', 'tab', 'button', 'menuitem']);

/**
 * No LLM: a deterministic walk. Open the app, assert its heading, sign in when a login form is present (and credentials
 * were given), then follow up to 3 links/tabs/buttons whose accessible names share words with the goal, asserting the
 * heading each lands on. Every proposed step was executed against the real page (with the policy), so it is known to work.
 * Without a goal match it proposes smoke assertions on the page's headings, tables and navigation instead.
 */
async function heuristicExplore(job: Job, target: Target, goal: string, signal: AbortSignal): Promise<ExploreOutput> {
  const browser = await sharedBrowser();
  const context = await browser.newContext({ baseURL: target.baseUrl });
  const blocks: PolicyBlock[] = [];
  await installNetworkPolicy(context, target, (b) => blocks.push(b));
  const page = await context.newPage();
  trackRequests(page);
  const steps: Action[] = [];
  const skipped: string[] = [];
  const snapshot = async () => ariaPairs(scrub(await page.locator('body').ariaSnapshot({ timeout: 5000 }).catch(() => ''), job.creds));
  const run = async (a: Action): Promise<boolean> => {
    if (signal.aborted) return false;
    const r = await execute(page, a, target, job.creds, blocks, join(jobDir(job.job_id), 'downloads')).catch((e: Error) => ({ outcome: 'error' as const, message: e.message }));
    if (r.outcome === 'ok') { if (!steps.some((x) => JSON.stringify(x) === JSON.stringify(a))) steps.push(a); emit(job, 'step_pass', null, describeAction(a)); await settlePage(page).catch(() => {}); return true; }
    emit(job, r.outcome === 'blocked' ? 'blocked' : 'step_fail', null, `${describeAction(a)}: ${scrub(r.message ?? r.outcome, job.creds).slice(0, 200)}`);
    return false;
  };
  try {
    const gt = tokens(goal);
    await run({ action: 'goto', url: '/', group: 'Open the app' });
    let pairs = await snapshot();
    const heading = pairs.find((p) => p.role === 'heading');
    if (heading) await run({ action: 'expectVisible', target: { role: 'heading', name: heading.name }, group: 'Open the app' });

    const keys = Object.keys(job.creds);
    const user = pairs.find((p) => p.role === 'textbox' && /user|email|login/i.test(p.name));
    const pass = pairs.find((p) => p.role === 'textbox' && /pass/i.test(p.name));
    const submit = pairs.find((p) => p.role === 'button' && /sign in|log ?in|continue/i.test(p.name));
    let signedIn = false;
    if (user && pass && submit) {
      const uk = keys.find((k) => /user/i.test(k)), pk = keys.find((k) => /pass/i.test(k));
      const form: Action[] = [
        { action: 'fill', target: { label: user.name }, value: `{{${uk ?? 'username'}}}`, group: 'Sign in' },
        { action: 'fill', target: { label: pass.name }, value: `{{${pk ?? 'password'}}}`, group: 'Sign in' },
        { action: 'click', target: { role: 'button', name: submit.name }, group: 'Sign in' },
      ];
      if (uk && pk) {
        signedIn = true;
        for (const a of form) if (!(await run(a))) { signedIn = false; break; }
        if (signedIn) {
          pairs = await snapshot();
          // MFA step: single-digit code boxes and a TOTP secret in the credentials.
          const tk = keys.find((k) => /totp|otp/i.test(k));
          const verify = pairs.find((p) => p.role === 'button' && /verify|confirm|submit|continue/i.test(p.name));
          if (tk && verify && pairs.some((p) => p.role === 'textbox' && /^digit 1\b|code/i.test(p.name))) {
            await run({ action: 'otp', value: `{{totp:${tk}}}`, group: 'MFA' } as Action);
            await run({ action: 'click', target: { role: 'button', name: verify.name }, group: 'MFA' });
            pairs = await snapshot();
          } const h = pairs.find((p) => p.role === 'heading'); if (h) await run({ action: 'expectVisible', target: { role: 'heading', name: h.name }, group: 'Signed in' });
        }
      } else steps.push(...form); // no credentials given: propose the form, with placeholders, unexecuted
    }

    // Follow what the goal asks for.
    const seen = new Set<string>();
    for (let round = 0; round < 3 && !signal.aborted; round++) {
      const cands = pairs
        .map((p, n) => ({ p, n, score: NAV_ROLES.has(p.role) && p !== submit ? tokens(p.name).filter((t) => gt.includes(t)).length : 0 }))
        .filter((c) => c.score > 0 && !seen.has(`${c.p.role}|${c.p.name}`))
        .sort((a, b) => b.score - a.score || a.n - b.n);
      const pick = cands.find((c) => { const why = checkAction(target, c.p.role, c.p.name); if (why) { skipped.push(`${c.p.role} "${c.p.name}" (forbidden: ${why})`); seen.add(`${c.p.role}|${c.p.name}`); } return !why; });
      if (!pick) break;
      seen.add(`${pick.p.role}|${pick.p.name}`);
      const group = `Go to ${pick.p.name}`;
      if (!(await run({ action: 'click', target: { role: pick.p.role as never, name: pick.p.name }, group }))) continue;
      pairs = await snapshot();
      const h = pairs.find((p) => p.role === 'heading');
      if (h) await run({ action: 'expectVisible', target: { role: 'heading', name: h.name }, group });
      const t = pairs.find((p) => p.role === 'table' || p.role === 'tab' || p.role === 'tabpanel');
      if (t && t.name) await run({ action: 'expectVisible', target: { role: t.role as never, name: t.name }, group });
    }
    if (steps.every((s) => s.action !== 'click' || ['Sign in', 'MFA'].includes((s as { group?: string }).group ?? ''))) {
      // The goal named nothing on the page: smoke assertions on what is there.
      for (const p of pairs.filter((q) => ['heading', 'table', 'navigation', 'tab'].includes(q.role) && q.name).slice(0, 4)) {
        await run({ action: 'expectVisible', target: { role: p.role as never, name: p.name }, group: 'Page contents' });
      }
    }
    emit(job, 'log', null, `heuristic explore: ${steps.length} step(s) kept, ${pairs.length} accessible elements on ${page.url()}`);
    return {
      steps, source: 'heuristic',
      notes: `LLM unavailable: deterministic walk for "${goal}" (goal words: ${gt.join(', ') || 'none'}). ${signedIn ? 'Signed in with the given credentials. ' : user && pass ? 'A login form is present; credentials were not given, so the sign-in steps are proposed but unexecuted. ' : ''}${steps.length} step(s) kept, all executed against the live page.${skipped.length ? ` Skipped by policy: ${skipped.join(', ')}.` : ''} Controls on the final page: ${pairs.filter((p) => ['button', 'link', 'textbox', 'combobox', 'tab'].includes(p.role)).slice(0, 15).map((p) => `${p.role} "${p.name}"`).join(', ')}`,
      blocked: blocks,
    };
  } finally {
    await context.close().catch(() => {});
  }
}
