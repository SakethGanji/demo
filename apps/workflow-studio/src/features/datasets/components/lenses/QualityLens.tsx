/**
 * The quality lens: the rules that define "correct" for this dataset, what the
 * last run made of them, and the health dimensions that sit outside the rule
 * set entirely.
 *
 * Three facts shape every decision below:
 *
 *  1. **The only quality gate in the system is tag promotion.** Upload and
 *     publish are ungated, and warnings never block anything. So the gate copy
 *     names exactly what is blocked and by which count, rather than letting a
 *     red number imply the world has stopped.
 *  2. **A validation run is a stored row, not a job.** The latest run for the
 *     version on screen is readable without re-running anything — which is why
 *     the strip at the top carries numbers before the button is touched — and a
 *     run belongs to the version it measured, never to "the dataset".
 *  3. **Profiling is not automatic.** Health dimensions and insights are
 *     computed from persisted profiles, so a dataset nobody profiled reads
 *     `unknown` rather than `ok`, and the panel offers the run instead of
 *     leaving a dead end.
 *
 * Deleting a rule is wired even though deleting a dataset is not — a rule is an
 * annotation, and removing one loses no rows. Past validation results keep their
 * own snapshot of the rule, so history survives the delete.
 *
 * Design notes (INSTRUMENT): the dock spends NO accent — the shell already
 * spends it on the active route — so repeated state (the eleven on/off toggles,
 * an opened editor) is marked with value and elevation instead. Severity is
 * typographic and status is shape + hue + word, which is what keeps a screen
 * full of passing `error`-severity rules from reading as a screen on fire.
 */

import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Pencil, Plus, Trash2, X } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { analytics, errorText, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { cn } from '@/shared/lib/utils';
import { num } from '@/shared/lib/format';
import {
  Eyebrow,
  Footnote,
  Identifier,
  Metric,
} from '@/shared/components/instrument/Typography';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { Severity } from '@/shared/components/instrument/Severity';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage } from '@/shared/components/instrument/coverage';
import { useHealth, type HealthDimension } from '../../hooks/useAnalysis';
import {
  COLUMN_SCOPED_RULES,
  RULE_TYPES,
  useCreateRule,
  useDeleteRule,
  useUpdateRule,
  useValidate,
  type RulePatch,
  type RuleType,
  type ValidationDetail,
} from '../../hooks/useDatasetActions';
import type { QualityRule } from '../../hooks/useDatasets';
import { DuplicatesExplorer, MissingExplorer } from './QualityExplorers';
import { LensEmpty, LensError, LensLoading, Section } from './primitives';
import { fieldClass } from '../fieldStyles';

/** Types that carry no `parameters`; anything else needs the JSON box. */
const NO_PARAM_RULES: readonly RuleType[] = ['not_null', 'unique', 'sheet_exists'];

const PARAM_HINTS: Partial<Record<RuleType, string>> = {
  row_count_min: '{"min": 100}',
  accepted_values: '{"values": ["paid", "pending"]}',
  range: '{"min": 0, "max": 1000}',
  regex_match: '{"pattern": "^[A-Z]{2}-\\\\d+$"}',
  foreign_key: '{"ref_sheet": "Customers", "ref_column": "customer_id"}',
};

/**
 * The scope the server will derive from each `rule_type`. Shown, never sent:
 * `scope_type` is not a field the form owns, and posting one is ignored.
 */
const DERIVED_SCOPE: Record<RuleType, string> = {
  sheet_exists: 'dataset',
  row_count_min: 'sheet',
  not_null: 'column',
  unique: 'column',
  accepted_values: 'column',
  range: 'column',
  regex_match: 'column',
  foreign_key: 'cross',
};

/* ------------------------------------------------------------------ reads */

/**
 * Local read hooks.
 *
 * These are three GETs the shared hook modules do not expose yet — the
 * validation history for a version, one run's per-rule results, and the
 * persisted profile runs that carry the insight rows. They are written here
 * rather than invented: every path exists in the service. If a second surface
 * ever needs them they belong in `useDatasets.ts` / `useAnalysis.ts` beside
 * their siblings, keyed by seat exactly as they are here.
 */
function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/** A run row as the history listing returns it — counts, no per-rule results. */
interface ValidationRunSummary {
  id: string;
  status: string;
  rules_total?: number | null;
  rules_passed?: number | null;
  rules_failed?: number | null;
  error_failures?: number | null;
  warning_failures?: number | null;
  completed_at?: string | null;
  started_at?: string | null;
}

/**
 * The wire carries timestamps that the shared `ValidationDetail` type omits;
 * widen locally rather than pretend the response is smaller than it is.
 */
type RunDetail = ValidationDetail & {
  completed_at?: string | null;
  started_at?: string | null;
};

type RuleResult = NonNullable<ValidationDetail['results']>[number];

/** Newest first, and scoped to the version on screen — never "the dataset". */
function useValidationRuns(datasetId: string | null, version: number | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'validations', datasetId, version],
    queryFn: () =>
      analytics.get<Page<ValidationRunSummary>>(
        `/datasets/${datasetId}/versions/${version}/validations`,
        { limit: 5 },
      ),
    enabled: Boolean(datasetId) && version != null,
    retry: false,
  });
}

function useValidationDetail(datasetId: string | null, runId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'validation-detail', datasetId, runId],
    queryFn: () => analytics.get<RunDetail>(`/datasets/${datasetId}/validations/${runId}`),
    enabled: Boolean(datasetId) && Boolean(runId),
    retry: false,
  });
}

interface Insight {
  rule: string;
  severity: string;
  column_name?: string | null;
  message: string;
  evidence?: Record<string, unknown>;
}

interface ProfileRun {
  id: string;
  sheet_name?: string | null;
  status: string;
  completed_at?: string | null;
  insights?: Insight[];
}

function useProfileRuns(datasetId: string | null, version: number | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'profile-runs', datasetId, version],
    queryFn: () =>
      analytics.get<Page<ProfileRun>>(`/datasets/${datasetId}/versions/${version}/profile-runs`, {
        limit: 50,
      }),
    enabled: Boolean(datasetId) && version != null,
    retry: false,
  });
}

/**
 * Profiling every sheet of a version, persisted. Nothing profiles on upload, so
 * without this affordance the insight list is a dead end that blames the data.
 */
function useRunProfile(datasetId: string | null, version: number | null) {
  const qc = useQueryClient();
  const seat = useSeat();
  return useMutation<ProfileRun[], unknown, void>({
    mutationFn: () =>
      analytics.post<ProfileRun[]>(`/datasets/${datasetId}/versions/${version}/profile-runs`),
    onSuccess: (runs) => {
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      toast.success(`Profiled ${runs.length} sheet(s).`);
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ------------------------------------------------------------- formatting */

/** `08-16 12:07` — a run stamp, not a date. Null when the server sent none. */
function stamp(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** A rule's `parameters` bag, narrowed off the index signature. */
function ruleParams(rule: QualityRule): Record<string, unknown> | null {
  const p = rule.parameters;
  if (!p || typeof p !== 'object' || Array.isArray(p)) return null;
  const entries = Object.entries(p as Record<string, unknown>);
  return entries.length > 0 ? (p as Record<string, unknown>) : null;
}

/** `min: 0, max: 5000` — the parameters column, compressed to one line. */
function paramSummary(rule: QualityRule): string | null {
  const p = ruleParams(rule);
  if (!p) return null;
  return Object.entries(p)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join(', ');
}

/** The target a rule points at: `sheet · column`, whichever it declares. */
function ruleTarget(rule: QualityRule): string {
  return [rule.sheet_selector, rule.column_selector].filter(Boolean).join(' · ');
}

function severityOf(rule: QualityRule): 'error' | 'warning' {
  return rule.severity === 'warning' ? 'warning' : 'error';
}

/**
 * A rule result's status mapped onto the five shapes.
 *
 * A *failure* takes its shape from the rule's severity, because those are the
 * two things that differ in consequence: only an error-severity failure gates
 * promotion. `errored` is a square — the rule could not be evaluated, which is
 * neither a pass nor a fail — and `skipped` is a ring, which reads as absent.
 */
function resultKind(result: RuleResult): StatusKind {
  switch (result.status) {
    case 'passed':
      return 'good';
    case 'failed':
      return result.severity === 'error' ? 'critical' : 'serious';
    case 'skipped':
      return 'unknown';
    default:
      return 'critical';
  }
}

/** The service's health vocabulary — `ok | warning | attention | unknown`. */
function healthKind(status: string): StatusKind {
  switch (status) {
    case 'ok':
      return 'good';
    case 'warn':
    case 'warning':
      return 'warning';
    case 'attention':
      return 'serious';
    case 'fail':
    case 'failed':
      return 'critical';
    default:
      return 'unknown';
  }
}

const HEALTH_ORDER: readonly string[] = [
  'validation',
  'schema_stability',
  'missing_data',
  'duplicates',
  'drift',
  'freshness',
  'documentation',
];

/** Dimensions whose only input is a persisted profile. */
const PROFILE_BACKED: readonly string[] = ['missing_data', 'duplicates', 'drift'];

function scalarText(v: unknown): string | null {
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (typeof v === 'string') return v.length > 22 ? `${v.slice(0, 20)}…` : v;
  if (Array.isArray(v)) {
    if (v.length === 0) return null;
    if (v.every((x) => typeof x === 'string' || typeof x === 'number')) {
      const joined = v.join(', ');
      return joined.length > 22 ? `${joined.slice(0, 20)}…` : joined;
    }
    return `${v.length} entries`;
  }
  return null;
}

function evidencePairs(bag: Record<string, unknown>, depth = 0): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(bag)) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'object' && !Array.isArray(v)) {
      if (depth === 0) {
        out.push(...evidencePairs(v as Record<string, unknown>, 1).map((s) => `${k}.${s}`));
      }
      continue;
    }
    const text = scalarText(v);
    if (text) out.push(`${k} ${text}`);
  }
  return out;
}

/**
 * The evidence a health dimension cites, folded into the one footnote register.
 * Three pairs is the cap: this is provenance, not a second summary.
 */
function evidenceLine(dimension: HealthDimension): string | null {
  const bag = (dimension as HealthDimension & { evidence?: Record<string, unknown> | null })
    .evidence;
  if (!bag || typeof bag !== 'object') return null;
  const pairs = evidencePairs(bag).slice(0, 3);
  return pairs.length > 0 ? pairs.join(' · ') : null;
}

/* ------------------------------------------------------------- components */

/**
 * A caveat inside a row is a left-ruled indent, never a second box — rule 8.
 * Local because it is the only place in the dock that needs one so far.
 */
function Guard({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'gate' }) {
  return (
    <p
      className={cn(
        'border-l-2 pl-2 text-footnote leading-relaxed text-muted-foreground',
        tone === 'gate' ? 'border-[var(--st-crit)]' : 'border-[var(--r3)]',
      )}
    >
      {children}
    </p>
  );
}

interface QualityLensProps {
  datasetId: string | null;
  version: number | null;
  sheet: string | null;
  columns: { name: string }[];
  rules: QualityRule[];
  rulesLoading: boolean;
}

export function QualityLens({
  datasetId,
  version,
  sheet,
  columns,
  rules,
  rulesLoading,
}: QualityLensProps) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [ruleType, setRuleType] = useState<RuleType>('not_null');
  const [column, setColumn] = useState('');
  const [severity, setSeverity] = useState<'error' | 'warning'>('error');
  const [params, setParams] = useState('');
  const [paramError, setParamError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editSeverity, setEditSeverity] = useState<'error' | 'warning'>('error');
  const [editParams, setEditParams] = useState('');
  const [editError, setEditError] = useState<string | null>(null);

  /**
   * The run this seat just triggered, keyed by what it measured. Without the
   * key a run stays on screen after the dataset or version underneath it
   * changes, which is a validation result attributed to the wrong data.
   */
  const runKey = `${datasetId ?? ''}:${version ?? ''}`;
  const [justRan, setJustRan] = useState<{ key: string; detail: RunDetail } | null>(null);
  const fresh = justRan && justRan.key === runKey ? justRan.detail : null;

  const create = useCreateRule(datasetId);
  const update = useUpdateRule(datasetId);
  const remove = useDeleteRule(datasetId);
  const validate = useValidate(datasetId);

  const history = useValidationRuns(datasetId, version);
  const latestSummary = history.data?.items?.[0] ?? null;
  const detail = useValidationDetail(datasetId, fresh ? null : (latestSummary?.id ?? null));
  const run: RunDetail | null = fresh ?? detail.data ?? null;
  const runStamp = stamp(run?.completed_at ?? run?.started_at ?? latestSummary?.completed_at);

  const health = useHealth(datasetId);
  const profileRuns = useProfileRuns(datasetId, version);
  const runProfile = useRunProfile(datasetId, version);

  const needsColumn = COLUMN_SCOPED_RULES.includes(ruleType);
  const needsParams = !NO_PARAM_RULES.includes(ruleType);
  const enabledCount = rules.filter((r) => r.enabled !== false).length;

  const results = new Map<string, RuleResult>();
  for (const r of run?.results ?? []) {
    if (r.rule_id) results.set(r.rule_id, r);
  }

  const rulesTotal = run?.rules_total ?? 0;
  const rulesPassed = run?.rules_passed ?? 0;
  const errorFailures = run?.error_failures ?? 0;
  const warningFailures = run?.warning_failures ?? 0;
  const verdict: { kind: StatusKind; word: string } =
    errorFailures > 0
      ? { kind: 'critical', word: 'failed' }
      : (run?.rules_failed ?? 0) > 0
        ? { kind: 'warning', word: 'passed with warnings' }
        : { kind: 'good', word: 'passed' };

  const reset = () => {
    setAdding(false);
    setName('');
    setColumn('');
    setParams('');
    setParamError(null);
  };

  const submit = () => {
    let parsed: Record<string, unknown> = {};
    if (needsParams && params.trim()) {
      try {
        parsed = JSON.parse(params);
      } catch {
        // Catch it here rather than let the server 422 — the user is mid-form.
        setParamError('Parameters must be valid JSON.');
        return;
      }
    }
    setParamError(null);
    create.mutate(
      {
        name: name.trim(),
        rule_type: ruleType,
        sheet_selector: sheet ?? '',
        column_selector: needsColumn ? column || null : null,
        parameters: parsed,
        severity,
      },
      { onSuccess: reset },
    );
  };

  const startEdit = (rule: QualityRule) => {
    const p = ruleParams(rule);
    setEditingId(rule.id);
    setEditName(rule.name ?? '');
    setEditSeverity(severityOf(rule));
    setEditParams(p ? JSON.stringify(p) : '');
    setEditError(null);
  };

  /**
   * PATCH is a partial: only what changed is sent, so an untouched field cannot
   * be cleared by a form that merely rendered it. `rule_type` and `scope_type`
   * are absent by construction — they are not patchable.
   */
  const submitEdit = (rule: QualityRule) => {
    const patch: RulePatch = {};
    const trimmed = editName.trim();
    if (trimmed && trimmed !== (rule.name ?? '')) patch.name = trimmed;
    if (editSeverity !== severityOf(rule)) patch.severity = editSeverity;

    const before = ruleParams(rule);
    const typed = editParams.trim();
    if (typed && typed !== (before ? JSON.stringify(before) : '')) {
      try {
        patch.parameters = JSON.parse(typed) as Record<string, unknown>;
      } catch {
        setEditError('Parameters must be valid JSON.');
        return;
      }
    }

    setEditError(null);
    if (Object.keys(patch).length === 0) {
      setEditingId(null);
      return;
    }
    update.mutate({ ruleId: rule.id, patch }, { onSuccess: () => setEditingId(null) });
  };

  const dimensions = health.data?.dimensions ?? {};
  const dimensionKeys = [
    ...HEALTH_ORDER.filter((k) => k in dimensions),
    ...Object.keys(dimensions).filter((k) => !HEALTH_ORDER.includes(k)),
  ];
  const unprofiled = dimensionKeys.some(
    (k) => PROFILE_BACKED.includes(k) && dimensions[k]?.status === 'unknown',
  );

  const profileItems = profileRuns.data?.items ?? [];
  const forSheet = profileItems.filter((r) => !sheet || r.sheet_name === sheet);
  const insightRuns = forSheet.length > 0 ? forSheet : profileItems;
  const insights = insightRuns
    .flatMap((r) => (r.insights ?? []).map((i) => ({ insight: i, from: r })))
    // Warnings first; the order inside each band is the engine's own.
    .sort((a, b) =>
      a.insight.severity === b.insight.severity ? 0 : a.insight.severity === 'warning' ? -1 : 1,
    );

  return (
    <>
      {/* ── The first read: what the last run made of this version ────────── */}
      <div className="mb-4">
        {run ? (
          <div data-testid="validation-result">
            <div className="flex items-baseline gap-2">
              <Status kind={verdict.kind} className="text-label font-medium">
                {verdict.word}
              </Status>
              <Footnote className="ml-auto shrink-0">
                {runStamp ? `run ${runStamp}` : 'run time unknown'}
              </Footnote>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <Metric label="Passed" value={num(run.rules_passed)} />
              <Metric label="Failed" value={num(run.rules_failed)} />
            </div>

            <MagnitudeBar of={coverage(rulesPassed, rulesTotal)} className="mt-2.5" />
            <Footnote className="mt-1 tabular-nums">
              {rulesPassed} of {rulesTotal} rules passed · v{version ?? '—'}
            </Footnote>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <Metric
                size="figure"
                label="Error failures"
                value={num(run.error_failures)}
                note={
                  errorFailures > 0 ? (
                    <Status kind="critical">blocks tag promotion</Status>
                  ) : (
                    'nothing is blocked'
                  )
                }
              />
              <Metric
                size="figure"
                label="Warning failures"
                value={num(run.warning_failures)}
                note={warningFailures > 0 ? 'advisory — never blocks' : 'never blocks'}
              />
            </div>
          </div>
        ) : (
          <div>
            <Eyebrow>Validation</Eyebrow>
            <div className="mt-1.5">
              <Status kind="unknown" className="text-label font-medium">
                {history.isLoading ? 'reading history' : 'not validated'}
              </Status>
            </div>
            {history.error ? (
              <LensError>{errorText(history.error)}</LensError>
            ) : (
              <Footnote className="mt-1.5">
                No run for v{version ?? '—'} yet. A result belongs to the version it measured, so
                a run on another version says nothing about this one.
              </Footnote>
            )}
          </div>
        )}

        <Button
          size="xs"
          className="mt-3 w-full"
          // No enabled rules is a 400, not an empty pass — don't offer the click.
          disabled={enabledCount === 0 || version == null || validate.isPending}
          onClick={() =>
            version != null &&
            validate.mutate(version, {
              onSuccess: (d) => setJustRan({ key: runKey, detail: d }),
            })
          }
          data-testid="validate-run"
        >
          {validate.isPending ? 'Running…' : `Run validation · v${version ?? '—'}`}
        </Button>

        <div className="mt-2">
          {enabledCount === 0 ? (
            <Guard>Enable at least one rule — validating with none is refused, not an empty pass.</Guard>
          ) : (
            <Guard tone={errorFailures > 0 ? 'gate' : 'neutral'}>
              Validation runs synchronously and gates exactly one thing: tag promotion. Upload and
              publish are ungated, and warning failures never block anything.
            </Guard>
          )}
        </div>
      </div>

      {/* ── The rules themselves ──────────────────────────────────────────── */}
      <Section
        title={`Rules (${rules.length})`}
        action={
          <Button
            size="xs"
            variant="ghost"
            onClick={() => setAdding((v) => !v)}
            data-testid="rule-add-toggle"
          >
            <Plus className="size-3" />
            Add
          </Button>
        }
      >
        {adding && (
          <div className="mb-3 rounded-md bg-card p-2 shadow-[var(--hi)]" data-testid="rule-form">
            <label className="text-micro text-muted-foreground">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Rule name"
              aria-label="Rule name"
              className={fieldClass}
              data-testid="rule-name"
            />

            <label className="mt-1.5 block text-micro text-muted-foreground">
              Rule type · everything below follows from it
            </label>
            <select
              value={ruleType}
              onChange={(e) => setRuleType(e.target.value as RuleType)}
              aria-label="Rule type"
              className={fieldClass}
              data-testid="rule-type"
            >
              {RULE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <Footnote className="mt-1">
              scope <Identifier>{DERIVED_SCOPE[ruleType]}</Identifier> — derived server-side, never
              sent
            </Footnote>

            {needsColumn && (
              <>
                <label className="mt-1.5 block text-micro text-muted-foreground">
                  Target · column
                </label>
                <select
                  value={column}
                  onChange={(e) => setColumn(e.target.value)}
                  aria-label="Rule column"
                  className={fieldClass}
                  data-testid="rule-column"
                >
                  <option value="">Choose a column…</option>
                  {columns.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </>
            )}

            {needsParams && (
              <>
                <label className="mt-1.5 block text-micro text-muted-foreground">Parameters</label>
                <input
                  value={params}
                  onChange={(e) => setParams(e.target.value)}
                  placeholder={PARAM_HINTS[ruleType] ?? '{}'}
                  aria-label="Rule parameters"
                  className={`${fieldClass} font-mono`}
                  data-testid="rule-params"
                />
              </>
            )}

            <div className="mt-1.5">
              <label className="text-micro text-muted-foreground">
                Severity · exactly two exist
              </label>
              {/* Repeated selectable state: value + elevation, never the accent. */}
              <div className="mt-1 flex gap-1">
                {(['error', 'warning'] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={severity === s}
                    onClick={() => setSeverity(s)}
                    data-testid={`rule-severity-${s}`}
                    className={cn(
                      'flex-1 rounded px-2 py-1 text-left text-micro',
                      severity === s
                        ? 'bg-secondary shadow-[var(--hi)]'
                        : 'bg-transparent hover:bg-muted',
                    )}
                  >
                    <Severity level={s} className="text-micro" />
                    <span className="ml-1.5 text-footnote text-muted-foreground">
                      {s === 'error' ? 'gates promotion' : 'advisory'}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {paramError && <p className="mt-1.5 text-micro text-destructive">{paramError}</p>}

            <div className="mt-2 flex gap-1">
              <Button
                size="xs"
                disabled={!name.trim() || !sheet || (needsColumn && !column) || create.isPending}
                onClick={submit}
                data-testid="rule-save"
              >
                {create.isPending ? 'Saving…' : 'Create'}
              </Button>
              <Button size="xs" variant="ghost" onClick={reset}>
                Cancel
              </Button>
            </div>
            <Footnote className="mt-1.5">
              Scoped to sheet <Identifier>{sheet ?? '—'}</Identifier>.
            </Footnote>
          </div>
        )}

        {rules.length > 0 && (
          <Footnote className="mb-2 tabular-nums">
            {enabledCount} enabled · {rules.length - enabledCount} off — a disabled rule keeps its
            definition and its history, and runs in nothing.
          </Footnote>
        )}

        {rulesLoading && <LensLoading />}
        {!rulesLoading && rules.length === 0 && (
          <LensEmpty>No quality rules defined — nothing is checked and nothing is gated.</LensEmpty>
        )}

        {/* A homogeneous list: a whisper rule, no per-row box. */}
        <div className="divide-y divide-[var(--r1)]">
          {rules.map((r) => {
            const result = results.get(r.id);
            const off = r.enabled === false;
            const target = ruleTarget(r);
            const summary = paramSummary(r);
            const editing = editingId === r.id;

            return (
              <div key={r.id} className="py-2 first:pt-0" data-testid="rule">
                <div className="flex items-baseline gap-2">
                  <span
                    className={cn('min-w-0 flex-1 truncate text-body', off && 'text-muted-foreground')}
                    title={r.name ?? undefined}
                    data-testid="rule-name-text"
                  >
                    {r.name ?? r.rule_type ?? r.id}
                  </span>
                  {result ? (
                    <Status kind={resultKind(result)} className="shrink-0 text-micro">
                      {result.status}
                    </Status>
                  ) : (
                    <Footnote className="shrink-0">{off ? 'not in run' : 'no result'}</Footnote>
                  )}
                </div>

                <div className="mt-0.5 flex items-baseline gap-1.5 text-micro">
                  <Identifier className={cn('shrink-0 text-micro', off ? 'text-muted-foreground' : 'text-foreground')}>
                    {r.rule_type}
                  </Identifier>
                  {target && (
                    <Identifier className="min-w-0 truncate text-micro text-muted-foreground" title={target}>
                      {target}
                    </Identifier>
                  )}
                  <Severity level={severityOf(r)} className={cn('ml-auto shrink-0 text-micro', off && 'opacity-60')} />
                </div>

                {(summary || (result?.failure_count != null && result.failure_count > 0)) && (
                  <Footnote className="mt-0.5 truncate font-mono" title={summary ?? undefined}>
                    {result?.failure_count != null && result.failure_count > 0
                      ? `${result.failure_count.toLocaleString()} rows${summary ? ` · ${summary}` : ''}`
                      : summary}
                  </Footnote>
                )}

                <div className="mt-1 flex items-center gap-1">
                  {/* Enable/disable is a PATCH with {enabled} — there is no separate route. */}
                  <button
                    onClick={() => update.mutate({ ruleId: r.id, patch: { enabled: off } })}
                    className={cn(
                      'rounded px-1.5 py-px font-mono text-footnote',
                      off
                        ? 'text-muted-foreground hover:bg-muted'
                        : 'bg-secondary text-foreground shadow-[var(--hi)]',
                    )}
                    data-testid="rule-toggle"
                    aria-label={off ? 'Enable rule' : 'Disable rule'}
                  >
                    {off ? 'off' : 'on'}
                  </button>
                  <button
                    onClick={() => (editing ? setEditingId(null) : startEdit(r))}
                    className={cn(
                      'ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground',
                      editing && 'bg-secondary text-foreground shadow-[var(--hi)]',
                    )}
                    data-testid="rule-edit"
                    aria-label={`Edit rule ${r.name ?? r.id}`}
                  >
                    {editing ? <X className="size-3" /> : <Pencil className="size-3" />}
                  </button>
                  <button
                    onClick={() => remove.mutate(r.id)}
                    className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    data-testid="rule-delete"
                    aria-label={`Delete rule ${r.name ?? r.id}`}
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>

                {/* An opened row reads as opened: elevation + a left rule, no accent. */}
                {editing && (
                  <div
                    className="mt-2 rounded-md bg-card p-2 shadow-[var(--hi)]"
                    data-testid="rule-edit-form"
                  >
                    <label className="text-micro text-muted-foreground">Name</label>
                    <input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      aria-label="Edit rule name"
                      className={fieldClass}
                      data-testid="rule-edit-name"
                    />

                    <label className="mt-1.5 block text-micro text-muted-foreground">Severity</label>
                    <div className="mt-1 flex gap-1">
                      {(['error', 'warning'] as const).map((s) => (
                        <button
                          key={s}
                          type="button"
                          aria-pressed={editSeverity === s}
                          onClick={() => setEditSeverity(s)}
                          data-testid={`rule-edit-severity-${s}`}
                          className={cn(
                            'flex-1 rounded px-2 py-1 text-left text-micro',
                            editSeverity === s
                              ? 'bg-secondary shadow-[var(--hi)]'
                              : 'bg-transparent hover:bg-muted',
                          )}
                        >
                          <Severity level={s} className="text-micro" />
                        </button>
                      ))}
                    </div>

                    {!NO_PARAM_RULES.includes(r.rule_type as RuleType) && (
                      <>
                        <label className="mt-1.5 block text-micro text-muted-foreground">
                          Parameters
                        </label>
                        <input
                          value={editParams}
                          onChange={(e) => setEditParams(e.target.value)}
                          placeholder={PARAM_HINTS[r.rule_type as RuleType] ?? '{}'}
                          aria-label="Edit rule parameters"
                          className={`${fieldClass} font-mono`}
                          data-testid="rule-edit-params"
                        />
                      </>
                    )}

                    {editError && <p className="mt-1.5 text-micro text-destructive">{editError}</p>}

                    <div className="mt-2 flex gap-1">
                      <Button
                        size="xs"
                        disabled={update.isPending}
                        onClick={() => submitEdit(r)}
                        data-testid="rule-edit-save"
                      >
                        {update.isPending ? 'Saving…' : 'Save changes'}
                      </Button>
                      <Button size="xs" variant="ghost" onClick={() => setEditingId(null)}>
                        Cancel
                      </Button>
                    </div>

                    <div className="mt-2">
                      <Guard>
                        <Identifier>rule_type</Identifier> and <Identifier>scope_type</Identifier>{' '}
                        are not patchable — retyping a rule would invalidate every stored result
                        that cites it. Delete and re-create instead; the old results stay truthful
                        about what they measured.
                      </Guard>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Section>

      {/* ── Health: seven dimensions, reported independently ──────────────── */}
      <Section title="Health">
        {health.isLoading && <LensLoading />}
        {!health.isLoading && health.error && <LensError>{errorText(health.error)}</LensError>}
        {!health.isLoading && !health.error && dimensionKeys.length === 0 && (
          <LensEmpty>No health dimensions reported.</LensEmpty>
        )}
        {!health.isLoading && !health.error && dimensionKeys.length > 0 && (
          <>
            <div className="divide-y divide-[var(--r1)]">
              {dimensionKeys.map((key) => {
                const d = dimensions[key];
                const ev = evidenceLine(d);
                return (
                  <div key={key} className="py-2 first:pt-0" data-testid="health-dimension">
                    <div className="flex items-baseline gap-2">
                      <Identifier className="min-w-0 flex-1 truncate text-small font-semibold text-foreground">
                        {key}
                      </Identifier>
                      <Status kind={healthKind(d.status)} className="shrink-0 text-micro">
                        {d.status}
                      </Status>
                    </div>
                    <p className="mt-1 text-small text-muted-foreground">{d.summary}</p>
                    {ev && (
                      <Footnote className="mt-1 truncate" title={ev}>
                        evidence {ev}
                      </Footnote>
                    )}
                  </div>
                );
              })}
            </div>
            <Footnote className="mt-2">
              No aggregate score, by design — a single number would average away the one dimension
              you need to act on.
            </Footnote>
            {unprofiled && (
              <div className="mt-2">
                <Guard>
                  Nothing profiles on upload, so <Identifier>missing_data</Identifier>,{' '}
                  <Identifier>duplicates</Identifier> and <Identifier>drift</Identifier> read
                  unknown until someone runs a profile.
                </Guard>
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── Insights: deterministic findings over persisted profiles ──────── */}
      <Section title={`Insights (${insights.length})`}>
        {profileRuns.isLoading && <LensLoading>Reading profile runs…</LensLoading>}
        {!profileRuns.isLoading && profileRuns.error && (
          <LensError>{errorText(profileRuns.error)}</LensError>
        )}
        {!profileRuns.isLoading && !profileRuns.error && insights.length === 0 && (
          <LensEmpty>
            No insights for v{version ?? '—'} — they are computed from a persisted profile, never
            from a live scan.
          </LensEmpty>
        )}
        {!profileRuns.isLoading && !profileRuns.error && insights.length > 0 && (
          <div className="divide-y divide-[var(--r1)]">
            {insights.slice(0, 10).map(({ insight, from }, i) => (
              <div key={`${from.id}-${insight.rule}-${i}`} className="py-2 first:pt-0" data-testid="insight">
                <div className="flex items-baseline gap-2">
                  <Footnote className="shrink-0 tabular-nums">
                    {String(i + 1).padStart(2, '0')}
                  </Footnote>
                  <Identifier className="min-w-0 flex-1 truncate text-small font-semibold text-foreground">
                    {insight.rule}
                  </Identifier>
                  {insight.severity === 'warning' ? (
                    <Severity level="warning" className="shrink-0 text-micro" />
                  ) : (
                    <Footnote className="shrink-0">{insight.severity}</Footnote>
                  )}
                </div>
                <p className="mt-0.5 text-small text-muted-foreground">{insight.message}</p>
                <Footnote className="mt-0.5 truncate">
                  profile {from.sheet_name ?? 'sheet'}
                  {stamp(from.completed_at) ? ` · ${stamp(from.completed_at)}` : ''}
                </Footnote>
              </div>
            ))}
          </div>
        )}

        <Button
          size="xs"
          variant="outline"
          className="mt-2 w-full"
          disabled={version == null || runProfile.isPending}
          onClick={() => runProfile.mutate()}
          data-testid="profile-run"
        >
          {runProfile.isPending ? 'Profiling…' : `Run profile · v${version ?? '—'}`}
        </Button>
        <Footnote className="mt-1.5">
          Ten deterministic rules over a persisted profile — no model, no sampling. Each finding
          re-computes identically from the run it cites.
        </Footnote>
      </Section>

      {/* ── The two explorers: the follow-up question health cannot answer ──
       *
       * `duplicates` and `missing_data` are two of the seven health dimensions
       * above, and both of them can only report a verdict. These answer WHICH:
       * which groups, which columns, which rows. They are read-only, they are
       * scoped to the version and sheet on screen exactly as the run strip is,
       * and they mask rather than refuse — see `QualityExplorers`.
       */}
      <DuplicatesExplorer
        datasetId={datasetId}
        version={version}
        sheet={sheet}
        columns={columns}
      />
      <MissingExplorer datasetId={datasetId} version={version} sheet={sheet} />
    </>
  );
}
