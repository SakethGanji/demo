// F-JOBS: the long writes (an edit commit, a Data Doctor apply) above the API's row threshold answer 202 with a
// job id instead of the version. These helpers poll GET /api/jobs/{id} until the job ends and hand back the body the
// synchronous route would have returned, so callers keep one code path.
import { ApiError, request } from './client'

export type JobProgress = { pct: number; phase: string; at?: string }
export type Job = {
  id: string; kind: string; status: 'queued' | 'leased' | 'succeeded' | 'failed' | 'cancelled'; attempts: number
  progress?: JobProgress | null; result?: unknown; error?: string | null
}
/** The 202 body: {async: true, job_id, poll, rows, threshold_rows, note}. */
export type Accepted = { async: true; job_id: string; poll: string; rows: number; threshold_rows: number; note: string }

export const isAccepted = (r: unknown): r is Accepted =>
  !!r && typeof r === 'object' && (r as { async?: unknown }).async === true && typeof (r as { job_id?: unknown }).job_id === 'string'

export const getJob = (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`, { timeoutMs: 15_000 })

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Poll a job (every second) until it ends. Resolves to its result; a refusal (the 4xx the route would have sent)
 *  or a failure rejects with an ApiError, like a synchronous call. `onProgress` sees {pct 0–1, phase}. */
export async function waitJob<T>(id: string, onProgress?: (p: JobProgress, j: Job) => void, maxMs = 3_600_000): Promise<T> {
  const t0 = Date.now()
  let misses = 0
  for (;;) {
    let j: Job
    try {
      j = await getJob(id)
      misses = 0
    } catch (e) {
      // the API restarting: keep polling for a while (the job itself is on the durable queue)
      if (e instanceof ApiError && e.unreachable && ++misses < 30) { await sleep(2000); continue }
      throw e
    }
    if (j.status === 'succeeded') { onProgress?.({ pct: 1, phase: 'done' }, j); return j.result as T }
    if (j.status === 'failed' || j.status === 'cancelled') {
      const ref = (j.result as { refused?: { status: number; detail: unknown; message: string } } | null)?.refused
      if (ref) throw new ApiError(ref.message || `Refused (${ref.status}).`, ref.status, ref.detail)
      throw new ApiError(j.status === 'cancelled' ? 'The job was cancelled.' : `The job failed: ${j.error ?? 'no detail'}`, 500)
    }
    onProgress?.(j.progress ?? { pct: 0, phase: j.status === 'queued' ? 'queued' : 'started' }, j)
    if (Date.now() - t0 > maxMs) throw new ApiError('The job is still running; check it later under Jobs.', 0)
    await sleep(1000)
  }
}

/** A route that may answer 202 + a job: wait for the job when it does. */
export async function settle<T>(p: Promise<T | Accepted>, onProgress?: (p: JobProgress, j: Job) => void): Promise<T> {
  const r = await p
  return isAccepted(r) ? waitJob<T>(r.job_id, onProgress) : r
}

export const pctText = (p: JobProgress | null | undefined) =>
  p ? `${p.phase} ${Math.round(p.pct * 100)} %` : ''
