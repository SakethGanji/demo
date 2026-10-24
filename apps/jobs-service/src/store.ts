// Job state: in memory, mirrored to data/jobs/<id>/job.json + events.jsonl. Credentials are never persisted.
import { mkdirSync, writeFileSync, appendFileSync, readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import type { BrowserContext, CDPSession, Page } from '@playwright/test';
import { scrub, scrubDeep } from '../../test-builder/src/secrets.ts';

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
export const DATA_DIR = process.env.JOBS_DATA_DIR ?? join(ROOT, 'data');

export type JobType = 'test-run' | 'triage' | 'explore';
export type JobStatus = 'queued' | 'running' | 'passed' | 'failed' | 'error' | 'cancelled';
export type CaseStatus = 'queued' | 'running' | 'passed' | 'failed' | 'blocked' | 'error' | 'cancelled';
export type EventType = 'step_start' | 'step_pass' | 'step_fail' | 'request' | 'blocked' | 'log' | 'case_start' | 'case_end';

export interface JobEvent { seq: number; ts: string; case_id: string | null; type: EventType; text: string; data?: unknown }

export interface CaseResult {
  case_id: string;
  kind: 'ui' | 'api';
  title?: string;
  status: CaseStatus;
  duration_ms: number;
  failed_step?: { index: number; step: unknown; description?: string };
  error?: string;
  artifacts: string[];
  /** Plain notes about the artifacts, e.g. what was not kept because the page was taken over. */
  notes?: string[];
}

/** Everything triage needs about a failed case (already scrubbed of credential values). */
export interface FailureContext {
  case_id: string;
  kind: 'ui' | 'api';
  steps: unknown[];
  failed_index: number;
  failed_step: unknown;
  error: string;
  url?: string;
  title?: string;
  /** Accessible names on the page with the same role as the failed target, most similar first. */
  candidates?: { role: string; name: string; similarity: number }[];
  aria_snapshot?: string;
  network?: { method: string; url: string; status: number | string }[];
  response?: unknown;
  /** Triage evidence (UI): the whole run's xhr/fetch/document requests, visible text lines, the failure screenshot. */
  full_network?: { method: string; url: string; status: number | string }[];
  text_lines?: string[];
  screenshot?: string;
  /** The job the failure happened in (so triage can find the last pass of the same case before it). */
  job_id?: string;
}

/** What a passing UI case looked like, kept per case id as the baseline a later failure is compared with. */
export interface PassBaseline {
  case_id: string;
  job_id: string;
  at: string;
  url: string;
  aria_snapshot: string;
  text_lines: string[];
  network: { method: string; url: string; status: number | string }[];
  screenshot?: string;
  /** The page as it was just before step N ran (key = step index; -1 = the implicit opening goto), so a failure at step N
   *  is compared with the same moment of the passing run, not with its end state. */
  before?: Record<string, { aria_snapshot: string; text_lines: string[] }>;
  /** How many of `network` had been seen once step N had finished. */
  net_count?: Record<string, number>;
  /** The passing run's steps, to tell "the page changed" from "the step was edited". */
  steps?: unknown[];
}

/** Pause/step state, honoured by UI cases between steps. */
export interface JobControl {
  paused: boolean; stepCredits: number; /** the user drives the live page (take over) */ takenOver?: boolean;
  /** text typed during take over (memory only, never persisted): redacted from anything a case keeps */ typed?: string[];
}

export interface LiveCase {
  page: Page; context: BrowserContext; cdp?: CDPSession;
  /** set by the UI case: stops recording (trace) before the user gets the page */ onTakeover?: () => Promise<void>;
  /** resolves once onTakeover has finished; relayed input waits for it */ cut?: Promise<void>;
}

export interface Job {
  job_id: string;
  type: JobType;
  label?: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: { done: number; total: number; current_case_id: string | null; current_step: string | null; paused?: boolean };
  results: CaseResult[];
  output?: unknown;
  error?: string;
  /** Request body with credential values replaced by MASK. */
  request: unknown;
  failures: Record<string, FailureContext>;
  // ---- runtime only (not persisted)
  events: JobEvent[];
  seq: number;
  creds: Record<string, string>;
  cancelRequested: boolean;
  frame: Buffer | null;
  frameAt: number;
  live: Map<string, LiveCase>;
  artifacts: Set<string>;
  onCancel: (() => void)[];
  control: JobControl;
}

export const jobs = new Map<string, Job>();

export const jobDir = (id: string) => join(DATA_DIR, 'jobs', id);
export const artifactsDir = (id: string) => join(jobDir(id), 'artifacts');

export function newJobId(): string {
  const d = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  return `job_${d}_${randomBytes(5).toString('hex')}`;
}

export function createJob(type: JobType, request: unknown, creds: Record<string, string>, label?: string): Job {
  const id = newJobId();
  const job: Job = {
    job_id: id, type, label, status: 'queued', created_at: new Date().toISOString(), started_at: null, finished_at: null,
    progress: { done: 0, total: 0, current_case_id: null, current_step: null },
    results: [], request: scrubDeep(request, creds), failures: {},
    events: [], seq: 0, creds, cancelRequested: false, frame: null, frameAt: 0, live: new Map(), artifacts: new Set(), onCancel: [], control: { paused: false, stepCredits: 0 },
  };
  jobs.set(id, job);
  mkdirSync(artifactsDir(id), { recursive: true });
  persist(job);
  return job;
}

export function publicView(j: Job) {
  return {
    job_id: j.job_id, type: j.type, label: j.label, status: j.status, created_at: j.created_at,
    started_at: j.started_at, finished_at: j.finished_at, progress: j.progress, results: j.results,
    ...(j.output !== undefined ? { output: j.output } : {}),
    ...(j.error ? { error: j.error } : {}),
  };
}

export function persist(j: Job): void {
  const { events: _e, creds: _c, frame: _f, live: _l, onCancel: _o, control: _k, artifacts, ...rest } = j;
  writeFileSync(join(jobDir(j.job_id), 'job.json'), JSON.stringify({ ...rest, artifacts: [...artifacts] }, null, 2));
}

export function emit(j: Job, type: EventType, caseId: string | null, text: string, data?: unknown): JobEvent {
  const ev: JobEvent = { seq: ++j.seq, ts: new Date().toISOString(), case_id: caseId, type, text: scrub(text, j.creds), ...(data !== undefined ? { data: scrubDeep(data, j.creds) } : {}) };
  j.events.push(ev);
  appendFileSync(join(jobDir(j.job_id), 'events.jsonl'), JSON.stringify(ev) + '\n');
  return ev;
}

export const isFinal = (s: JobStatus) => s !== 'queued' && s !== 'running';

/** Reload finished jobs from disk; anything that was mid-flight when the service stopped becomes an error. */
export function loadFromDisk(): void {
  const root = join(DATA_DIR, 'jobs');
  if (!existsSync(root)) return;
  for (const id of readdirSync(root)) {
    try {
      const raw = JSON.parse(readFileSync(join(root, id, 'job.json'), 'utf8')) as Job & { artifacts: string[] };
      const events: JobEvent[] = existsSync(join(root, id, 'events.jsonl'))
        ? readFileSync(join(root, id, 'events.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as JobEvent)
        : [];
      const job: Job = { ...raw, events, seq: events.at(-1)?.seq ?? 0, creds: {}, cancelRequested: false, frame: null, frameAt: 0, live: new Map(), artifacts: new Set(raw.artifacts ?? []), onCancel: [], control: { paused: false, stepCredits: 0 } };
      if (!isFinal(job.status)) {
        job.status = 'error';
        job.error = 'jobs service restarted while this job was in flight';
        job.finished_at = new Date().toISOString();
        persist(job);
      }
      jobs.set(id, job);
    } catch { /* skip unreadable */ }
  }
}

// ---------------------------------------------------------------- pass baselines (triage compares a failure with them)
const baselinePath = (caseId: string) => join(DATA_DIR, 'baselines', `${caseId.replace(/[^\w.-]/g, '_')}.json`);
export function saveBaseline(b: PassBaseline): void {
  mkdirSync(join(DATA_DIR, 'baselines'), { recursive: true });
  writeFileSync(baselinePath(b.case_id), JSON.stringify(b));
}
export function loadBaseline(caseId: string): PassBaseline | null {
  try { return JSON.parse(readFileSync(baselinePath(caseId), 'utf8')) as PassBaseline; } catch { return null; }
}
