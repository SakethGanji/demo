// Take over in a real browser: typed text must never be kept in any artifact (trace, video, screenshots, baseline).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import JSZip from '../../test-builder/node_modules/jszip/lib/index.js';
process.env.JOBS_DATA_DIR = mkdtempSync(join(tmpdir(), 'jobs-takeover-'));
const { createJob, artifactsDir, DATA_DIR } = await import('../src/store.ts');
const { buildTarget } = await import('../src/spec.ts');
const { runTestRun } = await import('../src/testRun.ts');
const { applyControl, relayInput } = await import('../src/control.ts');
const { closeBrowser } = await import('../src/uiCase.ts');
const { scanForLeaks } = await import('../../test-builder/src/secrets.ts');

const TYPED = 'hello-typed-secret';
const DESK = `<!doctype html><html><body style="margin:0;font:16px sans-serif">
<input id="n" aria-label="Note" style="position:fixed;top:0;left:0;width:100vw;height:50vh;font-size:40px">
<p role="status" id="s" style="margin-top:60vh">waiting</p><p id="l"></p>
<script>let k=0;const t=setInterval(()=>{k++;const e=document.createElement('span');e.textContent='Loaded'+k;l.append(e,' ');if(k===3)clearInterval(t)},1000);
n.addEventListener('input',()=>{s.textContent=n.value.length?'Ready':'waiting'})</script></body></html>`;
const srv = createServer((_q, r) => { r.writeHead(200, { 'content-type': 'text/html' }).end(DESK); });
await new Promise<void>((ok) => srv.listen(0, '127.0.0.1', ok));
const base = `http://127.0.0.1:${(srv.address() as { port: number }).port}`;
after(async () => { srv.close(); await closeBrowser(); });

const until = async (f: () => boolean, ms = 20000) => {
  const end = Date.now() + ms;
  while (!f()) { if (Date.now() > end) throw new Error('timed out'); await new Promise((r) => setTimeout(r, 50)); }
};

test('a taken-over case keeps no typed text: trace cut at take over, no video or later screenshots, a note says so', async () => {
  const w = (n: number) => ({ action: 'waitFor', target: { text: `Loaded${n}` }, state: 'visible' });
  const cases = [{ id: 'c1', kind: 'ui', steps: [{ action: 'goto', url: '/' }, w(1), w(2), w(3), { action: 'expectText', target: { role: 'status' }, text: 'Ready' }] }];
  const job = createJob('test-run', { cases }, {});
  const target = buildTarget(job.job_id, join(DATA_DIR, 'jobs', job.job_id), { base_url: base }, {});
  job.status = 'running';
  const ctl = new AbortController();
  const run = runTestRun({ job, target, cases: cases as never, concurrency: 1, signal: ctl.signal });
  await until(() => job.events.some((e) => e.type === 'step_pass'));
  applyControl(job, 'takeover');
  await relayInput(job, { type: 'click', x: 0.5, y: 0.2 });
  await relayInput(job, { type: 'type', text: TYPED });
  await new Promise((r) => setTimeout(r, 400));
  applyControl(job, 'handback');
  await run;
  job.status = 'passed';

  const res = job.results[0]!;
  assert.equal(res.status, 'passed', res.error);
  // the typed text is nowhere on disk: artifacts (zip entries too), events, job.json, baselines
  const leak = await scanForLeaks(DATA_DIR, [TYPED]);
  assert.equal(leak.occurrences, 0, JSON.stringify(leak.hits));
  // the trace, if kept, stops at the take over: no relayed input, no DOM values from that session
  const trace = res.artifacts.find((a) => a.endsWith('.trace.zip'));
  assert.ok(trace, `the steps before take over keep their trace: ${res.artifacts}`);
  {
    const zip = await JSZip.loadAsync(await import('node:fs').then((f) => f.readFileSync(join(artifactsDir(job.job_id), trace))));
    for (const e of Object.values(zip.files)) {
      if (e.dir) continue;
      const s = (await e.async('nodebuffer')).toString('utf8');
      assert.ok(!/keyboardType|"keyboard\.type"|mouseClick/.test(s), `${e.name} records take-over input`);
      assert.ok(!s.includes(TYPED), `${e.name} holds the typed value`);
    }
    const main = await zip.file('trace.trace')!.async('string');
    assert.ok(main.includes('"goto"') || main.includes('Frame.goto') || main.includes('"method":"goto"'), 'the pre-take-over chunk holds the scripted goto');
  }
  // video and screenshots after the take over would show the typed value
  assert.ok(!res.artifacts.some((a) => a.endsWith('.webm')), `video kept: ${res.artifacts}`);
  assert.ok(!res.artifacts.some((a) => a.endsWith('.final.png')), `final screenshot kept: ${res.artifacts}`);
  for (const a of res.artifacts) assert.ok(existsSync(join(artifactsDir(job.job_id), a)));
  // a clear note says what was not kept
  assert.ok((res.notes ?? []).some((n) => /take over/.test(n) && /trace/.test(n)), JSON.stringify(res.notes));
});

test('a case that is not taken over keeps its full trace, video and final screenshot (unchanged)', async () => {
  const cases = [{ id: 'c2', kind: 'ui', steps: [{ action: 'goto', url: '/' }, { action: 'waitFor', target: { text: 'Loaded1' }, state: 'visible' }] }];
  const job = createJob('test-run', { cases }, {});
  const target = buildTarget(job.job_id, join(DATA_DIR, 'jobs', job.job_id), { base_url: base }, {});
  job.status = 'running';
  await runTestRun({ job, target, cases: cases as never, concurrency: 1, signal: new AbortController().signal });
  const res = job.results[0]!;
  assert.equal(res.status, 'passed', res.error);
  for (const ext of ['.trace.zip', '.video.webm', '.final.png']) assert.ok(res.artifacts.some((a) => a.endsWith(ext)), `${ext} missing: ${res.artifacts}`);
  assert.equal(res.notes, undefined);
});
