// test-run jobs: run cases with a per-job concurrency limit, mask every artifact, write evidence.json.
import { mkdirSync, renameSync, readFileSync, statSync, writeFileSync, rmSync } from 'node:fs';
import { join, basename } from 'node:path';
import { createRequire } from 'node:module';
import { maskArtifacts, scanForLeaks, scrubDeep, variants, type LeakReport } from '../../test-builder/src/secrets.ts';
import { sha256 } from '../../test-builder/src/run.ts';
import type { Target } from '../../test-builder/src/config.ts';
import { artifactsDir, emit, jobDir, persist, type CaseResult, type Job } from './store.ts';
import type { CaseBody } from './spec.ts';
import { runUiCase } from './uiCase.ts';
import { runApiCase } from './apiCase.ts';

const require = createRequire(import.meta.url);
const pwVersion = (require('@playwright/test/package.json') as { version: string }).version;

export interface TestRunInput { job: Job; target: Target; cases: CaseBody[]; concurrency: number; signal: AbortSignal }

interface Hygiene { case_id: string; before: LeakReport; masked: string[]; after: LeakReport }

export async function runTestRun(i: TestRunInput): Promise<void> {
  const { job, target, cases } = i;
  const secrets = Object.values(job.creds).filter((v) => v && v.length >= 4);
  const hygiene: Hygiene[] = [];
  job.progress.total = cases.length;
  job.results = cases.map((c) => ({ case_id: c.id, kind: c.kind, title: c.title, status: 'queued', duration_ms: 0, artifacts: [] }));
  emit(job, 'log', null, `test-run started: ${cases.length} case(s), concurrency ${i.concurrency}`, {
    allowed_hosts: target.allowedHosts, forbidden_names: target.forbiddenActions.map(String), forbidden_requests: target.forbiddenRequests.map((r) => r.raw),
  });

  let next = 0;
  const worker = async () => {
    while (next < cases.length) {
      const c = cases[next++]!;
      const res = job.results.find((r) => r.case_id === c.id)!;
      if (i.signal.aborted) { res.status = 'cancelled'; continue; }
      await runOne(i, c, res, secrets, hygiene);
      job.progress.done++;
      persist(job);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(i.concurrency, cases.length)) }, worker));
  job.progress.current_case_id = null;
  job.progress.current_step = null;
  writeEvidence(job, target, secrets, hygiene);
}

async function runOne(i: TestRunInput, c: CaseBody, res: CaseResult, secrets: string[], hygiene: Hygiene[]): Promise<void> {
  const { job } = i;
  const caseDir = join(jobDir(job.job_id), 'cases', c.id);
  mkdirSync(caseDir, { recursive: true });
  res.status = 'running';
  job.progress.current_case_id = c.id;
  emit(job, 'case_start', c.id, `${c.id} ${c.title ?? ''}`.trim(), { kind: c.kind });
  const t0 = Date.now();
  let out: { status: CaseResult['status']; failed_step?: CaseResult['failed_step']; error?: string; notes?: string[]; files: string[] };
  try {
    out = c.kind === 'ui'
      ? await runUiCase({ job, target: i.target, caseId: c.id, steps: c.steps, caseDir, signal: i.signal })
      : await runApiCase({ job, target: i.target, caseId: c.id, requests: c.requests, caseDir, signal: i.signal });
  } catch (e) {
    out = { status: i.signal.aborted ? 'cancelled' : 'error', error: (e as Error).message.slice(0, 600), files: [] };
  }
  res.duration_ms = Date.now() - t0;
  res.status = out.status;
  if (out.failed_step) res.failed_step = scrubDeep(out.failed_step, job.creds);
  if (out.error) res.error = scrubDeep(out.error, job.creds);
  if (out.notes?.length) res.notes = out.notes;

  // Mask before anything becomes downloadable: traces record fill() values verbatim. Text typed during take over is
  // treated like a secret too (memory only; the take-over trace chunk is already gone, this is the second line).
  const caseSecrets = [...new Set([...secrets, ...(job.control.typed ?? []), (job.control.typed ?? []).join('')].filter((v) => v && v.length >= 4))];
  const before = await scanForLeaks(caseDir, caseSecrets);
  const masked = before.occurrences ? await maskArtifacts(caseDir, caseSecrets) : [];
  const after = await scanForLeaks(caseDir, caseSecrets);
  hygiene.push({ case_id: c.id, before, masked, after });
  if (after.occurrences) {
    emit(job, 'log', c.id, `LEAK: ${after.occurrences} secret occurrence(s) survived masking; artifacts withheld`, after.hits);
  } else {
    for (const f of out.files) {
      const name = basename(f);
      renameSync(f, join(artifactsDir(job.job_id), name));
      job.artifacts.add(name);
      res.artifacts.push(name);
    }
  }
  rmSync(caseDir, { recursive: true, force: true });
  emit(job, 'case_end', c.id, `${c.id} ${res.status} in ${res.duration_ms} ms${res.error ? `: ${res.error}` : ''}`, { status: res.status, duration_ms: res.duration_ms, artifacts: res.artifacts });
}

function writeEvidence(job: Job, target: Target, secrets: string[], hygiene: Hygiene[]): void {
  const dir = artifactsDir(job.job_id);
  const artifacts = [...job.artifacts].filter((n) => n !== 'evidence.json').sort().map((name) => {
    const p = join(dir, name);
    return { name, bytes: statSync(p).size, sha256: sha256(readFileSync(p)) };
  });
  const evidence = scrubDeep({
    schema: 'jobs-service/evidence@1',
    job_id: job.job_id,
    label: job.label,
    started_at: job.started_at,
    finished_at: new Date().toISOString(),
    target: { base_url: target.baseUrl, allowed_hosts: target.allowedHosts },
    policy: { forbidden_names: target.forbiddenActions.map(String), forbidden_requests: target.forbiddenRequests.map((r) => r.raw) },
    tooling: { playwright: pwVersion, node: process.version, platform: `${process.platform}-${process.arch}` },
    results: job.results,
    failures: job.failures,
    blocked_events: job.events.filter((e) => e.type === 'blocked').map(({ seq, ts, case_id, text }) => ({ seq, ts, case_id, text })),
    secret_hygiene: {
      secrets_checked: secrets.length,
      cases: hygiene.map((h) => ({ case_id: h.case_id, before_masking: h.before.occurrences, masked_files: h.masked.map((m) => basename(m)), after_masking: h.after.occurrences })),
    },
    artifacts,
  }, job.creds);
  const text = JSON.stringify(evidence, null, 2);
  const leaked = secrets.flatMap(variants).filter((v) => text.includes(v)).length;
  if (leaked) throw new Error('LEAK: evidence.json would contain a secret value');
  writeFileSync(join(dir, 'evidence.json'), text);
  job.artifacts.add('evidence.json');
}
