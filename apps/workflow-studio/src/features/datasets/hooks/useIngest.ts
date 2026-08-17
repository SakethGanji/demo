/**
 * Ingest — the upload queue, both transports, driven directly against the API.
 *
 * There are genuinely two upload paths in the service and this hook drives both
 * for real:
 *
 *   multipart  POST /upload?sync=true          one request, capped at 1 GB
 *   resumable  POST /tus/ → PATCH /tus/{id}    tus 1.0.0, capped at 100 GB
 *
 * The resumable path is not decoration. `PATCH` answers with `Upload-Offset`,
 * `HEAD` re-reads the server's authoritative offset, and both headers are on
 * the service's CORS `expose_headers` list — so a browser can actually read
 * them and actually resume. Pause aborts the in-flight chunk; resume asks the
 * server where it got to and continues from exactly there. Nothing here
 * simulates progress: every byte count in this file was either acknowledged by
 * the server (tus) or reported by the socket (multipart, via XHR — `fetch` has
 * no upload-progress event, which is the only reason XHR is here).
 *
 * Two honesty notes that shape the API of this hook:
 *
 *  - Upload is UNGATED. Nothing is validated, profiled or PII-scanned on the
 *    way in. `runProfile` exists because profiling is a separate, explicit
 *    call — a fresh version has no profile at all until someone asks for one.
 *
 *  - A multi-sheet workbook is NOT held at upload. The service ingests every
 *    sheet by default; `include_sheets` is a comma-separated *opt-in* filter,
 *    and naming a sheet the workbook lacks fails the whole upload with
 *    `sheet-not-found`. (The "name a sheet or I refuse" contract is a READ-time
 *    rule, not an ingest-time one.) The UI must not imply a gate that the
 *    server does not have.
 */

import { useCallback, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ANALYTICS_BASE, analytics, errorText } from '@/shared/lib/analyticsClient';
import { getIdentityHeaders, useIdentityStore } from '@/shared/lib/identity';
import type { components } from '@/shared/lib/analyticsSchema';

export type UploadResponse = components['schemas']['UploadResponse'];
export type ColumnInfo = components['schemas']['ColumnInfo'];
export type ProfileRun = components['schemas']['ProfileRunOut'];

/** `app/shared/constants.py: ALLOWED_EXTENSIONS` — enforced server-side too. */
export const ACCEPTED_EXTENSIONS = ['.csv', '.parquet', '.xlsx', '.xls'] as const;
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(',');

/** `settings.max_upload_bytes` — over this, `POST /upload` answers 413. */
export const MULTIPART_MAX_BYTES = 1024 * 1024 * 1024;
/** `TUS_MAX_SIZE` — over this, `POST /tus/` answers 413. */
export const TUS_MAX_BYTES = 100 * 1024 * 1024 * 1024;
/** Where we stop asking a single request to survive the whole file. */
export const RESUMABLE_THRESHOLD_BYTES = 100 * 1024 * 1024;
/** One PATCH body. Small enough that a drop costs little, big enough to be cheap. */
export const CHUNK_BYTES = 8 * 1024 * 1024;

const TUS_VERSION = '1.0.0';
/** How many times one chunk may be re-driven from the server's offset. */
const MAX_CHUNK_ATTEMPTS = 4;

export type Transport = 'multipart' | 'resumable';

/**
 * `queued → uploading → processing → ready | failed`, plus the two states only
 * the resumable path can reach (`paused`, `cancelled`).
 */
export type IngestPhase =
  | 'queued'
  | 'uploading'
  | 'paused'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'cancelled';

export interface IngestItem {
  /** Client-side id. Not the upload id — that only exists once tus has created one. */
  readonly id: string;
  readonly file: File;
  readonly transport: Transport;
  readonly phase: IngestPhase;
  /** Bytes the server has acknowledged (tus) or the socket has flushed (multipart). */
  readonly sent: number;
  readonly total: number;
  /** Bytes/second over the last acknowledged chunk. Null until one lands. */
  readonly rate: number | null;
  /** tus upload id, from the `Location` header of `POST /tus/`. */
  readonly uploadId: string | null;
  readonly datasetId: string | null;
  readonly versionId: string | null;
  readonly rowCount: number | null;
  readonly columnCount: number | null;
  readonly columns: readonly ColumnInfo[] | null;
  /** Set when the transfer restarted from a server-confirmed offset. The receipt. */
  readonly resumedFrom: number | null;
  /** Dropped connections survived. Each one cost zero re-sent bytes. */
  readonly interruptions: number;
  readonly error: string | null;
  /** problem+json `code`, or the upload response's `error_kind`. Branch on this. */
  readonly errorCode: string | null;
  readonly message: string | null;
  readonly includeSheets: string | null;
  /** True when this upload appended a version rather than creating a dataset. */
  readonly asNewVersion: boolean;
  readonly startedAt: number | null;
  readonly finishedAt: number | null;
}

export interface RejectedFile {
  readonly id: string;
  readonly name: string;
  readonly reason: string;
}

/** A file that never became an item — rejected before a single byte was sent. */
function rejectionReason(file: File): string | null {
  const dot = file.name.lastIndexOf('.');
  const ext = dot === -1 ? '' : file.name.slice(dot).toLowerCase();
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `Unsupported type ${ext || '(none)'} — accepted: ${ACCEPTED_EXTENSIONS.join(' ')}`;
  }
  if (file.size > TUS_MAX_BYTES) {
    return 'Larger than the 100 GB service maximum';
  }
  return null;
}

/** Which transport a file of this size can actually use. */
export function defaultTransport(size: number): Transport {
  return size > RESUMABLE_THRESHOLD_BYTES ? 'resumable' : 'multipart';
}

/** Over the 1 GB single-shot cap the choice is not a choice. */
export function requiresResumable(size: number): boolean {
  return size > MULTIPART_MAX_BYTES;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** A problem+json failure from a hand-rolled request. `code`, never prose. */
class IngestError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'IngestError';
    this.status = status;
    this.code = code;
  }
}

/** Pause and cancel abort the in-flight request; that is not a failure. */
class Interrupted extends Error {
  constructor() {
    super('interrupted');
    this.name = 'Interrupted';
  }
}

function isInterrupted(e: unknown): boolean {
  return e instanceof Interrupted || (e instanceof DOMException && e.name === 'AbortError');
}

function problemFromBody(status: number, raw: string): IngestError {
  try {
    const body = JSON.parse(raw) as Record<string, unknown>;
    const code = typeof body.code === 'string' ? body.code : 'error';
    const detail =
      (typeof body.detail === 'string' && body.detail) ||
      (typeof body.title === 'string' && body.title) ||
      raw ||
      `Request failed (${status})`;
    return new IngestError(status, code, detail);
  } catch {
    return new IngestError(status, 'error', raw || `Request failed (${status})`);
  }
}

async function problemFromResponse(res: Response): Promise<IngestError> {
  return problemFromBody(res.status, await res.text().catch(() => ''));
}

/** `Upload-Metadata` values are base64 of UTF-8, not of UTF-16 code units. */
function encodeMetadataValue(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function tusHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { ...getIdentityHeaders(), 'Tus-Resumable': TUS_VERSION, ...extra };
}

// ---------------------------------------------------------------------------
// Per-item control channel — what the running transfer loop watches
// ---------------------------------------------------------------------------

interface Control {
  paused: boolean;
  cancelled: boolean;
  running: boolean;
  abort: AbortController | null;
  xhr: XMLHttpRequest | null;
}

function newControl(): Control {
  return { paused: false, cancelled: false, running: false, abort: null, xhr: null };
}

// ---------------------------------------------------------------------------
// Transport: multipart (XHR, because fetch has no upload progress)
// ---------------------------------------------------------------------------

interface MultipartArgs {
  file: File;
  datasetId: string | null;
  includeSheets: string | null;
  control: Control;
  onProgress: (sent: number) => void;
}

function multipartUpload({
  file,
  datasetId,
  includeSheets,
  control,
  onProgress,
}: MultipartArgs): Promise<UploadResponse> {
  return new Promise<UploadResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    control.xhr = xhr;
    // sync=true is the default, but the whole point of this path is that the id
    // comes back inline and is queryable at once, so say it out loud.
    xhr.open('POST', `${ANALYTICS_BASE}/upload?sync=true`);
    for (const [key, value] of Object.entries(getIdentityHeaders())) {
      xhr.setRequestHeader(key, value);
    }

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      // `loaded` counts multipart framing too, so it can overshoot the file.
      onProgress(Math.min(event.loaded, file.size));
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResponse);
        } catch {
          reject(new IngestError(xhr.status, 'error', 'Upload succeeded but the reply was unreadable.'));
        }
        return;
      }
      reject(problemFromBody(xhr.status, xhr.responseText));
    };
    xhr.onerror = () =>
      reject(new IngestError(0, 'network-error', 'The connection dropped. This path cannot resume — retry sends the file again.'));
    xhr.onabort = () => reject(new Interrupted());

    const form = new FormData();
    form.append('file', file);
    if (datasetId) form.append('dataset_id', datasetId);
    if (includeSheets) form.append('include_sheets', includeSheets);
    xhr.send(form);
  });
}

// ---------------------------------------------------------------------------
// Transport: tus 1.0.0
// ---------------------------------------------------------------------------

interface TusCreated {
  uploadId: string;
}

async function tusCreate(
  file: File,
  datasetId: string | null,
  includeSheets: string | null,
): Promise<TusCreated> {
  const metadata: string[] = [`filename ${encodeMetadataValue(file.name)}`];
  if (datasetId) metadata.push(`dataset_id ${encodeMetadataValue(datasetId)}`);
  // Carried through to ingest, so a workbook too big for the single-shot path
  // can still select sheets.
  if (includeSheets) metadata.push(`include_sheets ${encodeMetadataValue(includeSheets)}`);

  const res = await fetch(`${ANALYTICS_BASE}/tus/`, {
    method: 'POST',
    headers: tusHeaders({
      'Upload-Length': String(file.size),
      'Upload-Metadata': metadata.join(','),
    }),
  });
  if (!res.ok) throw await problemFromResponse(res);

  const location = res.headers.get('Location');
  if (!location) {
    // Exposed via CORS `expose_headers`; if it is missing, resuming is impossible
    // and pretending otherwise would be the lie this screen exists to avoid.
    throw new IngestError(res.status, 'missing-location', 'The server did not return an upload Location.');
  }
  const uploadId = location.replace(/\/+$/, '').split('/').pop() ?? '';
  if (!uploadId) throw new IngestError(res.status, 'missing-location', 'The upload Location was unreadable.');
  return { uploadId };
}

/** The server's authoritative offset. The only thing a resume may trust. */
async function tusOffset(uploadId: string): Promise<number> {
  const res = await fetch(`${ANALYTICS_BASE}/tus/${uploadId}`, {
    method: 'HEAD',
    headers: tusHeaders(),
  });
  if (!res.ok) throw await problemFromResponse(res);
  return Number(res.headers.get('Upload-Offset') ?? '0');
}

async function tusTerminate(uploadId: string): Promise<void> {
  await fetch(`${ANALYTICS_BASE}/tus/${uploadId}`, {
    method: 'DELETE',
    headers: tusHeaders(),
  });
}

// ---------------------------------------------------------------------------
// The hook
// ---------------------------------------------------------------------------

export interface EnqueueOptions {
  datasetId: string | null;
  includeSheets: string | null;
  transport?: Transport;
}

let seq = 0;
const nextId = () => `ingest-${++seq}`;

export function useIngest() {
  const [items, setItems] = useState<readonly IngestItem[]>([]);
  const [rejected, setRejected] = useState<readonly RejectedFile[]>([]);
  const controls = useRef(new Map<string, Control>());
  const snapshot = useRef(new Map<string, IngestItem>());
  const qc = useQueryClient();
  const seat = useIdentityStore((s) => s.identity.userId);

  /** Everything the ingest touched is stale afterwards — the catalog most of all. */
  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['analytics', seat] });
  }, [qc, seat]);

  // The snapshot ref is what the async transfer loops read: they run outside
  // React's render cycle and must never see a stale closure. It is written here
  // rather than inside the state updater, which StrictMode may run twice.
  const patch = useCallback((id: string, next: Partial<IngestItem>) => {
    const current = snapshot.current.get(id);
    if (!current) return;
    const merged: IngestItem = { ...current, ...next };
    snapshot.current.set(id, merged);
    setItems((prev) => prev.map((item) => (item.id === id ? merged : item)));
  }, []);

  const enqueue = useCallback(
    (files: readonly File[], options: EnqueueOptions): IngestItem[] => {
      const accepted: IngestItem[] = [];
      const refused: RejectedFile[] = [];

      for (const file of files) {
        const reason = rejectionReason(file);
        if (reason) {
          refused.push({ id: nextId(), name: file.name, reason });
          continue;
        }
        const transport =
          requiresResumable(file.size) ? 'resumable' : options.transport ?? defaultTransport(file.size);
        const item: IngestItem = {
          id: nextId(),
          file,
          transport,
          phase: 'queued',
          sent: 0,
          total: file.size,
          rate: null,
          uploadId: null,
          datasetId: options.datasetId,
          versionId: null,
          rowCount: null,
          columnCount: null,
          columns: null,
          resumedFrom: null,
          interruptions: 0,
          error: null,
          errorCode: null,
          message: null,
          includeSheets: options.includeSheets,
          asNewVersion: Boolean(options.datasetId),
          startedAt: null,
          finishedAt: null,
        };
        accepted.push(item);
        snapshot.current.set(item.id, item);
        controls.current.set(item.id, newControl());
      }

      if (accepted.length) setItems((prev) => [...accepted, ...prev]);
      if (refused.length) setRejected((prev) => [...refused, ...prev]);
      return accepted;
    },
    [],
  );

  /** Apply a finished `UploadResponse`, including the partial-failure fields. */
  const settle = useCallback(
    (id: string, res: UploadResponse) => {
      const failed = res.status === 'error' || Boolean(res.error);
      patch(id, {
        phase: failed ? 'failed' : 'ready',
        datasetId: res.dataset_id || null,
        versionId: res.version_id ?? null,
        rowCount: res.row_count ?? null,
        columnCount: res.column_count ?? null,
        columns: res.columns ?? null,
        error: res.error ?? null,
        errorCode: res.error_kind ?? null,
        message: res.message ?? null,
        finishedAt: Date.now(),
      });
      if (failed) {
        toast.error(res.error ?? 'Ingest failed.');
      } else {
        toast.success(
          res.row_count != null
            ? `Ingested ${res.row_count.toLocaleString()} rows.`
            : 'Ingest complete.',
        );
      }
      invalidate();
    },
    [invalidate, patch],
  );

  /**
   * Poll the tus status endpoint until the background ingest stops moving.
   *
   * The bytes landing is not the end of the story on this path: the final PATCH
   * only queues processing. Both `uploading` and `uploaded` mean "not done yet".
   */
  const awaitProcessing = useCallback(
    async (id: string, uploadId: string, control: Control) => {
      for (let attempt = 0; attempt < 600; attempt++) {
        if (control.cancelled) return;
        const res = await analytics.get<UploadResponse>(`/tus/${uploadId}/status`);
        if (res.status !== 'uploading' && res.status !== 'uploaded' && res.status !== 'processing') {
          settle(id, res);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      patch(id, {
        phase: 'failed',
        error: 'Still processing after 10 minutes — check the dataset directly.',
        errorCode: 'processing-timeout',
        finishedAt: Date.now(),
      });
    },
    [patch, settle],
  );

  /** Drive one resumable transfer from wherever the server says it is. */
  const runResumable = useCallback(
    async (id: string, control: Control) => {
      const start = snapshot.current.get(id);
      if (!start) return;

      let uploadId = start.uploadId;
      if (!uploadId) {
        const created = await tusCreate(start.file, start.datasetId, start.includeSheets);
        uploadId = created.uploadId;
        patch(id, { uploadId });
      }

      // Never trust the local byte count across a pause or a drop; ask.
      let offset = await tusOffset(uploadId);
      if (offset !== start.sent) {
        patch(id, { sent: offset, resumedFrom: offset });
      } else if (start.sent > 0) {
        patch(id, { resumedFrom: offset });
      }

      const total = start.total;
      let attempts = 0;

      while (offset < total) {
        if (control.cancelled) throw new Interrupted();
        if (control.paused) throw new Interrupted();

        const end = Math.min(offset + CHUNK_BYTES, total);
        const abort = new AbortController();
        control.abort = abort;
        const chunkStartedAt = performance.now();

        try {
          const res = await fetch(`${ANALYTICS_BASE}/tus/${uploadId}`, {
            method: 'PATCH',
            headers: tusHeaders({
              'Content-Type': 'application/offset+octet-stream',
              'Upload-Offset': String(offset),
            }),
            body: start.file.slice(offset, end),
            signal: abort.signal,
          });
          if (!res.ok) throw await problemFromResponse(res);

          const acknowledged = Number(res.headers.get('Upload-Offset') ?? String(end));
          const elapsed = Math.max(performance.now() - chunkStartedAt, 1) / 1000;
          const moved = acknowledged - offset;
          offset = acknowledged;
          attempts = 0;
          patch(id, { sent: offset, rate: moved > 0 ? moved / elapsed : null });
        } catch (e) {
          if (control.paused || control.cancelled) throw new Interrupted();
          // 409 offset-mismatch and 423 locked are both "re-read the offset and
          // carry on", not "start again" — that is the whole point of tus.
          const retryable =
            e instanceof IngestError
              ? e.status === 0 || e.status === 409 || e.status === 423 || e.status >= 500
              : e instanceof TypeError; // fetch's network failure
          if (!retryable || ++attempts >= MAX_CHUNK_ATTEMPTS) throw e;

          await new Promise((resolve) => setTimeout(resolve, 400 * attempts));
          // The receipt: the server says where it got to, and we continue from
          // exactly there. No byte already acknowledged is ever sent twice.
          const confirmed = await tusOffset(uploadId);
          patch(id, {
            sent: confirmed,
            resumedFrom: confirmed,
            interruptions: (snapshot.current.get(id)?.interruptions ?? 0) + 1,
          });
          offset = confirmed;
        }
      }

      control.abort = null;
      patch(id, { phase: 'processing' });
      await awaitProcessing(id, uploadId, control);
    },
    [awaitProcessing, patch],
  );

  const drive = useCallback(
    async (id: string) => {
      const control = controls.current.get(id);
      const item = snapshot.current.get(id);
      if (!control || !item || control.running) return;

      control.running = true;
      control.paused = false;
      patch(id, {
        phase: 'uploading',
        error: null,
        errorCode: null,
        startedAt: item.startedAt ?? Date.now(),
      });

      try {
        if (item.transport === 'resumable') {
          await runResumable(id, control);
        } else {
          const res = await multipartUpload({
            file: item.file,
            datasetId: item.datasetId,
            includeSheets: item.includeSheets,
            control,
            onProgress: (sent) => {
              patch(id, { sent, phase: sent >= item.total ? 'processing' : 'uploading' });
            },
          });
          settle(id, res);
        }
      } catch (e) {
        if (isInterrupted(e)) {
          patch(id, { phase: control.cancelled ? 'cancelled' : 'paused' });
        } else {
          const code = e instanceof IngestError ? e.code : 'error';
          patch(id, {
            phase: 'failed',
            error: e instanceof IngestError ? e.message : errorText(e),
            errorCode: code,
            finishedAt: Date.now(),
          });
          toast.error(e instanceof IngestError ? e.message : errorText(e));
          // A failed upload still leaves a dataset and a numbered version behind;
          // the catalog must show that rather than hide it.
          invalidate();
        }
      } finally {
        control.running = false;
        control.abort = null;
        control.xhr = null;
      }
    },
    [invalidate, patch, runResumable, settle],
  );

  const start = useCallback(
    (ids: readonly string[]) => {
      for (const id of ids) void drive(id);
    },
    [drive],
  );

  /** Pause is resumable-only, and the button says so rather than lying. */
  const pause = useCallback((id: string) => {
    const control = controls.current.get(id);
    if (!control) return;
    control.paused = true;
    control.abort?.abort();
  }, []);

  const resume = useCallback(
    (id: string) => {
      const control = controls.current.get(id);
      if (!control) return;
      control.paused = false;
      void drive(id);
    },
    [drive],
  );

  /**
   * Terminate. `DELETE /tus/{id}` drops the staging bytes but deliberately
   * KEEPS the dataset and its version number — the documented retry is to
   * upload again onto the same dataset, which becomes the next version.
   */
  const cancel = useCallback(
    (id: string) => {
      const control = controls.current.get(id);
      const item = snapshot.current.get(id);
      if (control) {
        control.cancelled = true;
        control.paused = false;
        control.abort?.abort();
        control.xhr?.abort();
      }
      if (item?.uploadId) void tusTerminate(item.uploadId);
      patch(id, { phase: 'cancelled', finishedAt: Date.now() });
      invalidate();
    },
    [invalidate, patch],
  );

  const remove = useCallback((id: string) => {
    controls.current.delete(id);
    snapshot.current.delete(id);
    setItems((prev) => prev.filter((entry) => entry.id !== id));
  }, []);

  const dismissRejected = useCallback((id: string) => {
    setRejected((prev) => prev.filter((entry) => entry.id !== id));
  }, []);

  /**
   * Profiling never runs on upload. This is the explicit ask, and it is the
   * only way any health dimension for the new version stops reading "unknown".
   */
  const runProfile = useCallback(
    async (datasetId: string, versionNumber: number) => {
      try {
        await analytics.post<ProfileRun[]>(
          `/datasets/${datasetId}/versions/${versionNumber}/profile-runs`,
        );
        toast.success('Profile run started.');
        invalidate();
      } catch (e) {
        toast.error(errorText(e));
      }
    },
    [invalidate],
  );

  return {
    items,
    rejected,
    enqueue,
    start,
    pause,
    resume,
    cancel,
    remove,
    dismissRejected,
    runProfile,
  };
}
