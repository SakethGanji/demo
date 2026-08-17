/**
 * The versions lens: immutable history, the moving pointers into it, and what
 * actually changed between two of them.
 *
 * Three things this panel is built to keep straight.
 *
 * 1. **A version is never edited.** New data is a new version, and nothing in
 *    the system deletes one. So history is a ledger, and the only mutable thing
 *    on this screen is where a tag points.
 *
 * 2. **There are two ways to move a tag, and they are not equivalent.**
 *
 *      promote — gated. The target must be `ready`, and if the dataset has any
 *                enabled rule the target needs a validation run with no error
 *                failures. This is the ONLY quality gate in the whole system:
 *                upload and publish are ungated, and so is `set` below.
 *      set     — ungated. Writes the pointer straight at a version. Useful, but
 *                it is an escape hatch and is labelled as one.
 *
 *    A `validation-required` / `validation-failed` refusal from promote is
 *    therefore the system working. It is rendered beside the tag it would have
 *    moved, with the problem+json `code` as the anchor and the explanation in
 *    the footnote register — never as a generic error toast alone.
 *
 * 3. **Tags apply to a whole version**, never a sheet or a subset.
 *
 * The diff section adapts the prototype's centre surface (L1 workbook → L2
 * schema) into the dock: the workbook diff is the summary, and the column-level
 * detail is fetched for the one modified sheet whose schema actually changed.
 * Base (A) is always an EARLIER ready version than the one on screen (B) — the
 * lens cannot change which version the page is viewing, so B is the app's scope
 * and only A is pickable here.
 *
 * Diff semantics ride the reserved status palette (added = good, removed =
 * critical, changed = warning) and every one carries the word, so the reading
 * survives greyscale. Column ORDER changes wear ink instead: a reorder is not a
 * data delta.
 *
 * Four further capabilities hang off the same ledger, and each one is here
 * because it answers a question the sections above raise but cannot close:
 *
 *   - **Tag history** (`GET /tags/{tag}/history`) — the pointer above says where
 *     a tag is now. The ledger says how it got there, who moved it and why, and
 *     it SURVIVES the tag being deleted. Without it "promote" is an event with
 *     no record.
 *   - **Row diff** (`POST .../row-diff/...`) — the workbook and column diffs are
 *     shape. This is the only thing on the screen that answers "and did the
 *     VALUES move?". It needs a key, and when the sheet declares none the server
 *     says so; that refusal is rendered as a key picker, because it is a
 *     question, not a fault.
 *   - **Confirm rename** (`POST .../confirm-rename`) — the diff offers rename
 *     candidates and refuses to act on them. This is the act. The consequence is
 *     that metadata and quality rules FOLLOW the sheet instead of detaching.
 *   - **Download** and **timeline** — getting a version back out, and the merged
 *     history of everything that has happened to the dataset.
 *
 * Row diff and download both read DATA, so both are gated by `ensure_raw_access`
 * and both can be refused outright on a dataset with sensitive columns. A
 * refusal renders as `LensRestricted` — a state, never an error.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpCircle, Check, Download, History, Rows3, Undo2 } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat, StatList } from '@/shared/components/instrument/Stat';
import { coverage } from '@/shared/components/instrument/coverage';
import { foldTopN, vizSlot } from '@/shared/components/instrument/series';
import { middleTruncate } from '@/shared/components/instrument/shape';
import { MagnitudeBar, Sparkline } from '@/shared/components/instrument/charts';
import {
  Eyebrow,
  Footnote,
  Identifier,
  Metric,
} from '@/shared/components/instrument/Typography';
import { AnalyticsApiError, analytics, errorText } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { compact, formatBytes } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import type { components } from '@/shared/lib/analyticsSchema';
import { isRestricted, useTags } from '../../hooks/useAnalysis';
import { usePromoteTag, useRollbackTag, useSetTag } from '../../hooks/useDatasetActions';
import { useQualityRules, useSheets, type VersionSummary } from '../../hooks/useDatasets';
import {
  diffKeyExplanation,
  needsDiffKey,
  useConfirmRename,
  useDatasetTimeline,
  useDownloadVersion,
  useRowDiff,
  useTagHistory,
  type DownloadFormat,
  type TimelineEvent,
} from '../../hooks/useVersionHistory';
import { LensEmpty, LensError, LensLoading, LensRestricted, Section } from './primitives';
import { fieldClass } from '../fieldStyles';

/* ------------------------------------------------------------------ diffs */

type WorkbookDiff = components['schemas']['WorkbookDiffResponse'];
type SheetDiff = components['schemas']['SheetDiffResponse'];

/**
 * Both diff queries are local to this lens rather than in `useAnalysis.ts`
 * because nothing else asks for them yet. They follow that file's contract
 * exactly — seat-scoped key, `retry: false` — and should move there the moment
 * a second caller appears.
 */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/** Workbook-level: which sheets arrived, left, or changed shape. */
function useWorkbookDiff(datasetId: string | null, from: number | null, to: number | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'workbook-diff', datasetId, from, to],
    queryFn: () =>
      analytics.get<WorkbookDiff>(`/datasets/${datasetId}/versions/${from}/diff/${to}`),
    enabled: Boolean(datasetId) && from != null && to != null && from !== to,
    retry: false,
  });
}

/** Column-level, for ONE sheet: adds, drops, type/nullability/order changes. */
function useSheetDiff(
  datasetId: string | null,
  from: number | null,
  sheet: string | null,
  to: number | null,
) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'sheet-diff', datasetId, from, sheet, to],
    queryFn: () =>
      analytics.get<SheetDiff>(
        `/datasets/${datasetId}/versions/${from}/sheets/${encodeURIComponent(sheet!)}/diff/${to}`,
      ),
    enabled: Boolean(datasetId) && from != null && to != null && Boolean(sheet) && from !== to,
    retry: false,
  });
}

/* ---------------------------------------------------------------- helpers */

const isReady = (v: VersionSummary) => v.status === 'ready';

/**
 * The ingest vocabulary mapped onto the five Instrument states. Anything
 * unrecognised is a ring, which reads as "not assessed" rather than borrowing
 * the look of a pass.
 */
function versionKind(status?: string | null): StatusKind {
  switch (status) {
    case 'ready':
      return 'good';
    case 'failed':
    case 'error':
      return 'critical';
    case 'pending':
    case 'processing':
      return 'warning';
    default:
      return 'unknown';
  }
}

/** A signed delta. `±0` rather than `0`, so "unchanged" is not read as absent. */
function signed(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n === 0) return '±0';
  return `${n > 0 ? '+' : '−'}${compact(Math.abs(n))}`;
}

function shortDate(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
}

/**
 * The three refusals promote can produce, explained. Keyed on the problem+json
 * `code`, never on the prose — the wording changes, the code does not.
 */
function gateExplanation(error: unknown, target: number | null): string {
  if (error instanceof AnalyticsApiError) {
    if (error.code === 'validation-required')
      return `Enabled rules exist but no validation run has completed on v${target ?? '—'}. Run the rules from the Quality lens, then promote.`;
    if (error.code === 'validation-failed')
      return `The validation run on v${target ?? '—'} has error failures. Warnings never block; errors always do.`;
    if (error.code === 'conflict' || error.status === 409)
      return `The gate refused: v${target ?? '—'} is not in a state a tag may point at. The tag has not moved.`;
  }
  return errorText(error);
}

/** A change line: identifier, the word + shape, and the before → after beneath. */
function ChangeRow({
  name,
  kind,
  word,
  detail,
}: {
  name: string;
  /** Omitted for changes that are not data deltas — a reorder wears ink. */
  kind?: StatusKind;
  word: string;
  detail?: string;
}) {
  return (
    <div className="py-0.5" data-testid="version-diff-change">
      <div className="flex items-baseline gap-2">
        <Identifier className="min-w-0 flex-1 truncate text-small text-foreground" title={name}>
          {name}
        </Identifier>
        {kind ? (
          <Status kind={kind} className="shrink-0 text-footnote">
            {word}
          </Status>
        ) : (
          <span className="shrink-0 rounded bg-secondary px-1 font-mono text-footnote text-foreground">
            {word}
          </span>
        )}
      </div>
      {detail && <Footnote className="font-mono">{detail}</Footnote>}
    </div>
  );
}

/* ------------------------------------------------------- ledger vocabulary */

/**
 * A `details` or sample-row value as text, only when it is genuinely a scalar.
 * An object or an array is not a fact this panel can render in 384px, and
 * `[object Object]` is worse than an absence.
 */
function str(v: unknown): string | null {
  if (typeof v === 'string') return v.length > 0 ? v : null;
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  if (typeof v === 'boolean') return String(v);
  return null;
}

/** A `details` value, only when it is genuinely a finite number. */
function int(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** `set | promote | rollback | delete` mapped onto the five Instrument states. */
function tagActionKind(action: string): StatusKind {
  switch (action) {
    case 'promote':
      return 'good';
    case 'rollback':
      return 'warning';
    case 'delete':
      return 'critical';
    default:
      // `set` is the ungated escape hatch — a ring, because it asserts nothing
      // about the quality of what it points at.
      return 'unknown';
  }
}

/**
 * The eleven event types the timeline merges, as words. Anything unrecognised
 * falls through to its own `event_type`, which is honest — a new event type is
 * better shown raw than silently relabelled as something it is not.
 */
const TIMELINE_WORD: Record<string, string> = {
  version_created: 'version created',
  tag_set: 'tag set',
  tag_promote: 'tag promoted',
  tag_rollback: 'tag rolled back',
  tag_delete: 'tag deleted',
  validation_run: 'validation run',
  profile_run: 'profile run',
  transformation_run: 'transformation run',
  derived_from: 'derived from',
  published_to: 'published to',
  audit: 'write',
};

function timelineKind(e: TimelineEvent): StatusKind {
  const d = e.details ?? {};
  switch (e.event_type) {
    case 'version_created':
      return versionKind(str(d.status));
    case 'tag_promote':
      return 'good';
    case 'tag_rollback':
      return 'warning';
    case 'tag_delete':
      return 'critical';
    case 'validation_run':
      if ((int(d.error_failures) ?? 0) > 0) return 'critical';
      if ((int(d.rules_failed) ?? 0) > 0) return 'warning';
      return str(d.status) === 'failed' ? 'critical' : 'good';
    case 'profile_run':
    case 'transformation_run':
      return str(d.status) === 'failed' ? 'critical' : 'good';
    case 'audit':
      return (int(d.status_code) ?? 0) >= 400 ? 'critical' : 'unknown';
    default:
      return 'unknown';
  }
}

/**
 * One line of detail per event, built only from the keys that event actually
 * carries. Nothing is invented: an absent key produces an absent clause rather
 * than an em dash standing in for a fact that was never recorded.
 */
function timelineDetail(e: TimelineEvent): string {
  const d = e.details ?? {};
  const parts: string[] = [];
  const push = (s: string | null) => {
    if (s) parts.push(s);
  };

  switch (e.event_type) {
    case 'version_created': {
      push(int(d.version_number) != null ? `v${int(d.version_number)}` : null);
      push(str(d.status));
      push(int(d.row_count) != null ? `${compact(int(d.row_count))} rows` : null);
      break;
    }
    case 'tag_set':
    case 'tag_promote':
    case 'tag_rollback':
    case 'tag_delete': {
      push(str(d.tag));
      const from = int(d.from_version);
      const to = int(d.to_version);
      push(from != null || to != null ? `v${from ?? '—'} → v${to ?? '—'}` : null);
      push(str(d.reason));
      break;
    }
    case 'validation_run': {
      push(int(d.version_number) != null ? `v${int(d.version_number)}` : null);
      push(str(d.status));
      if (int(d.rules_total) != null)
        push(`${int(d.rules_failed) ?? 0}/${int(d.rules_total)} failed`);
      if ((int(d.error_failures) ?? 0) > 0) push(`${int(d.error_failures)} error`);
      break;
    }
    case 'profile_run': {
      push(int(d.version_number) != null ? `v${int(d.version_number)}` : null);
      push(str(d.sheet));
      push(str(d.status));
      break;
    }
    case 'transformation_run': {
      push(str(d.transformation));
      push(str(d.mode));
      push(int(d.version_number) != null ? `v${int(d.version_number)}` : null);
      push(str(d.status));
      push(int(d.row_count) != null ? `${compact(int(d.row_count))} rows` : null);
      break;
    }
    case 'derived_from': {
      push(str(d.relation));
      push(str(d.parent_dataset));
      push(int(d.parent_version) != null ? `v${int(d.parent_version)}` : null);
      break;
    }
    case 'published_to': {
      push(str(d.relation));
      push(str(d.child_dataset));
      break;
    }
    case 'audit': {
      const method = str(d.method);
      const path = str(d.path);
      push(method && path ? `${method} ${middleTruncate(path, 34)}` : (method ?? path));
      push(int(d.status_code) != null ? String(int(d.status_code)) : null);
      break;
    }
    default:
      break;
  }

  return parts.join(' · ');
}

const DOWNLOAD_FORMATS: readonly DownloadFormat[] = ['csv', 'parquet', 'xlsx'];

interface VersionsLensProps {
  datasetId: string | null;
  versions: VersionSummary[];
  version: number | null;
}

export function VersionsLens({ datasetId, versions, version }: VersionsLensProps) {
  const tags = useTags(datasetId);
  const rules = useQualityRules(datasetId);
  const setTag = useSetTag(datasetId);
  const promote = usePromoteTag(datasetId);
  const rollback = useRollbackTag(datasetId);
  const [newTag, setNewTag] = useState('');
  const [pickedBase, setPickedBase] = useState<number | null>(null);

  /** One tag's ledger at a time — N open tags would be N requests to render. */
  const [openTag, setOpenTag] = useState<string | null>(null);
  const tagHistory = useTagHistory(datasetId, openTag);

  const [format, setFormat] = useState<DownloadFormat>('csv');
  const [exportSheet, setExportSheet] = useState('');
  const download = useDownloadVersion(datasetId);

  const [rowSheet, setRowSheet] = useState('');
  const [keyCols, setKeyCols] = useState<string[]>([]);
  const [showKeyPicker, setShowKeyPicker] = useState(false);
  const rowDiff = useRowDiff(datasetId);

  const confirmRename = useConfirmRename(datasetId, version);

  const [timelineLimit, setTimelineLimit] = useState(20);
  const timeline = useDatasetTimeline(datasetId, timelineLimit);

  /** Sheets of the version on screen — the column source for the key picker. */
  const sheets = useSheets(datasetId, version);
  const sheetItems = sheets.data?.items ?? [];

  const tagItems = tags.data?.items ?? [];

  /** Newest first, whatever order the caller handed us. */
  const ordered = useMemo(
    () => [...versions].sort((a, b) => b.version_number - a.version_number),
    [versions],
  );

  const target = ordered.find((v) => v.version_number === version) ?? null;

  /**
   * The base (A) is an EARLIER ready version. A failed version has no readable
   * content, so it can be neither tagged nor diffed against — offering it as a
   * comparison would produce a refusal the user could not have predicted.
   */
  const baseNumber = useMemo(() => {
    const earlierReady = ordered.filter(
      (v) => isReady(v) && version != null && v.version_number < version,
    );
    if (pickedBase != null && earlierReady.some((v) => v.version_number === pickedBase))
      return pickedBase;
    return earlierReady[0]?.version_number ?? null;
  }, [ordered, pickedBase, version]);

  const canDiff = target != null && isReady(target) && baseNumber != null;
  const from = canDiff ? baseNumber : null;
  const to = canDiff ? version : null;

  const diff = useWorkbookDiff(datasetId, from, to);
  const modified = diff.data?.modified ?? [];
  const addedSheets = diff.data?.added ?? [];
  const removedSheets = diff.data?.removed ?? [];
  const renameCandidates = diff.data?.rename_candidates ?? [];

  /** Column detail is only worth a request for a sheet whose schema moved. */
  const focusSheet = modified.find((m) => m.schema_changed)?.to_sheet ?? null;
  const sheetDiff = useSheetDiff(datasetId, from, focusSheet, to);

  /**
   * Only a sheet present in BOTH versions can be row-diffed. That is exactly
   * `modified` plus `unchanged` — an added sheet has no earlier rows to match
   * against, and a removed one has no later rows.
   */
  const diffableSheets = useMemo(
    () => [
      ...(diff.data?.modified ?? []).map((m) => m.to_sheet),
      ...(diff.data?.unchanged ?? []),
    ],
    [diff.data],
  );

  /** The row diff runs against one sheet; default to the one whose schema moved. */
  const activeRowSheet =
    rowSheet && diffableSheets.includes(rowSheet)
      ? rowSheet
      : (focusSheet ?? diffableSheets[0] ?? '');

  const rowSheetColumns =
    sheetItems.find((s) => s.name === activeRowSheet)?.columns ?? [];
  const rowSheetColumnCount =
    sheetItems.find((s) => s.name === activeRowSheet)?.column_count ?? null;

  const rowResult = rowDiff.data ?? null;
  /** Matched rows — the population a per-column change count is measured over. */
  const matchedRows = rowResult ? rowResult.changed + rowResult.unchanged : 0;
  /** Every row on either side of the join, which is what added/removed span. */
  const comparedRows = rowResult
    ? rowResult.added + rowResult.removed + rowResult.changed + rowResult.unchanged
    : 0;
  const columnChanges = rowResult?.column_changes ?? [];
  const foldedColumns = useMemo(
    () => foldTopN(rowResult?.column_changes ?? [], (c) => c.changed_rows),
    [rowResult],
  );
  const keyRefused = needsDiffKey(rowDiff.error);

  /** Row counts oldest → newest, ready versions only: a trend, not a ledger. */
  const trend = useMemo(
    () =>
      [...ordered]
        .reverse()
        .filter(isReady)
        .map((v) => v.row_count ?? 0),
    [ordered],
  );

  const baseRow = ordered.find((v) => v.version_number === baseNumber) ?? null;
  const rowDelta =
    target?.row_count != null && baseRow?.row_count != null
      ? target.row_count - baseRow.row_count
      : null;
  const rowDeltaPct =
    rowDelta != null && baseRow?.row_count ? (rowDelta / baseRow.row_count) * 100 : null;

  const enabledRules = (rules.data?.items ?? []).filter((r) => r.enabled !== false);
  const errorRules = enabledRules.filter((r) => (r.severity ?? 'error') === 'error').length;
  const warningRules = enabledRules.length - errorRules;

  const trimmedTag = newTag.trim();
  /** The server's own rule; catching it here beats discovering it as a 422. */
  const tagNameInvalid = trimmedTag.length > 0 && /[\s/\\]/.test(trimmedTag);
  const suggestedTag = trimmedTag.replace(/[\s/\\]+/g, '-').toLowerCase();

  const failedCount = ordered.filter((v) => !isReady(v)).length;

  return (
    <>
      <Section
        title="Rows per version"
        action={
          trend.length > 1 ? (
            <Identifier className="text-footnote text-muted-foreground">
              {trend.length} ready
            </Identifier>
          ) : undefined
        }
      >
        <div className="flex items-end justify-between gap-3">
          <Metric
            label={version != null ? `rows · v${version}` : 'rows'}
            value={compact(target?.row_count)}
            note={
              rowDelta != null && baseNumber != null
                ? `${signed(rowDelta)} vs v${baseNumber}${
                    rowDeltaPct != null ? ` (${rowDeltaPct >= 0 ? '+' : '−'}${Math.abs(rowDeltaPct).toFixed(2)}%)` : ''
                  }`
                : 'no earlier ready version to compare'
            }
          />
          <Sparkline points={trend} width={132} height={30} className="mb-1" />
        </div>
      </Section>

      <Section
        title={`Versions (${versions.length})`}
        action={
          <Identifier className="text-footnote text-muted-foreground">immutable</Identifier>
        }
      >
        {versions.length === 0 && <LensEmpty>No versions yet.</LensEmpty>}

        {ordered.map((v) => {
          const isTarget = v.version_number === version;
          const isBase = v.version_number === baseNumber;
          const selectable = isReady(v) && version != null && v.version_number < version;
          const pinned = tagItems.filter((t) => t.version_number === v.version_number);
          const previous = ordered.find(
            (o) => o.version_number < v.version_number && isReady(o),
          );
          const delta =
            v.row_count != null && previous?.row_count != null
              ? v.row_count - previous.row_count
              : null;

          return (
            <button
              key={v.version_number}
              type="button"
              disabled={!selectable}
              onClick={() => setPickedBase(v.version_number)}
              title={
                selectable
                  ? `Compare v${v.version_number} → v${version} (set as base A)`
                  : undefined
              }
              data-testid="version-entry"
              className={cn(
                'mb-0.5 grid w-full grid-cols-[18px_1fr_auto] items-center gap-x-2 gap-y-0.5 rounded-md px-1.5 py-1.5 text-left',
                // A and B are repeated state: value + elevation carry them, never a hue.
                isTarget && 'bg-secondary',
                isBase && !isTarget && 'bg-muted',
                !isTarget && !isBase && selectable && 'hover:bg-muted/60',
                !isReady(v) && 'opacity-60',
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  'row-span-2 grid size-[18px] shrink-0 place-items-center rounded font-mono text-footnote font-bold',
                  isTarget
                    ? 'bg-primary text-primary-foreground'
                    : isBase
                      ? 'bg-[var(--s6)] text-foreground'
                      : 'text-muted-foreground',
                )}
              >
                {isTarget ? 'B' : isBase ? 'A' : '·'}
              </span>

              <span className="flex min-w-0 items-center gap-1.5">
                <Identifier className="shrink-0 text-small font-semibold text-foreground">
                  v{v.version_number}
                </Identifier>
                <Status kind={versionKind(v.status)} className="shrink-0 text-footnote">
                  {v.status ?? 'unknown'}
                </Status>
                {/* Tags pinned to this version, so history and pointers read together. */}
                {pinned.map((t) => (
                  <span
                    key={t.tag_name}
                    className="min-w-0 truncate rounded bg-secondary px-1 font-mono text-footnote text-foreground"
                  >
                    {t.tag_name}
                  </span>
                ))}
              </span>

              <span className="row-span-2 text-right">
                <Identifier className="block text-small font-semibold text-foreground">
                  {isReady(v) ? compact(v.row_count) : '—'}
                </Identifier>
                <Identifier className="block text-footnote text-muted-foreground">
                  {formatBytes(v.size_bytes)}
                </Identifier>
              </span>

              <span className="col-start-2 flex min-w-0 items-center gap-2 text-footnote text-muted-foreground">
                <Identifier>{shortDate(v.created_at)}</Identifier>
                {isReady(v) && delta != null && <Identifier>{signed(delta)} rows</Identifier>}
                {isTarget && <span>viewing</span>}
              </span>
            </button>
          );
        })}

        <Footnote className="mt-1">
          Versions are immutable — new data is a new version, and nothing deletes one.
          {failedCount > 0 &&
            ` ${failedCount} failed ${failedCount === 1 ? 'version is' : 'versions are'} kept for the audit trail but can never be tagged or diffed against.`}
        </Footnote>
      </Section>

      <Section
        title="Export"
        action={
          <Identifier className="text-footnote text-muted-foreground">reads raw data</Identifier>
        }
      >
        <div className="flex gap-1">
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as DownloadFormat)}
            aria-label="Download format"
            className={fieldClass}
            data-testid="download-format"
          >
            {DOWNLOAD_FORMATS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <select
            value={exportSheet}
            onChange={(e) => setExportSheet(e.target.value)}
            aria-label="Download sheet"
            className={fieldClass}
            data-testid="download-sheet"
          >
            <option value="">default sheet</option>
            {sheetItems.map((s) => (
              <option key={s.sheet_key} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-1 flex gap-1">
          <Button
            size="xs"
            variant="outline"
            className="flex-1"
            disabled={version == null || download.isPending}
            onClick={() =>
              version != null &&
              download.mutate({ versionNumber: version, format, sheet: exportSheet || null })
            }
            title={`Export v${version ?? '—'} — the version this panel is viewing`}
            data-testid="version-download"
          >
            <Download className="size-3" />
            {download.isPending ? 'Exporting…' : `Download v${version ?? '—'}`}
          </Button>
          <Button
            size="xs"
            variant="ghost"
            disabled={datasetId == null || download.isPending}
            onClick={() =>
              download.mutate({ versionNumber: null, format, sheet: exportSheet || null })
            }
            title="Export whatever the dataset's current version is — not necessarily the one on screen"
            data-testid="dataset-download"
          >
            Current
          </Button>
        </div>

        {download.isError &&
          (isRestricted(download.error) ? (
            <div className="mt-1.5">
              <LensRestricted what="Downloading a version" />
            </div>
          ) : (
            <div className="mt-1.5">
              <LensError>{errorText(download.error)}</LensError>
            </div>
          ))}

        {download.isSuccess && download.data && (
          <Footnote className="mt-1">
            <Identifier className="text-foreground">
              {middleTruncate(download.data.filename, 30)}
            </Identifier>{' '}
            · {formatBytes(download.data.bytes)}
            {download.data.versionNumber == null
              ? ' · from the current version'
              : ` · from v${download.data.versionNumber}`}
          </Footnote>
        )}

        <Footnote className="mt-1">
          <span className="font-medium text-foreground">Download v{version ?? '—'}</span> exports the
          version you are viewing; <span className="font-medium text-foreground">Current</span> exports
          whichever version the dataset points at now, which may be a different one. Both convert
          on the way out — csv, parquet or xlsx from the same stored rows.
        </Footnote>

        <Guard className="mt-1.5">
          Export reads raw rows, so it is refused on a dataset with sensitive columns unless you
          are an admin, owner or superuser. Masking the grid would be theatre if the file were
          still downloadable. A multi-sheet workbook needs a sheet named above.
        </Guard>
      </Section>

      <Section
        title="Diff vs base"
        action={
          canDiff ? (
            <Identifier className="text-footnote text-muted-foreground">
              v{from} → v{to}
            </Identifier>
          ) : undefined
        }
      >
        {!canDiff && (
          <Footnote>
            {versions.length < 2
              ? 'One version so far — nothing to compare.'
              : target != null && !isReady(target)
                ? `v${version} is ${target.status ?? 'not ready'}, so it cannot be diffed.`
                : 'No earlier ready version below the one you are viewing.'}
          </Footnote>
        )}

        {canDiff && diff.isLoading && <LensLoading>Diffing…</LensLoading>}
        {canDiff && diff.error && <LensError>{errorText(diff.error)}</LensError>}

        {canDiff && diff.data && (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro">
              <Status kind="good">{addedSheets.length} added</Status>
              <Status kind="critical">{removedSheets.length} removed</Status>
              <Status kind="warning">{modified.length} modified</Status>
              <Status kind="unknown">{(diff.data.unchanged ?? []).length} unchanged</Status>
            </div>
            <Footnote className="mt-0.5">sheets in the workbook</Footnote>

            {/* Rule 8: a block inside a block becomes a left-ruled indent. */}
            <div className="mt-2 pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
              {modified.map((m) => (
                <div key={m.sheet_key} className="py-0.5" data-testid="version-diff-sheet">
                  <div className="flex items-baseline gap-2">
                    <Identifier
                      className="min-w-0 flex-1 truncate text-small text-foreground"
                      title={m.to_sheet}
                    >
                      {m.to_sheet}
                    </Identifier>
                    <Identifier className="shrink-0 text-small tabular-nums">
                      {signed(m.row_count_delta)} rows
                    </Identifier>
                  </div>
                  {(m.schema_changed || m.from_sheet !== m.to_sheet) && (
                    <Footnote>
                      {m.schema_changed ? 'schema changed' : 'same schema'}
                      {m.from_sheet !== m.to_sheet && ` · relinked from ${m.from_sheet}`}
                    </Footnote>
                  )}
                </div>
              ))}

              {addedSheets.map((s) => (
                <ChangeRow key={`add-${s.name}`} name={s.name} kind="good" word="sheet added" />
              ))}
              {removedSheets.map((s) => (
                <ChangeRow
                  key={`rem-${s.name}`}
                  name={s.name}
                  kind="critical"
                  word="sheet removed"
                />
              ))}

              {removedSheets.length > 0 && (
                <Footnote className="mt-0.5">
                  Removed sheets are still readable at v{from}; that version is untouched.
                </Footnote>
              )}

              {modified.length + addedSheets.length + removedSheets.length === 0 && (
                <Footnote>No sheet-level change — the workbook shape is identical.</Footnote>
              )}
            </div>

            {renameCandidates.length > 0 && (
              <div className="mt-2">
                <Eyebrow>
                  Rename candidates ({renameCandidates.length}) · advisory
                </Eyebrow>
                {renameCandidates.map((c) => {
                  const pending =
                    confirmRename.isPending && confirmRename.variables?.to_sheet === c.to_sheet;
                  const failed =
                    confirmRename.isError && confirmRename.variables?.to_sheet === c.to_sheet
                      ? confirmRename.error
                      : null;
                  const notCandidate =
                    failed instanceof AnalyticsApiError && failed.code === 'rename-not-candidate';

                  return (
                    <div key={`${c.from_sheet}→${c.to_sheet}`} className="mt-1" data-testid="rename-candidate">
                      <div className="flex items-baseline gap-2">
                        <Identifier
                          className="min-w-0 flex-1 truncate text-small text-foreground"
                          title={`${c.from_sheet} ↔ ${c.to_sheet}`}
                        >
                          {middleTruncate(c.from_sheet, 14)} ↔ {middleTruncate(c.to_sheet, 14)}
                        </Identifier>
                        <span className="shrink-0 rounded bg-secondary px-1 font-mono text-footnote text-foreground">
                          {c.confidence}
                        </span>
                      </div>
                      <Footnote>{c.reason}</Footnote>
                      <div className="mt-1 flex gap-1">
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={version == null || confirmRename.isPending}
                          onClick={() =>
                            confirmRename.mutate({
                              from_sheet: c.from_sheet,
                              to_sheet: c.to_sheet,
                              force: false,
                            })
                          }
                          title={`Confirm that "${c.to_sheet}" in v${version ?? '—'} is "${c.from_sheet}" renamed`}
                          data-testid="rename-confirm"
                        >
                          <Check className="size-3" />
                          {pending ? 'Confirming…' : 'Confirm rename'}
                        </Button>
                        {/* Force exists for rename + schema change in one version;
                            it is offered only after the server has said the pair
                            is not a candidate, never up front. */}
                        {notCandidate && (
                          <Button
                            size="xs"
                            variant="ghost"
                            disabled={confirmRename.isPending}
                            onClick={() =>
                              confirmRename.mutate({
                                from_sheet: c.from_sheet,
                                to_sheet: c.to_sheet,
                                force: true,
                              })
                            }
                            data-testid="rename-force"
                          >
                            Confirm anyway
                          </Button>
                        )}
                      </div>
                      {failed != null && (
                        <Guard tone="critical" className="mt-1">
                          <Identifier className="text-foreground">
                            {failed instanceof AnalyticsApiError
                              ? `${failed.status} ${failed.code}`
                              : 'error'}
                          </Identifier>{' '}
                          — {errorText(failed)}
                          {notCandidate &&
                            ' The schema fingerprints differ, so this is a rename plus a schema change. Confirm anyway only if you know they are the same sheet.'}
                        </Guard>
                      )}
                    </div>
                  );
                })}
                <Footnote className="mt-1.5">
                  Advisory only — the server never auto-declares a rename. Until confirmed each
                  pair stays counted as one removed plus one added sheet. Confirming keeps the
                  sheet&apos;s logical identity, so its metadata, dictionary entries and quality
                  rules follow the new name instead of silently detaching.
                </Footnote>
              </div>
            )}

            {/* L2: the column-level diff, for the one sheet whose schema moved. */}
            {focusSheet && (
              <div className="mt-3">
                <Eyebrow>columns · {focusSheet}</Eyebrow>
                {sheetDiff.isLoading && <LensLoading>Comparing columns…</LensLoading>}
                {sheetDiff.error && <LensError>{errorText(sheetDiff.error)}</LensError>}
                {sheetDiff.data && (
                  <div className="mt-1">
                    {(sheetDiff.data.added_columns ?? []).map((c) => (
                      <ChangeRow
                        key={`c-add-${c.name}`}
                        name={c.name}
                        kind="good"
                        word="added"
                        detail={`${c.dtype} · ${c.nullable ? 'nullable' : 'not null'}`}
                      />
                    ))}
                    {(sheetDiff.data.removed_columns ?? []).map((c) => (
                      <ChangeRow
                        key={`c-rem-${c.name}`}
                        name={c.name}
                        kind="critical"
                        word="removed"
                        detail={`was ${c.dtype} at position ${c.position}`}
                      />
                    ))}
                    {(sheetDiff.data.type_changes ?? []).map((c) => (
                      <ChangeRow
                        key={`c-type-${c.column}`}
                        name={c.column}
                        kind="warning"
                        word="type"
                        detail={`${c.from_dtype} → ${c.to_dtype}`}
                      />
                    ))}
                    {(sheetDiff.data.nullability_changes ?? []).map((c) => (
                      <ChangeRow
                        key={`c-null-${c.column}`}
                        name={c.column}
                        kind="warning"
                        word={c.to_nullable ? 'loosened' : 'tightened'}
                        detail={`${c.from_nullable ? 'nullable' : 'not null'} → ${c.to_nullable ? 'nullable' : 'not null'}`}
                      />
                    ))}
                    {/* A reorder is not a data delta, so it wears ink, not a hue. */}
                    {(sheetDiff.data.order_changes ?? []).map((c) => (
                      <ChangeRow
                        key={`c-ord-${c.column}`}
                        name={c.column}
                        word="reordered"
                        detail={`position ${c.from_position} → ${c.to_position}`}
                      />
                    ))}
                    {sheetDiff.data.identical && (
                      <Footnote>Identical schema and row count.</Footnote>
                    )}
                  </div>
                )}
                {modified.filter((m) => m.schema_changed).length > 1 && (
                  <Footnote className="mt-1">
                    {modified.filter((m) => m.schema_changed).length - 1} other modified sheet
                    {modified.filter((m) => m.schema_changed).length === 2 ? '' : 's'} also changed
                    schema; column detail is shown for {focusSheet} only.
                  </Footnote>
                )}
              </div>
            )}
          </>
        )}
      </Section>

      <Section
        title="Rows changed"
        action={
          rowResult ? (
            <Identifier className="text-footnote text-muted-foreground">
              v{rowResult.from_version} → v{rowResult.to_version}
            </Identifier>
          ) : undefined
        }
      >
        {!canDiff && (
          <Footnote>
            A row diff needs two ready versions. The sheet diff above is shape; this is the only
            thing here that reads values.
          </Footnote>
        )}

        {canDiff && diffableSheets.length === 0 && (
          <Footnote>
            No sheet is present in both v{from} and v{to}, so there is nothing to match rows
            across.
          </Footnote>
        )}

        {canDiff && diffableSheets.length > 0 && (
          <>
            <select
              value={activeRowSheet}
              onChange={(e) => {
                setRowSheet(e.target.value);
                setKeyCols([]);
                rowDiff.reset();
              }}
              aria-label="Row diff sheet"
              className={fieldClass}
              data-testid="row-diff-sheet"
            >
              {diffableSheets.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>

            <div className="mt-1 flex gap-1">
              <Button
                size="xs"
                variant="outline"
                className="flex-1"
                disabled={!activeRowSheet || rowDiff.isPending}
                onClick={() =>
                  rowDiff.mutate({
                    from: from!,
                    to: to!,
                    sheet: activeRowSheet,
                    key: keyCols.length > 0 ? keyCols : null,
                  })
                }
                data-testid="row-diff-run"
              >
                <Rows3 className="size-3" />
                {rowDiff.isPending ? 'Matching rows…' : 'Diff rows'}
              </Button>
              {rowSheetColumns.length > 0 && !keyRefused && (
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => setShowKeyPicker((v) => !v)}
                  data-testid="row-diff-key-toggle"
                >
                  {showKeyPicker ? 'Hide key' : 'Key'}
                </Button>
              )}
            </div>

            {/*
              The key picker is a STATE, not an error box. `diff-key-required`
              means the sheet declares no primary key; `ambiguous-diff-key` means
              the one on offer is not unique. Both are the server asking which
              columns identify a row — so the answer lives right here.
            */}
            {(keyRefused || showKeyPicker) && (
              <div
                className={cn(
                  'mt-1.5 pl-2.5',
                  keyRefused
                    ? 'shadow-[inset_2px_0_0_var(--st-warn)]'
                    : 'shadow-[inset_2px_0_0_var(--r3)]',
                )}
                data-testid="row-diff-key-picker"
              >
                <Eyebrow>Match rows on</Eyebrow>
                {keyRefused && (
                  <Footnote className="mt-0.5">
                    <Identifier className="text-foreground">
                      {rowDiff.error instanceof AnalyticsApiError
                        ? `${rowDiff.error.status} ${rowDiff.error.code}`
                        : 'refused'}
                    </Identifier>{' '}
                    — {diffKeyExplanation(rowDiff.error)}
                  </Footnote>
                )}

                {rowSheetColumns.length === 0 ? (
                  <Footnote className="mt-1">
                    The column list for {activeRowSheet} has not loaded, so the key cannot be
                    picked here. Declare the sheet&apos;s primary key from the overview lens
                    instead.
                  </Footnote>
                ) : (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {rowSheetColumns.map((c) => {
                      const picked = keyCols.includes(c.name);
                      return (
                        <button
                          key={c.name}
                          type="button"
                          onClick={() =>
                            setKeyCols((prev) =>
                              prev.includes(c.name)
                                ? prev.filter((k) => k !== c.name)
                                : [...prev, c.name],
                            )
                          }
                          title={c.name}
                          className={cn(
                            'max-w-full truncate rounded px-1 py-0.5 font-mono text-footnote',
                            picked
                              ? 'bg-primary text-primary-foreground'
                              : 'bg-secondary text-muted-foreground hover:text-foreground',
                          )}
                        >
                          {middleTruncate(c.name, 16)}
                        </button>
                      );
                    })}
                  </div>
                )}

                <Footnote className="mt-1">
                  {keyCols.length === 0
                    ? 'Nothing picked — the server will fall back to the sheet’s declared primary key.'
                    : `Composite key of ${keyCols.length} column${keyCols.length === 1 ? '' : 's'}: `}
                  {keyCols.length > 0 && (
                    <Identifier className="text-foreground">{keyCols.join(' + ')}</Identifier>
                  )}
                </Footnote>
              </div>
            )}

            {rowDiff.isPending && <LensLoading>Matching rows…</LensLoading>}

            {rowDiff.isError &&
              !keyRefused &&
              (isRestricted(rowDiff.error) ? (
                <div className="mt-1.5">
                  <LensRestricted what="A row-level diff" />
                </div>
              ) : (
                <div className="mt-1.5">
                  <LensError>{errorText(rowDiff.error)}</LensError>
                </div>
              ))}

            {rowResult && (
              <div className="mt-2">
                <StatList coverage={coverage(matchedRows, comparedRows, 'rows')}>
                  <Stat
                    name="added"
                    value={compact(rowResult.added)}
                    coverage={coverage(rowResult.added, comparedRows, 'rows')}
                  />
                  <Stat
                    name="removed"
                    value={compact(rowResult.removed)}
                    coverage={coverage(rowResult.removed, comparedRows, 'rows')}
                  />
                  <Stat
                    name="changed"
                    value={compact(rowResult.changed)}
                    coverage={coverage(rowResult.changed, matchedRows, 'rows')}
                  />
                  <Stat
                    name="unchanged"
                    value={compact(rowResult.unchanged)}
                    coverage={coverage(rowResult.unchanged, matchedRows, 'rows')}
                  />
                  {rowSheetColumnCount != null && (
                    <Stat
                      name="compared"
                      value={compact(rowResult.compared_columns?.length ?? 0)}
                      coverage={coverage(
                        rowResult.compared_columns?.length ?? 0,
                        rowSheetColumnCount,
                        'columns',
                      )}
                    />
                  )}
                </StatList>

                <Footnote className="mt-1">
                  Matched on{' '}
                  <Identifier className="text-foreground">
                    {rowResult.key.join(' + ') || '—'}
                  </Identifier>{' '}
                  · added and removed are measured over all {compact(comparedRows)} rows on either
                  side; changed and unchanged only over the {compact(matchedRows)} that matched.
                </Footnote>

                {/* R7: rank, take eight, and state the tail rather than cycling. */}
                {foldedColumns.head.length > 0 && (
                  <div className="mt-2">
                    <Eyebrow>Where the values moved</Eyebrow>
                    {foldedColumns.head.map((c, i) => (
                      <div key={c.column} className="mt-1" data-testid="row-diff-column">
                        <div className="flex items-baseline gap-2">
                          <Identifier
                            className="min-w-0 flex-1 truncate text-small text-foreground"
                            title={c.column}
                          >
                            {middleTruncate(c.column, 22)}
                          </Identifier>
                          <Identifier className="shrink-0 text-small tabular-nums">
                            {compact(c.changed_rows)}
                          </Identifier>
                        </div>
                        <MagnitudeBar
                          className="mt-0.5"
                          of={coverage(c.changed_rows, matchedRows, 'rows')}
                          color={vizSlot(i)}
                        />
                      </div>
                    ))}
                    {/*
                      The tail carries its own count and share, and no bar: its
                      value is a SUM across columns, so one row that moved in
                      three folded columns is counted three times. Drawing that
                      on the same row-denominator axis as the head would be a
                      different claim from the one the bars above make.
                    */}
                    {foldedColumns.other && (
                      <div className="mt-1">
                        <div className="flex items-baseline gap-2">
                          <span className="min-w-0 flex-1 truncate text-small text-muted-foreground">
                            Other ({foldedColumns.other.count} column
                            {foldedColumns.other.count === 1 ? '' : 's'})
                          </span>
                          <Identifier className="shrink-0 text-small tabular-nums text-muted-foreground">
                            {compact(foldedColumns.other.value)}
                          </Identifier>
                        </div>
                        <Footnote>
                          {(foldedColumns.other.share * 100).toFixed(1)}% of all per-column
                          changes, folded rather than drawn — the palette is eight slots and never
                          cycles.
                        </Footnote>
                      </div>
                    )}
                    <Footnote className="mt-1">
                      Matched rows whose value moved, per column, busiest first — over the{' '}
                      {compact(matchedRows)} matched rows.
                    </Footnote>
                  </div>
                )}

                {columnChanges.length === 0 && rowResult.changed === 0 && (
                  <Footnote className="mt-1.5">
                    No matched row changed a compared value — every difference is a row arriving or
                    leaving.
                  </Footnote>
                )}

                {(rowResult.changed_sample ?? []).length > 0 && (
                  <div className="mt-2">
                    <Eyebrow>Cell changes · sample</Eyebrow>
                    {(rowResult.changed_sample ?? []).slice(0, 6).map((row, i) => {
                      const rowKey = str(row.row_key) ?? '—';
                      const column = str(row.column_name) ?? '—';
                      const before = str(row.before_value) ?? str(row.before) ?? '∅';
                      const after = str(row.after_value) ?? str(row.after) ?? '∅';
                      return (
                        <div key={`${rowKey}-${column}-${i}`} className="py-0.5">
                          <div className="flex items-baseline gap-2">
                            <Identifier
                              className="min-w-0 flex-1 truncate text-small text-foreground"
                              title={`${rowKey} · ${column}`}
                            >
                              {middleTruncate(rowKey, 12)} · {middleTruncate(column, 12)}
                            </Identifier>
                          </div>
                          <Footnote className="font-mono">
                            {middleTruncate(before, 18)} → {middleTruncate(after, 18)}
                          </Footnote>
                        </div>
                      );
                    })}
                    <Footnote className="mt-0.5">
                      {(rowResult.changed_sample ?? []).length} of {compact(rowResult.changed)}{' '}
                      changed rows previewed inline
                      {(rowResult.added_sample ?? []).length > 0 &&
                        `; ${(rowResult.added_sample ?? []).length} added and ${(rowResult.removed_sample ?? []).length} removed rows are in the artifact`}
                      .
                    </Footnote>
                  </div>
                )}

                {(rowResult.masked_columns ?? []).length > 0 && (
                  <Guard tone="warning" className="mt-1.5">
                    {(rowResult.masked_columns ?? []).length} sensitive column
                    {(rowResult.masked_columns ?? []).length === 1 ? '' : 's'} withheld from the
                    samples —{' '}
                    <Identifier className="text-foreground">
                      {(rowResult.masked_columns ?? []).join(', ')}
                    </Identifier>
                    . The counts above still include them.
                  </Guard>
                )}

                {rowResult.diff_file ? (
                  <Footnote className="mt-1.5">
                    Full cell-level diff written to{' '}
                    <Identifier className="text-foreground">
                      {middleTruncate(rowResult.diff_file, 26)}
                    </Identifier>
                    {rowResult.diff_artifact_id ? ' · artifact registered' : ''} — one row per
                    changed cell, readable from the library lens.
                  </Footnote>
                ) : (
                  <Footnote className="mt-1.5">
                    No artifact written — nothing changed, so there was no diff to store.
                  </Footnote>
                )}
              </div>
            )}
          </>
        )}
      </Section>

      <Section
        title={`Tags (${tagItems.length})`}
        action={
          <Identifier className="text-footnote text-muted-foreground">whole version only</Identifier>
        }
      >
        {tagItems.length === 0 && <LensEmpty>No tags yet.</LensEmpty>}

        {tagItems.map((t) => {
          const pointsAt = ordered.find((v) => v.version_number === t.version_number) ?? null;
          const behind = ordered.filter((v) => v.version_number > t.version_number).length;
          const refused =
            promote.isError && promote.variables?.tag === t.tag_name ? promote.error : null;

          return (
            <div key={t.tag_name} className="mb-1.5 rounded-md bg-muted px-2 py-1.5" data-testid="tag-entry">
              <div className="flex items-baseline gap-2">
                <Identifier
                  className="min-w-0 flex-1 truncate text-small font-semibold text-foreground"
                  data-testid="tag-name"
                >
                  {t.tag_name}
                </Identifier>
                <Identifier className="shrink-0 text-small text-foreground">
                  → v{t.version_number}
                </Identifier>
              </div>

              <Footnote className="mt-0.5">
                {t.version_number === version
                  ? 'the version you are viewing'
                  : behind === 0
                    ? 'the newest version'
                    : `${behind} version${behind === 1 ? '' : 's'} behind the newest`}
                {pointsAt == null
                  ? ' · target version not in this list'
                  : !isReady(pointsAt)
                    ? ` · target is ${pointsAt.status ?? 'not ready'}`
                    : ''}
                {t.updated_at ? ` · moved ${shortDate(t.updated_at)}` : ''}
              </Footnote>

              <div className="mt-1.5 flex gap-1">
                <Button
                  size="xs"
                  variant="secondary"
                  disabled={version == null || promote.isPending || t.version_number === version}
                  onClick={() =>
                    version != null && promote.mutate({ tag: t.tag_name, version_number: version })
                  }
                  title="Promote to the version you are viewing — runs the quality gate"
                  data-testid="tag-promote"
                >
                  <ArrowUpCircle className="size-3" />
                  Promote → v{version ?? '—'}
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={rollback.isPending}
                  onClick={() => rollback.mutate({ tag: t.tag_name })}
                  title="Move this tag to the previous version in its own history — never gated"
                  data-testid="tag-rollback"
                >
                  <Undo2 className="size-3" />
                  Roll back
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => setOpenTag((cur) => (cur === t.tag_name ? null : t.tag_name))}
                  title="Every transition this tag has made — the record promote and roll back write to"
                  data-testid="tag-history-toggle"
                >
                  <History className="size-3" />
                  {openTag === t.tag_name ? 'Hide' : 'History'}
                </Button>
              </div>

              {/*
                The ledger. The pointer above is a single mutable number; this is
                the only thing on the screen that says how it got there, and it
                outlives the tag — a deleted tag keeps its history.
              */}
              {openTag === t.tag_name && (
                <div
                  className="mt-1.5 pl-2.5 shadow-[inset_2px_0_0_var(--r3)]"
                  data-testid="tag-history"
                >
                  {tagHistory.isLoading && <LensLoading>Reading the ledger…</LensLoading>}
                  {tagHistory.error && <LensError>{errorText(tagHistory.error)}</LensError>}
                  {tagHistory.data && (
                    <>
                      <Stat
                        name="moves"
                        value={compact(tagHistory.data.items.length)}
                        coverage={coverage(
                          tagHistory.data.items.length,
                          tagHistory.data.total,
                          'transitions',
                        )}
                      />
                      {tagHistory.data.items.length === 0 && (
                        <Footnote className="mt-0.5">
                          No recorded transition. This tag predates the history table, or was
                          written by a path that does not audit.
                        </Footnote>
                      )}
                      {tagHistory.data.items.map((h) => (
                        <div key={h.id} className="mt-1" data-testid="tag-history-entry">
                          <div className="flex items-baseline gap-2">
                            <Status kind={tagActionKind(h.action)} className="shrink-0 text-footnote">
                              {h.action}
                            </Status>
                            <Identifier className="ml-auto shrink-0 text-footnote text-foreground">
                              v{h.from_version_number ?? '—'} → v{h.to_version_number ?? '—'}
                            </Identifier>
                          </div>
                          <Footnote>
                            {shortDate(h.created_at)}
                            {h.actor_email || h.actor_user_id
                              ? ` · ${middleTruncate(h.actor_email ?? h.actor_user_id ?? '', 22)}`
                              : ' · actor not recorded'}
                            {h.reason ? ` · ${h.reason}` : ''}
                          </Footnote>
                          {h.request_id && (
                            <Footnote className="font-mono">
                              {middleTruncate(h.request_id, 26)}
                            </Footnote>
                          )}
                        </div>
                      ))}
                      <Footnote className="mt-1">
                        Newest first. The history survives the tag: deleting a tag does not delete
                        this, which is what makes a promote auditable after the fact.
                      </Footnote>
                    </>
                  )}
                </div>
              )}

              {/* The refusal sits beside the tag it would have moved. */}
              {refused != null && (
                <div
                  className="mt-1.5 pl-2.5 shadow-[inset_2px_0_0_var(--st-crit)]"
                  data-testid="tag-promote-refused"
                >
                  <div className="flex items-baseline gap-2">
                    <Status kind="critical" className="text-micro">
                      Promote refused
                    </Status>
                    <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
                      {refused instanceof AnalyticsApiError
                        ? `${refused.status} ${refused.code}`
                        : 'error'}
                    </Identifier>
                  </div>
                  <Footnote className="mt-0.5">
                    {gateExplanation(refused, version)} The tag still points at v{t.version_number}.
                  </Footnote>
                </div>
              )}
            </div>
          );
        })}

        <Footnote className="mt-1">
          <span className="font-medium text-foreground">
            Promote is the only quality gate in the system.
          </span>{' '}
          {enabledRules.length === 0
            ? 'This dataset has no enabled rules, so promote checks only that the target version is ready.'
            : `It refuses unless the target is ready and its validation run has no error failures — ${enabledRules.length} enabled rule${enabledRules.length === 1 ? '' : 's'}, ${errorRules} error, ${warningRules} warning. Warnings never block.`}{' '}
          Upload, publish and Set tag below are ungated.
        </Footnote>

        <div className="mt-3">
          <Eyebrow>Set a tag directly</Eyebrow>
          <input
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            placeholder="new-tag-name"
            aria-label="New tag name"
            className={`mt-1 font-mono ${fieldClass}`}
            data-testid="tag-new-name"
          />
          <Button
            size="xs"
            variant="outline"
            className="mt-1 w-full"
            disabled={!trimmedTag || tagNameInvalid || version == null || setTag.isPending}
            onClick={() =>
              version != null &&
              setTag.mutate(
                { tag_name: trimmedTag, version_number: version },
                { onSuccess: () => setNewTag('') },
              )
            }
            data-testid="tag-set"
          >
            Set tag → v{version ?? '—'}
          </Button>

          {tagNameInvalid ? (
            <div className="mt-1 pl-2.5 shadow-[inset_2px_0_0_var(--st-crit)]">
              <Footnote>
                <Identifier className="text-foreground">invalid_tag_name</Identifier> — a tag name
                may not contain whitespace, <Identifier>/</Identifier> or{' '}
                <Identifier>\</Identifier>. Suggested:{' '}
                <Identifier className="text-foreground">{suggestedTag}</Identifier>.
              </Footnote>
            </div>
          ) : (
            /* Name the escape hatch as an escape hatch. */
            <Footnote className="mt-1">
              Writes the pointer straight at v{version ?? '—'}, skipping the status and quality
              gates Promote enforces. A tag always covers the whole version, never one sheet.
            </Footnote>
          )}
        </div>
      </Section>

      <Section
        title="Activity"
        action={
          timeline.data ? (
            <Identifier className="text-footnote text-muted-foreground">newest first</Identifier>
          ) : undefined
        }
      >
        {timeline.isLoading && <LensLoading>Merging history…</LensLoading>}
        {timeline.error && <LensError>{errorText(timeline.error)}</LensError>}

        {timeline.data && timeline.data.items.length === 0 && (
          <LensEmpty>Nothing has happened to this dataset yet.</LensEmpty>
        )}

        {timeline.data && timeline.data.items.length > 0 && (
          <>
            <Stat
              name="events"
              value={compact(timeline.data.items.length)}
              coverage={coverage(timeline.data.items.length, timeline.data.total, 'events')}
            />

            <div className="mt-1.5">
              {timeline.data.items.map((e, i) => {
                const detail = timelineDetail(e);
                return (
                  <div
                    key={`${e.event_type}-${e.occurred_at}-${i}`}
                    className="py-0.5"
                    data-testid="timeline-event"
                  >
                    <div className="flex items-baseline gap-2">
                      <Status kind={timelineKind(e)} className="min-w-0 shrink-0 text-footnote">
                        {TIMELINE_WORD[e.event_type] ?? e.event_type}
                      </Status>
                      <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
                        {shortDate(e.occurred_at)}
                      </Identifier>
                    </div>
                    {/* One line, always — R10 keeps the row height fixed. */}
                    <Footnote className="truncate">
                      {e.actor ? middleTruncate(e.actor, 22) : 'actor not recorded'}
                      {detail ? ` · ${detail}` : ''}
                    </Footnote>
                  </div>
                );
              })}
            </div>

            {timeline.data.total > timeline.data.items.length && (
              <Button
                size="xs"
                variant="ghost"
                className="mt-1 w-full"
                disabled={timeline.isFetching}
                onClick={() => setTimelineLimit((n) => Math.min(200, n + 20))}
                data-testid="timeline-more"
              >
                {timeline.isFetching
                  ? 'Loading…'
                  : `Show more (${timeline.data.total - timeline.data.items.length} older)`}
              </Button>
            )}

            <Footnote className="mt-1">
              Version uploads, tag transitions, validation and profile runs, transformations,
              lineage in both directions, and audited WRITES. Reads are usage, not history, so an
              empty timeline means nothing has happened to this dataset — not that nobody has
              looked at it.
            </Footnote>
          </>
        )}
      </Section>
    </>
  );
}
