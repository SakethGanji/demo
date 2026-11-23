// Proves a (masked) trace.zip still opens in the real Playwright trace viewer.
// Spawns `playwright show-trace --host 127.0.0.1 --port 0 <zip>`, drives the viewer with Chromium and
// checks with DOM assertions (no screenshots): the action tree loads, every top-level step can be
// selected, the "After" DOM snapshot renders, the Network tab lists requests, there are no console
// errors / page errors / failed viewer requests, and no secret value is visible anywhere in the viewer.
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import JSZip from 'jszip';
import { chromium } from '@playwright/test';
import { variants } from './secrets.ts';

export interface TraceCheck {
  trace: string;
  ok: boolean;
  actions: number;
  topLevelSteps: number;
  stepsWithSnapshot: number;
  /** Steps without a recorded snapshot, or whose snapshot is a browser error page. */
  emptyErrorPageSnapshots: number;
  networkEntries: number;
  consoleErrors: string[];
  secretVisible: boolean;
  resourceNameMismatches: string[];
  problems: string[];
  ms: number;
}

/** Content-addressed resources must keep name == sha1(content). */
export async function resourceIntegrity(zipPath: string): Promise<string[]> {
  const zip = await JSZip.loadAsync(readFileSync(zipPath));
  const bad: string[] = [];
  for (const e of Object.values(zip.files)) {
    const m = /^resources\/([0-9a-f]{40})(\.\w+)?$/.exec(e.name);
    if (!m || e.dir) continue;
    const sha = createHash('sha1').update(await e.async('nodebuffer')).digest('hex');
    if (sha !== m[1]) bad.push(e.name);
  }
  return bad;
}

export async function checkTrace(zipPath: string, rootDir: string, secrets: string[] = []): Promise<TraceCheck> {
  const started = Date.now();
  const r: TraceCheck = {
    trace: zipPath, ok: false, actions: 0, topLevelSteps: 0, stepsWithSnapshot: 0, emptyErrorPageSnapshots: 0, networkEntries: 0,
    consoleErrors: [], secretVisible: false, resourceNameMismatches: [], problems: [], ms: 0,
  };
  // API-only tests have no DOM snapshots; only page-driving traces must render "After" snapshots.
  let hasPageSnapshots = false;
  try {
    r.resourceNameMismatches = await resourceIntegrity(zipPath);
    const zipObj = await JSZip.loadAsync(readFileSync(zipPath));
    hasPageSnapshots = Object.keys(zipObj.files).some((n) => /\.html$/.test(n)) ||
      /"frame-snapshot"/.test(await zipObj.file('0-trace.trace')?.async('string') ?? '');
  } catch (e) {
    r.problems.push(`not a readable zip: ${(e as Error).message}`);
  }
  const cli = join(rootDir, 'node_modules', '@playwright', 'test', 'cli.js');
  // CLAUDECODE=1 only stops show-trace from also launching the desktop browser (it prints the URL instead).
  const child = spawn(process.execPath, [cli, 'show-trace', '--host', '127.0.0.1', '--port', '0', zipPath], {
    env: { ...process.env, CLAUDECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const browser = await chromium.launch();
  try {
    const url = await new Promise<string>((res, rej) => {
      let buf = '';
      const to = setTimeout(() => rej(new Error('show-trace did not start within 20s')), 20_000);
      child.stdout.on('data', (d: Buffer) => {
        buf += d.toString();
        const m = /Listening on (\S+)/.exec(buf);
        if (m) { clearTimeout(to); res(m[1]!); }
      });
      child.on('exit', (c) => rej(new Error(`show-trace exited early (${c}): ${buf.slice(0, 300)}`)));
    });
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    page.on('console', (m) => { if (m.type() === 'error') r.consoleErrors.push(m.text().slice(0, 300)); });
    page.on('pageerror', (e) => r.consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));
    page.on('response', (res) => { if (res.status() >= 400) r.consoleErrors.push(`HTTP ${res.status()} ${res.url().slice(0, 200)}`); });
    await page.goto(url);
    const tree = page.getByRole('tabpanel', { name: 'Actions' }).getByRole('tree');
    await tree.getByRole('treeitem').first().waitFor({ timeout: 15_000 });
    if (await page.getByText(/Could not load trace/i).count()) r.problems.push('viewer says "Could not load trace"');

    // Top-level items: direct children of the tree (Before Hooks, each test.step, After Hooks).
    const top = tree.locator(':scope > [role=treeitem]');
    r.topLevelSteps = await top.count();
    for (let i = 0; i < r.topLevelSteps; i++) {
      const item = top.nth(i);
      const title = (await item.innerText()).split('\n')[0] ?? '';
      if (/^(Before|After) Hooks/.test(title) || !hasPageSnapshots) continue;
      await item.click();
      await page.getByRole('tab', { name: 'After' }).click();
      // The snapshot renders in an iframe served by the viewer's service worker.
      const frame = page.frameLocator('iframe.snapshot-visible, iframe[name=snapshot]').first();
      const bodyText = await frame.locator('body').innerText({ timeout: 5000 }).catch(() => '');
      // An action with no recorded snapshot (e.g. waitForEvent) leaves the visible iframe without a src;
      // an error page (navigation aborted by policy) has a src but an empty body. Neither is a defect.
      // A real app page whose snapshot renders empty is.
      const snapSrc = await page.evaluate(() => (document.querySelector('iframe.snapshot-visible') as HTMLIFrameElement | null)?.getAttribute('src') ?? '').catch(() => '');
      const snapUrl = await page.evaluate(() => document.querySelector('[class*="address-bar"]')?.textContent ?? '').catch(() => '');
      if (bodyText.trim().length) r.stepsWithSnapshot++;
      else if (!snapSrc || /^(chrome-error:|about:)/.test(snapUrl.trim())) r.emptyErrorPageSnapshots++;
      else r.problems.push(`"After" snapshot of step "${title}" renders empty (${snapUrl.trim()})`);
    }
    r.actions = await tree.getByRole('treeitem').count();
    const net = page.getByRole('tab', { name: /^Network/ });
    await net.click();
    const label = (await net.innerText()).trim();
    r.networkEntries = Number(/(\d+)\s*$/.exec(label)?.[1] ?? 0);

    if (secrets.length) {
      const needles = secrets.flatMap(variants);
      const texts = [await page.locator('body').innerText()];
      for (const f of page.frames()) texts.push(await f.locator('body').innerText({ timeout: 2000 }).catch(() => ''));
      for (const f of page.frames()) texts.push(await f.content().catch(() => ''));
      r.secretVisible = texts.some((t) => needles.some((n) => t.includes(n)));
    }
  } catch (e) {
    r.problems.push((e as Error).message.split('\n')[0]!);
  } finally {
    await browser.close().catch(() => {});
    child.kill('SIGTERM');
  }
  if (!r.topLevelSteps) r.problems.push('no actions in the viewer');
  if (r.consoleErrors.length) r.problems.push(`${r.consoleErrors.length} console/page error(s) in the viewer`);
  if (r.secretVisible) r.problems.push('a secret value is visible in the viewer');
  if (r.resourceNameMismatches.length) r.problems.push(`${r.resourceNameMismatches.length} resource(s) whose name != sha1(content)`);
  r.ok = r.problems.length === 0;
  r.ms = Date.now() - started;
  return r;
}
