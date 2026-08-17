/**
 * Runs — the execution monitor over the analytics service's `jobs` table.
 *
 * The spine of this screen is execution-model honesty, because the thing an
 * operator most wants from a runs monitor is to know whether something is
 * still going to happen. Here, usually, it already has:
 *
 *  - Only FOUR of the eight job types have a worker handler. The other four run
 *    inside the API request that created them, and the row is a record of work
 *    that is already over. So `mode` is a column, and it is told typographically
 *    (rule 7 — it is a property of the type, not a state of the run).
 *  - A `pending` row of a handler-less type is not queued, it is STRANDED: the
 *    worker's claim query filters on the registered types, so nothing will ever
 *    pick it up. It gets its own status shape rather than being folded into
 *    "queued", which would promise a worker that does not exist.
 *  - Nothing schedules `artifact_gc`. There is no scheduler and no cron in the
 *    service at all, so this page shows no "next run" and no cadence anywhere.
 *
 * WHAT THE API WILL NOT GIVE US, AND WHAT WE THEREFORE DO NOT DRAW
 *
 * `GET /jobs` takes `status`, `job_type`, `limit`, `offset` and nothing else.
 * There is no date filter, so there is no "runs today" and no time-window
 * segmented control — the KPI band says "recorded", counts come from the
 * server's own `total`, and the text filter states that it only narrows the
 * loaded page. There is no sort parameter, so no column is sortable. And
 * `JobOut` does not expose the row's stored `parameters`, so the detail dock
 * shows the `result` payload it does expose and says the parameters are absent
 * rather than inventing a plausible-looking block.
 *
 * ACCENT BUDGET (rule 1) — three uses, all of them scope or liveness:
 *   1. the polling lamp, 2. the selected row, 3. that run's id in the dock head.
 * Status is repeated on every row, so it never takes the accent; it takes the
 * five shapes (rule 6).
 */

import { useMemo, useState, type ReactNode } from 'react';
import { Search, X } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableNumericCell,
  TableRow,
} from '@/shared/components/ui/table';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import {
  Footnote,
  Identifier,
  Metric,
  SectionTitle,
} from '@/shared/components/instrument/Typography';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { complete, coverage } from '@/shared/components/instrument/coverage';
import JsonViewer from '@/shared/components/ui/json-viewer';
import { errorText } from '@/shared/lib/analyticsClient';
import { compact } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { controlClass } from './fieldStyles';
import { PagerButton } from './PagerButton';
import { useDatasetCatalog } from '../hooks/useDatasets';
import {
  formatClock,
  formatDuration,
  formatStamp,
  hasWorkerHandler,
  jobDurationMs,
  jobRowCount,
  JOB_STATUSES,
  JOB_TYPES,
  JOBS_PAGE_LIMIT,
  IN_REQUEST_JOB_TYPES,
  SYNC_DEFAULT_TRUE_TYPES,
  WORKER_JOB_TYPES,
  useJob,
  useJobCounts,
  useJobs,
  type Job,
} from '../hooks/useJobs';

/** Refresh cadences offered. `0` is "hold still", and it is a real choice. */
const POLL_CHOICES = [0, 5000, 15000, 60000] as const;

function pollLabel(ms: number): string {
  return ms === 0 ? 'Paused' : `Every ${ms / 1000}s`;
}

/**
 * Status is shape + hue + WORD (rule 6), so every branch names itself.
 *
 * `pending` splits on whether a worker can claim the type. Calling a stranded
 * row "queued" is the single most misleading thing this screen could say.
 */
function statusOf(job: Job): { kind: StatusKind; word: string } {
  switch (job.status) {
    case 'completed':
      return { kind: 'good', word: 'completed' };
    case 'failed':
      return { kind: 'critical', word: 'failed' };
    case 'running':
      // Unknown, not good: a running job has no outcome yet. And it is the
      // ring rather than the accent, because several rows carry it at once.
      return { kind: 'unknown', word: 'running' };
    case 'pending':
      return hasWorkerHandler(job.job_type)
        ? { kind: 'warning', word: 'queued' }
        : { kind: 'serious', word: 'stranded' };
    default:
      return { kind: 'unknown', word: job.status };
  }
}

/** The mode word. Typographic weight only — never a hue, never a chip. */
function ModeWord({ jobType, className }: { jobType: string; className?: string }) {
  const worker = hasWorkerHandler(jobType);
  return (
    <Identifier
      data-mode={worker ? 'worker' : 'in-request'}
      title={
        worker
          ? 'A worker handler is registered for this type; it can run on the background loop.'
          : 'No worker handler is registered; this ran inside the API request and the row is an execution record.'
      }
      className={cn(
        'text-micro',
        worker ? 'font-semibold text-foreground' : 'font-normal text-muted-foreground',
        className,
      )}
    >
      {worker ? 'worker' : 'in-request'}
    </Identifier>
  );
}

/** A short handle for a uuid. The full value lives in the dock. */
function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : '—';
}

/** An informational aside: a left rule and an indent, never a nested box (rule 8). */
function Note({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <p
      data-testid={testid}
      className="pl-2.5 text-small leading-relaxed text-muted-foreground shadow-[inset_2px_0_0_var(--r2)]"
    >
      {children}
    </p>
  );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span className="text-small text-muted-foreground">{label}</span>
      <span className="min-w-0 font-mono text-small break-all text-foreground">{children}</span>
    </>
  );
}

export function RunsPage() {
  const [status, setStatus] = useState<string>('all');
  const [jobType, setJobType] = useState<string>('all');
  const [needle, setNeedle] = useState('');
  const [pollMs, setPollMs] = useState<number>(5000);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const poll = pollMs === 0 ? null : pollMs;
  const filters = {
    status: status === 'all' ? undefined : status,
    jobType: jobType === 'all' ? undefined : jobType,
  };

  const jobs = useJobs(filters, { offset, limit: JOBS_PAGE_LIMIT, pollMs: poll });
  // Counts follow the type filter but NOT the status filter — a status
  // breakdown that had already been narrowed to one status would just be the
  // page total four times over.
  const counts = useJobCounts({ jobType: filters.jobType }, { pollMs: poll });
  const detail = useJob(selectedId, { pollMs: poll });

  // Dataset names for the `dataset` column. `JobOut` carries only ids, and an
  // id is not what anyone is looking for in a list of runs.
  const catalog = useDatasetCatalog();
  const datasetNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const d of catalog.data?.items ?? []) map.set(d.id, d.name);
    return map;
  }, [catalog.data]);

  const loaded = useMemo(() => jobs.data?.items ?? [], [jobs.data]);
  const total = jobs.data?.total ?? 0;

  // Applied to the loaded page ONLY — `/jobs` has no search parameter, and the
  // footnote under the table says so rather than implying a global match.
  const rows = useMemo(() => {
    const q = needle.trim().toLowerCase();
    if (!q) return loaded;
    return loaded.filter((job) => {
      const name = job.dataset_id ? (datasetNames.get(job.dataset_id) ?? '') : '';
      return (
        job.id.toLowerCase().includes(q) ||
        job.job_type.toLowerCase().includes(q) ||
        job.status.toLowerCase().includes(q) ||
        name.toLowerCase().includes(q)
      );
    });
  }, [loaded, needle, datasetNames]);

  // One instant for the whole render, so a column of elapsed times agrees
  // with itself. It advances when the poll re-renders us.
  const nowMs = Date.now();

  // Finished runs only. A still-running job has an elapsed time, not a
  // duration, and folding a growing number into a median moves the median
  // every poll — so the population is the completed rows, and `StatList`
  // states that denominator rather than letting it go unsaid.
  const durations = useMemo(
    () =>
      loaded
        // `completed_at` is set, so the `now` argument is never consulted here.
        .map((job) => (job.completed_at ? jobDurationMs(job, 0) : null))
        .filter((ms): ms is number => ms !== null)
        .sort((a, b) => a - b),
    [loaded],
  );

  const measured = durations.length;
  const quantile = (p: number) =>
    measured === 0 ? '—' : formatDuration(durations[Math.min(measured - 1, Math.floor(p * measured))]);

  const byStatus = counts.data?.byStatus;
  const recorded = counts.data?.total ?? null;
  const completedShare =
    byStatus && recorded ? `${((byStatus.completed / recorded) * 100).toFixed(1)}% of recorded` : undefined;

  // `pending` rows of handler-less types are the stranded ones.
  const strandedOnPage = loaded.filter(
    (job) => job.status === 'pending' && !hasWorkerHandler(job.job_type),
  ).length;

  const selected = detail.data ?? loaded.find((job) => job.id === selectedId) ?? null;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + loaded.length;

  const resetPage = () => {
    setOffset(0);
    setSelectedId(null);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="runs-page">
      {/* ── head ───────────────────────────────────────────────────────── */}
      <div className="flex shrink-0 items-start gap-4 px-4 pt-3 pb-2">
        <div className="min-w-0">
          <h1 className="text-figure leading-none font-medium">Runs</h1>
          <p className="mt-1.5 text-body text-muted-foreground">
            Every job row this seat can see. Some are work waiting to happen; most are a record of
            work that already did.
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {/* ACCENT 1/3 — liveness. The only lamp on the page. */}
          <span
            data-testid="runs-live-lamp"
            data-live={poll ? '' : undefined}
            className="flex items-center gap-1.5 text-small text-muted-foreground"
          >
            <span
              aria-hidden="true"
              className={cn(
                'size-[6px] rounded-full',
                poll
                  ? 'bg-[var(--sig)] shadow-[0_0_0_3px_var(--sig-dim),0_0_9px_-1px_var(--sig)] motion-safe:animate-pulse'
                  : 'bg-[var(--m5)]',
              )}
            />
            {poll ? 'live' : 'held'}
          </span>
          <select
            value={pollMs}
            onChange={(e) => setPollMs(Number(e.target.value))}
            aria-label="Refresh interval"
            data-testid="runs-poll-select"
            className={cn(controlClass, 'w-auto')}
          >
            {POLL_CHOICES.map((ms) => (
              <option key={ms} value={ms}>
                {pollLabel(ms)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ── KPI band ───────────────────────────────────────────────────── */}
      <div
        data-testid="runs-kpis"
        className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-3 bg-card px-4 py-3 shadow-[var(--hi),0_1px_0_rgba(0,0,0,.4)] sm:grid-cols-3 lg:grid-cols-5"
      >
        <Metric
          data-testid="runs-kpi-recorded"
          size="figure"
          label="Runs recorded"
          value={recorded === null ? '—' : recorded.toLocaleString()}
          note={jobType === 'all' ? 'all time — /jobs has no date filter' : `job_type = ${jobType}`}
        />
        <Metric
          data-testid="runs-kpi-completed"
          size="figure"
          label="Completed"
          value={byStatus ? byStatus.completed.toLocaleString() : '—'}
          note={completedShare}
        />
        <Metric
          data-testid="runs-kpi-failed"
          size="figure"
          label="Failed"
          value={byStatus ? byStatus.failed.toLocaleString() : '—'}
          note="error text is on the row; the problem+json code is not"
        />
        <Metric
          data-testid="runs-kpi-running"
          size="figure"
          label="Running"
          value={byStatus ? byStatus.running.toLocaleString() : '—'}
          note="in-request runs hold this status too"
        />
        <Metric
          data-testid="runs-kpi-pending"
          size="figure"
          label="Pending"
          value={byStatus ? byStatus.pending.toLocaleString() : '—'}
          note={
            strandedOnPage > 0
              ? `${strandedOnPage} on this page can never be claimed`
              : 'only worker-backed types can be claimed'
          }
        />
      </div>

      {/* ── execution model ────────────────────────────────────────────── */}
      <div
        data-testid="runs-execution-model"
        className="mx-4 mt-3 shrink-0 rounded-lg bg-card px-4 py-3"
      >
        <SectionTitle>How these actually execute</SectionTitle>

        <div className="mt-2.5 grid gap-4 md:grid-cols-3">
          <div className="min-w-0">
            <div className="text-small font-medium text-foreground">
              Worker-backed · {WORKER_JOB_TYPES.length} of {JOB_TYPES.length}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
              {WORKER_JOB_TYPES.map((t) => (
                <Identifier key={t} className="text-micro font-semibold text-foreground">
                  {t}
                </Identifier>
              ))}
            </div>
            <Footnote className="mt-1.5">
              A handler is registered, so the background loop can claim these. Here{' '}
              <span className="text-foreground">pending</span> genuinely means waiting.
            </Footnote>
          </div>

          <div className="min-w-0">
            <div className="text-small font-medium text-foreground">
              In-request · {IN_REQUEST_JOB_TYPES.length} of {JOB_TYPES.length}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
              {IN_REQUEST_JOB_TYPES.map((t) => (
                <Identifier key={t} className="text-micro text-muted-foreground">
                  {t}
                </Identifier>
              ))}
            </div>
            <Footnote className="mt-1.5">
              No handler is registered. The work ran inside the API request and the row is an
              execution record — there is no queue behind it.
            </Footnote>
          </div>

          {/* Durations are the one genuinely partial statistic here: only jobs
              that finished have one, so the population is stated once. */}
          <StatList data-testid="runs-durations" coverage={coverage(measured, loaded.length)}>
            <div className="mb-1 text-small font-medium text-foreground">
              Durations · loaded page
            </div>
            <Stat name="median" value={quantile(0.5)} coverage={complete(measured)} />
            <Stat name="p95" value={quantile(0.95)} coverage={complete(measured)} />
            <Stat name="max" value={quantile(1)} coverage={complete(measured)} />
          </StatList>
        </div>

        <div className="mt-3 flex flex-col gap-2">
          <Note testid="runs-note-sync">
            <span className="font-mono text-micro text-foreground">?sync=</span> defaults to{' '}
            <span className="text-foreground">true</span> on every endpoint that has it (
            {SYNC_DEFAULT_TRUE_TYPES.join(', ')}). So even a worker-backed run has usually already
            finished in-request by the time its row appears; <span className="font-mono text-micro">sync=false</span>{' '}
            is the opt-in.
          </Note>
          <Note testid="runs-note-scheduler">
            There is no scheduler and no cron in this service. Nothing enqueues{' '}
            <span className="font-mono text-micro text-foreground">artifact_gc</span> — a row for it
            was triggered from outside, and there is no next run to show.
          </Note>
        </div>
      </div>

      {/* ── filters ────────────────────────────────────────────────────── */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 px-4 py-3">
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            resetPage();
          }}
          aria-label="Status"
          data-testid="runs-status-filter"
          className={cn(controlClass, 'w-auto')}
        >
          <option value="all">All statuses</option>
          {JOB_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <select
          value={jobType}
          onChange={(e) => {
            setJobType(e.target.value);
            resetPage();
          }}
          aria-label="Job type"
          data-testid="runs-type-filter"
          className={cn(controlClass, 'w-auto')}
        >
          <option value="all">All {JOB_TYPES.length} job types</option>
          {JOB_TYPES.map((t) => (
            <option key={t} value={t}>
              {t} · {hasWorkerHandler(t) ? 'worker' : 'in-request'}
            </option>
          ))}
        </select>

        <div className="relative w-56">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
            placeholder={`Filter these ${loaded.length} rows…`}
            aria-label="Filter loaded rows"
            data-testid="runs-search"
            className={cn(controlClass, 'pl-7')}
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Footnote data-testid="runs-range">
            {total === 0
              ? 'no runs'
              : `${rangeStart.toLocaleString()}–${rangeEnd.toLocaleString()} of ${total.toLocaleString()} · newest first`}
            {needle.trim() ? ` · ${rows.length} match this page` : ''}
          </Footnote>
          <PagerButton
            direction="prev"
            testid="runs-pager-prev"
            disabled={offset === 0 || jobs.isFetching}
            onClick={() => setOffset(Math.max(0, offset - JOBS_PAGE_LIMIT))}
          />
          <PagerButton
            direction="next"
            testid="runs-pager-next"
            disabled={rangeEnd >= total || jobs.isFetching}
            onClick={() => setOffset(offset + JOBS_PAGE_LIMIT)}
          />
        </div>
      </div>

      {/* ── the list, and the run it is showing ────────────────────────── */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-h-0 flex-col">
          <Table data-testid="runs-table" containerClassName="min-h-0 flex-1">
            <TableHeader>
              <TableRow>
                {['run', 'type', 'mode', 'dataset', 'status', 'progress'].map((h) => (
                  <TableHead key={h} className="font-mono lowercase">
                    {h}
                  </TableHead>
                ))}
                <TableHead className="font-mono lowercase">started</TableHead>
                <TableHead className="text-right font-mono lowercase">dur</TableHead>
                <TableHead className="text-right font-mono lowercase">rows</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((job) => {
                const s = statusOf(job);
                const chosen = job.id === selectedId;
                const name = job.dataset_id ? datasetNames.get(job.dataset_id) : undefined;
                const count = jobRowCount(job);
                return (
                  <TableRow
                    key={job.id}
                    data-testid={`runs-row-${job.id}`}
                    data-selected={chosen ? '' : undefined}
                    tabIndex={0}
                    onClick={() => setSelectedId(job.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedId(job.id);
                      }
                    }}
                    className={cn(
                      'cursor-pointer',
                      // ACCENT 2/3 — scope: this is the run the dock is showing.
                      chosen && 'bg-[var(--sig-dim)] shadow-[inset_2px_0_0_var(--sig)]',
                    )}
                  >
                    <TableCell>
                      <Identifier
                        className={cn(
                          'text-small',
                          chosen ? 'font-semibold text-[var(--sig)]' : 'text-muted-foreground',
                        )}
                      >
                        {shortId(job.id)}
                      </Identifier>
                    </TableCell>
                    <TableCell>
                      <Identifier className="text-small text-foreground">{job.job_type}</Identifier>
                    </TableCell>
                    <TableCell>
                      <ModeWord jobType={job.job_type} />
                    </TableCell>
                    <TableCell className="max-w-[190px] truncate">
                      {name ? (
                        <span className="text-small text-foreground">{name}</span>
                      ) : (
                        <Identifier className="text-small text-muted-foreground">
                          {shortId(job.dataset_id)}
                        </Identifier>
                      )}
                    </TableCell>
                    <TableCell>
                      <Status kind={s.kind} className="text-small">
                        {s.word}
                      </Status>
                    </TableCell>
                    <TableCell>
                      {job.progress == null ? (
                        <span className="text-small text-muted-foreground">—</span>
                      ) : (
                        <div className="flex items-center gap-2">
                          <MagnitudeBar
                            of={coverage(job.progress, 100)}
                            className="w-14"
                            aria-label={`${job.progress} percent`}
                          />
                          <Identifier className="text-micro text-muted-foreground">
                            {job.progress}%
                          </Identifier>
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Identifier className="text-small text-muted-foreground">
                        {formatClock(job.started_at ?? job.created_at)}
                      </Identifier>
                    </TableCell>
                    <TableNumericCell>
                      <Identifier className="text-small text-foreground">
                        {formatDuration(jobDurationMs(job, nowMs))}
                      </Identifier>
                    </TableNumericCell>
                    <TableNumericCell>
                      <Identifier className="text-small text-muted-foreground">
                        {count === null ? '—' : compact(count)}
                      </Identifier>
                    </TableNumericCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          <div className="shrink-0 px-4 py-2">
            {jobs.isError ? (
              <Footnote data-testid="runs-error">{errorText(jobs.error)}</Footnote>
            ) : rows.length === 0 ? (
              <Footnote data-testid="runs-empty">
                {jobs.isPending
                  ? 'Loading runs…'
                  : needle.trim()
                    ? 'Nothing on this page matches. The filter only narrows the loaded rows — /jobs has no search parameter.'
                    : 'No runs recorded for these filters.'}
              </Footnote>
            ) : (
              <Footnote>
                Row counts are read from each job&rsquo;s own result payload; only some job types
                record one. Ordering is the server&rsquo;s — /jobs has no sort parameter.
              </Footnote>
            )}
          </div>
        </div>

        {/* ── detail dock ──────────────────────────────────────────────── */}
        <aside
          data-testid="runs-detail"
          className="flex min-h-0 flex-col bg-card shadow-[-1px_0_0_rgba(0,0,0,.45)]"
        >
          <div className="flex h-[38px] shrink-0 items-center gap-2 px-3 shadow-[inset_0_-1px_0_var(--r1)]">
            <SectionTitle>Run detail</SectionTitle>
            {selected ? (
              // ACCENT 3/3 — scope: the id of the run this dock is showing.
              <Identifier className="text-small font-semibold text-[var(--sig)]">
                {shortId(selected.id)}
              </Identifier>
            ) : null}
            {selectedId ? (
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                aria-label="Close run detail"
                data-testid="runs-detail-close"
                className="ml-auto flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {!selectedId ? (
              <Footnote data-testid="runs-detail-empty">
                Select a run to see its timings, result payload and recorded error.
              </Footnote>
            ) : detail.isError && !selected ? (
              <Footnote data-testid="runs-detail-error">
                {errorText(detail.error, {
                  notFound: 'That run is not available to this seat.',
                })}
              </Footnote>
            ) : !selected ? (
              <Footnote>Loading run…</Footnote>
            ) : (
              <div className="flex flex-col gap-4">
                <div>
                  <Identifier className="text-label font-semibold text-foreground">
                    {selected.job_type}
                  </Identifier>
                  <div className="mt-1.5 flex flex-wrap items-center gap-3">
                    <Status kind={statusOf(selected).kind} className="text-body">
                      {statusOf(selected).word}
                    </Status>
                    <ModeWord jobType={selected.job_type} className="text-small" />
                    <Identifier className="text-small text-foreground">
                      {formatDuration(jobDurationMs(selected, nowMs))}
                    </Identifier>
                  </div>
                </div>

                {/* A recessed well — an identifier is something you copy out. */}
                <div className="flex items-center gap-2 rounded-md bg-[var(--s0)] px-2.5 py-1.5 shadow-[inset_0_1px_2px_rgba(0,0,0,.5)]">
                  <span className="font-mono text-footnote text-muted-foreground">job_id</span>
                  <Identifier
                    data-testid="runs-detail-id"
                    className="min-w-0 truncate text-small font-semibold text-foreground"
                  >
                    {selected.id}
                  </Identifier>
                </div>

                <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-3 gap-y-1.5">
                  <DetailRow label="Team">{selected.team_id ?? '—'}</DetailRow>
                  <DetailRow label="Dataset">
                    {selected.dataset_id
                      ? (datasetNames.get(selected.dataset_id) ?? selected.dataset_id)
                      : '—'}
                  </DetailRow>
                  <DetailRow label="Version">{selected.dataset_version_id ?? '—'}</DetailRow>
                  <DetailRow label="Created">{formatStamp(selected.created_at)}</DetailRow>
                  <DetailRow label="Started">{formatStamp(selected.started_at)}</DetailRow>
                  <DetailRow label="Completed">{formatStamp(selected.completed_at)}</DetailRow>
                </div>

                {selected.progress != null && (
                  <div>
                    <div className="mb-1.5 flex items-baseline justify-between">
                      <span className="text-small text-muted-foreground">Progress</span>
                      <Identifier className="text-small text-foreground">
                        {selected.progress}%
                      </Identifier>
                    </div>
                    <MagnitudeBar of={coverage(selected.progress, 100)} />
                  </div>
                )}

                {selected.error ? (
                  <div data-testid="runs-detail-error-payload">
                    <SectionTitle className="mb-1.5">Error</SectionTitle>
                    <div className="rounded-md bg-[var(--s0)] p-2.5 font-mono text-small break-words whitespace-pre-wrap text-foreground shadow-[inset_0_2px_5px_-2px_rgba(0,0,0,.7)]">
                      {selected.error}
                    </div>
                    <Note>
                      This is the free text the handler recorded on the row. The machine-readable{' '}
                      <span className="font-mono text-micro text-foreground">code</span> lives on the
                      problem+json response the caller got, and is not stored here — so nothing on
                      this screen can branch on it.
                    </Note>
                  </div>
                ) : null}

                {selected.result ? (
                  <div data-testid="runs-detail-result">
                    <SectionTitle className="mb-1.5">Result</SectionTitle>
                    <JsonViewer value={selected.result} maxHeight="220px" />
                  </div>
                ) : null}

                <div className="flex flex-col gap-2">
                  <Note testid="runs-detail-mode-note">
                    {hasWorkerHandler(selected.job_type) ? (
                      <>
                        <span className="font-mono text-micro text-foreground">
                          {selected.job_type}
                        </span>{' '}
                        has a registered worker handler, so a{' '}
                        <span className="text-foreground">pending</span> row of this type is waiting
                        for the loop to claim it. The same handler runs in-request when the caller
                        passes <span className="font-mono text-micro">sync=true</span>.
                      </>
                    ) : (
                      <>
                        <span className="font-mono text-micro text-foreground">
                          {selected.job_type}
                        </span>{' '}
                        has no registered worker handler. This ran inside the API request that
                        created it; the row is an execution record, and nothing will pick it up.
                      </>
                    )}
                  </Note>
                  <Note>
                    The stored <span className="font-mono text-micro">parameters</span> of a job are
                    not part of <span className="font-mono text-micro">JobOut</span>, so this dock
                    cannot show what the run was asked to do — only what it recorded.
                  </Note>
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
