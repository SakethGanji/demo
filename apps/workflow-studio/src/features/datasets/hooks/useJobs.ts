/**
 * Read hooks for the analytics service's `jobs` table.
 *
 * Queries only, matching the split in `useDatasets.ts` — there is nothing to
 * write here anyway: the service exposes exactly `GET /jobs` and
 * `GET /jobs/{id}`. There is no cancel, no retry, no re-queue and no create.
 *
 * WHAT A JOB ROW ACTUALLY IS
 *
 * Eight job types exist. Only FOUR of them have a handler registered with the
 * worker (`shared/worker.py: register_handler`), and the claim loop only ever
 * selects `job_type = ANY(<registered types>)`. For the other four the row is
 * an execution RECORD: the work already ran inside the API request that
 * created it. Rendering those as "queued work" would describe a queue that
 * does not exist, so `hasWorkerHandler` is exported and every surface that
 * shows a status is expected to consult it.
 *
 * Worse, a `pending` row of a handler-less type is not waiting — it is
 * stranded. No worker will ever claim it, because the claim query filters on
 * the registered set. That is a real state and the UI names it.
 *
 * `?sync=` DEFAULTS TO TRUE on every endpoint that has it (files upload →
 * `import`, transformations run → `transform`, relationships suggest →
 * `relationship_discovery`). So even a worker-backed type has usually already
 * run in-request by the time its row appears; `sync=false` is the opt-in.
 *
 * THERE IS NO SCHEDULER. Nothing in the service enqueues `artifact_gc` — the
 * handler is registered and never dispatched from anywhere in the codebase.
 * Any `artifact_gc` row was triggered from outside. Do not render a "next run".
 *
 * `JobOut` emits ISO-8601 timestamps (a `field_validator` normalises them),
 * unlike most entities here which emit a Postgres `::text` timestamp. Both
 * parse, but everything below is guarded anyway: a malformed value must render
 * as an em dash, never as "Invalid Date".
 */

import { useQuery } from '@tanstack/react-query';
import { analytics, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import type { components } from '@/shared/lib/analyticsSchema';

export type Job = components['schemas']['JobOut'];

/**
 * Every key is scoped by the acting seat. `/jobs` filters on
 * `team_id = ANY(principal.team_ids)`, so switching seats changes what the
 * same URL returns and a shared cache entry would show another seat's runs.
 */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/**
 * The eight job types, as enumerated by the service itself
 * (`features/mcp/tools/context.py`, the `job_type` filter description) and
 * confirmed against every `jobs.create_job` / `worker.dispatch` call site.
 */
export const JOB_TYPES = [
  'import',
  'validation',
  'profiling',
  'analytics',
  'transform',
  'relationship_discovery',
  'artifact_gc',
  'webhook_delivery',
] as const;

export type JobType = (typeof JOB_TYPES)[number];

/**
 * The four types with a `worker.register_handler(...)` call. These are the
 * only types the background loop can claim, so they are the only types for
 * which `pending` means "waiting for a worker".
 */
export const WORKER_JOB_TYPES = [
  'transform',
  'relationship_discovery',
  'artifact_gc',
  'webhook_delivery',
] as const;

/**
 * The four with no handler. They run inside the request that created them and
 * leave the row behind as an execution record.
 */
export const IN_REQUEST_JOB_TYPES = [
  'import',
  'validation',
  'profiling',
  'analytics',
] as const;

/**
 * Types reachable through an endpoint that exposes `?sync=`, which defaults to
 * TRUE. `transform` and `relationship_discovery` are worker-BACKED yet still
 * run in-request by default; `import`'s `sync` controls a BackgroundTask, not
 * the job worker.
 */
export const SYNC_DEFAULT_TRUE_TYPES = [
  'import',
  'transform',
  'relationship_discovery',
] as const;

const WORKER_SET: ReadonlySet<string> = new Set<string>(WORKER_JOB_TYPES);

/** True when the worker loop can actually claim this type. */
export function hasWorkerHandler(jobType: string): boolean {
  return WORKER_SET.has(jobType);
}

/** The statuses `shared/jobs.py` writes. Anything else is data we did not expect. */
export const JOB_STATUSES = ['pending', 'running', 'completed', 'failed'] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

/** Parse a timestamp from either wire format, or `null`. Never `Invalid Date`. */
export function parseInstant(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Wall-clock time for a dense row, or an em dash. */
export function formatClock(value: string | null | undefined): string {
  const d = parseInstant(value);
  return d ? d.toLocaleTimeString() : '—';
}

/** Date and time, for the detail dock where there is room to be unambiguous. */
export function formatStamp(value: string | null | undefined): string {
  const d = parseInstant(value);
  return d ? d.toLocaleString() : '—';
}

/**
 * How long the job took, in milliseconds.
 *
 * A finished job is `started_at → completed_at`. A running job is
 * `started_at → now`, which is why `nowMs` is a parameter rather than a call
 * to `Date.now()` inside: every row in one render must measure against the
 * same instant, or a list of elapsed times disagrees with itself.
 *
 * Returns `null` when the job never started (a `pending` row has no
 * `started_at`), because zero would claim it ran instantly.
 */
export function jobDurationMs(job: Job, nowMs: number): number | null {
  const started = parseInstant(job.started_at);
  if (!started) return null;
  const ended = parseInstant(job.completed_at);
  const end = ended ? ended.getTime() : nowMs;
  const ms = end - started.getTime();
  return ms >= 0 ? ms : null;
}

/** `812ms` / `3.1s` / `2m 04s`. */
export function formatDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60_000);
  const secs = Math.round((ms % 60_000) / 1000);
  return `${mins}m ${String(secs).padStart(2, '0')}s`;
}

/**
 * Rows touched, read from the job's own `result` payload.
 *
 * Only some handlers record one — `transform` writes `row_count`, and the
 * others summarise different things entirely (`pairs_examined`,
 * `orphans_deleted`, `response_status`). So this reads ONE verified key and
 * returns `null` otherwise rather than guessing at whichever number the
 * payload happens to contain first, which is how a column starts reporting a
 * webhook's HTTP status as a row count.
 */
export function jobRowCount(job: Job): number | null {
  const value = job.result?.row_count;
  return typeof value === 'number' ? value : null;
}

export interface JobFilters {
  /** Server-side. One of `JOB_STATUSES`. */
  status?: string;
  /** Server-side. One of `JOB_TYPES`. */
  jobType?: string;
}

/** `GET /jobs` caps `limit` at 200; asking for more is a 422, not a bigger page. */
export const MAX_JOBS_LIMIT = 200;

export const JOBS_PAGE_LIMIT = 50;

export interface JobsQueryOptions {
  offset?: number;
  limit?: number;
  /** Milliseconds between refetches, or `null` to hold still. */
  pollMs?: number | null;
}

/**
 * One page of jobs, newest first.
 *
 * `/jobs` pages by `offset`, not by cursor, and returns `total` — so unlike
 * the row endpoints a real "51–100 of 1,284" is honest here.
 *
 * There is NO date filter and NO free-text search on this endpoint. Anything
 * resembling either has to be applied to the loaded page by the caller, and
 * has to say so.
 */
export function useJobs(filters: JobFilters = {}, opts: JobsQueryOptions = {}) {
  const seat = useSeat();
  const limit = Math.min(opts.limit ?? JOBS_PAGE_LIMIT, MAX_JOBS_LIMIT);
  const offset = opts.offset ?? 0;

  return useQuery({
    queryKey: ['analytics', seat, 'jobs', filters.status ?? null, filters.jobType ?? null, limit, offset],
    queryFn: () =>
      analytics.get<Page<Job>>('/jobs', {
        status: filters.status,
        job_type: filters.jobType,
        limit,
        offset,
      }),
    refetchInterval: opts.pollMs ?? false,
    // Poll without blanking the table: a monitor that empties every few
    // seconds is unreadable, and the row you were about to click moves.
    placeholderData: (prev) => prev,
  });
}

export interface JobCounts {
  /** What the server says exists for these filters — not what we hold. */
  total: number;
  byStatus: Record<JobStatus, number>;
}

/**
 * Exact counts per status, straight from the server.
 *
 * Deliberately five `limit=1` probes rather than tallying the loaded page:
 * a KPI computed over the 50 rows we happen to hold, labelled with no
 * denominator, is the silent-wrong-answer shape this codebase spent an audit
 * removing. `total` on a filtered list IS the count, so ask for it.
 */
export function useJobCounts(filters: Pick<JobFilters, 'jobType'> = {}, opts: { pollMs?: number | null } = {}) {
  const seat = useSeat();
  const jobType = filters.jobType;

  return useQuery({
    queryKey: ['analytics', seat, 'job-counts', jobType ?? null],
    queryFn: async (): Promise<JobCounts> => {
      const [all, ...perStatus] = await Promise.all([
        analytics.get<Page<Job>>('/jobs', { job_type: jobType, limit: 1 }),
        ...JOB_STATUSES.map((status) =>
          analytics.get<Page<Job>>('/jobs', { job_type: jobType, status, limit: 1 }),
        ),
      ]);

      const byStatus = { pending: 0, running: 0, completed: 0, failed: 0 };
      JOB_STATUSES.forEach((status, i) => {
        byStatus[status] = perStatus[i].total;
      });

      return { total: all.total, byStatus };
    },
    refetchInterval: opts.pollMs ?? false,
    placeholderData: (prev) => prev,
  });
}

/**
 * One job.
 *
 * `retry: false` because the interesting failure is a 404, and a 404 here is
 * deliberately ambiguous: the job may not exist, or it may belong to another
 * team. Cross-tenant reads return 404 and never 403 — retrying cannot resolve
 * that, and no caller may render "access denied", which would leak the very
 * existence the 404 is hiding.
 */
export function useJob(jobId: string | null, opts: { pollMs?: number | null } = {}) {
  const seat = useSeat();

  return useQuery({
    queryKey: ['analytics', seat, 'job', jobId],
    queryFn: () => analytics.get<Job>(`/jobs/${jobId}`),
    enabled: Boolean(jobId),
    retry: false,
    refetchInterval: opts.pollMs ?? false,
  });
}
