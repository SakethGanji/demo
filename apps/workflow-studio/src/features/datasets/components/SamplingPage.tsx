/**
 * The sampling studio: configure a draw, run it, read what came out.
 *
 *   draw (method + parameters)  |  result  |  reproducibility & output
 *
 * ── The rule this screen lives or dies by ──────────────────────────────────
 *
 * A SAMPLE IS A STATISTIC ABOUT A POPULATION, so every number here is rendered
 * with the denominator it was computed over. Not as a convention — `Stat` will
 * not compile without a `Coverage`, and `ratio()` returns `null` on an empty
 * population so a zero-row source renders a state instead of `NaN%`.
 *
 * Three ways a sample can quietly stop describing what its label claims, all of
 * which this page is built to expose:
 *
 *   1. **Stratified allocation narrows the population.** `class_targets` makes
 *      the engine draw ONLY from the classes it names. Balancing across the top
 *      8 of 40 classes is a sample of 8 classes, not of the dataset — so the
 *      folded tail is rendered with its own count and share (SHAPE-R7), and the
 *      eligible population is stated beside the full one.
 *   2. **The engine tops up short draws.** When the configured steps come in
 *      under `target_total_volume`, a `random_fill` step appends a uniform
 *      random draw from whatever is left. A balanced sample that gets filled is
 *      no longer balanced and the sampled count says nothing about it, so the
 *      fill is called out by name with its share of the sample.
 *   3. **A filter narrows the pool before the draw.** `filter_matched` over
 *      `pool_before` is the honest reading of "what did this actually sample
 *      from", and it is shown per step.
 *
 * ── Two refusals that are states, not errors ───────────────────────────────
 *
 * `/sample` and `/profile` both run `ensure_raw_access`: a dataset that
 * declares any sensitive column is refused for a viewer or editor, because a
 * sample of raw values leaks them verbatim and the parquet it writes bypasses
 * the preview entirely. That renders as `LensRestricted`, never as an error.
 * Cross-tenant reads are 404 and stay 404 — nothing here says "access denied".
 *
 * ── The artifact has a clock ───────────────────────────────────────────────
 *
 * A run registers a `sample_output` artifact (from the artifacts table, never a
 * bucket scan) kept 30 days; an export of it is kept 7. Nothing in the service
 * schedules the sweep, so past-window means eligible for collection, not
 * collected — the dock says both, so "it vanished" is never a surprise.
 *
 * ── Two scopes, one reading model ──────────────────────────────────────────
 *
 * `POST /sample/coordinated` samples a DRIVER sheet exactly as `/sample` does
 * and then semi-joins related sheets down to the rows the draw references, so
 * the result is a referentially consistent slice of one version. Its `driver`
 * field IS a `SampleResponse`, so this page renders it through the machinery
 * above rather than growing a second way to read step accounting — the only
 * thing coordinated mode adds is per-related-sheet coverage.
 *
 * And it adds one hard rule with it: EVERY SHEET HAS ITS OWN POPULATION. The
 * response reports no combined total and this page invents none, because rows
 * drawn from an orders sheet plus rows kept from a line-items sheet is a count
 * of nothing.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { Download, Play, Plus, X } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Checkbox } from '@/shared/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableNumericCell,
  TableRow,
} from '@/shared/components/ui/table';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage, ratio, type Coverage } from '@/shared/components/instrument/coverage';
import { foldTopN, vizSlot } from '@/shared/components/instrument/series';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat } from '@/shared/components/instrument/Stat';
import { Status } from '@/shared/components/instrument/Status';
import {
  Eyebrow,
  Figure,
  Footnote,
  Identifier,
  Metric,
} from '@/shared/components/instrument/Typography';
import { ANALYTICS_BASE, errorText } from '@/shared/lib/analyticsClient';
import { compact, num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { isRestricted } from '../hooks/useAnalysis';
import { useDatasetCatalog, useSheets, useVersions } from '../hooks/useDatasets';
import type { SheetSummary } from '../hooks/useDatasets';
import { KEY_SOURCE_NOTE, useRunCoordinatedSample } from '../hooks/useCoordinatedSample';
import type {
  CoordinatedSampleRequestBody,
  CoordinatedSampleResponse,
  RelatedSheetLinkSpec,
} from '../hooks/useCoordinatedSample';
import {
  allocateEvenly,
  EXPORT_RETENTION_DAYS,
  METHOD_NOTE,
  SAMPLE_RETENTION_DAYS,
  SAMPLING_METHODS,
  strataFor,
  SYNTHESISED_STEPS,
  useExportSample,
  useRunSample,
  useSamplingProfile,
  type SamplingMethod,
  type SamplingStepSpec,
  type SampleRequestBody,
  type StepResult,
  type Strata,
} from '../hooks/useSampling';
import { controlClass, fieldClass } from './fieldStyles';
import { LensEmpty, LensError, LensLoading, LensRestricted, Row, Section } from './lenses/primitives';

/** The engine returns exactly five preview rows, whatever the sample size. */
const PREVIEW_ROWS = 5;

/** Palette width. Above this, rank and fold — never cycle (SHAPE-R7). */
const STRATA_SLOTS = 8;

/**
 * A deliberate, visible default rather than a random one. A seed that changes
 * on every mount would make two runs of "the same" configuration differ for a
 * reason nothing on screen explains.
 */
const DEFAULT_SEED = 1123581321;

const EXPORT_FORMATS = ['csv', 'xlsx', 'parquet'] as const;

/* --------------------------------------------------------------- formatting */

/**
 * A share, or `null` when the population is empty.
 *
 * Callers must handle the null — that is SHAPE-R9 doing its job. There is no
 * variant of this that returns `'0%'` for a zero denominator, because zero rows
 * is a state and 0% is a measurement.
 */
function share(c: Coverage): string | null {
  const r = ratio(c);
  if (r === null) return null;
  const pct = r * 100;
  return `${pct >= 10 ? pct.toFixed(1) : pct.toFixed(2)}%`;
}

/** `n of N` — the only readable form of a sampled count. */
function overPopulation(counted: number, total: number): string {
  return `${num(counted)} of ${num(total)}`;
}

function parseIntOr(value: string, fallback: number): number {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

function parseFloatOr(value: string, fallback: number): number {
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
}

/* ------------------------------------------------------------------ pieces */

/** A labelled control. Grouped by space and a recessive label, never a box. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="mb-2 block last:mb-0">
      <span className="mb-1 flex items-baseline gap-2">
        <Identifier className="text-footnote text-muted-foreground">{label}</Identifier>
        {hint && <span className="ml-auto text-footnote text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

/**
 * One of six methods. Selection is REPEATED state — six of these sit side by
 * side — so the chosen one wins on value and elevation, never on the accent
 * (rule 1). Spending `--sig` here would turn it into texture.
 */
function MethodChip({
  method,
  selected,
  onSelect,
}: {
  method: SamplingMethod;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-testid={`sampling-method-${method}`}
      className={cn(
        'flex min-w-0 flex-col rounded-md px-2 py-1 text-left transition-colors',
        selected
          ? 'bg-[var(--s6)] shadow-[inset_0_1px_0_rgba(255,255,255,.045)]'
          : 'hover:bg-muted/50',
      )}
    >
      <Identifier
        className={cn(
          'truncate text-micro font-semibold',
          selected ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {method}
      </Identifier>
      <span
        className={cn(
          'truncate text-footnote',
          selected ? 'text-muted-foreground' : 'text-muted-foreground/80',
        )}
      >
        {METHOD_NOTE[method]}
      </span>
    </button>
  );
}

/**
 * The stratification picker — and the place this screen earns its keep.
 *
 * A balanced allocation names classes, and the engine draws only from the
 * classes it is given. So the panel does three things at once: it shows each
 * class's size over the whole sheet, it folds everything past the eighth into a
 * graphite "Other" carrying its own count and share (SHAPE-R7 — the palette is
 * never cycled), and it states in words how much of the population a balanced
 * draw would therefore be blind to.
 */
function StrataPanel({
  strata,
  balanced,
  targets,
}: {
  strata: Strata;
  balanced: boolean;
  targets: Record<string, number>;
}) {
  const population = strata.coverage.total;
  const folded = foldTopN(strata.items, (s) => s.count, STRATA_SLOTS);
  const headTotal = folded.head.reduce((sum, s) => sum + s.count, 0);
  const eligible = coverage(balanced ? headTotal : strata.coverage.counted, population);
  const eligibleShare = share(eligible);

  if (population <= 0) {
    return <LensEmpty>This sheet has no rows, so it has no classes to stratify by.</LensEmpty>;
  }

  return (
    <div data-testid="sampling-strata">
      {folded.head.map((s, rank) => (
        <div key={s.value} className="mb-1.5 last:mb-0">
          <div className="flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="size-2 shrink-0 translate-y-px rounded-[2px]"
              style={{ background: vizSlot(rank) }}
            />
            <span className="min-w-0 flex-1 truncate text-small" title={s.value}>
              {s.value}
            </span>
            <span className="shrink-0 text-small text-muted-foreground tabular-nums">
              {overPopulation(s.count, population)}
            </span>
            {balanced && (
              <Identifier className="w-12 shrink-0 text-right text-footnote text-foreground">
                →{num(targets[s.value] ?? 0)}
              </Identifier>
            )}
          </div>
          <MagnitudeBar
            className="mt-0.5 h-1"
            of={coverage(s.count, population)}
            color={vizSlot(rank)}
          />
        </div>
      ))}

      {folded.other && (
        <div className="mt-1.5" data-testid="sampling-strata-other">
          <div className="flex items-baseline gap-2">
            <span
              aria-hidden="true"
              className="size-2 shrink-0 translate-y-px rounded-[2px] bg-[var(--m5)]"
            />
            <span className="min-w-0 flex-1 truncate text-small text-muted-foreground">
              Other ({folded.other.count} classes)
            </span>
            <span className="shrink-0 text-small text-muted-foreground tabular-nums">
              {overPopulation(folded.other.value, population)}
            </span>
            {balanced && (
              <Identifier className="w-12 shrink-0 text-right text-footnote text-muted-foreground">
                →0
              </Identifier>
            )}
          </div>
          <MagnitudeBar className="mt-0.5 h-1" of={coverage(folded.other.value, population)} />
        </div>
      )}

      {/* The narrowing, in words and in numbers. This is the sentence the whole
          panel exists for: a balanced draw over the top 8 is a sample of those
          8 classes, and the rest of the sheet contributes nothing to it. */}
      {balanced && folded.other && (
        <Footnote className="mt-2 pl-2.5 shadow-[inset_2px_0_0_rgba(250,178,25,.45)]">
          A balanced allocation draws <span className="text-foreground">only</span> from the{' '}
          {folded.head.length} classes named above —{' '}
          <span className="text-foreground tabular-nums">
            {overPopulation(headTotal, population)} rows
          </span>
          {eligibleShare && <span className="tabular-nums"> ({eligibleShare})</span>}. The other{' '}
          {folded.other.count} classes, {num(folded.other.value)} rows, are not drawn from at all
          and are not in the sample's denominator. Choose proportional to keep the whole sheet
          eligible.
        </Footnote>
      )}

      {strata.truncated && (
        <Footnote className="mt-2">
          {strata.distinct != null
            ? `${num(strata.distinct)} distinct classes exist; the profile returned the ${strata.items.length} largest, covering ${overPopulation(strata.coverage.counted, population)} rows.`
            : `The listed classes cover ${overPopulation(strata.coverage.counted, population)} rows; the rest are below the profile's top-N cutoff.`}
        </Footnote>
      )}
    </div>
  );
}

/** Per-step results. `rows_selected` never appears without `pool_before`. */
function StepTable({ steps }: { steps: readonly StepResult[] }) {
  return (
    <Table containerClassName="flex-none overflow-x-auto">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="px-2 py-1 font-mono text-footnote normal-case">step</TableHead>
          <TableHead className="px-2 py-1 font-mono text-footnote normal-case">method</TableHead>
          <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
            pool in
          </TableHead>
          <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
            filter match
          </TableHead>
          <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
            selected
          </TableHead>
          <TableHead className="border-l border-border px-2 py-1 text-right font-mono text-footnote normal-case">
            of pool
          </TableHead>
          <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
            pool out
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {steps.map((s, i) => {
          const drawn = coverage(s.rows_selected, s.pool_before);
          const drawnShare = share(drawn);
          const matched =
            s.filter_matched != null ? coverage(s.filter_matched, s.pool_before) : null;
          const synthesised = s.method in SYNTHESISED_STEPS;
          return (
            <TableRow key={`${s.step_index}-${s.method}-${i}`} data-testid="sampling-step-row">
              <TableCell className="px-2 py-1 font-mono text-footnote text-muted-foreground">
                {String(s.step_index + 1).padStart(2, '0')}
              </TableCell>
              <TableCell className="px-2 py-1">
                <Identifier
                  className={cn(
                    'text-micro',
                    synthesised ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {s.method}
                </Identifier>
              </TableCell>
              <TableNumericCell className="px-2 py-1 text-micro">
                {num(s.pool_before)}
              </TableNumericCell>
              <TableNumericCell className="px-2 py-1 text-micro text-muted-foreground">
                {matched ? `${num(s.filter_matched)}${share(matched) ? ` · ${share(matched)}` : ''}` : '—'}
              </TableNumericCell>
              <TableNumericCell className="px-2 py-1 text-micro">
                {num(s.rows_selected)}
              </TableNumericCell>
              <TableNumericCell className="border-l border-border px-2 py-1 text-micro text-muted-foreground">
                {drawnShare ?? 'no rows'}
              </TableNumericCell>
              <TableNumericCell className="px-2 py-1 text-micro text-muted-foreground">
                {num(s.pool_after)}
              </TableNumericCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/* -------------------------------------------------- coordinated: the links */

/**
 * One related-sheet link, as the rail edits it.
 *
 * `keyMode` is a real fork in the API, not a convenience. Naming `left_on` and
 * `right_on` is one contract; omitting BOTH is a different one, under which the
 * service resolves the pair from a confirmed relationship (§22) or, failing
 * that, from an enabled `foreign_key` quality rule. Half a key is neither, so
 * the two column pickers appear and disappear together.
 */
interface LinkDraft {
  readonly id: string;
  sheet: string;
  /** Sheet whose sampled keys drive this filter. The driver, or another link. */
  parent: string;
  keyMode: 'infer' | 'explicit';
  leftOn: string;
  rightOn: string;
}

/** Column names of one sheet, from the version-scoped sheet list. */
function columnsOf(sheets: readonly SheetSummary[], name: string): string[] {
  return (sheets.find((s) => s.name === name)?.columns ?? []).map((c) => c.name);
}

/**
 * The result side of coordinated mode.
 *
 * Every figure here is stated against the sheet it came out of. There is no
 * total row and there will not be one: `original_count` on a related sheet is
 * that sheet's population, and the only thing summing them would produce is a
 * confident number describing no population at all.
 */
function RelatedSheetsPanel({
  coord,
  versionSheets,
}: {
  coord: CoordinatedSampleResponse;
  versionSheets: number;
}) {
  const covered = 1 + coord.related.length;
  return (
    <Section title="Related sheets">
      <Guard data-testid="coordinated-population-guard">
        Each sheet below carries its <span className="text-foreground">own</span> denominator. The
        response reports no combined total and this page invents none — rows drawn from{' '}
        <Identifier>{coord.driver_sheet}</Identifier> added to rows kept from another sheet would
        be a count of nothing.
      </Guard>

      <div className="mt-2 max-w-md">
        <Stat
          name="sheets"
          value={`${covered} of ${versionSheets}`}
          coverage={coverage(covered, versionSheets, 'sheets')}
          data-testid="coordinated-sheet-coverage"
        />
      </div>
      <Footnote className="mt-1">
        The driver plus every sheet filtered to it, over the sheets this version has. Sheets not
        listed here are absent from the slice entirely.
      </Footnote>

      <div className="mt-3">
        {coord.related.map((r) => {
          const kept = coverage(r.sampled_count, r.original_count);
          const keyNote = KEY_SOURCE_NOTE[r.key_source ?? 'explicit'] ?? r.key_source;
          return (
            <div key={r.sheet} className="mb-3 last:mb-0" data-testid="coordinated-related-row">
              <div className="flex flex-wrap items-baseline gap-2">
                <Identifier className="text-small font-medium text-foreground">{r.sheet}</Identifier>
                <Status
                  kind={r.sampled_count > 0 ? 'good' : 'warning'}
                  className="text-footnote"
                >
                  {r.sampled_count > 0 ? 'consistent' : 'nothing referenced'}
                </Status>
                <Identifier className="ml-auto truncate text-footnote text-muted-foreground">
                  {r.parent_sheet}.{r.left_on} → {r.sheet}.{r.right_on}
                </Identifier>
              </div>

              <Stat
                className="mt-1"
                name="kept"
                value={overPopulation(r.sampled_count, r.original_count)}
                coverage={kept}
              />
              <MagnitudeBar className="mt-1 h-1" of={kept} />

              <Footnote className="mt-1">
                {keyNote}
                {r.relationship_id ? ` · relationship ${r.relationship_id.slice(0, 8)}` : ''} ·{' '}
                {num(r.sampled_count)} of this sheet's {num(r.original_count)} rows are referenced
                by the {r.parent_sheet} sample
                {r.referenced_count != null && r.referenced_count !== r.sampled_count
                  ? `; ${num(r.referenced_count)} were referenced before sub-sampling`
                  : ''}
                .
              </Footnote>

              {r.sample_file && (
                <div className="mt-1 flex items-center gap-1.5">
                  <span className="truncate font-mono text-footnote" title={r.sample_file}>
                    {r.sample_file}
                  </span>
                  <a
                    href={`${ANALYTICS_BASE}/samples/${encodeURIComponent(r.sample_file)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-muted-foreground transition-colors hover:text-foreground"
                    title="Download"
                    data-testid="coordinated-related-download"
                  >
                    <Download className="size-3" />
                  </a>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <Guard className="mt-2">
        Every referenced row is kept: this surface sends no per-sheet sampling steps, so a sheet is
        filtered to the rows its parent references and nothing further. Sub-sampling a sheet that
        another sheet is itself filtered from is refused by the service —{' '}
        <Identifier>cannot-subsample-parent</Identifier> — because dropping a parent row would
        silently drop the children that depend on it.
      </Guard>
    </Section>
  );
}

/* -------------------------------------------------------------------- page */

interface SamplingPageProps {
  /** Preselected source, e.g. from `?dataset=<id>`. */
  initialDatasetId?: string | null;
}

export function SamplingPage({ initialDatasetId = null }: SamplingPageProps) {
  const [pickedId, setPickedId] = useState<string | null>(initialDatasetId);
  const [pickedVersion, setPickedVersion] = useState<number | null>(null);
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);

  /** One sheet on its own, or a driver plus the sheets kept consistent with it. */
  const [scope, setScope] = useState<'single' | 'coordinated'>('single');
  const [links, setLinks] = useState<readonly LinkDraft[]>([]);

  const [method, setMethod] = useState<SamplingMethod>('random');
  const [sizeMode, setSizeMode] = useState<'count' | 'fraction'>('count');
  const [sizeCount, setSizeCount] = useState(1000);
  const [sizePercent, setSizePercent] = useState(5);
  const [rounds, setRounds] = useState(1);
  const [replace, setReplace] = useState(false);
  const [filterExpr, setFilterExpr] = useState('');

  const [stratifyColumn, setStratifyColumn] = useState<string | null>(null);
  const [allocation, setAllocation] = useState<'proportional' | 'balanced'>('proportional');
  const [weightColumn, setWeightColumn] = useState<string | null>(null);
  const [clusterColumn, setClusterColumn] = useState<string | null>(null);
  const [numClusters, setNumClusters] = useState(10);
  const [timeColumn, setTimeColumn] = useState<string | null>(null);
  const [timeBins, setTimeBins] = useState(12);

  const [dedupe, setDedupe] = useState(false);
  const [shuffle, setShuffle] = useState(false);
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [sortDesc, setSortDesc] = useState(false);
  const [seed, setSeed] = useState<number | null>(DEFAULT_SEED);

  const catalog = useDatasetCatalog({});
  const datasets = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const datasetId = pickedId ?? datasets[0]?.id ?? null;
  const dataset = datasets.find((d) => d.id === datasetId) ?? null;

  const versionsQuery = useVersions(datasetId);
  const versions = useMemo(() => versionsQuery.data?.items ?? [], [versionsQuery.data]);
  const version =
    pickedVersion != null && versions.some((v) => v.version_number === pickedVersion)
      ? pickedVersion
      : versions.length > 0
        ? Math.max(...versions.map((v) => v.version_number))
        : null;

  const sheetsQuery = useSheets(datasetId, version);
  const sheets = useMemo(() => sheetsQuery.data?.items ?? [], [sheetsQuery.data]);
  const sheet =
    pickedSheet && sheets.some((s) => s.name === pickedSheet)
      ? pickedSheet
      : (sheets[0]?.name ?? null);
  const sheetRow = sheets.find((s) => s.name === sheet) ?? null;

  const profileQuery = useSamplingProfile(datasetId, version, sheet);
  const profile = profileQuery.data;

  const run = useRunSample();
  const coord = useRunCoordinatedSample();
  const exportSample = useExportSample();

  const coordinated = scope === 'coordinated';
  const coordResult = coordinated ? (coord.data ?? null) : null;
  /**
   * `CoordinatedSampleResponse.driver` is a `SampleResponse`, so the entire
   * result side below reads a coordinated run through exactly the code that
   * reads a single one. Nothing about steps, goals or reproducibility is
   * duplicated for the second endpoint.
   */
  const result = coordinated ? (coordResult?.driver ?? null) : (run.data ?? null);
  const drawPending = coordinated ? coord.isPending : run.isPending;
  const drawError = coordinated ? coord.error : run.error;

  /** Both scopes reset together: a result from the other one would be a lie. */
  const resetRuns = () => {
    run.reset();
    coord.reset();
  };

  /**
   * A refusal, from either endpoint. `/profile`, `/sample` and
   * `/sample/coordinated` are gated by the same `ensure_raw_access`, so the
   * screen is restricted as a whole rather than half-working with a dead Run
   * button.
   */
  const restricted =
    isRestricted(profileQuery.error) || isRestricted(run.error) || isRestricted(coord.error);

  /**
   * The population, before anything is drawn. After a run the response's
   * `original_count` is authoritative — it is what the engine actually counted.
   */
  const declaredPopulation = profile?.row_count ?? sheetRow?.row_count ?? null;
  const population = result?.original_count ?? declaredPopulation;

  const columns = useMemo(() => profile?.columns ?? [], [profile]);
  const categorical = useMemo(
    () => columns.filter((c) => c.dtype === 'categorical' || c.dtype === 'boolean'),
    [columns],
  );
  const numeric = useMemo(() => columns.filter((c) => c.dtype === 'numeric'), [columns]);
  const temporal = useMemo(() => columns.filter((c) => c.dtype === 'datetime'), [columns]);

  const strata = useMemo(() => strataFor(profile, stratifyColumn), [profile, stratifyColumn]);

  // Size, as both an n and a share of the population. Neither is derived from
  // the other behind the user's back: whichever they typed is what is sent, and
  // the other is shown as its consequence.
  const requested =
    sizeMode === 'count'
      ? sizeCount
      : population != null
        ? Math.max(1, Math.round((population * sizePercent) / 100))
        : sizeCount;

  const balanced = method === 'stratified' && allocation === 'balanced';
  const targetClasses = useMemo(() => {
    if (!balanced || !strata) return [] as string[];
    return foldTopN(strata.items, (s) => s.count, STRATA_SLOTS).head.map((s) => s.value);
  }, [balanced, strata]);
  const classTargets = useMemo(
    () => allocateEvenly(targetClasses, requested),
    [targetClasses, requested],
  );

  /* ------------------------------------------------- coordinated: the links */

  /** Sheets of this version that are neither the driver nor already linked. */
  const linkableSheets = useMemo(
    () => sheets.filter((s) => s.name !== sheet && !links.some((l) => l.sheet === s.name)),
    [sheets, sheet, links],
  );

  const addLink = () => {
    const next = linkableSheets[0];
    if (!next || !sheet) return;
    setLinks((prev) => [
      ...prev,
      {
        id: `${next.sheet_key}-${prev.length}`,
        sheet: next.name,
        parent: sheet,
        keyMode: 'infer',
        leftOn: '',
        rightOn: '',
      },
    ]);
    resetRuns();
  };

  const patchLink = (id: string, patch: Partial<LinkDraft>) => {
    setLinks((prev) => prev.map((l) => (l.id === id ? { ...l, ...patch } : l)));
    resetRuns();
  };

  /**
   * Point a link at a different sheet. Any link that was filtered BY the old
   * sheet falls back to the driver — the same fallback the service applies when
   * `parent_sheet` is omitted — so a chain can never be left naming a sheet
   * that is no longer in the request.
   */
  const retargetLink = (id: string, nextSheet: string) => {
    setLinks((prev) => {
      const old = prev.find((l) => l.id === id)?.sheet;
      return prev.map((l) =>
        l.id === id
          ? { ...l, sheet: nextSheet, rightOn: '' }
          : l.parent === old
            ? { ...l, parent: sheet ?? l.parent, leftOn: '' }
            : l,
      );
    });
    resetRuns();
  };

  const dropLink = (id: string) => {
    // A link that was another link's parent falls back to the driver, which is
    // what the service does when `parent_sheet` is omitted.
    setLinks((prev) => {
      const gone = prev.find((l) => l.id === id);
      return prev
        .filter((l) => l.id !== id)
        .map((l) => (gone && l.parent === gone.sheet ? { ...l, parent: sheet ?? l.parent } : l));
    });
    resetRuns();
  };

  const halfKeyed = links.some(
    (l) => l.keyMode === 'explicit' && (!l.leftOn.trim() || !l.rightOn.trim()),
  );

  /**
   * Capability gating: say WHY the draw cannot run rather than presenting a
   * dead button. Every branch names the missing thing.
   */
  const blocker: string | null = (() => {
    if (restricted)
      return 'This dataset declares sensitive columns, so sampling is refused for this seat — a sample of raw values would leak them verbatim.';
    if (!datasetId) return 'Choose a dataset to sample.';
    if (!sheet) return 'Choose a sheet — the API refuses to guess on a multi-sheet workbook.';
    if (population == null) return 'Waiting for the source row count.';
    if (population <= 0) return 'This sheet has no rows. There is nothing to draw from.';
    if (requested <= 0) return 'Sample size must be at least one row.';
    if (method === 'stratified' && !stratifyColumn) return 'Stratified needs a column to stratify by.';
    if (balanced && targetClasses.length === 0)
      return 'No per-class counts are available for this column, so a balanced allocation cannot be built.';
    if (method === 'weighted' && !weightColumn) return 'Weighted needs a numeric weight column.';
    if (method === 'cluster' && !clusterColumn) return 'Cluster needs a column to cluster on.';
    if (method === 'time_stratified' && !timeColumn)
      return 'Time-stratified needs a date or timestamp column.';
    if (coordinated && links.length === 0)
      return 'Coordinated needs at least one related sheet — with none it is the single draw, run through a second endpoint.';
    if (coordinated && halfKeyed)
      return 'A key is both columns or neither. Name the parent column and this sheet’s column, or switch the link back to inferred keys.';
    return null;
  })();

  const buildBody = (): SampleRequestBody => {
    const step: SamplingStepSpec = { method, rounds, replace };
    if (sizeMode === 'count') step.sample_size = requested;
    else step.sample_fraction = sizePercent / 100;
    if (filterExpr.trim()) step.filter_expr = filterExpr.trim();

    if (method === 'stratified') {
      step.stratify_column = stratifyColumn;
      // Named classes narrow the population — only sent when the user chose
      // the allocation that does so, and the rail says as much beside it.
      if (balanced) step.class_targets = classTargets;
    }
    if (method === 'weighted') step.weight_column = weightColumn;
    if (method === 'cluster') {
      step.cluster_column = clusterColumn;
      step.num_clusters = numClusters;
    }
    if (method === 'time_stratified') {
      step.time_column = timeColumn;
      step.time_bins = timeBins;
    }

    return {
      dataset_id: datasetId!,
      version_number: version,
      sheet,
      target_total_volume: requested,
      sampling_steps: [step],
      // Goals are only claimed when they were actually asked for. A balanced
      // allocation gets its per-class minimums so the response can report which
      // class fell short and by how much.
      distribution_goals:
        balanced && stratifyColumn ? { column: stratifyColumn, class_minimums: classTargets } : null,
      seed,
      // The rows are in the artifact. Pulling the whole sample through the
      // browser to render five preview rows would be a read nobody asked for.
      return_data: false,
      deduplicate: dedupe,
      shuffle,
      sort_by: sortBy,
      sort_descending: sortDesc,
    };
  };

  /**
   * The coordinated body is the single body plus `driver_sheet` and `related`.
   * Everything else — steps, goals, seed, post-processing — describes the
   * DRIVER draw and is passed through unchanged, which is exactly what the
   * request model says those fields mean.
   *
   * No per-link `sampling_steps` are sent, so every referenced row is kept and
   * the §24 `cannot-subsample-parent` guard is never reached.
   */
  const buildCoordinatedBody = (): CoordinatedSampleRequestBody => {
    const base = buildBody();
    const related: RelatedSheetLinkSpec[] = links.map((l) => ({
      sheet: l.sheet,
      parent_sheet: l.parent,
      // Both keys or neither — half a key is a request the service rejects.
      left_on: l.keyMode === 'explicit' ? l.leftOn.trim() : null,
      right_on: l.keyMode === 'explicit' ? l.rightOn.trim() : null,
    }));
    return {
      dataset_id: base.dataset_id,
      version_number: base.version_number,
      driver_sheet: sheet!,
      target_total_volume: base.target_total_volume,
      sampling_steps: base.sampling_steps,
      distribution_goals: base.distribution_goals,
      seed: base.seed,
      return_data: false,
      deduplicate: base.deduplicate,
      shuffle: base.shuffle,
      sort_by: base.sort_by,
      sort_descending: base.sort_descending,
      related,
    };
  };

  const goals = result?.goal_validation ?? null;
  const steps = result?.steps_summary ?? [];
  const fillStep = steps.find((s) => s.method === 'random_fill') ?? null;
  const sampled = result ? coverage(result.sampled_count, result.original_count) : null;
  const preview = result?.preview ?? [];
  const previewColumns = result?.columns ?? [];
  const artifact = result?.sample_file ?? null;

  const columnNames = columns.length > 0 ? columns.map((c) => c.name) : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-9 shrink-0 items-center gap-2 px-3">
        <span className="text-label font-medium text-foreground">
          {dataset?.name ?? 'Sampling'}
        </span>
        {version != null && <Badge variant="glass">v{version}</Badge>}
        {sheet && <Identifier className="text-small text-muted-foreground">{sheet}</Identifier>}
        {dataset?.classification && <Badge variant="glass">{dataset.classification}</Badge>}
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ─────────────────────────── the draw ─────────────────────────── */}
        <aside className="flex w-[344px] shrink-0 flex-col border-r border-border bg-[var(--surface)]/40">
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
            <Section title="Source">
              {/* SIGNAL 1 of 2 — scope: what this draw is taken from. */}
              <div className="mb-2 flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-[5px] shrink-0 rounded-full bg-[var(--sig)] shadow-[0_0_8px_-1px_var(--sig)]"
                />
                <Footnote>the population every number below is measured against</Footnote>
              </div>

              <Field label="dataset">
                <select
                  value={datasetId ?? ''}
                  onChange={(e) => {
                    setPickedId(e.target.value);
                    setPickedVersion(null);
                    setPickedSheet(null);
                    setStratifyColumn(null);
                    setWeightColumn(null);
                    setClusterColumn(null);
                    setTimeColumn(null);
                    setSortBy(null);
                    setLinks([]);
                    resetRuns();
                  }}
                  className={controlClass}
                  data-testid="sampling-dataset"
                >
                  {datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </Field>

              <div className="flex gap-2">
                <Field label="version">
                  <select
                    value={version ?? ''}
                    onChange={(e) => {
                      setPickedVersion(Number(e.target.value));
                      setPickedSheet(null);
                      setLinks([]);
                      resetRuns();
                    }}
                    className={controlClass}
                    data-testid="sampling-version"
                  >
                    {versions.map((v) => (
                      <option key={v.version_number} value={v.version_number}>
                        v{v.version_number}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="sheet">
                  <select
                    value={sheet ?? ''}
                    onChange={(e) => {
                      setPickedSheet(e.target.value);
                      // A sheet cannot be its own related sheet, and a link
                      // whose parent just became the driver is still valid.
                      setLinks((prev) => prev.filter((l) => l.sheet !== e.target.value));
                      resetRuns();
                    }}
                    className={controlClass}
                    data-testid="sampling-sheet"
                  >
                    {sheets.map((s) => (
                      <option key={s.sheet_key} value={s.name}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              <Row
                label="Population"
                value={
                  population == null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <Identifier className="tabular-nums">{num(population)} rows</Identifier>
                  )
                }
              />
            </Section>

            <Section title="Scope">
              <div className="mb-2 flex gap-0.5" role="group" aria-label="Draw scope">
                {(['single', 'coordinated'] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setScope(s);
                      resetRuns();
                    }}
                    aria-pressed={scope === s}
                    data-testid={`sampling-scope-${s}`}
                    className={cn(
                      'flex-1 rounded px-2 py-1 text-micro transition-colors',
                      scope === s
                        ? 'bg-[var(--s6)] font-medium text-foreground'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {s === 'single' ? 'one sheet' : 'coordinated'}
                  </button>
                ))}
              </div>
              <Footnote>
                {coordinated
                  ? 'The sheet above is the driver: it is drawn exactly as a single sample, then each related sheet is filtered to the rows the draw references. One version, one seed, a referentially consistent slice.'
                  : 'One sheet, drawn on its own. Nothing else in the workbook is filtered, so a related sheet still holds rows the sample does not reference.'}
              </Footnote>
              {coordinated && (
                <Guard className="mt-1.5">
                  Coordinated is one dataset and one resolved version — the request carries a single{' '}
                  <Identifier>dataset_id</Identifier>. It coordinates SHEETS, not datasets.
                </Guard>
              )}
            </Section>

            {coordinated && (
              <Section
                title="Related sheets"
                action={
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={linkableSheets.length === 0 || !sheet}
                    onClick={addLink}
                    data-testid="sampling-add-link"
                  >
                    <Plus />
                    add
                  </Button>
                }
              >
                {links.length === 0 ? (
                  <LensEmpty>
                    No related sheet yet. Add one and it is filtered down to the rows the driver
                    sample references.
                  </LensEmpty>
                ) : (
                  links.map((l) => {
                    const parentColumns = columnsOf(sheets, l.parent);
                    const ownColumns = columnsOf(sheets, l.sheet);
                    const parentOptions = [
                      ...(sheet ? [sheet] : []),
                      ...links.filter((o) => o.id !== l.id).map((o) => o.sheet),
                    ];
                    return (
                      <div key={l.id} className="mb-2.5 last:mb-0" data-testid="sampling-link">
                        <div className="flex items-center gap-1">
                          <select
                            value={l.sheet}
                            onChange={(e) => retargetLink(l.id, e.target.value)}
                            className={controlClass}
                            aria-label="Related sheet"
                            data-testid="sampling-link-sheet"
                          >
                            <option value={l.sheet}>{l.sheet}</option>
                            {linkableSheets.map((s) => (
                              <option key={s.sheet_key} value={s.name}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`Remove ${l.sheet}`}
                            onClick={() => dropLink(l.id)}
                            data-testid="sampling-link-remove"
                          >
                            <X />
                          </Button>
                        </div>

                        <div className="mt-1 flex gap-1">
                          <Field label="filtered by">
                            <select
                              value={l.parent}
                              onChange={(e) =>
                                patchLink(l.id, { parent: e.target.value, leftOn: '' })
                              }
                              className={controlClass}
                              data-testid="sampling-link-parent"
                            >
                              {parentOptions.map((p) => (
                                <option key={p} value={p}>
                                  {p}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Field label="keys">
                            <select
                              value={l.keyMode}
                              onChange={(e) =>
                                patchLink(l.id, {
                                  keyMode: e.target.value === 'explicit' ? 'explicit' : 'infer',
                                })
                              }
                              className={controlClass}
                              data-testid="sampling-link-keymode"
                            >
                              <option value="infer">inferred</option>
                              <option value="explicit">named here</option>
                            </select>
                          </Field>
                        </div>

                        {l.keyMode === 'explicit' ? (
                          <div className="flex gap-1">
                            <Field label={`${l.parent}.left_on`}>
                              <select
                                value={l.leftOn}
                                onChange={(e) => patchLink(l.id, { leftOn: e.target.value })}
                                className={controlClass}
                                data-testid="sampling-link-left"
                              >
                                <option value="">— column —</option>
                                {parentColumns.map((c) => (
                                  <option key={c} value={c}>
                                    {c}
                                  </option>
                                ))}
                              </select>
                            </Field>
                            <Field label={`${l.sheet}.right_on`}>
                              <select
                                value={l.rightOn}
                                onChange={(e) => patchLink(l.id, { rightOn: e.target.value })}
                                className={controlClass}
                                data-testid="sampling-link-right"
                              >
                                <option value="">— column —</option>
                                {ownColumns.map((c) => (
                                  <option key={c} value={c}>
                                    {c}
                                  </option>
                                ))}
                              </select>
                            </Field>
                          </div>
                        ) : (
                          <Footnote>
                            Both keys omitted, so the service resolves them — a confirmed
                            relationship first, then an enabled{' '}
                            <Identifier>foreign_key</Identifier> rule. The result says which it
                            used.
                          </Footnote>
                        )}
                      </div>
                    );
                  })
                )}

                <Guard className="mt-2">
                  A related sheet is filtered, not sampled: it keeps every row whose key appears in
                  its parent's sampled rows. So its count is a consequence of the driver draw, and
                  it is reported over that sheet's own population — never over the driver's.
                </Guard>
              </Section>
            )}

            <Section title="Method">
              <div className="grid grid-cols-2 gap-0.5">
                {SAMPLING_METHODS.map((m) => (
                  <MethodChip
                    key={m}
                    method={m}
                    selected={method === m}
                    onSelect={() => setMethod(m)}
                  />
                ))}
              </div>
              <Footnote className="mt-1.5">
                `llm_semantic` exists in the engine and is not offered here: every mode of it needs
                an embeddings provider and key on the request body, and a browser form is the wrong
                place to type a credential.
              </Footnote>
            </Section>

            <Section title="Size">
              <div className="flex gap-2">
                <Field label="rows" hint={sizeMode === 'count' ? 'sent' : 'derived'}>
                  <input
                    type="number"
                    min={1}
                    value={sizeMode === 'count' ? sizeCount : requested}
                    onChange={(e) => {
                      setSizeMode('count');
                      setSizeCount(Math.max(1, parseIntOr(e.target.value, sizeCount)));
                    }}
                    className={fieldClass}
                    data-testid="sampling-size-count"
                  />
                </Field>
                <Field
                  label="% of population"
                  hint={sizeMode === 'fraction' ? 'sent' : 'derived'}
                >
                  <input
                    type="number"
                    min={0.01}
                    max={100}
                    step={0.1}
                    value={
                      sizeMode === 'fraction'
                        ? sizePercent
                        : population && population > 0
                          ? Number(((sizeCount / population) * 100).toFixed(2))
                          : ''
                    }
                    onChange={(e) => {
                      setSizeMode('fraction');
                      setSizePercent(
                        Math.min(100, Math.max(0.01, parseFloatOr(e.target.value, sizePercent))),
                      );
                    }}
                    className={fieldClass}
                    data-testid="sampling-size-fraction"
                  />
                </Field>
              </div>

              {/* The requested size, stated against the population it comes out
                  of. `Stat` will not render without that denominator, and on a
                  zero-row sheet it renders the state instead of a number.
                  Withheld entirely while the population is unknown: "0 of 0" and
                  "not counted yet" are different claims. */}
              {population == null ? (
                <Footnote className="mt-1">
                  Population not known yet — a size has no meaning until there is something to
                  measure it against.
                </Footnote>
              ) : (
                <Stat
                  className="mt-1"
                  name="request"
                  value={num(requested)}
                  coverage={coverage(Math.min(requested, population), population)}
                  data-testid="sampling-request-stat"
                />
              )}
              {population != null && population > 0 && requested > population && (
                <Footnote className="mt-1 pl-2.5 shadow-[inset_2px_0_0_rgba(250,178,25,.45)]">
                  {num(requested)} rows is more than the sheet holds. Without{' '}
                  <Identifier>replace</Identifier> the draw stops at {num(population)}.
                </Footnote>
              )}

              <div className="mt-2 flex gap-2">
                <Field label="rounds">
                  <input
                    type="number"
                    min={1}
                    value={rounds}
                    onChange={(e) => setRounds(Math.max(1, parseIntOr(e.target.value, rounds)))}
                    className={fieldClass}
                    data-testid="sampling-rounds"
                  />
                </Field>
                <div className="flex flex-1 items-end pb-1">
                  <label className="flex cursor-pointer items-center gap-2 text-small">
                    <Checkbox
                      checked={replace}
                      onCheckedChange={(v) => setReplace(v === true)}
                      data-testid="sampling-replace"
                    />
                    <Identifier className="text-footnote text-muted-foreground">replace</Identifier>
                  </label>
                </div>
              </div>
            </Section>

            {method === 'stratified' && (
              <Section title="Stratification">
                <Field label="stratify_column">
                  <select
                    value={stratifyColumn ?? ''}
                    onChange={(e) => setStratifyColumn(e.target.value || null)}
                    className={controlClass}
                    data-testid="sampling-stratify-column"
                  >
                    <option value="">— choose a column —</option>
                    {(categorical.length > 0 ? categorical : columns).map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                        {c.unique_count != null ? ` · ${num(c.unique_count)} classes` : ''}
                      </option>
                    ))}
                  </select>
                </Field>

                <div className="mb-2 flex gap-0.5" role="group" aria-label="Allocation">
                  {(['proportional', 'balanced'] as const).map((a) => (
                    <button
                      key={a}
                      type="button"
                      onClick={() => setAllocation(a)}
                      aria-pressed={allocation === a}
                      data-testid={`sampling-allocation-${a}`}
                      className={cn(
                        'flex-1 rounded px-2 py-1 text-micro transition-colors',
                        allocation === a
                          ? 'bg-[var(--s6)] font-medium text-foreground'
                          : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {a}
                    </button>
                  ))}
                </div>
                <Footnote className="mb-2">
                  {allocation === 'proportional'
                    ? 'Every class keeps its share of the sheet, and every row stays eligible.'
                    : 'Equal counts per named class. Naming classes is what narrows the population.'}
                </Footnote>

                {strata ? (
                  <StrataPanel strata={strata} balanced={balanced} targets={classTargets} />
                ) : stratifyColumn ? (
                  <LensEmpty>
                    No per-class counts for this column — it is not profiled as categorical, so a
                    balanced allocation cannot be built from it.
                  </LensEmpty>
                ) : null}
              </Section>
            )}

            {method === 'weighted' && (
              <Section title="Weights">
                <Field label="weight_column" hint="probability ∝ value">
                  <select
                    value={weightColumn ?? ''}
                    onChange={(e) => setWeightColumn(e.target.value || null)}
                    className={controlClass}
                    data-testid="sampling-weight-column"
                  >
                    <option value="">— choose a numeric column —</option>
                    {numeric.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {numeric.length === 0 && (
                  <Footnote>No numeric column is profiled on this sheet to weight by.</Footnote>
                )}
              </Section>
            )}

            {method === 'cluster' && (
              <Section title="Clusters">
                <Field label="cluster_column" hint="whole clusters are drawn">
                  <select
                    value={clusterColumn ?? ''}
                    onChange={(e) => setClusterColumn(e.target.value || null)}
                    className={controlClass}
                    data-testid="sampling-cluster-column"
                  >
                    <option value="">— choose a column —</option>
                    {(categorical.length > 0 ? categorical : columns).map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="num_clusters">
                  <input
                    type="number"
                    min={1}
                    value={numClusters}
                    onChange={(e) =>
                      setNumClusters(Math.max(1, parseIntOr(e.target.value, numClusters)))
                    }
                    className={fieldClass}
                    data-testid="sampling-num-clusters"
                  />
                </Field>
                <Footnote>
                  Cluster sampling takes every row of the chosen clusters, so the drawn count is a
                  consequence of cluster sizes rather than of the size above.
                </Footnote>
              </Section>
            )}

            {method === 'time_stratified' && (
              <Section title="Time buckets">
                <Field label="time_column">
                  <select
                    value={timeColumn ?? ''}
                    onChange={(e) => setTimeColumn(e.target.value || null)}
                    className={controlClass}
                    data-testid="sampling-time-column"
                  >
                    <option value="">— choose a date column —</option>
                    {(temporal.length > 0 ? temporal : columns).map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="time_bins">
                  <input
                    type="number"
                    min={1}
                    value={timeBins}
                    onChange={(e) => setTimeBins(Math.max(1, parseIntOr(e.target.value, timeBins)))}
                    className={fieldClass}
                    data-testid="sampling-time-bins"
                  />
                </Field>
              </Section>
            )}

            <Section title="Filter">
              <Field label="filter_expr" hint="SQL WHERE">
                <input
                  type="text"
                  value={filterExpr}
                  onChange={(e) => setFilterExpr(e.target.value)}
                  placeholder="region = 'US'"
                  className={fieldClass}
                  data-testid="sampling-filter"
                />
              </Field>
              <Footnote>
                A filter narrows the pool before the draw. The result reports how many rows it
                matched, out of the pool it was applied to, so the sample's real population is
                visible rather than assumed.
              </Footnote>
            </Section>

            <Section title="After the draw">
              <label className="mb-1 flex cursor-pointer items-center gap-2 text-small">
                <Checkbox
                  checked={dedupe}
                  onCheckedChange={(v) => setDedupe(v === true)}
                  data-testid="sampling-dedupe"
                />
                <Identifier className="text-footnote text-muted-foreground">deduplicate</Identifier>
              </label>
              <label className="mb-2 flex cursor-pointer items-center gap-2 text-small">
                <Checkbox
                  checked={shuffle}
                  onCheckedChange={(v) => setShuffle(v === true)}
                  data-testid="sampling-shuffle"
                />
                <Identifier className="text-footnote text-muted-foreground">shuffle</Identifier>
              </label>
              <div className="flex gap-2">
                <Field label="sort_by">
                  <select
                    value={sortBy ?? ''}
                    onChange={(e) => setSortBy(e.target.value || null)}
                    className={controlClass}
                    data-testid="sampling-sort-by"
                  >
                    <option value="">— none —</option>
                    {columnNames.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="flex flex-1 items-end pb-1">
                  <label className="flex cursor-pointer items-center gap-2 text-small">
                    <Checkbox
                      checked={sortDesc}
                      onCheckedChange={(v) => setSortDesc(v === true)}
                      data-testid="sampling-sort-desc"
                    />
                    <Identifier className="text-footnote text-muted-foreground">
                      descending
                    </Identifier>
                  </label>
                </div>
              </div>
            </Section>

            <Section title="Seed">
              <Field label="seed" hint={seed == null ? 'not reproducible' : 'locked'}>
                <input
                  type="number"
                  value={seed ?? ''}
                  onChange={(e) =>
                    setSeed(e.target.value === '' ? null : parseIntOr(e.target.value, DEFAULT_SEED))
                  }
                  className={fieldClass}
                  data-testid="sampling-seed"
                />
              </Field>
              {seed == null && (
                <Footnote className="pl-2.5 shadow-[inset_2px_0_0_rgba(250,178,25,.45)]">
                  The service does not invent a seed. Without one this draw cannot be reproduced —
                  the same configuration will return different rows.
                </Footnote>
              )}
            </Section>
          </div>

          <div className="shrink-0 px-3 py-2 shadow-[inset_0_1px_0_var(--r1)]">
            {blocker && <Footnote className="mb-1.5">{blocker}</Footnote>}
            <Button
              className="w-full"
              disabled={Boolean(blocker) || restricted || drawPending}
              onClick={() =>
                coordinated ? coord.mutate(buildCoordinatedBody()) : run.mutate(buildBody())
              }
              data-testid="sampling-run"
            >
              <Play className="size-3" />
              {drawPending
                ? 'Drawing…'
                : coordinated
                  ? `Draw across ${links.length + 1} sheets`
                  : 'Draw sample'}
            </Button>
          </div>
        </aside>

        {/* ────────────────────────── the result ────────────────────────── */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto px-4 py-3">
          {restricted ? (
            <LensRestricted what="Sampling" />
          ) : drawPending ? (
            <LensLoading>Drawing…</LensLoading>
          ) : drawError ? (
            <LensError>
              {errorText(drawError, {
                notFound: 'This dataset, version or sheet is not available to this seat.',
              })}
            </LensError>
          ) : !result || !sampled ? (
            <LensEmpty>
              No draw yet. Configure a method on the left and run it — every count on this side
              will arrive with the population it came out of.
            </LensEmpty>
          ) : (
            <div data-testid="sampling-result">
              {/* First read: the sample, and what it is a sample OF. */}
              <div className="flex flex-wrap items-start gap-6">
                <Metric
                  label="Population"
                  value={compact(result.original_count)}
                  note={`${num(result.original_count)} rows in the source`}
                />
                <Metric
                  label="Sampled"
                  value={compact(result.sampled_count)}
                  note={
                    share(sampled)
                      ? `${share(sampled)} of the population`
                      : 'no rows in the population'
                  }
                  data-testid="sampling-sampled"
                />
                <div className="min-w-0">
                  <Eyebrow>Target</Eyebrow>
                  <Figure className="mt-1">
                    {num(goals?.target_total_volume ?? result.sampled_count)}
                  </Figure>
                  <div className="mt-0.5">
                    {goals ? (
                      <Status kind={goals.met ? 'good' : 'warning'} className="text-small">
                        {goals.met ? 'goals met' : 'goals missed'}
                      </Status>
                    ) : (
                      <Footnote>no goals declared</Footnote>
                    )}
                  </div>
                </div>
              </div>

              <div className="mt-3 max-w-md">
                <Stat
                  name="drawn"
                  value={overPopulation(result.sampled_count, result.original_count)}
                  coverage={sampled}
                />
              </div>

              {/* In coordinated mode every figure above is the DRIVER's. Saying
                  so once, here, is what stops it being read as the slice. */}
              {coordResult && (
                <Footnote className="mt-1">
                  These are <Identifier>{coordResult.driver_sheet}</Identifier>'s figures — the
                  driver sheet's own population, drawn by the steps below. Each related sheet is
                  reported separately, against its own.
                </Footnote>
              )}

              {/* Provenance, from the RESPONSE rather than from the rail's
                  current state. Editing the configuration on the left does not
                  re-run anything, so a result that described the controls beside
                  it would start lying the moment one of them changed. */}
              <Footnote className="mt-1.5">
                drawn by{' '}
                {steps
                  .filter((s) => !(s.method in SYNTHESISED_STEPS))
                  .map((s) => s.method)
                  .join(' → ') || '—'}{' '}
                · target {num(goals?.target_total_volume ?? result.sampled_count)} · seed{' '}
                {result.reproducibility?.seed ?? 'none'}
                {result.reproducibility?.timestamp
                  ? ` · ${new Date(result.reproducibility.timestamp).toLocaleString()}`
                  : ''}
                . Changing the configuration does not change this result until you run again.
              </Footnote>

              {/* The engine's own top-up. A balanced draw that gets filled is no
                  longer balanced, and nothing in `sampled_count` says so. */}
              {fillStep && fillStep.rows_selected > 0 && (
                <Footnote
                  className="mt-3 pl-2.5 shadow-[inset_2px_0_0_rgba(250,178,25,.45)]"
                  data-testid="sampling-fill-warning"
                >
                  <span className="text-foreground">
                    {num(fillStep.rows_selected)} of {num(result.sampled_count)} sampled rows
                  </span>{' '}
                  were not drawn by <Identifier>{method}</Identifier>. The configured steps came in
                  under the target of {num(goals?.target_total_volume ?? result.sampled_count)}, so
                  the engine {SYNTHESISED_STEPS.random_fill} — those rows follow the pool's own
                  distribution, not this method's.
                </Footnote>
              )}

              {(goals?.warnings?.length ?? 0) > 0 && (
                <div className="mt-3" data-testid="sampling-goal-warnings">
                  {goals?.warnings?.map((w) => (
                    <Footnote key={w} className="pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
                      {w}
                    </Footnote>
                  ))}
                </div>
              )}

              {/* ── goal validation: the one genuine categorical series here ── */}
              {goals?.class_minimum_results && (
                <Section title="Distribution goals">
                  <Table containerClassName="flex-none">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="px-2 py-1 font-mono text-footnote normal-case">
                          class
                        </TableHead>
                        <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
                          required
                        </TableHead>
                        <TableHead className="px-2 py-1 text-right font-mono text-footnote normal-case">
                          actual
                        </TableHead>
                        <TableHead className="border-l border-border px-2 py-1 font-mono text-footnote normal-case">
                          of sample
                        </TableHead>
                        <TableHead className="px-2 py-1 font-mono text-footnote normal-case">
                          status
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {Object.entries(goals.class_minimum_results).map(([cls, r], rank) => {
                        const inSample = coverage(r.actual, result.sampled_count);
                        return (
                          <TableRow key={cls} data-testid="sampling-goal-row">
                            <TableCell className="px-2 py-1">
                              <span className="flex items-center gap-2">
                                <span
                                  aria-hidden="true"
                                  className="size-2 shrink-0 rounded-[2px]"
                                  style={{ background: vizSlot(rank) }}
                                />
                                <span className="truncate text-small">{cls}</span>
                              </span>
                            </TableCell>
                            <TableNumericCell className="px-2 py-1 text-micro text-muted-foreground">
                              {num(r.required)}
                            </TableNumericCell>
                            <TableNumericCell className="px-2 py-1 text-micro">
                              {num(r.actual)}
                            </TableNumericCell>
                            <TableCell className="border-l border-border px-2 py-1">
                              <span className="flex items-center gap-2">
                                <MagnitudeBar
                                  className="w-24"
                                  of={inSample}
                                  color={vizSlot(rank)}
                                />
                                <span className="text-footnote text-muted-foreground tabular-nums">
                                  {share(inSample) ?? 'no rows'}
                                </span>
                              </span>
                            </TableCell>
                            <TableCell className="px-2 py-1">
                              <Status kind={r.met ? 'good' : 'warning'} className="text-small">
                                {r.met ? 'met' : 'missed'}
                              </Status>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                  <Footnote className="mt-1.5">
                    Shares are of the {num(result.sampled_count)} sampled rows, not of the{' '}
                    {num(result.original_count)}-row source. A class allocation names the classes it
                    draws from, so anything unnamed is absent from both the sample and this table.
                  </Footnote>
                </Section>
              )}

              {goals?.distribution_results && (
                <Section title="Target distribution">
                  {Object.entries(goals.distribution_results).map(([cls, r], rank) => (
                    <div key={cls} className="mb-1.5 last:mb-0">
                      <div className="flex items-baseline gap-2">
                        <span
                          aria-hidden="true"
                          className="size-2 shrink-0 translate-y-px rounded-[2px]"
                          style={{ background: vizSlot(rank) }}
                        />
                        <span className="min-w-0 flex-1 truncate text-small">{cls}</span>
                        <span className="text-small text-muted-foreground tabular-nums">
                          {overPopulation(r.actual_count, result.sampled_count)}
                        </span>
                        <Status kind={r.met ? 'good' : 'warning'} className="text-footnote">
                          {`${(r.actual_pct * 100).toFixed(1)}% vs ${(r.target_pct * 100).toFixed(1)}%`}
                        </Status>
                      </div>
                      <MagnitudeBar
                        className="mt-0.5 h-1"
                        of={coverage(r.actual_count, result.sampled_count)}
                        color={vizSlot(rank)}
                      />
                    </div>
                  ))}
                </Section>
              )}

              {/* ─────────────────── per-step accounting ─────────────────── */}
              {steps.length > 0 && (
                <Section title="Per-step results">
                  <StepTable steps={steps} />
                  {steps.some((s) => s.method in SYNTHESISED_STEPS) && (
                    <Footnote className="mt-1.5">
                      Steps in grey were added by the engine, not configured here:{' '}
                      {steps
                        .filter((s) => s.method in SYNTHESISED_STEPS)
                        .map((s) => `${s.method} — ${SYNTHESISED_STEPS[s.method]}`)
                        .join('; ')}
                      .
                    </Footnote>
                  )}
                  {steps.flatMap((s) => s.warnings ?? []).length > 0 && (
                    <div className="mt-1.5">
                      {steps.flatMap((s, i) =>
                        (s.warnings ?? []).map((w) => (
                          <Footnote
                            key={`${i}-${w}`}
                            className="pl-2.5 shadow-[inset_2px_0_0_var(--r2)]"
                          >
                            {w}
                          </Footnote>
                        )),
                      )}
                    </div>
                  )}
                </Section>
              )}

              {/* ───────────────────────── preview ───────────────────────── */}
              <Section title="Sampled rows">
                {preview.length === 0 ? (
                  <LensEmpty>
                    The draw returned no rows. That is a result, not a failure — the pool the
                    method was applied to was empty after filtering.
                  </LensEmpty>
                ) : (
                  <>
                    <Table containerClassName="flex-none" data-testid="sampling-preview">
                      <TableHeader>
                        <TableRow className="hover:bg-transparent">
                          <TableHead className="w-8 px-2 py-1 text-right font-mono text-footnote normal-case">
                            #
                          </TableHead>
                          {previewColumns.map((c) => (
                            <TableHead
                              key={c.name}
                              className="px-2 py-1 font-mono text-footnote normal-case"
                              title={c.dtype}
                            >
                              {c.name}
                            </TableHead>
                          ))}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {preview.map((row, i) => (
                          <TableRow key={i}>
                            <TableNumericCell className="px-2 py-1 text-footnote text-muted-foreground">
                              {i + 1}
                            </TableNumericCell>
                            {previewColumns.map((c) => (
                              <TableCell key={c.name} className="px-2 py-1 text-micro">
                                {row[c.name] == null ? (
                                  <span className="text-muted-foreground">null</span>
                                ) : (
                                  num(row[c.name])
                                )}
                              </TableCell>
                            ))}
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    <Footnote className="mt-1.5">
                      Showing {preview.length} of {num(result.sampled_count)} sampled rows — the
                      service returns a fixed {PREVIEW_ROWS}-row preview whatever the sample size.
                      The full sample is the artifact, not this table.
                    </Footnote>
                  </>
                )}
              </Section>

              {/* Everything above described the DRIVER, through the single-draw
                  reading model. This is the only part coordinated mode adds. */}
              {coordResult && (
                <RelatedSheetsPanel coord={coordResult} versionSheets={sheets.length} />
              )}
            </div>
          )}
        </main>

        {/* ──────────────────── reproducibility & output ──────────────────── */}
        <aside className="flex w-[300px] shrink-0 flex-col overflow-y-auto border-l border-border bg-[var(--surface)]/40 px-3 py-3">
          <Section title="Reproducibility">
            <div className="mb-1 flex items-center gap-2">
              {/* SIGNAL 2 of 2 — liveness: this exact run. Only once a run
                  exists; before that there is nothing to identify. */}
              {result && (
                <span
                  aria-hidden="true"
                  className="size-[5px] shrink-0 rounded-full bg-[var(--sig)] shadow-[0_0_8px_-1px_var(--sig)]"
                />
              )}
              <Eyebrow>Seed</Eyebrow>
            </div>
            <Figure className="font-mono">
              {result?.reproducibility?.seed != null
                ? String(result.reproducibility.seed)
                : seed != null
                  ? String(seed)
                  : 'none'}
            </Figure>
            <Footnote className="mt-1">
              {(result ? result.reproducibility?.seed : seed) == null
                ? 'No seed. The same configuration will return different rows on every run, and the audit record cannot reconstruct this one.'
                : 'Same seed and same configuration reproduce the same sample. The seed is recorded with the run.'}
            </Footnote>

            {result?.reproducibility?.timestamp && (
              <Footnote className="mt-1.5">
                run {new Date(result.reproducibility.timestamp).toLocaleString()}
              </Footnote>
            )}
          </Section>

          <Section title="Output">
            {artifact ? (
              <div data-testid="sampling-artifact">
                <div className="rounded-md bg-card px-2 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-micro" title={artifact}>
                      {artifact}
                    </span>
                    <a
                      href={`${ANALYTICS_BASE}/samples/${encodeURIComponent(artifact)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
                      title="Download"
                      data-testid="sampling-artifact-download"
                    >
                      <Download className="size-3" />
                    </a>
                  </div>
                  <Footnote className="mt-1">
                    sample_output · kept {SAMPLE_RETENTION_DAYS} days from the moment it was
                    written
                  </Footnote>
                </div>

                <div className="mt-2 flex gap-1">
                  {EXPORT_FORMATS.map((f) => (
                    <Button
                      key={f}
                      variant="outline"
                      size="xs"
                      className="flex-1"
                      disabled={exportSample.isPending}
                      onClick={() => exportSample.mutate({ filename: artifact, format: f })}
                      data-testid={`sampling-export-${f}`}
                    >
                      {f}
                    </Button>
                  ))}
                </div>
                <Footnote className="mt-1">
                  An export is its own artifact with its own, much shorter clock —{' '}
                  {EXPORT_RETENTION_DAYS} days against the sample's {SAMPLE_RETENTION_DAYS}.
                </Footnote>
                {coordResult && coordResult.related.length > 0 && (
                  <Guard className="mt-2">
                    This is the DRIVER's artifact. Each related sheet writes its own —{' '}
                    {coordResult.related.length} more, listed with their sheets — so the slice is{' '}
                    {coordResult.related.length + 1} files on the same {SAMPLE_RETENTION_DAYS}-day
                    clock, not one.
                  </Guard>
                )}
              </div>
            ) : (
              <LensEmpty>
                No artifact yet. A run writes the full sample as a parquet artifact and links it
                here; the preview is never the sample.
              </LensEmpty>
            )}
          </Section>

          <div className="space-y-2">
            <Footnote className="pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
              Retention is stamped when the artifact is written, so editing the policy later never
              shortens something already stored. Nothing here deletes an artifact — the clock is
              the only lifetime it has.
            </Footnote>
            <Footnote className="pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
              Nothing runs the sweep on a schedule. A sample past its window is eligible for
              collection, not collected: it stays listed and downloadable until a sweep is actually
              run, so treat “past window” as gone rather than as still available.
            </Footnote>
            <Footnote className="pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
              Samples are listed from the artifacts table, never a bucket scan. The row is the only
              thing that maps a filename to a storage key, so a blob written without one is
              unreachable by everyone, superusers included.
            </Footnote>
          </div>
        </aside>
      </div>
    </div>
  );
}
