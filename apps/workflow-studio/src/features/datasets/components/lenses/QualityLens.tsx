/**
 * The quality lens: the rules that define "correct" for this dataset, and the
 * last time they were checked.
 *
 * Deleting a rule is wired even though deleting a dataset is not — a rule is an
 * annotation, and removing one loses no rows. Past validation results keep their
 * own snapshot of the rule, so history survives the delete.
 */

import { useState } from 'react';
import { Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import {
  COLUMN_SCOPED_RULES,
  RULE_TYPES,
  useCreateRule,
  useDeleteRule,
  useUpdateRule,
  useValidate,
  type RuleType,
  type ValidationDetail,
} from '../../hooks/useDatasetActions';
import type { QualityRule } from '../../hooks/useDatasets';
import { LensEmpty, Section } from './primitives';

/** Types that carry no `parameters`; anything else needs the JSON box. */
const NO_PARAM_RULES: readonly RuleType[] = ['not_null', 'unique', 'sheet_exists'];

const PARAM_HINTS: Partial<Record<RuleType, string>> = {
  row_count_min: '{"min": 100}',
  accepted_values: '{"values": ["paid", "pending"]}',
  range: '{"min": 0, "max": 1000}',
  regex_match: '{"pattern": "^[A-Z]{2}-\\\\d+$"}',
  foreign_key: '{"ref_sheet": "Customers", "ref_column": "customer_id"}',
};

const inputClass =
  'h-6 w-full rounded border border-border bg-background px-1.5 text-[11px] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40';

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
  const [lastRun, setLastRun] = useState<ValidationDetail | null>(null);

  const create = useCreateRule(datasetId);
  const update = useUpdateRule(datasetId);
  const remove = useDeleteRule(datasetId);
  const validate = useValidate(datasetId);

  const needsColumn = COLUMN_SCOPED_RULES.includes(ruleType);
  const needsParams = !NO_PARAM_RULES.includes(ruleType);
  const enabledCount = rules.filter((r) => r.enabled !== false).length;

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

  return (
    <>
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
          <div className="mb-2 rounded-md border border-border p-2" data-testid="rule-form">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Rule name"
              aria-label="Rule name"
              className={inputClass}
              data-testid="rule-name"
            />
            <select
              value={ruleType}
              onChange={(e) => setRuleType(e.target.value as RuleType)}
              aria-label="Rule type"
              className={`${inputClass} mt-1`}
              data-testid="rule-type"
            >
              {RULE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>

            {needsColumn && (
              <select
                value={column}
                onChange={(e) => setColumn(e.target.value)}
                aria-label="Rule column"
                className={`${inputClass} mt-1`}
                data-testid="rule-column"
              >
                <option value="">Choose a column…</option>
                {columns.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}

            {needsParams && (
              <input
                value={params}
                onChange={(e) => setParams(e.target.value)}
                placeholder={PARAM_HINTS[ruleType] ?? '{}'}
                aria-label="Rule parameters"
                className={`${inputClass} mt-1 font-mono`}
                data-testid="rule-params"
              />
            )}

            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as 'error' | 'warning')}
              aria-label="Rule severity"
              className={`${inputClass} mt-1`}
            >
              <option value="error">error</option>
              <option value="warning">warning</option>
            </select>

            {paramError && <p className="mt-1 text-[10px] text-destructive">{paramError}</p>}

            <div className="mt-1.5 flex gap-1">
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
            <p className="mt-1 text-[10px] text-muted-foreground/70">
              Scoped to sheet <span className="font-mono">{sheet ?? '—'}</span>.
            </p>
          </div>
        )}

        {rulesLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {!rulesLoading && rules.length === 0 && <LensEmpty>No quality rules defined.</LensEmpty>}

        {rules.map((r) => (
          <div key={r.id} className="mb-1 rounded-md border border-border px-2 py-1.5" data-testid="rule">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[12px]" data-testid="rule-name-text">
                {r.name ?? r.rule_type ?? r.id}
              </span>
              <Badge variant={r.severity === 'error' ? 'destructive' : 'glass'}>
                {r.severity ?? 'info'}
              </Badge>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span>{r.rule_type}</span>
              {r.column_selector && <span className="font-mono">· {r.column_selector}</span>}

              <div className="ml-auto flex items-center gap-1">
                {/* Enable/disable is a PATCH with {enabled} — there is no separate route. */}
                <button
                  onClick={() => update.mutate({ ruleId: r.id, patch: { enabled: r.enabled === false } })}
                  className="rounded px-1 hover:bg-muted"
                  data-testid="rule-toggle"
                  aria-label={r.enabled === false ? 'Enable rule' : 'Disable rule'}
                >
                  {r.enabled === false ? 'off' : 'on'}
                </button>
                <button
                  onClick={() => remove.mutate(r.id)}
                  className="rounded px-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  data-testid="rule-delete"
                  aria-label={`Delete rule ${r.name ?? r.id}`}
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </Section>

      <Section title="Validation">
        <Button
          size="xs"
          variant="outline"
          className="w-full"
          // No enabled rules is a 400, not an empty pass — don't offer the click.
          disabled={enabledCount === 0 || version == null || validate.isPending}
          onClick={() => version != null && validate.mutate(version, { onSuccess: setLastRun })}
          data-testid="validate-run"
        >
          <ShieldCheck className="size-3" />
          {validate.isPending ? 'Running…' : `Validate v${version ?? '—'}`}
        </Button>

        {enabledCount === 0 && (
          <p className="mt-1 text-[10px] text-muted-foreground/70">
            Enable at least one rule to validate.
          </p>
        )}

        {lastRun && (
          <div className="mt-2 rounded-md border border-border px-2 py-1.5" data-testid="validation-result">
            <div className="flex items-center gap-1.5">
              <Badge variant={(lastRun.error_failures ?? 0) > 0 ? 'destructive' : 'success'}>
                {(lastRun.error_failures ?? 0) > 0 ? 'failed' : 'passed'}
              </Badge>
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {lastRun.rules_passed ?? 0}/{lastRun.rules_total ?? 0} rules
              </span>
            </div>
            {(lastRun.results ?? [])
              .filter((r) => r.status !== 'passed')
              .map((r, i) => (
                <p key={i} className="mt-1 text-[10px] text-muted-foreground">
                  <span className="font-medium">{r.rule_name}</span>
                  {r.failure_count != null && ` — ${r.failure_count.toLocaleString()} rows`}
                </p>
              ))}
          </div>
        )}
      </Section>
    </>
  );
}
