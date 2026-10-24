import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
process.env.JOBS_DATA_DIR = mkdtempSync(join(tmpdir(), 'jobs-test-'));
mkdirSync(join(process.env.JOBS_DATA_DIR, 'jobs', 'job_test_unit'), { recursive: true });
const { domDiff, networkDiff } = await import('../src/triageEvidence.ts');
const { applyControl, gate, ControlError } = await import('../src/control.ts');
import type { FailureContext, Job, PassBaseline } from '../src/store.ts';

const snap = (rows: string[]) => rows.map((r) => `- ${r}`).join('\n');
const fail = (over: Partial<FailureContext> = {}): FailureContext => ({
  case_id: 'TC-1', kind: 'ui', steps: [], failed_index: 0,
  failed_step: { action: 'click', target: { role: 'button', name: 'Continue' } }, error: 'timeout waiting for button Continue',
  aria_snapshot: snap(['heading "Transfer"', 'button "Review payment"', 'link "Home"']),
  candidates: [{ role: 'button', name: 'Review payment', similarity: 0.6 }],
  text_lines: ['Transfer', 'Review payment'], ...over,
});
const base = (over: Partial<PassBaseline> = {}): PassBaseline => ({
  case_id: 'TC-1', job_id: 'job_old', at: '', url: '/', aria_snapshot: snap(['heading "Transfer"', 'button "Continue"', 'link "Home"']),
  text_lines: ['Transfer', 'Continue'], net_count: { 0: 1 }, network: [{ method: 'GET', url: 'http://x/api/accounts', status: 200 }], ...over,
});

test('dom_diff vs last pass finds the rename and the text change', () => {
  const d = domDiff(fail(), base());
  assert.equal(d.baseline, 'last_pass');
  assert.deepEqual({ ...d.changes[0], similarity: 0 }, { kind: 'renamed', role: 'button', before: 'Continue', after: 'Review payment', similarity: 0 });
  assert.deepEqual(d.text_removed, ['Continue']);
  assert.deepEqual(d.text_added, ['Review payment']);
});

test('dom_diff notes when the step itself was edited since the pass', () => {
  const d = domDiff(fail(), base({ steps: [{ action: 'click', target: { role: 'button', name: 'Verify' } }] }));
  assert.deepEqual(d.step_changed, { before: 'button "Verify"', after: 'button "Continue"' });
});

test('dom_diff without a baseline compares expected with the nearest actual element', () => {
  const d = domDiff(fail({ candidates: [{ role: 'button', name: 'Continue now', similarity: 0.75 }] }), null);
  assert.equal(d.baseline, 'expected_vs_actual');
  assert.equal(d.changes[0]!.kind, 'renamed');
  assert.deepEqual(d.expected, { role: 'button', name: 'Continue' });
  const gone = domDiff(fail({ candidates: [] }), null);
  assert.equal(gone.changes[0]!.kind, 'removed');
});

test('network_diff: status changes, missing and unexpected requests', () => {
  const f = fail({ full_network: [{ method: 'GET', url: 'http://x/api/accounts', status: 500 }, { method: 'POST', url: 'http://x/api/review', status: 404 }] });
  const n = networkDiff(f, base());
  assert.deepEqual(n.map((c) => c.kind), ['status_changed', 'unexpected_status']);
  assert.equal(n[0]!.expected, 200);
  assert.equal(n[0]!.actual, 500);
  assert.equal(networkDiff(fail({ full_network: [] }), base()).map((c) => c.kind)[0], 'missing');
  const api = networkDiff({ ...fail(), kind: 'api', failed_step: { method: 'GET', path: '/api/v1/x' }, error: 'expected status 200, got 403' }, null);
  assert.deepEqual(api[0], { method: 'GET', path: '/api/v1/x', kind: 'status_mismatch', expected: '200', actual: 403 });
});

test('pause / step / resume gate a UI case between steps', async () => {
  const job = { status: 'running', control: { paused: false, stepCredits: 0 }, progress: {}, events: [], seq: 0, creds: {}, job_id: 'job_test_unit' } as unknown as Job;
  const ctl = new AbortController();
  await gate(job, 'c', ctl.signal); // not paused: returns at once
  assert.equal(applyControl(job, 'pause').paused, true);
  let released = false;
  const waiting = gate(job, 'c', ctl.signal).then(() => { released = true; });
  await new Promise((r) => setTimeout(r, 250));
  assert.equal(released, false);
  applyControl(job, 'step');
  await waiting;
  assert.equal(released, true);
  assert.equal(job.control.paused, true); // still paused after the one step
  assert.equal(job.control.stepCredits, 0);
  applyControl(job, 'resume');
  await gate(job, 'c', ctl.signal);
  job.status = 'passed';
  assert.throws(() => applyControl(job, 'pause'), ControlError);
});

// ---- take over: the user drives the live page, then hands it back ------------------------------------------------
const { relayInput, InputError } = await import('../src/control.ts');

function fakeLive() {
  const calls: unknown[][] = [];
  const page = {
    viewportSize: () => ({ width: 1280, height: 800 }),
    mouse: { click: async (x: number, y: number) => { calls.push(['click', x, y]); }, wheel: async (dx: number, dy: number) => { calls.push(['wheel', dx, dy]); } },
    keyboard: { type: async (t: string) => { calls.push(['type', t]); }, press: async (k: string) => { calls.push(['press', k]); } },
  };
  return { calls, live: { page, context: {} } };
}

test('takeover pauses and relays click / type / key / scroll to the live page; handback resumes', async () => {
  const { calls, live } = fakeLive();
  const job = { status: 'running', control: { paused: false, stepCredits: 0, takenOver: false }, progress: { current_case_id: 'c1' }, events: [], seq: 0, creds: { pw: 's3cret-value' }, job_id: 'job_test_unit', live: new Map([['c1', live]]) } as unknown as Job;
  // input before takeover is refused
  await assert.rejects(relayInput(job, { type: 'click', x: 0.5, y: 0.5 }), ControlError);
  const tk = applyControl(job, 'takeover');
  assert.deepEqual([tk.paused, tk.taken_over], [true, true]);
  // the scripted steps wait while the user has the page
  const ctl = new AbortController();
  let released = false;
  const waiting = gate(job, 'c1', ctl.signal).then(() => { released = true; });
  try {
  await relayInput(job, { type: 'click', x: 0.5, y: 0.25 });
  await relayInput(job, { type: 'type', text: 's3cret-value' });
  await relayInput(job, { type: 'key', key: 'Enter' });
  await relayInput(job, { type: 'scroll', dx: 0, dy: 300 });
  assert.deepEqual(calls, [['click', 640, 200], ['type', 's3cret-value'], ['press', 'Enter'], ['wheel', 0, 300]]);
  assert.throws(() => applyControl(job, 'step'), ControlError); // hand back first
  await new Promise((r) => setTimeout(r, 150));
  assert.equal(released, false);
  // the typed text never reaches the event log
  assert.ok(!JSON.stringify(job.events).includes('s3cret'), JSON.stringify(job.events));
  assert.ok(job.events.some((e) => /typed 12 characters/.test(e.text)));
  // bad input is a 400, not a crash
  for (const bad of [{ type: 'click', x: 2, y: 0 }, { type: 'key', key: 'Control+Alt+Delete' }, { type: 'type', text: 'x'.repeat(5000) }, { type: 'drag' }, { type: 'scroll', dx: 'a', dy: 1 }])
    await assert.rejects(relayInput(job, bad as never), InputError, JSON.stringify(bad));
  const h = applyControl(job, 'handback');
  assert.deepEqual([h.paused, h.taken_over], [false, false]);
  await waiting;
  assert.equal(released, true);
  } finally { ctl.abort(); }
  await assert.rejects(relayInput(job, { type: 'key', key: 'Tab' }), ControlError); // no longer taken over
  assert.throws(() => applyControl(job, 'handback'), ControlError);
  // nothing live → nothing to take over
  const idle = { ...job, live: new Map(), control: { paused: false, stepCredits: 0, takenOver: false }, events: [] } as unknown as Job;
  assert.throws(() => applyControl(idle, 'takeover'), ControlError);
});
