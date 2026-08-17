/**
 * The relationships lens: how this dataset joins to itself and to others, what
 * the evidence behind each edge actually is, and where the dataset came from.
 *
 * Four things here are easy to render dishonestly and are handled on purpose:
 *
 *  - An edge pointing at a dataset this seat cannot read is dropped from both
 *    `items` and `total`. So the count is "relationships you can see" — never
 *    labelled as the dataset's total.
 *  - `suggest` reports `skipped`, meaning the candidate cap bit and the sweep
 *    was NOT exhaustive. A run that skipped pairs says so; silence would read
 *    as "nothing else exists".
 *  - Discovery measures, it never confirms. An unreviewed edge cannot back a
 *    cross-dataset join, so the review verbs sit on the evidence rather than in
 *    a separate screen.
 *  - A lineage entry whose other side is not visible keeps its row: the edge is
 *    real even when the name is not. That is never phrased as "access denied" —
 *    a cross-tenant read is answered as absence (404), and saying "denied"
 *    would disclose the existence the 404 exists to hide.
 *
 * Evidence shapes come from the service: a `statistical` edge carries
 * `coverage` / `target_uniqueness` / `child_distinct` / `matched_distinct`, an
 * `fk_rule` edge carries `rule_name`, and a declaration later corroborated by a
 * scan keeps its own evidence with the measurement nested under
 * `evidence.statistical` — which is why the reader below looks in both places.
 */

import { useState, type ReactNode } from 'react';
import {
  ArrowRight,
  Check,
  CornerDownRight,
  GitBranch,
  Play,
  Search,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage, type Coverage } from '@/shared/components/instrument/coverage';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat } from '@/shared/components/instrument/Stat';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { middleTruncate } from '@/shared/components/instrument/shape';
import {
  Eyebrow,
  Footnote,
  Identifier,
  Metric,
} from '@/shared/components/instrument/Typography';
import { errorText } from '@/shared/lib/analyticsClient';
import { num } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import {
  isRestricted,
  useLineage,
  useRelationships,
  useReviewRelationship,
  useSeedRelationships,
  useSuggestRelationships,
  type Relationship,
} from '../../hooks/useAnalysis';
import {
  LINEAGE_ROW_CAP,
  SEARCH_MIN_CHARS,
  errorCodeOf,
  lineageBranch,
  useColumnSearch,
  useDatasetSearch,
  useJoinExecute,
  useJoinPreview,
  useJoinPublish,
  useLineageGraph,
  type JoinHow,
  type JoinWarnings,
  type LineageBranchRow,
} from '../../hooks/useJoins';
import { fieldClass } from '../fieldStyles';
import { DtypeChip, LensEmpty, LensError, LensLoading, LensRestricted } from './primitives';

/* ------------------------------------------------------------- local helpers */

/**
 * A dock section — sentence case, with the hairline that carries the eye across
 * to the count, exactly as the prototype's `.sect > .h` does.
 *
 * `Section` in ./primitives is the shared version, but it renders an UPPERCASE
 * micro-label, which INSTRUMENT rule 5 names as the loudest generated-looking
 * tell. This is deliberately the same shape (title / meta / children) so the
 * two can be collapsed into one the moment the shared primitive moves.
 */
function LensSection({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-5">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="shrink-0 text-small font-medium text-foreground">{title}</h3>
        {meta}
        <span className="h-px min-w-2 flex-1 bg-[var(--r1)]" />
      </div>
      {children}
    </section>
  );
}

/** A ratio in [0,1] as a Coverage, so the magnitude marks can take it. */
function share(ratio: number): Coverage {
  return coverage(Math.round(Math.min(1, Math.max(0, ratio)) * 1000), 1000);
}

function pct(ratio: number): string {
  return `${(ratio * 100).toFixed(1).replace(/\.0$/, '')}%`;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function numberAt(o: Record<string, unknown>, key: string): number | null {
  const v = o[key];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function stringAt(o: Record<string, unknown>, key: string): string | null {
  const v = o[key];
  return typeof v === 'string' && v.trim() ? v : null;
}

interface EdgeEvidence {
  /** Share of the child's distinct keys that exist upstream. */
  overlap: number | null;
  /** Share of the target's values that are unique — what orients the edge. */
  uniqueness: number | null;
  childDistinct: number | null;
  matchedDistinct: number | null;
  ruleName: string | null;
  declaredBy: string | null;
  /** The measurement arrived under `evidence.statistical`: a scan corroborating a declaration. */
  corroborated: boolean;
}

function readEvidence(edge: Relationship): EdgeEvidence {
  const root = edge.evidence ?? {};
  const nested = asRecord(root.statistical);
  const stats = nested ?? root;
  return {
    overlap: numberAt(stats, 'coverage'),
    uniqueness: numberAt(stats, 'target_uniqueness'),
    childDistinct: numberAt(stats, 'child_distinct'),
    matchedDistinct: numberAt(stats, 'matched_distinct'),
    ruleName: stringAt(root, 'rule_name'),
    declaredBy: stringAt(root, 'declared_by'),
    corroborated: nested != null,
  };
}

function statusKind(status: string): StatusKind {
  if (status === 'confirmed') return 'good';
  if (status === 'suggested') return 'warning';
  return 'unknown';
}

/** The word beside the shape. A `suggested` edge is a candidate, not a fact. */
function statusWord(status: string): string {
  if (status === 'suggested') return 'candidate';
  return status;
}

function qualified(sheet: string | null | undefined, column: string | null | undefined): string {
  const col = column ?? '—';
  return sheet ? `${sheet}.${col}` : col;
}

/* ---------------------------------------------------------------- edge parts */

function EdgePair({ edge, struck }: { edge: Relationship; struck?: boolean }) {
  const from = qualified(edge.from_sheet, edge.from_column);
  const to = qualified(edge.to_sheet, edge.to_column);
  const ink = struck ? 'text-muted-foreground line-through' : 'text-foreground';
  // Stacked rather than side by side: at 384px a `sheet.column → sheet.column`
  // line breaks mid-identifier, and a split column name is a different name.
  return (
    <div className="mt-1.5 font-mono text-micro">
      <div className={cn('truncate', ink)} title={from}>
        {from}
      </div>
      <div className="flex items-center gap-1">
        <ArrowRight className="size-2.5 shrink-0 text-muted-foreground" />
        <span className={cn('min-w-0 truncate', ink)} title={to}>
          {to}
        </span>
      </div>
    </div>
  );
}

/** One measured signal: a lowercase mono identifier, a neutral bar, a figure. */
function EvidenceRow({ label, of, value }: { label: string; of: Coverage; value: string }) {
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <Identifier className="w-[86px] shrink-0 truncate text-footnote text-muted-foreground">
        {label}
      </Identifier>
      <MagnitudeBar of={of} className="min-w-0 flex-1" />
      <span className="w-10 shrink-0 text-right text-micro font-medium text-foreground tabular-nums">
        {value}
      </span>
    </div>
  );
}

/**
 * The evidence behind one edge, plus a plain-language line saying what kind of
 * claim it is. A declaration has no statistics and must not be dressed as if it
 * did — the rule *is* the provenance.
 */
function EdgeEvidence({ edge }: { edge: Relationship }) {
  const ev = readEvidence(edge);
  const measured = ev.overlap != null || ev.uniqueness != null;

  const provenance: string[] = [];
  if (ev.matchedDistinct != null && ev.childDistinct != null) {
    provenance.push(
      `${num(ev.matchedDistinct)} of ${num(ev.childDistinct)} distinct keys matched`,
    );
  }
  if (ev.ruleName) provenance.push(`from quality rule ${ev.ruleName}`);
  if (ev.declaredBy) provenance.push('declared by hand');
  if (ev.corroborated) provenance.push('a later scan corroborated it');

  return (
    <>
      {edge.confidence != null && (
        <EvidenceRow
          label="confidence"
          of={share(edge.confidence)}
          value={edge.confidence.toFixed(2)}
        />
      )}
      {ev.overlap != null && (
        <EvidenceRow
          label="overlap"
          of={
            ev.matchedDistinct != null && ev.childDistinct != null
              ? coverage(ev.matchedDistinct, ev.childDistinct)
              : share(ev.overlap)
          }
          value={pct(ev.overlap)}
        />
      )}
      {ev.uniqueness != null && (
        <EvidenceRow label="uniqueness" of={share(ev.uniqueness)} value={pct(ev.uniqueness)} />
      )}
      <Footnote className="mt-1.5">
        {provenance.length > 0
          ? provenance.join(' · ')
          : measured
            ? 'Measured on the current version.'
            : 'Declared, not measured — this edge carries no join-key statistics.'}
      </Footnote>
    </>
  );
}

/* ------------------------------------------------------------------- the two
 * edge presentations: a candidate is a claim awaiting a human and earns a
 * surface; a reviewed edge is settled and lives in the list register.
 */

function CandidateCard({
  edge,
  onReview,
  reviewing,
}: {
  edge: Relationship;
  onReview: (id: string, action: 'confirm' | 'reject') => void;
  reviewing: boolean;
}) {
  return (
    <div
      className="mb-2 rounded-md bg-card px-2.5 py-2 shadow-[var(--hi)]"
      data-testid="relationship"
    >
      <div className="flex items-center gap-2">
        <Status kind={statusKind(edge.status)} className="text-micro font-medium">
          {statusWord(edge.status)}
        </Status>
        <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
          {edge.method ?? 'unknown method'}
        </Identifier>
      </div>

      <EdgePair edge={edge} />
      <EdgeEvidence edge={edge} />

      <div className="mt-2 flex gap-1">
        <Button
          size="xs"
          variant="secondary"
          disabled={reviewing}
          onClick={() => onReview(edge.id, 'confirm')}
          data-testid="relationship-confirm"
        >
          <Check className="size-3" />
          Confirm
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={reviewing}
          onClick={() => onReview(edge.id, 'reject')}
          data-testid="relationship-reject"
        >
          <X className="size-3" />
          Reject
        </Button>
      </div>
    </div>
  );
}

function ReviewedRow({ edge }: { edge: Relationship }) {
  const rejected = edge.status === 'rejected';
  const ev = readEvidence(edge);
  const meta = [
    edge.method ?? null,
    edge.confidence != null ? edge.confidence.toFixed(2) : null,
    ev.overlap != null ? `${pct(ev.overlap)} overlap` : null,
    rejected ? 'kept as a record; discovery will not re-propose it' : 'can back a join',
  ].filter((v): v is string => Boolean(v));

  return (
    <div className="py-2" data-testid="relationship">
      <Status kind={statusKind(edge.status)} className="text-micro font-medium">
        {statusWord(edge.status)}
      </Status>
      <EdgePair edge={edge} struck={rejected} />
      <Footnote className="mt-1">{meta.join(' · ')}</Footnote>
    </div>
  );
}

/* ----------------------------------------------------------------- lineage */

function LineageRow({
  testid,
  hidden,
  name,
  version,
  relation,
}: {
  testid: string;
  hidden: boolean;
  name: string | null | undefined;
  version: number | null | undefined;
  relation: string | null | undefined;
}) {
  return (
    <div className="flex items-baseline gap-2 py-1" data-testid={testid}>
      {/* A hidden side keeps its row: the edge is real even when the name is not. */}
      {hidden ? (
        <span className="min-w-0 flex-1 text-small text-muted-foreground italic">
          A dataset this seat cannot read
        </span>
      ) : (
        <span className="min-w-0 flex-1 truncate text-small text-foreground">
          {name ?? 'Unknown'}
        </span>
      )}
      {version != null && (
        <Identifier className="shrink-0 text-footnote text-muted-foreground">v{version}</Identifier>
      )}
      {relation && (
        <Identifier className="shrink-0 text-footnote text-muted-foreground">{relation}</Identifier>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- join builder */

/** One measured field, named exactly as the API names it. */
function FieldRow({
  label,
  value,
  strong,
}: {
  label: string;
  value: ReactNode;
  strong?: boolean;
}) {
  return (
    <div className="flex h-5 items-baseline gap-2">
      <Identifier className="min-w-0 flex-1 truncate text-footnote text-muted-foreground">
        {label}
      </Identifier>
      <span
        className={cn(
          'shrink-0 text-micro tabular-nums',
          strong ? 'font-semibold text-foreground' : 'text-foreground',
        )}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * The refusal states of the three join calls, as ONE exhaustive decision.
 *
 * A join reads raw values from BOTH sides, so `ensure_raw_access` runs twice
 * before anything is measured — a viewer or editor on either dataset is
 * refused, and that is a state, not a failure. Everything else branches on the
 * problem+json `code`, never on prose.
 */
function JoinRefusal({ error, what }: { error: unknown; what: string }) {
  if (!error) return null;
  if (isRestricted(error)) return <LensRestricted what={what} />;
  const code = errorCodeOf(error);
  if (code === 'relationship-not-confirmed')
    return (
      <LensError>
        This edge is no longer confirmed, so it cannot drive a join — discovery never confirms, and
        a join binds to a reviewed edge or to nothing.
      </LensError>
    );
  if (code === 'relationship-endpoint-mismatch')
    return (
      <LensError>
        The key this edge names is not present on that side in this version. The edge outlived a
        schema change; re-run discovery before joining.
      </LensError>
    );
  return (
    <LensError>
      {errorText(error, {
        notFound: 'That relationship is gone, or its other side is not readable from this seat.',
      })}
    </LensError>
  );
}

/**
 * Everything the pre-flight measured, plus the one number that matters.
 *
 * R11: a match count with no denominator is exactly the silent-wrong-answer
 * this whole codebase exists to prevent, so matched rows are stated OVER the
 * side they came from. The service returns unmatched as a percentage of rows
 * (two decimals), so the row counts here are derived from it — which is said
 * out loud rather than presented as if it had been counted.
 */
function JoinWarningPanel({ w, how, source }: { w: JoinWarnings; how: JoinHow; source: string }) {
  const unmatchedLeft = Math.round((w.left_rows * w.unmatched_left_pct) / 100);
  const unmatchedRight = Math.round((w.right_rows * w.unmatched_right_pct) / 100);
  const matchedLeft = Math.max(0, w.left_rows - unmatchedLeft);
  const matchedRight = Math.max(0, w.right_rows - unmatchedRight);
  const collisions = w.column_collisions ?? [];

  const shape: { kind: StatusKind; word: string } = w.many_to_many
    ? { kind: 'critical', word: 'many to many' }
    : w.row_expansion_factor > 1
      ? { kind: 'warning', word: 'fan-out' }
      : { kind: 'good', word: 'no fan-out' };

  return (
    <div data-testid="join-warnings">
      <div className="flex items-center gap-2">
        <Status kind={shape.kind} className="text-micro font-medium">
          {shape.word}
        </Status>
        <Identifier className="ml-auto text-footnote text-muted-foreground">{source}</Identifier>
      </div>

      <div className="mt-2 flex flex-col gap-1">
        <Stat
          name="left"
          value={num(matchedLeft)}
          coverage={coverage(matchedLeft, w.left_rows, 'left rows')}
        />
        <MagnitudeBar of={coverage(matchedLeft, w.left_rows, 'left rows')} />
        <Stat
          name="right"
          value={num(matchedRight)}
          coverage={coverage(matchedRight, w.right_rows, 'right rows')}
        />
        <MagnitudeBar of={coverage(matchedRight, w.right_rows, 'right rows')} />
      </div>
      <Footnote className="mt-1.5">
        Rows that find a partner. The API returns unmatched as a percentage of rows to two
        decimals, so these two counts are derived from it, not counted separately.
      </Footnote>

      <div className="mt-2 flex flex-col">
        <FieldRow label="left_rows" value={num(w.left_rows)} />
        <FieldRow label="right_rows" value={num(w.right_rows)} />
        <FieldRow label="left_duplicate_keys" value={num(w.left_duplicate_keys)} />
        <FieldRow label="right_duplicate_keys" value={num(w.right_duplicate_keys)} />
        <FieldRow label="many_to_many" value={String(w.many_to_many)} strong={w.many_to_many} />
        <FieldRow
          label="estimated_output_rows"
          value={num(w.estimated_output_rows)}
          strong={w.row_expansion_factor > 1}
        />
        <FieldRow
          label="row_expansion_factor"
          value={`${w.row_expansion_factor.toFixed(2)}×`}
          strong={w.row_expansion_factor > 1}
        />
        <FieldRow label="unmatched_left_pct" value={`${w.unmatched_left_pct}%`} />
        <FieldRow label="unmatched_right_pct" value={`${w.unmatched_right_pct}%`} />
        <FieldRow label="column_collisions" value={num(collisions.length)} />
      </div>

      {w.many_to_many ? (
        <Guard tone="critical" className="mt-1.5">
          Both sides repeat their key, so rows multiply:{' '}
          <b className="font-semibold text-foreground">{num(w.left_rows)}</b> left rows become an
          estimated <b className="font-semibold text-foreground">{num(w.estimated_output_rows)}</b>{' '}
          — {w.row_expansion_factor.toFixed(2)}× per input row. Fix the grain, or accept the
          fan-out knowingly.
        </Guard>
      ) : (
        <Guard className="mt-1.5">
          {how === 'inner'
            ? `${num(unmatchedLeft)} left rows (${w.unmatched_left_pct}%) match nothing and are dropped by inner. Switch to left to keep them with nulls.`
            : `left keeps all ${num(w.left_rows)} left rows; the ${num(unmatchedLeft)} unmatched ones (${w.unmatched_left_pct}%) arrive with nulls on the right.`}{' '}
          {num(unmatchedRight)} right rows ({w.unmatched_right_pct}%) are never referenced either
          way.
        </Guard>
      )}

      {collisions.length > 0 && (
        <Guard className="mt-1">
          <Identifier className="text-foreground">{collisions.join(', ')}</Identifier> exist on both
          sides. The right side&apos;s copy is re-aliased with that sheet&apos;s key as a prefix, so
          nothing is silently overwritten — the left column keeps its name.
        </Guard>
      )}
    </div>
  );
}

/** The five-row sample: real joined data, which is why preview is gated. */
function JoinSample({
  rows,
  columns,
}: {
  rows: Record<string, unknown>[];
  columns: string[];
}) {
  const names = columns.length > 0 ? columns : Object.keys(rows[0] ?? {});
  if (rows.length === 0 || names.length === 0) return null;
  return (
    <>
      <Table containerClassName="mt-2 max-h-32" className="w-max min-w-full" data-testid="join-sample">
        <TableHeader>
          <TableRow>
            {names.map((n) => (
              <TableHead key={n} className="whitespace-nowrap font-mono text-footnote">
                {middleTruncate(n, 16)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {names.map((n) => {
                const v = row[n];
                const text = v === null || v === undefined ? null : String(v);
                return (
                  <TableCell
                    key={n}
                    title={text ?? undefined}
                    className="h-6 whitespace-nowrap text-micro"
                  >
                    {text === null ? (
                      <span className="text-muted-foreground/50">—</span>
                    ) : (
                      middleTruncate(text, 18)
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Footnote className="mt-1">
        {num(rows.length)} sample rows over {num(names.length)} output columns. Real joined values
        from both sides — nothing masks a join output, which is why preview needs raw access on
        each.
      </Footnote>
    </>
  );
}

/**
 * The join builder.
 *
 * The one invariant it exists to keep true: **a cross-dataset join binds to a
 * CONFIRMED relationship id, never to free-form keys.** So the picker lists
 * confirmed edges and nothing else — candidates in review are absent, not
 * greyed, because a greyed row invites the question "how do I enable this"
 * whose answer is "review it", which the queue above already asks.
 *
 * Neither side's version is offered: `left_version` and `right_version` default
 * to current, and a control that restates a default is noise. `select_columns`
 * is likewise unsent — the projection over a join output is a decision worth
 * making after seeing the collisions, not before.
 *
 * Three steps, three different kinds of consequence, so they are three buttons
 * rather than one wizard: preview persists NOTHING; execute is a write on the
 * left dataset that outlives the request; publish creates a dataset or version
 * with both parents in lineage.
 */
function JoinBuilder({ confirmed }: { confirmed: Relationship[] }) {
  const [relId, setRelId] = useState<string | null>(null);
  const [how, setHow] = useState<JoinHow>('inner');
  const [publishMode, setPublishMode] = useState<'new_dataset' | 'new_version'>('new_dataset');
  const [publishName, setPublishName] = useState('');

  const preview = useJoinPreview();
  const execute = useJoinExecute();
  const publish = useJoinPublish();

  const selected = confirmed.find((e) => e.id === relId) ?? confirmed[0] ?? null;

  // A result belongs to the spec that produced it. Changing either half of the
  // spec drops all three, rather than leaving a run id on screen that was
  // measured against a different edge.
  const retarget = () => {
    preview.reset();
    execute.reset();
    publish.reset();
  };

  if (confirmed.length === 0) {
    return (
      <LensEmpty>
        No confirmed relationship, so there is nothing a join could bind to. Confirm a candidate
        above — a join takes a relationship id, never a pair of column names.
      </LensEmpty>
    );
  }

  const spec = selected ? { relationship_id: selected.id, how } : null;
  const warnings = execute.data?.warnings ?? preview.data?.warnings ?? null;
  const warningSource = execute.data ? 'measured by the run' : 'measured by preview';
  const estimated = execute.data?.warnings.estimated_output_rows ?? 0;

  return (
    <div data-testid="join-builder">
      <select
        aria-label="Relationship"
        data-testid="join-relationship"
        value={selected?.id ?? ''}
        onChange={(e) => {
          setRelId(e.target.value);
          retarget();
        }}
        className={cn(fieldClass, 'font-mono')}
      >
        {confirmed.map((e) => (
          <option key={e.id} value={e.id}>
            {qualified(e.from_sheet, e.from_column)} → {qualified(e.to_sheet, e.to_column)}
          </option>
        ))}
      </select>

      {selected && (
        <>
          <EdgePair edge={selected} />
          <Footnote className="mt-1">
            <Identifier>relationship_id</Identifier>{' '}
            <span className="text-foreground">{middleTruncate(selected.id, 22)}</span> · right side{' '}
            <Identifier>{middleTruncate(selected.to_dataset_id ?? '—', 18)}</Identifier>
          </Footnote>
        </>
      )}

      <div className="mt-2 flex items-center gap-1.5">
        <select
          aria-label="Join type"
          data-testid="join-how"
          value={how}
          onChange={(e) => {
            setHow(e.target.value as JoinHow);
            retarget();
          }}
          className={cn(fieldClass, 'w-[86px] font-mono')}
        >
          <option value="inner">inner</option>
          <option value="left">left</option>
        </select>
        <Footnote className="min-w-0 flex-1">
          equi-join, one column — no right, full, cross or anti
        </Footnote>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        <Button
          size="xs"
          variant="ghost"
          disabled={!spec || preview.isPending}
          onClick={() => spec && preview.mutate(spec)}
          title="Measure the join. Persists nothing."
          data-testid="join-preview"
        >
          <Play className="size-3" />
          {preview.isPending ? 'Measuring…' : 'Preview'}
        </Button>
        {/* The dock spends no accent: a write outranks a measurement on weight
            and elevation, not on hue. */}
        <Button
          size="xs"
          variant="secondary"
          disabled={!spec || execute.isPending}
          onClick={() => spec && execute.mutate(spec)}
          title="Run the join and store the result as a join_output artifact"
          data-testid="join-execute"
        >
          {execute.isPending ? 'Running…' : 'Execute'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={!execute.data || publish.isPending}
          onClick={() =>
            execute.data &&
            publish.mutate({
              runId: execute.data.run_id,
              mode: publishMode,
              name: publishMode === 'new_dataset' ? publishName.trim() || null : null,
            })
          }
          title="Publish only exists once a run has produced an artifact"
          data-testid="join-publish"
        >
          <Upload className="size-3" />
          {publish.isPending ? 'Publishing…' : 'Publish'}
        </Button>
      </div>

      <div className="mt-2">
        <JoinRefusal error={preview.error} what="Previewing a join" />
      </div>

      {warnings && (
        <div className="mt-2">
          <JoinWarningPanel w={warnings} how={how} source={warningSource} />
        </div>
      )}

      {preview.data && !execute.data && (
        <JoinSample rows={preview.data.preview ?? []} columns={preview.data.output_columns ?? []} />
      )}

      <div className="mt-2">
        <JoinRefusal error={execute.error} what="Running a join" />
      </div>

      {execute.data && (
        <div className="mt-2" data-testid="join-run">
          {estimated > 0 ? (
            <Stat
              name="written"
              value={num(execute.data.row_count)}
              coverage={coverage(execute.data.row_count, estimated, 'estimated rows')}
            />
          ) : (
            <Footnote className="tabular-nums">
              {num(execute.data.row_count)} rows written; the pre-flight estimated none, so there is
              no denominator to state.
            </Footnote>
          )}
          <Footnote className="mt-1">
            run <Identifier className="text-foreground">{middleTruncate(execute.data.run_id, 20)}</Identifier> ·{' '}
            <Identifier>{middleTruncate(execute.data.sample_file, 26)}</Identifier>
          </Footnote>
          <Guard className="mt-1.5">
            The output is registered as a <Identifier>join_output</Identifier> artifact with the
            retention its kind declares — it is listed in the Library lens. Nothing sweeps on a
            timer: an expired artifact is still listed and still readable until a sweep actually
            runs, so expiry here is a status, not a deletion.
          </Guard>

          <div className="mt-2 flex items-center gap-1.5">
            <select
              aria-label="Publish mode"
              data-testid="join-publish-mode"
              value={publishMode}
              onChange={(e) => setPublishMode(e.target.value as 'new_dataset' | 'new_version')}
              className={cn(fieldClass, 'w-[118px] font-mono')}
            >
              <option value="new_dataset">new_dataset</option>
              <option value="new_version">new_version</option>
            </select>
            <input
              aria-label="Published dataset name"
              data-testid="join-publish-name"
              value={publishName}
              disabled={publishMode !== 'new_dataset'}
              onChange={(e) => setPublishName(e.target.value)}
              placeholder="name — optional"
              className={cn(fieldClass, 'min-w-0 flex-1', publishMode !== 'new_dataset' && 'opacity-45')}
            />
          </div>
          <Footnote className="mt-1">
            {publishMode === 'new_dataset'
              ? 'A new dataset. Leave the name empty and the service derives one from the left side.'
              : 'A new version of the LEFT side of this relationship — the join output becomes its next version.'}
          </Footnote>
        </div>
      )}

      <div className="mt-2">
        <JoinRefusal error={publish.error} what="Publishing a join output" />
      </div>

      {publish.data && (
        <Footnote className="mt-2" data-testid="join-published">
          Published{' '}
          <span className="font-medium text-foreground">{publish.data.dataset_name}</span>{' '}
          <Identifier className="text-foreground">v{publish.data.version_number}</Identifier> ·{' '}
          <Identifier>{publish.data.mode}</Identifier>. Both parents are recorded, so it appears in
          the derivation graph below with an edge to each source.
        </Footnote>
      )}
    </div>
  );
}

/* --------------------------------------------------------- derivation graph */

function LineageGraphRow({ row }: { row: LineageBranchRow }) {
  return (
    <div
      className="flex h-6 items-center gap-1.5"
      style={{ paddingLeft: `${row.depth * 11}px` }}
      data-testid="lineage-graph-node"
    >
      <CornerDownRight className="size-2.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span
        className={cn(
          'min-w-0 flex-1 truncate text-small',
          row.node?.deprecated ? 'text-muted-foreground line-through' : 'text-foreground',
        )}
        title={row.node?.name ?? row.id}
      >
        {row.node ? middleTruncate(row.node.name, 26) : 'A dataset this seat cannot read'}
      </span>
      <Identifier className="shrink-0 text-footnote text-muted-foreground">
        {row.relation}
      </Identifier>
      {row.revisited && (
        <Identifier className="shrink-0 text-footnote text-muted-foreground">cycle</Identifier>
      )}
    </div>
  );
}

/**
 * The whole derivation DAG, drawn as an INDENTED TREE rather than a node graph.
 *
 * That is a deliberate choice, not an omission. No graph library may be added,
 * and a hand-rolled SVG would have to fit named nodes into a 384px dock: dataset
 * names here run 20–40 characters, two columns of boxes plus edge labels leaves
 * roughly 150px per node, and every name middle-truncates to "Q3 Ca…ups" — which
 * turns the only useful thing on the screen into a puzzle. An indented tree
 * carries exactly the same information (nodes, edges, relation, hop depth) at
 * full name width, and a DAG walked from one root IS a tree with repeats: the
 * indent is the depth, and a node reached twice is drawn twice because it was
 * genuinely derived twice.
 */
function LineageGraphSection({ datasetId }: { datasetId: string | null }) {
  const graph = useLineageGraph(datasetId);
  const nodes = graph.data?.nodes ?? [];
  const edges = graph.data?.edges ?? [];
  const hidden = graph.data?.hidden_nodes ?? 0;
  const upstream = lineageBranch(graph.data, datasetId, 'upstream');
  const downstream = lineageBranch(graph.data, datasetId, 'downstream');
  const capped = upstream.length >= LINEAGE_ROW_CAP || downstream.length >= LINEAGE_ROW_CAP;

  if (graph.isLoading) return <LensLoading>Walking the derivation chain…</LensLoading>;
  if (graph.error) return <LensError>{errorText(graph.error)}</LensError>;
  if (edges.length === 0)
    return (
      <LensEmpty>
        No derivation chain: nothing visible was built from this dataset, and it was not built from
        anything.
      </LensEmpty>
    );

  return (
    <div data-testid="lineage-graph">
      <Stat
        name="datasets"
        value={num(nodes.length)}
        coverage={coverage(nodes.length, nodes.length + hidden, 'datasets')}
      />
      <Footnote className="mt-1 tabular-nums">
        {num(edges.length)} derivation edges, walked to depth {graph.data?.max_depth ?? '—'}. An
        edge touching a dataset in a team this seat cannot read is withheld with it, so this is the
        graph you can see.
      </Footnote>

      {upstream.length > 0 && (
        <div className="mt-2">
          <Eyebrow>upstream — what this came from</Eyebrow>
          {upstream.map((r) => (
            <LineageGraphRow key={r.key} row={r} />
          ))}
        </div>
      )}

      {downstream.length > 0 && (
        <div className="mt-2">
          <Eyebrow>downstream — what came from this</Eyebrow>
          {downstream.map((r) => (
            <LineageGraphRow key={r.key} row={r} />
          ))}
        </div>
      )}

      {hidden > 0 && (
        <Guard className="mt-2">
          {num(hidden)} dataset{hidden === 1 ? '' : 's'} in this chain belong to teams this seat
          cannot read. They and their edges are withheld — the direct route answers those as
          absence, and lineage must not undo that.
        </Guard>
      )}

      {graph.data?.truncated && (
        <Guard tone="warning" className="mt-1">
          The chain continues past depth {graph.data.max_depth}. That is proven by a probe hop taken
          one step beyond the cap and then discarded, so it means the DAG really does go on — not
          that it merely reached the limit.
        </Guard>
      )}

      {capped && (
        <Footnote className="mt-1">
          Listing stops at {LINEAGE_ROW_CAP} rows per direction; a wider DAG has more.
        </Footnote>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ search */

/**
 * Global search, scoped to the question this lens asks: where else does this
 * shape appear?
 *
 * Column search is served from captured schemas in Postgres — the CURRENT
 * version of every dataset this seat can access, no file I/O — so it is fast
 * and it is exact-fragment, not fuzzy. Both routes are offset-paged and both
 * return `total`, so the honest rendering is "the first N of `total`" with the
 * remainder stated. Neither gets a pager: refining the fragment is the paging.
 */
function CounterpartSearch() {
  const [q, setQ] = useState('');
  const needle = q.trim();
  const ready = needle.length >= SEARCH_MIN_CHARS;
  const datasets = useDatasetSearch(q);
  const columns = useColumnSearch(q);

  const datasetHits = datasets.data?.items ?? [];
  const columnHits = columns.data?.items ?? [];
  const datasetTotal = datasets.data?.total ?? 0;
  const columnTotal = columns.data?.total ?? 0;

  return (
    <div data-testid="counterpart-search">
      <div className={cn(fieldClass, 'flex items-center gap-2')}>
        <Search className="size-3 shrink-0 text-muted-foreground" />
        <input
          aria-label="Search datasets and columns"
          data-testid="counterpart-query"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="customer_id, orders…"
          className="min-w-0 flex-1 bg-transparent text-small outline-none placeholder:text-muted-foreground"
        />
      </div>

      {!ready ? (
        <Footnote className="mt-1.5">
          {SEARCH_MIN_CHARS} characters or more. Columns match on a name fragment; datasets match on
          name and description.
        </Footnote>
      ) : (
        <>
          {columns.error && <LensError>{errorText(columns.error)}</LensError>}
          {!columns.error && (
            <div className="mt-2">
              <Eyebrow>columns with that fragment</Eyebrow>
              {columns.isLoading && <LensLoading>Searching columns…</LensLoading>}
              {!columns.isLoading && columnHits.length === 0 && (
                <Footnote>No column across your datasets carries that fragment.</Footnote>
              )}
              {columnHits.map((hit) => (
                <div
                  key={`${hit.dataset_id}:${hit.sheet_key}:${hit.column_name}`}
                  className="flex h-6 items-center gap-1.5"
                  data-testid="column-hit"
                >
                  <Identifier className="min-w-0 flex-1 truncate text-micro text-foreground">
                    {middleTruncate(`${hit.sheet_name}.${hit.column_name}`, 26)}
                  </Identifier>
                  <DtypeChip dtype={hit.dtype} />
                  <span
                    className="w-[92px] shrink-0 truncate text-right text-footnote text-muted-foreground"
                    title={hit.dataset_name}
                  >
                    {hit.dataset_name}
                  </span>
                </div>
              ))}
              {columnHits.length > 0 && (
                <Stat
                  className="mt-1"
                  name="columns"
                  value={num(columnTotal)}
                  coverage={coverage(columnHits.length, columnTotal, 'matches')}
                />
              )}
            </div>
          )}

          {datasets.error && <LensError>{errorText(datasets.error)}</LensError>}
          {!datasets.error && (
            <div className="mt-2">
              <Eyebrow>datasets by name or description</Eyebrow>
              {datasets.isLoading && <LensLoading>Searching datasets…</LensLoading>}
              {!datasets.isLoading && datasetHits.length === 0 && (
                <Footnote>No dataset you can read matches that.</Footnote>
              )}
              {datasetHits.map((hit) => (
                <div key={hit.id} className="flex h-6 items-center gap-1.5" data-testid="dataset-hit">
                  <span className="min-w-0 flex-1 truncate text-small text-foreground" title={hit.name}>
                    {middleTruncate(hit.name, 28)}
                  </span>
                  <Identifier className="shrink-0 text-footnote text-muted-foreground">
                    {num(hit.versions?.length ?? 0)}v
                  </Identifier>
                </div>
              ))}
              {datasetHits.length > 0 && (
                <Stat
                  className="mt-1"
                  name="datasets"
                  value={num(datasetTotal)}
                  coverage={coverage(datasetHits.length, datasetTotal, 'matches')}
                />
              )}
            </div>
          )}

          <Guard className="mt-2">
            Two columns sharing a name is a LEAD, not evidence, and nothing here creates an edge. A
            join still binds to a confirmed relationship — declare one, or run a scan and review
            what it proposes.
          </Guard>
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------- lens */

export function RelationshipsLens({ datasetId }: { datasetId: string | null }) {
  const rels = useRelationships(datasetId);
  const lineage = useLineage(datasetId);
  const suggest = useSuggestRelationships(datasetId);
  const seed = useSeedRelationships(datasetId);
  const review = useReviewRelationship(datasetId);

  const edges = rels.data?.items ?? [];
  const pending = edges.filter((e) => e.status === 'suggested');
  const reviewed = edges.filter((e) => e.status !== 'suggested');
  const confirmed = edges.filter((e) => e.status === 'confirmed');
  const rejected = edges.filter((e) => e.status === 'rejected');

  const parents = lineage.data?.parents ?? [];
  const children = lineage.data?.children ?? [];
  const hiddenLineage =
    parents.filter((p) => p.parent_visible === false).length +
    children.filter((c) => c.child_visible === false).length;

  // One exhaustive decision for the edge list, spelled out rather than run
  // through `LensList` because the same query feeds two grouped sections and
  // two copies of an error box is worse than either alone.
  const edgesLoaded = !rels.isLoading && !rels.error;
  const scan = suggest.data;
  const scanTotal = scan ? scan.pairs_examined + scan.skipped : 0;

  return (
    <>
      <LensSection title="Relationships">
        {rels.isLoading && <LensLoading>Loading relationships…</LensLoading>}
        {!rels.isLoading && rels.error && <LensError>{errorText(rels.error)}</LensError>}
        {edgesLoaded && edges.length === 0 && (
          <LensEmpty>
            No relationships visible to this seat. Seed from foreign-key rules, or run a scan.
          </LensEmpty>
        )}

        {edgesLoaded && edges.length > 0 && (
          <>
            <Metric size="hero" label="visible to this seat" value={num(edges.length)} />
            <div className="mt-3 grid grid-cols-3 gap-2">
              <Metric
                size="figure"
                label="confirmed"
                value={num(confirmed.length)}
                note={<Status kind="good">joinable</Status>}
              />
              <Metric
                size="figure"
                label="pending"
                value={num(pending.length)}
                note={<Status kind="warning">review</Status>}
              />
              <Metric
                size="figure"
                label="rejected"
                value={num(rejected.length)}
                note={<Status kind="unknown">archived</Status>}
              />
            </div>
            {/* The count is what you can see. It is NOT the dataset's total. */}
            <Footnote className="mt-2">
              An edge whose other side this seat cannot read is dropped from this list and from
              its count, so this is what you can see — not the dataset's total.
            </Footnote>
          </>
        )}
      </LensSection>

      <LensSection title="Discovery">
        <div className="flex gap-1">
          <Button
            size="xs"
            variant="ghost"
            disabled={seed.isPending || !datasetId}
            onClick={() => seed.mutate()}
            title="Create edges from this dataset's foreign_key quality rules"
            data-testid="relationship-seed"
          >
            <GitBranch className="size-3" />
            {seed.isPending ? 'Seeding…' : 'Seed'}
          </Button>
          <Button
            size="xs"
            disabled={suggest.isPending || !datasetId}
            onClick={() => suggest.mutate()}
            title="Statistically probe column pairs for overlapping keys"
            data-testid="relationship-suggest"
          >
            <Sparkles className="size-3" />
            {suggest.isPending ? 'Scanning…' : 'Suggest'}
          </Button>
        </div>

        {seed.data && (
          <Footnote className="mt-2">
            {seed.data.created > 0
              ? `Seeded ${num(seed.data.created)} new edge(s) from foreign-key rules.`
              : 'No new edges: every enabled foreign-key rule already has one, or none points at a sheet in this version.'}
          </Footnote>
        )}

        {scan && (
          <div className="mt-2 rounded-md bg-card px-2.5 py-2 shadow-[var(--hi)]">
            <Eyebrow>last scan, this session</Eyebrow>
            <div className="mt-1.5 grid grid-cols-3 gap-2">
              <Metric size="figure" label="probed" value={num(scan.pairs_examined)} />
              <Metric size="figure" label="derived" value={num(scan.suggested)} />
              <Metric size="figure" label="skipped" value={num(scan.skipped)} />
            </div>
            {scanTotal > 0 && (
              <MagnitudeBar
                className="mt-2"
                of={coverage(scan.pairs_examined, scanTotal)}
                title={`${scan.pairs_examined} of ${scanTotal} candidate pairs probed`}
              />
            )}
            {/* `skipped` means the sweep was capped. Silence would read as "nothing else exists". */}
            <Footnote className="mt-1.5">
              {scanTotal === 0
                ? 'No candidate pairs at all: this version has fewer than two ready sheets, or no two columns share a type family and a key-shaped name.'
                : scan.skipped > 0
                  ? `${num(scan.skipped)} candidate pairs were never probed — the cap bit, so this sweep was not exhaustive. Nothing here rules out an edge it did not look at.`
                  : 'Every candidate pair this run proposed was probed. Column pairs of different type families are not candidates and were never proposed.'}
            </Footnote>
          </div>
        )}

        {!scan && (
          <Footnote className="mt-2">
            No scan has run from this panel. The edges above are whatever was seeded, declared or
            discovered earlier — the service keeps no last-run timestamp to show.
          </Footnote>
        )}

        <Footnote className="mt-1.5">
          Both routes pair sheets inside this dataset's current version only. An edge to another
          dataset has to be declared, and derived counts pairs discovery re-derived, not rows
          inserted.
        </Footnote>
      </LensSection>

      {edgesLoaded && pending.length > 0 && (
        <LensSection
          title="Review queue"
          meta={
            <Status kind="warning" className="shrink-0 text-micro font-medium">
              {`${num(pending.length)} pending`}
            </Status>
          }
        >
          {/* The gate, stated once: a left rule and an indent, not a nested box. */}
          <p className="mb-2 pl-2.5 text-micro text-muted-foreground shadow-[inset_2px_0_0_var(--st-warn)]">
            Discovery never confirms.{' '}
            <span className="font-medium text-foreground">
              An unreviewed edge cannot back a join
            </span>{' '}
            — a cross-dataset join binds to a confirmed relationship id, never to free-form keys.
          </p>

          {pending.map((e) => (
            <CandidateCard
              key={e.id}
              edge={e}
              reviewing={review.isPending}
              onReview={(id, action) => review.mutate({ id, action })}
            />
          ))}
        </LensSection>
      )}

      {edgesLoaded && reviewed.length > 0 && (
        <LensSection
          title="Reviewed"
          meta={
            <Identifier className="shrink-0 text-footnote text-muted-foreground">
              {`${num(confirmed.length)} confirmed · ${num(rejected.length)} rejected`}
            </Identifier>
          }
        >
          {reviewed.map((e) => (
            <ReviewedRow key={e.id} edge={e} />
          ))}
        </LensSection>
      )}

      {edgesLoaded && edges.length > 0 && (
        <LensSection
          title="Join builder"
          meta={
            <Identifier className="shrink-0 text-footnote text-muted-foreground">
              POST /joins/preview
            </Identifier>
          }
        >
          <JoinBuilder confirmed={confirmed} />
        </LensSection>
      )}

      <LensSection title="Lineage">
        {lineage.isLoading && <LensLoading />}
        {!lineage.isLoading && lineage.error && <LensError>{errorText(lineage.error)}</LensError>}
        {!lineage.isLoading &&
          !lineage.error &&
          parents.length === 0 &&
          children.length === 0 && (
            <LensEmpty>
              This dataset was uploaded directly — no derived parents, and nothing visible was
              built from it.
            </LensEmpty>
          )}

        {parents.length > 0 && (
          <div className="mb-2">
            <Eyebrow>derived from</Eyebrow>
            {parents.map((p) => (
              <LineageRow
                key={p.id}
                testid="lineage-parent"
                hidden={p.parent_visible === false}
                name={p.parent_dataset_name}
                version={p.parent_version_number}
                relation={p.relation}
              />
            ))}
          </div>
        )}

        {children.length > 0 && (
          <div>
            <Eyebrow>used to build</Eyebrow>
            {children.map((c) => (
              <LineageRow
                key={c.id}
                testid="lineage-child"
                hidden={c.child_visible === false}
                name={c.child_dataset_name}
                version={c.child_version_number}
                relation={c.relation}
              />
            ))}
          </div>
        )}

        {hiddenLineage > 0 && (
          <Footnote className="mt-2">
            A row with no name is still a real edge — the dataset behind it is not readable from
            this seat, and the service answers that as absence rather than as a refusal.
          </Footnote>
        )}
      </LensSection>

      <LensSection
        title="Derivation graph"
        meta={
          <Identifier className="shrink-0 text-footnote text-muted-foreground">
            /lineage/graph
          </Identifier>
        }
      >
        <LineageGraphSection datasetId={datasetId} />
      </LensSection>

      <LensSection title="Find a counterpart">
        <CounterpartSearch />
      </LensSection>

      <LensSection title="What a confirmed edge buys">
        <Footnote>
          A cross-dataset join takes a confirmed relationship id and supplies both keys from it;
          ad-hoc key pairs are refused. Single-column equi-join only,{' '}
          <Identifier>inner</Identifier> or <Identifier>left</Identifier> — no composite keys, no
          fuzzy matching, no entity resolution.
        </Footnote>
      </LensSection>
    </>
  );
}
