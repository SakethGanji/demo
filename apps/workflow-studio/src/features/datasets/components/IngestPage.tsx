/**
 * Ingest — the upload surface, both transports, told honestly.
 *
 * Three things this screen refuses to imply:
 *
 *  1. That anything is validated on the way in. Upload is UNGATED. The only
 *     quality gate in the platform is tag promotion; a warning never blocked an
 *     upload and never will. So there is no "checks passed" anywhere here.
 *  2. That a profile exists afterwards. Profiling is never triggered by an
 *     upload, so a fresh version's health reads `unknown` — an absence of
 *     measurement, not a bad score. The result offers the explicit run.
 *  3. That sheet selection is a gate. The service ingests every sheet of a
 *     workbook by default; `include_sheets` is an opt-in filter. Naming a sheet
 *     the file lacks fails the upload with `sheet-not-found`.
 *
 * The resumable path is real: `Upload-Offset` comes back from the server on
 * every PATCH and from `HEAD` on every resume, so the byte figures on this page
 * are server-confirmed rather than optimistic. See `useIngest`.
 *
 * The fourth thing it refuses to imply is about the OTHER write on this page.
 * `POST /datasets/{id}/sheets/{sheet}/replace` is named after an edit and is not
 * one: it creates a new version whose other sheets point at the base version's
 * existing parquet files. Immutability is intact, and the rail says so in those
 * words — see `useSheetReplace` for the handler reading it comes from.
 */

import { useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  AlertTriangle,
  ArrowRight,
  FileUp,
  Pause,
  Play,
  Replace,
  Search,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog';
import {
  Eyebrow,
  Figure,
  Footnote,
  Identifier,
  SectionTitle,
} from '@/shared/components/instrument/Typography';
import { Status } from '@/shared/components/instrument/Status';
import type { StatusKind } from '@/shared/components/instrument/Status';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat } from '@/shared/components/instrument/Stat';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage } from '@/shared/components/instrument/coverage';
import { compact, formatBytes, num } from '@/shared/lib/format';
import { errorText } from '@/shared/lib/analyticsClient';
import { useDatasetCatalog, useSheets } from '../hooks/useDatasets';
import type { DatasetInfo } from '../hooks/useDatasets';
import {
  ACCEPTED_EXTENSIONS,
  ACCEPT_ATTR,
  MULTIPART_MAX_BYTES,
  RESUMABLE_THRESHOLD_BYTES,
  defaultTransport,
  requiresResumable,
  useIngest,
} from '../hooks/useIngest';
import type { IngestItem, IngestPhase, Transport } from '../hooks/useIngest';
import {
  REPLACE_ACCEPT_ATTR,
  REPLACE_EXTENSIONS,
  replaceRejection,
  useReplaceSheet,
} from '../hooks/useSheetReplace';
import type { SheetReplaceResult } from '../hooks/useSheetReplace';
import { DtypeChip } from './lenses/primitives';
import { controlClass } from './fieldStyles';

interface IngestPageProps {
  /** From `?dataset=<id>`. Present ⇒ this upload becomes a new VERSION of it. */
  targetDatasetId: string | null;
}

/** Status is shape + hue + word. Every phase maps to one, and the word is said. */
const PHASE_STATUS: Record<IngestPhase, { kind: StatusKind; word: string }> = {
  queued: { kind: 'unknown', word: 'queued' },
  uploading: { kind: 'unknown', word: 'uploading' },
  paused: { kind: 'unknown', word: 'paused' },
  processing: { kind: 'unknown', word: 'processing' },
  ready: { kind: 'good', word: 'ready' },
  failed: { kind: 'critical', word: 'failed' },
  cancelled: { kind: 'serious', word: 'cancelled' },
};

const TERMINAL: readonly IngestPhase[] = ['ready', 'failed', 'cancelled'];

export function IngestPage({ targetDatasetId }: IngestPageProps) {
  const navigate = useNavigate();
  const ingest = useIngest();
  const fileInput = useRef<HTMLInputElement>(null);

  const [staged, setStaged] = useState<readonly File[]>([]);
  const [asNewVersion, setAsNewVersion] = useState(Boolean(targetDatasetId));
  const [datasetId, setDatasetId] = useState<string | null>(targetDatasetId);
  const [search, setSearch] = useState('');
  const [transportChoice, setTransportChoice] = useState<Transport | null>(null);
  const [includeSheets, setIncludeSheets] = useState('');
  const [dragging, setDragging] = useState(false);

  const catalog = useDatasetCatalog();
  const datasets = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const target = datasets.find((d) => d.id === datasetId) ?? null;

  const matches = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const pool = needle ? datasets.filter((d) => d.name.toLowerCase().includes(needle)) : datasets;
    return pool.slice(0, 40);
  }, [datasets, search]);

  // Transport follows the largest staged file: it is the one that decides
  // whether a single request can carry the job at all.
  const largest = staged.reduce((max, file) => Math.max(max, file.size), 0);
  const forced = requiresResumable(largest);
  const transport: Transport = forced ? 'resumable' : transportChoice ?? defaultTransport(largest);

  const destinationReady = !asNewVersion || Boolean(datasetId);
  const canStart = staged.length > 0 && destinationReady;

  const addFiles = (files: FileList | null) => {
    if (!files?.length) return;
    setStaged((prev) => [...prev, ...Array.from(files)]);
  };

  const startIngest = () => {
    if (!canStart) return;
    const created = ingest.enqueue(staged, {
      datasetId: asNewVersion ? datasetId : null,
      includeSheets: includeSheets.trim() || null,
      transport,
    });
    ingest.start(created.map((item) => item.id));
    setStaged([]);
    if (fileInput.current) fileInput.current.value = '';
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="ingest-page">
      <header className="flex h-9 shrink-0 items-center gap-3 px-4">
        <SectionTitle className="text-figure">Ingest</SectionTitle>
        <Footnote className="mt-px">
          File upload is the only way data enters the platform.
        </Footnote>
        <span className="ml-auto font-mono text-footnote text-muted-foreground">
          POST /upload · POST /tus/
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ---------------- setup rail ---------------- */}
        <aside className="w-[352px] shrink-0 overflow-y-auto bg-[var(--surface)]/40">
          <div className="flex flex-col gap-5 p-4">
            {/* 1 · file */}
            <section>
              <Eyebrow className="mb-2">1 · File</Eyebrow>
              <div
                data-testid="ingest-drop"
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  addFiles(e.dataTransfer.files);
                }}
                onClick={() => fileInput.current?.click()}
                className={`cursor-pointer rounded-lg border border-dashed px-3 py-5 text-center transition-colors ${
                  dragging ? 'border-foreground/50 bg-muted' : 'border-border bg-black/15'
                }`}
              >
                <FileUp className="mx-auto size-5 text-muted-foreground" />
                <p className="mt-2 text-body font-medium text-foreground">
                  Drop files, or browse
                </p>
                <Footnote className="mt-1">
                  Over {formatBytes(RESUMABLE_THRESHOLD_BYTES)} switches to the resumable path.
                </Footnote>
                <div className="mt-2.5 flex flex-wrap justify-center gap-1">
                  {ACCEPTED_EXTENSIONS.map((ext) => (
                    <span
                      key={ext}
                      className="rounded bg-muted px-1.5 font-mono text-footnote text-muted-foreground"
                    >
                      {ext}
                    </span>
                  ))}
                </div>
              </div>
              <input
                ref={fileInput}
                type="file"
                multiple
                accept={ACCEPT_ATTR}
                aria-label="Data files"
                data-testid="ingest-file-input"
                className="hidden"
                onChange={(e) => addFiles(e.target.files)}
              />

              {ingest.rejected.map((entry) => (
                <div
                  key={entry.id}
                  className="mt-2 flex items-start gap-2 pl-2.5 shadow-[inset_2px_0_0_var(--st-crit)]"
                >
                  <div className="min-w-0 flex-1">
                    <Identifier className="block truncate text-small text-foreground">
                      {entry.name}
                    </Identifier>
                    <Footnote>Rejected · {entry.reason}</Footnote>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Dismiss ${entry.name}`}
                    onClick={() => ingest.dismissRejected(entry.id)}
                  >
                    <X />
                  </Button>
                </div>
              ))}

              {staged.length > 0 && (
                <ul className="mt-2.5 flex flex-col gap-1" data-testid="ingest-staged">
                  {staged.map((file, index) => (
                    <li key={`${file.name}-${index}`} className="flex items-baseline gap-2">
                      <Identifier className="min-w-0 flex-1 truncate text-small text-foreground">
                        {file.name}
                      </Identifier>
                      <Identifier className="text-footnote text-muted-foreground">
                        {formatBytes(file.size)}
                      </Identifier>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Remove ${file.name}`}
                        onClick={() => setStaged((prev) => prev.filter((_, i) => i !== index))}
                      >
                        <X />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* 2 · destination */}
            <section>
              <Eyebrow className="mb-2">2 · Destination</Eyebrow>

              <Choice
                testId="ingest-dest-new"
                selected={!asNewVersion}
                onSelect={() => setAsNewVersion(false)}
                title="Create a new dataset"
                detail={
                  <>
                    A fresh dataset starting at <Identifier>v1</Identifier>. There is no separate
                    “create dataset” call — the upload is what makes it.
                  </>
                }
              />
              <Choice
                testId="ingest-dest-version"
                selected={asNewVersion}
                onSelect={() => setAsNewVersion(true)}
                title="Add a version to an existing dataset"
                detail={
                  <>
                    Versions are <span className="text-foreground">immutable</span>, so appending a
                    new one is the only way to add data. Nothing is overwritten.
                  </>
                }
              />

              {asNewVersion && (
                <div className="mt-2 overflow-hidden rounded-lg bg-black/25 shadow-[inset_0_1px_2px_rgba(0,0,0,.5)]">
                  <div className="flex items-center gap-2 px-2.5 py-1.5">
                    <Search className="size-3 text-muted-foreground" />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search datasets"
                      aria-label="Search datasets"
                      data-testid="ingest-dataset-search"
                      className="w-full bg-transparent text-body outline-none placeholder:text-muted-foreground"
                    />
                  </div>
                  <div className="max-h-44 overflow-y-auto">
                    {matches.map((dataset) => {
                      const selected = dataset.id === datasetId;
                      return (
                        <button
                          key={dataset.id}
                          type="button"
                          data-testid={`ingest-dataset-${dataset.id}`}
                          onClick={() => setDatasetId(dataset.id)}
                          className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left ${
                            selected
                              ? 'bg-[var(--sig-dim)] shadow-[inset_3px_0_0_var(--sig)]'
                              : 'hover:bg-white/5'
                          }`}
                        >
                          <span className="min-w-0 flex-1">
                            <span
                              className={`block truncate text-body ${
                                selected ? 'font-medium text-foreground' : 'text-muted-foreground'
                              }`}
                            >
                              {dataset.name}
                            </span>
                            <Identifier className="block truncate text-footnote text-muted-foreground">
                              {dataset.id}
                            </Identifier>
                          </span>
                          <Identifier className="shrink-0 text-right text-footnote text-muted-foreground">
                            v{dataset.current_version ?? 0}
                            <br />
                            {compact(dataset.row_count)}
                          </Identifier>
                        </button>
                      );
                    })}
                    {matches.length === 0 && (
                      <Footnote className="px-2.5 py-3 text-center">
                        {catalog.isLoading ? 'Loading…' : 'No dataset matches that name.'}
                      </Footnote>
                    )}
                  </div>
                  <div className="px-2.5 py-1.5 shadow-[inset_0_1px_0_var(--r1)]">
                    <Footnote>
                      The picker holds the first 200 datasets — search rather than scroll.
                    </Footnote>
                  </div>
                </div>
              )}

              {asNewVersion && target && (
                <Footnote className="mt-2">
                  Next version of{' '}
                  <span className="text-foreground">{target.name}</span> ·{' '}
                  <Identifier>v{(target.current_version ?? 0) + 1}</Identifier>
                </Footnote>
              )}
            </section>

            {/* 3 · transport */}
            <section>
              <Eyebrow className="mb-2">3 · Transport</Eyebrow>
              <Choice
                testId="ingest-transport-multipart"
                selected={transport === 'multipart'}
                disabled={forced}
                onSelect={() => setTransportChoice('multipart')}
                title="Multipart — one request"
                detail={
                  <>
                    <Identifier>POST /upload?sync=true</Identifier> — the reply carries the dataset
                    id and it is queryable at once. Capped at {formatBytes(MULTIPART_MAX_BYTES)};
                    a dropped connection means starting over.
                  </>
                }
              />
              <Choice
                testId="ingest-transport-resumable"
                selected={transport === 'resumable'}
                onSelect={() => setTransportChoice('resumable')}
                title="Resumable — tus 1.0.0"
                detail={
                  <>
                    <Identifier>POST /tus/</Identifier> → chunked{' '}
                    <Identifier>PATCH</Identifier>. Survives a dropped connection: the server
                    confirms an offset and the transfer continues from exactly there.
                  </>
                }
              />
              {forced && (
                <Footnote className="mt-1">
                  Over {formatBytes(MULTIPART_MAX_BYTES)} the single-shot endpoint answers 413, so
                  resumable is the only path.
                </Footnote>
              )}
            </section>

            {/* 4 · sheets */}
            <section>
              <Eyebrow className="mb-2">4 · Sheets</Eyebrow>
              <input
                value={includeSheets}
                onChange={(e) => setIncludeSheets(e.target.value)}
                placeholder="Q3 Detail, Q2 Detail"
                aria-label="Sheets to include"
                data-testid="ingest-include-sheets"
                className={controlClass}
              />
              <Footnote className="mt-1.5">
                Workbooks only, and an <span className="text-foreground">opt-in</span>: leave it
                blank and every sheet is ingested. Naming a sheet the workbook does not have fails
                the whole upload with <Identifier>sheet-not-found</Identifier>. Reading a
                multi-sheet version later does require naming a sheet.
              </Footnote>
            </section>

            <div>
              <Button
                className="w-full"
                disabled={!canStart}
                onClick={startIngest}
                data-testid="ingest-start"
              >
                <Upload />
                Start ingest
              </Button>
              <Footnote className="mt-1.5">
                Nothing is validated on the way in. Quality rules and profiles run only when asked;
                the sole gate in the platform is tag promotion.
              </Footnote>
            </div>

            <SheetReplacePanel datasets={datasets} />
          </div>
        </aside>

        {/* ---------------- queue ---------------- */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto" data-testid="ingest-queue">
          <div className="flex flex-col gap-3 p-4">
            {ingest.items.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center">
                <Footnote>
                  Nothing in the queue. Staged files appear here once ingest starts, with
                  server-confirmed byte progress.
                </Footnote>
              </div>
            ) : (
              ingest.items.map((item) => (
                <QueueCard
                  key={item.id}
                  item={item}
                  onPause={() => ingest.pause(item.id)}
                  onResume={() => ingest.resume(item.id)}
                  onCancel={() => ingest.cancel(item.id)}
                  onRemove={() => ingest.remove(item.id)}
                  onProfile={(dataset, version) => void ingest.runProfile(dataset, version)}
                  onOpen={(dataset) =>
                    void navigate({ to: '/data', search: { dataset } })
                  }
                />
              ))
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

/**
 * The narrower write: swap ONE sheet's data.
 *
 * It sits under the upload flow rather than beside it because the two are not
 * peers — upload is how data normally arrives, this is the exception — and the
 * reader has to be able to tell which one they want before they pick a file.
 *
 * Everything the panel says about immutability comes from
 * `files/services/replace.py`, quoted in `useSheetReplace`: the handler creates
 * a new version and carries every other sheet across copy-on-write. The base
 * version is not written to, so tags and diffs against it are unaffected. That
 * is the sentence that has to be on screen BEFORE the button, which is why it
 * is a `Guard` here and repeated in the confirmation rather than a toast after.
 */
function SheetReplacePanel({ datasets }: { datasets: readonly DatasetInfo[] }) {
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [sheetName, setSheetName] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [confirming, setConfirming] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const replace = useReplaceSheet();

  const dataset = datasets.find((d) => d.id === datasetId) ?? null;
  // The endpoint takes no version argument — it always bases on the dataset's
  // current version, so that is the only sheet list that can be offered.
  const baseVersion = dataset?.current_version ?? null;
  const sheetsQuery = useSheets(datasetId, baseVersion);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);
  const sheet =
    sheetName && sheets.some((s) => s.name === sheetName) ? sheetName : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;

  const rejection = file ? replaceRejection(file) : null;
  const result = replace.data ?? null;

  const blocker: string | null = (() => {
    if (!datasetId) return 'Choose the dataset whose sheet is being replaced.';
    if (baseVersion == null) return 'This dataset has no version yet — upload one first.';
    if (sheetsQuery.error) return errorText(sheetsQuery.error);
    if (!sheet) return 'No sheet is listed for this version.';
    if (!file) return 'Choose the single-table file that becomes this sheet.';
    if (rejection) return rejection;
    return null;
  })();

  const clear = () => {
    setFile(null);
    if (fileInput.current) fileInput.current.value = '';
  };

  return (
    <section className="pt-4 shadow-[inset_0_1px_0_var(--r1)]" data-testid="ingest-replace">
      <Eyebrow className="mb-2">Or · replace one sheet</Eyebrow>
      <Footnote>
        Upload above adds a whole new version from a whole file. This is the narrower operation: it
        swaps the data of <span className="text-foreground">one named sheet</span> and carries every
        other sheet across untouched. Reach for upload when the file is the dataset; reach for this
        when a single tab of a workbook was re-issued.
      </Footnote>
      <span className="mt-1 block font-mono text-footnote text-muted-foreground">
        POST /datasets/{'{id}'}/sheets/{'{sheet}'}/replace
      </span>

      <div className="mt-3">
        <Identifier className="mb-1 block text-footnote text-muted-foreground">dataset</Identifier>
        <select
          value={datasetId ?? ''}
          onChange={(e) => {
            setDatasetId(e.target.value || null);
            setSheetName(null);
            replace.reset();
          }}
          className={controlClass}
          data-testid="ingest-replace-dataset"
        >
          <option value="">— choose a dataset —</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-2">
        <Identifier className="mb-1 block text-footnote text-muted-foreground">sheet</Identifier>
        <select
          value={sheet ?? ''}
          onChange={(e) => {
            setSheetName(e.target.value || null);
            replace.reset();
          }}
          disabled={sheets.length === 0}
          className={controlClass}
          data-testid="ingest-replace-sheet"
        >
          {sheets.length === 0 && <option value="">— no sheets —</option>}
          {sheets.map((s) => (
            <option key={s.sheet_key} value={s.name}>
              {s.name}
              {s.row_count != null ? ` · ${compact(s.row_count)} rows` : ''}
            </option>
          ))}
        </select>
        {dataset && baseVersion != null && (
          <Footnote className="mt-1">
            Base is <Identifier className="text-foreground">v{baseVersion}</Identifier> —{' '}
            {sheets.length} sheet{sheets.length === 1 ? '' : 's'}
            {sheetRow?.row_count != null
              ? `; ${sheet} holds ${num(sheetRow.row_count)} rows today`
              : ''}
            .
          </Footnote>
        )}
      </div>

      <div className="mt-2">
        <input
          ref={fileInput}
          type="file"
          accept={REPLACE_ACCEPT_ATTR}
          aria-label="Replacement file for this sheet"
          data-testid="ingest-replace-file"
          className="block w-full text-footnote text-muted-foreground file:mr-2 file:rounded file:border-0 file:bg-muted file:px-2 file:py-1 file:text-footnote file:text-foreground"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            replace.reset();
          }}
        />
        {file && (
          <Footnote className="mt-1 truncate">
            {file.name} · {formatBytes(file.size)}
          </Footnote>
        )}
      </div>

      {/* The sentence the control exists to be read next to. Immutability is
          NOT excepted here, and saying which of the two it is — new version, or
          edit in place — is the difference between an informed press and a
          surprise. */}
      <Guard className="mt-2.5" data-testid="ingest-replace-immutability">
        <span className="text-foreground">Versions stay immutable.</span> This does not edit{' '}
        {baseVersion != null ? `v${baseVersion}` : 'the base version'} — it writes the{' '}
        <span className="text-foreground">next</span> version, whose other sheets point at the base
        version's existing parquet files copy-on-write. No data is duplicated and no byte of the
        base is rewritten, so every tag keeps pointing at the version it already pointed at, and a
        diff already computed against the base still describes it. The new version becomes current.
      </Guard>

      <Guard className="mt-1.5">
        The base is always the dataset's <span className="text-foreground">current</span> version.
        The endpoint takes no version argument, so a sheet inside an older version cannot be
        replaced.
      </Guard>

      <Guard className="mt-1.5" tone="warning">
        The file must be single-table — one CSV, one parquet, or a one-sheet workbook (
        {REPLACE_EXTENSIONS.join(' ')}). A multi-sheet file is refused: this replaces one named
        sheet and the service will not guess which tab you meant.
      </Guard>

      {blocker && <Footnote className="mt-2">{blocker}</Footnote>}

      <Button
        variant="outline"
        className="mt-2 w-full"
        disabled={Boolean(blocker) || replace.isPending}
        onClick={() => setConfirming(true)}
        data-testid="ingest-replace-open"
      >
        <Replace />
        {replace.isPending ? 'Writing the new version…' : 'Replace sheet…'}
      </Button>

      {/* A ternary, not `&&`: the mutation's error is typed `unknown`, and
          `unknown && <p/>` is `unknown` — which is not a ReactNode. */}
      {replace.error ? (
        <p
          className="mt-2 rounded bg-destructive/10 px-2.5 py-2 text-micro leading-relaxed text-foreground"
          data-testid="ingest-replace-error"
        >
          {errorText(replace.error, {
            notFound: 'That dataset or sheet is not available to this seat.',
          })}
        </p>
      ) : null}

      {result && <SheetReplaceResultBody result={result} />}

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Replace {sheet ?? 'this sheet'} in a new version
            </AlertDialogTitle>
            <AlertDialogDescription>
              {baseVersion != null ? `v${baseVersion}` : 'The base version'} is not modified. This
              creates the next version of {dataset?.name ?? 'this dataset'} with {sheet}'s data
              taken from {file?.name ?? 'the chosen file'}; the other{' '}
              {Math.max(0, sheets.length - 1)} sheet
              {sheets.length - 1 === 1 ? '' : 's'} are carried across copy-on-write, pointing at the
              base version's existing files. Tags stay where they are, and the new version becomes
              current.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="ingest-replace-cancel">Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={Boolean(blocker) || replace.isPending}
              data-testid="ingest-replace-confirm"
              onClick={() => {
                if (!datasetId || !sheet || !file) return;
                setConfirming(false);
                replace.mutate(
                  { datasetId, sheetName: sheet, file },
                  { onSuccess: clear },
                );
              }}
            >
              Create the new version
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

/**
 * What came back. Every field of `SheetReplaceResponse` is rendered and nothing
 * else is: `row_count` is documented as the TOTAL across every sheet of the new
 * version, so it is labelled that way rather than passed off as the replaced
 * sheet's own count, which the response does not carry.
 */
function SheetReplaceResultBody({ result }: { result: SheetReplaceResult }) {
  const total = result.reused_sheets.length + 1;
  return (
    <div className="mt-3" data-testid="ingest-replace-result">
      <div className="flex items-baseline gap-2">
        <Status kind="good">created</Status>
        <Identifier className="text-label font-medium text-foreground">
          v{result.version_number}
        </Identifier>
        <Identifier className="ml-auto truncate text-footnote text-muted-foreground">
          {result.version_id.slice(0, 8)}
        </Identifier>
      </div>

      <div className="mt-2">
        <Stat
          name="replaced"
          value={`1 of ${total}`}
          coverage={coverage(1, total, 'sheets')}
          data-testid="ingest-replace-stat"
        />
        <Stat
          name="reused"
          value={`${result.reused_sheets.length} of ${total}`}
          coverage={coverage(result.reused_sheets.length, total, 'sheets')}
        />
      </div>

      <Footnote className="mt-1.5">
        <Identifier className="text-foreground">{result.replaced_sheet}</Identifier> now holds the
        uploaded file's rows.{' '}
        {result.reused_sheets.length === 0
          ? 'There was no other sheet to carry over.'
          : `Carried over unchanged: ${result.reused_sheets.join(', ')}.`}
      </Footnote>

      <Footnote className="mt-1.5">
        <span className="text-foreground tabular-nums">{num(result.row_count)}</span> rows across
        all {total} sheet{total === 1 ? '' : 's'} of v{result.version_number} — the response reports
        the version total, not the replaced sheet's own count.
      </Footnote>

      <Guard className="mt-2">
        Nothing was checked on the way in here either. The replacement ran no rules, no profile and
        no PII detection, and the new version's health reads unknown until someone profiles it.
      </Guard>
    </div>
  );
}

/** A fork. Selected wins on elevation and near-white ink, never on the accent. */
function Choice({
  selected,
  disabled,
  onSelect,
  title,
  detail,
  testId,
}: {
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
  title: string;
  detail: ReactNode;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      data-testid={testId}
      onClick={onSelect}
      className={`mb-1.5 flex w-full items-start gap-2.5 rounded-lg p-2.5 text-left disabled:opacity-40 ${
        selected
          ? 'bg-[var(--s4)] shadow-[var(--hi)]'
          : 'shadow-[inset_0_0_0_1px_var(--r1)] hover:bg-white/5'
      }`}
    >
      <span
        className={`mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full ${
          selected ? 'ring-[1.5px] ring-foreground' : 'ring-[1.5px] ring-muted-foreground'
        }`}
      >
        {selected && <span className="size-1.5 rounded-full bg-foreground" />}
      </span>
      <span className="min-w-0">
        <span
          className={`block text-body font-medium ${
            selected ? 'text-foreground' : 'text-muted-foreground'
          }`}
        >
          {title}
        </span>
        <span className="mt-1 block text-micro leading-relaxed text-muted-foreground">
          {detail}
        </span>
      </span>
    </button>
  );
}

interface QueueCardProps {
  item: IngestItem;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onRemove: () => void;
  onProfile: (datasetId: string, versionNumber: number) => void;
  onOpen: (datasetId: string) => void;
}

function QueueCard({
  item,
  onPause,
  onResume,
  onCancel,
  onRemove,
  onProfile,
  onOpen,
}: QueueCardProps) {
  const status = PHASE_STATUS[item.phase];
  const inFlight = item.phase === 'uploading' || item.phase === 'processing';
  const resumable = item.transport === 'resumable';
  const done = TERMINAL.includes(item.phase);
  const percent = item.total > 0 ? (item.sent / item.total) * 100 : 0;

  return (
    <article
      className="rounded-lg bg-[var(--s3)] p-3 shadow-[var(--hi)]"
      data-testid="ingest-item"
      data-phase={item.phase}
    >
      <header className="flex flex-wrap items-center gap-2">
        {/* The one live lamp — an in-flight transfer is what this screen is for. */}
        {inFlight && (
          <span
            aria-hidden
            className="size-1.5 shrink-0 animate-pulse rounded-full bg-[var(--sig)] shadow-[0_0_9px_-1px_var(--sig)]"
          />
        )}
        <Status kind={status.kind}>{status.word}</Status>
        <Identifier className="min-w-0 flex-1 truncate text-label font-medium text-foreground">
          {item.file.name}
        </Identifier>
        <span className="rounded bg-muted px-1.5 font-mono text-footnote text-muted-foreground">
          {resumable ? 'tus 1.0.0' : 'multipart'}
        </span>
        <span className="font-mono text-footnote text-muted-foreground">
          {item.asNewVersion ? 'new version' : 'new dataset'}
        </span>
      </header>

      {!done && (
        <>
          <div className="mt-2.5">
            <MagnitudeBar of={coverage(item.sent, item.total)} />
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-4">
            <Fact
              label="Sent"
              value={formatBytes(item.sent)}
              note={`of ${formatBytes(item.total)} · ${percent.toFixed(1)}%`}
            />
            <Fact
              label={resumable ? 'Upload-Offset' : 'Bytes flushed'}
              value={item.sent.toLocaleString()}
              note={
                resumable ? 'server-confirmed · the resume anchor' : 'reported by the socket'
              }
            />
            <Fact
              label="Rate"
              value={item.rate == null ? '—' : `${formatBytes(item.rate)}/s`}
              note={item.rate == null ? 'no chunk acknowledged yet' : 'last acknowledged chunk'}
            />
            <Fact
              label="Interruptions"
              value={String(item.interruptions)}
              note={
                item.interruptions === 0
                  ? 'none'
                  : `resumed · ${formatBytes(item.resumedFrom ?? 0)} never re-sent`
              }
            />
          </div>

          {item.resumedFrom != null && item.resumedFrom > 0 && (
            <p className="mt-2.5 rounded bg-black/30 px-2.5 py-2 text-micro leading-relaxed text-muted-foreground shadow-[inset_3px_0_0_var(--st-good)]">
              <span className="font-medium text-foreground">Resumed from a confirmed offset.</span>{' '}
              The server reported <Identifier className="text-foreground">
                Upload-Offset: {item.resumedFrom.toLocaleString()}
              </Identifier>{' '}
              and the transfer continued from exactly there — no acknowledged byte was sent twice.
            </p>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            {resumable ? (
              item.phase === 'paused' ? (
                <Button variant="outline" size="sm" onClick={onResume} data-testid="ingest-resume">
                  <Play />
                  Resume from offset
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onPause}
                  disabled={item.phase === 'processing'}
                  data-testid="ingest-pause"
                >
                  <Pause />
                  Pause
                </Button>
              )
            ) : (
              <Footnote>
                This path cannot pause — a single request either completes or starts over.
              </Footnote>
            )}
            <Button
              variant="destructive"
              size="sm"
              onClick={onCancel}
              className="ml-auto"
              data-testid="ingest-cancel"
            >
              Terminate
            </Button>
          </div>
          {item.phase === 'processing' && (
            <Footnote className="mt-1.5">
              Bytes are in. The service is parsing and writing the artifact — the version is not
              queryable until this finishes.
            </Footnote>
          )}
        </>
      )}

      {item.phase === 'ready' && <ReadyBody item={item} onProfile={onProfile} onOpen={onOpen} />}

      {(item.phase === 'failed' || item.phase === 'cancelled') && (
        <div className="mt-2.5">
          <p className="rounded bg-destructive/10 px-2.5 py-2 text-micro leading-relaxed text-foreground">
            <span className="font-medium">
              {item.phase === 'cancelled' ? 'Terminated.' : 'Ingest failed.'}
            </span>{' '}
            {item.error ?? 'The upload did not complete.'}
            {item.errorCode && (
              <>
                {' '}
                <Identifier className="text-muted-foreground">{item.errorCode}</Identifier>
              </>
            )}
          </p>
          <Footnote className="mt-1.5">
            {item.asNewVersion
              ? 'The dataset is kept and the version number is kept — it stays failed, forever. Uploading again onto the same dataset becomes the next version.'
              : 'A dataset this upload created is rolled back, so nothing unusable is left in the catalog.'}
          </Footnote>
        </div>
      )}

      {done && (
        <div className="mt-2.5 flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onRemove} data-testid="ingest-dismiss">
            Dismiss
          </Button>
        </div>
      )}
    </article>
  );
}

function ReadyBody({
  item,
  onProfile,
  onOpen,
}: {
  item: IngestItem;
  onProfile: (datasetId: string, versionNumber: number) => void;
  onOpen: (datasetId: string) => void;
}) {
  const [versionInput, setVersionInput] = useState('');
  const version = Number(versionInput);
  const canProfile = Boolean(item.datasetId) && Number.isInteger(version) && version > 0;

  return (
    <div className="mt-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-4">
        <Fact
          label="Rows"
          value={item.rowCount == null ? 'unknown' : item.rowCount.toLocaleString()}
          note={item.rowCount === 0 ? 'an empty export is a valid version' : 'ingested'}
          hero
        />
        <Fact
          label="Columns"
          value={item.columnCount == null ? 'unknown' : String(item.columnCount)}
          note="types inferred, not declared"
          hero
        />
        <Fact label="Size" value={formatBytes(item.total)} note="raw upload" />
        <Fact
          label="Version id"
          value={item.versionId ? item.versionId.slice(0, 8) : '—'}
          note={item.datasetId ?? 'no dataset id returned'}
        />
      </div>

      {item.message && <Footnote className="mt-2">{item.message}</Footnote>}

      {item.columns && item.columns.length > 0 && (
        <div className="mt-3">
          <Eyebrow className="mb-1.5">Inferred columns</Eyebrow>
          <div className="flex flex-wrap gap-1.5">
            {item.columns.map((column) => (
              <span
                key={column.name}
                className="inline-flex items-center gap-1.5 rounded bg-muted px-1.5 py-0.5"
              >
                <Identifier className="text-small text-foreground">{column.name}</Identifier>
                <DtypeChip dtype={column.dtype} />
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 flex items-start gap-2 pl-2.5 shadow-[inset_2px_0_0_var(--st-warn)]">
        <AlertTriangle className="mt-0.5 size-3 shrink-0 text-[var(--st-warn)]" />
        <p className="text-micro leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Nothing was checked, and nothing is
          masked.</span>{' '}
          The upload ran no rules and no PII detection — there is none anywhere in the platform.
          Every value is readable by anyone with dataset access until a person declares a
          sensitivity in the data dictionary.
        </p>
      </div>

      <div className="mt-3">
        <Eyebrow className="mb-1.5">Next, and not automatic</Eyebrow>
        <Footnote>
          Profiling never runs on upload, so every health dimension for this version reads{' '}
          <span className="text-foreground">unknown</span> — an absence of measurement, not a bad
          score. The upload response does not carry the version number, so name it to profile.
        </Footnote>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={versionInput}
            onChange={(e) => setVersionInput(e.target.value.replace(/[^0-9]/g, ''))}
            placeholder="version #"
            aria-label="Version number to profile"
            data-testid="ingest-profile-version"
            className={`${controlClass} w-24`}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={!canProfile}
            data-testid="ingest-run-profile"
            onClick={() => {
              if (item.datasetId && canProfile) onProfile(item.datasetId, version);
            }}
          >
            Run profile
          </Button>
          {item.datasetId && (
            <Button
              size="sm"
              data-testid="ingest-open-dataset"
              onClick={() => {
                if (item.datasetId) onOpen(item.datasetId);
              }}
            >
              Open in workspace
              <ArrowRight />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Eyebrow over figure. No box — a fact is not a container. */
function Fact({
  label,
  value,
  note,
  hero,
}: {
  label: string;
  value: string;
  note?: string;
  hero?: boolean;
}) {
  return (
    <div className="min-w-0">
      <Eyebrow>{label}</Eyebrow>
      {/* Size comes from `size` alone — restating a text-* class here would
          win the merge and silently cancel the hero step. */}
      <Figure size={hero ? 'hero' : 'figure'} className="truncate font-mono">
        {value}
      </Figure>
      {note && <Footnote className="truncate">{note}</Footnote>}
    </div>
  );
}
