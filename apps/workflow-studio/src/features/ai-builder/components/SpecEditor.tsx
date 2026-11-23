import { CheckCheck, HelpCircle, Loader2, Lock, Plus, TriangleAlert } from 'lucide-react';
import { newTestCase } from '@/features/workflow-editor/lib/tests';
import { Button } from '@/shared/components/ui/button';
import { Callout, SectionHeader, Well, WellList, WellRow } from '@/shared/components/ui/surface';
import { Input } from '@/shared/components/ui/input';
import { Switch } from '@/shared/components/ui/switch';
import { Textarea } from '@/shared/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { TRIGGER_WORDS } from '../lib/catalog';
import { useSpecCatalog } from '../useSpecCatalog';
import type { Spec, SpecIssue, TriggerKind } from '../types';
import { isErrorIssue, issuesAt } from '../lib/specIssues';
import { IssueList } from './SpecIssues';
import { SpecRule } from './SpecRule';
import { SpecStep } from './SpecStep';
import { SpecTest } from './SpecTest';

type Edit = (update: (s: Spec) => Spec) => void;

function nextRuleId(spec: Spec): string {
  const taken = new Set(spec.rules.map((r) => r.id));
  let n = spec.rules.length + 1;
  while (taken.has(`r${n}`)) n += 1;
  return `r${n}`;
}

const triggerItems = Object.fromEntries(Object.entries(TRIGGER_WORDS)) as Record<string, string>;

/**
 * Step 2: the plan, in plain language, all editable. Edits are saved a moment after typing. Approving it locks the
 * tests and starts the build.
 */
export function SpecEditor({ spec, issues, warnings, projectId, edit, readOnly, approved, saving, busy, onApprove }: {
  spec: Spec;
  issues: SpecIssue[];
  warnings: string[];
  projectId: string;
  edit: Edit;
  /** nothing can be edited (the plan is already approved or the proposal is closed) */
  readOnly?: boolean;
  /** the tests are locked */
  approved?: boolean;
  saving: boolean;
  busy: boolean;
  onApprove: () => void;
}) {
  const { stepTypes, apiRefs } = useSpecCatalog(projectId);
  const errors = issues.filter(isErrorIssue);
  const general = issues.filter((i) => !/^(summary|trigger|rules|steps|tests|rule_tests|interface)/.test(i.path));
  const ruleTests = spec.rule_tests ?? {};
  const testRules = (id: string) => Object.entries(ruleTests).filter(([, ids]) => ids.includes(id)).map(([r]) => r);
  const testIssues = (i: number) => issuesAt(issues, `tests[${i}]`);

  return (
    <div className="flex min-w-0 flex-col gap-4" data-testid="ai-spec-panel">
      {warnings.map((w) => (
        <Callout key={w} tone="warn" icon={<TriangleAlert />} data-testid="ai-spec-warning">{w}</Callout>
      ))}

      <section className="flex flex-col gap-2">
        <SectionHeader title="In short" />
        <Textarea
          value={spec.summary}
          rows={3}
          readOnly={readOnly}
          aria-label="Summary"
          onChange={(e) => edit((s) => ({ ...s, summary: e.target.value }))}
          data-testid="ai-spec-summary"
        />
        <IssueList issues={issuesAt(issues, 'summary')} />
      </section>

      <section className="flex flex-col gap-2">
        <SectionHeader title="It starts when" />
        <Select
          value={spec.trigger.kind}
          items={triggerItems}
          disabled={readOnly}
          onValueChange={(v) => v && edit((s) => ({ ...s, trigger: { ...s.trigger, kind: v as TriggerKind } }))}
        >
          <SelectTrigger className="w-full" aria-label="What starts it"><SelectValue /></SelectTrigger>
          <SelectContent>
            {Object.entries(triggerItems).map(([k, label]) => <SelectItem key={k} value={k}>{label}</SelectItem>)}
          </SelectContent>
        </Select>
        {spec.trigger.fields.length > 0 && (
          <Well className="flex flex-col divide-y-0 p-2">
            {spec.trigger.fields.map((f, i) => (
              <div key={f.name} className="flex items-center gap-2 py-1" data-testid={`ai-spec-trigger-field-${f.name}`}>
                <Input
                  value={f.label ?? f.name}
                  readOnly={readOnly}
                  aria-label={`Label of ${f.name}`}
                  className="min-w-0 flex-1"
                  onChange={(e) => edit((s) => ({
                    ...s, trigger: { ...s.trigger, fields: s.trigger.fields.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) },
                  }))}
                />
                <span className="shrink-0 text-[12px] text-muted-foreground">{f.type}</span>
                <label className="flex shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground">
                  <Switch
                    checked={f.required}
                    disabled={readOnly}
                    onCheckedChange={(v) => edit((s) => ({
                      ...s, trigger: { ...s.trigger, fields: s.trigger.fields.map((x, j) => (j === i ? { ...x, required: v } : x)) },
                    }))}
                  />
                  Required
                </label>
              </div>
            ))}
          </Well>
        )}
        <IssueList issues={issuesAt(issues, 'trigger')} />
      </section>

      <section className="flex flex-col gap-1">
        <SectionHeader title="Rules" sub="What must always hold" />
        <WellList className="empty:hidden">
          {spec.rules.map((r, i) => (
            <SpecRule
              key={r.id}
              rule={r}
              testCount={(ruleTests[r.id] ?? []).length}
              issues={[...issuesAt(issues, `rules[${i}]`), ...issuesAt(issues, `rule_tests.${r.id}`)]}
              readOnly={readOnly}
              onChange={(text) => edit((s) => ({ ...s, rules: s.rules.map((x) => (x.id === r.id ? { ...x, text } : x)) }))}
              onRemove={() => edit((s) => {
                const { [r.id]: _gone, ...rest } = s.rule_tests;
                void _gone;
                return {
                  ...s,
                  rules: s.rules.filter((x) => x.id !== r.id),
                  rule_tests: rest,
                  steps: s.steps.map((st) => ({ ...st, rules: st.rules.filter((x) => x !== r.id) })),
                };
              })}
            />
          ))}
        </WellList>
        {!readOnly && (
          <div>
            <Button
              variant="ghost" size="sm" data-testid="ai-spec-rule-add"
              onClick={() => edit((s) => ({ ...s, rules: [...s.rules, { id: nextRuleId(s), text: '' }] }))}
            ><Plus /> Add a rule</Button>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-1">
        <SectionHeader title="Steps" sub="In the order they happen" />
        <WellList className="empty:hidden">
          {spec.steps.map((st, i) => (
            <SpecStep
              key={i}
              index={i}
              step={st}
              stepTypes={stepTypes}
              apiRefs={apiRefs}
              issues={issuesAt(issues, `steps[${i}]`)}
              readOnly={readOnly}
              onChange={(patch) => edit((s) => ({ ...s, steps: s.steps.map((x, j) => (j === i ? { ...x, ...patch } : x)) }))}
              onRemove={() => edit((s) => ({ ...s, steps: s.steps.filter((_, j) => j !== i) }))}
            />
          ))}
        </WellList>
        {!readOnly && (
          <div>
            <Button
              variant="ghost" size="sm" data-testid="ai-spec-step-add"
              onClick={() => edit((s) => ({ ...s, steps: [...s.steps, { name: '', node_type: stepTypes.find((t) => t.type === 'Set')?.type ?? stepTypes[0]?.type ?? 'Set', purpose: '', rules: [] }] }))}
            ><Plus /> Add a step</Button>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-1">
        <SectionHeader
          title="Tests"
          sub={approved ? 'Approved and locked' : 'Check these before you approve'}
          icon={approved ? <Lock /> : undefined}
        />
        {!approved && !readOnly && (
          <p className="text-[12.5px] leading-5 text-muted-foreground">
            These tests decide whether the AI got it right. Once you approve, they are locked: the AI can ask for a change, but only you can make it.
          </p>
        )}
        <WellList data-testid="ai-spec-tests">
          {spec.tests.map((t, i) => (
            <SpecTest
              key={t.id}
              test={t}
              ruleIds={testRules(t.id)}
              issues={testIssues(i)}
              readOnly={readOnly}
              locked={approved}
              onChange={(next) => edit((s) => ({ ...s, tests: s.tests.map((x) => (x.id === t.id ? next : x)) }))}
              onRemove={() => edit((s) => {
                const rt = Object.fromEntries(Object.entries(s.rule_tests).map(([r, ids]) => [r, ids.filter((x) => x !== t.id)]));
                return { ...s, tests: s.tests.filter((x) => x.id !== t.id), rule_tests: rt };
              })}
            />
          ))}
          {spec.tests.length === 0 && <WellRow className="text-warning-text">There are no tests yet. Add one so the result can be checked.</WellRow>}
        </WellList>
        {!readOnly && !approved && (
          <div>
            <Button
              variant="ghost" size="sm" data-testid="ai-spec-test-add"
              onClick={() => edit((s) => ({ ...s, tests: [...s.tests, { ...newTestCase(null), name: 'New test', source: 'human' }] }))}
            ><Plus /> Add a test</Button>
          </div>
        )}
      </section>

      {spec.assumptions.length > 0 && (
        <section className="flex flex-col gap-1.5" data-testid="ai-spec-assumptions">
          <SectionHeader title="The AI assumed" />
          <ul className="flex list-disc flex-col gap-0.5 pl-5 text-[12.5px] leading-5 text-muted-foreground">
            {spec.assumptions.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </section>
      )}

      {spec.questions.length > 0 && (
        <Callout tone="warn" icon={<HelpCircle />} data-testid="ai-spec-questions" className="items-start">
          <div className="font-medium">Worth deciding before you approve</div>
          <ul className="mt-1 list-disc pl-4">{spec.questions.map((q) => <li key={q}>{q}</li>)}</ul>
        </Callout>
      )}

      {general.length > 0 && <IssueList issues={general} />}

      {!readOnly && !approved && (
        <div className="glass-strip sticky bottom-0 flex flex-col gap-2 pb-1 pt-2">
          {errors.length > 0 && (
            <Callout tone="error" icon={<TriangleAlert />}>
              <div>{errors.length === 1 ? 'One thing in the plan needs fixing' : `${errors.length} things in the plan need fixing`} before you can approve it.</div>
              <div className="mt-0.5 text-[12px]">{errors[0].message}</div>
            </Callout>
          )}
          <div className="flex items-center gap-2">
            <Button onClick={onApprove} disabled={busy || errors.length > 0 || spec.tests.length === 0} data-testid="ai-spec-approve">
              {busy ? <Loader2 className="animate-spin" /> : <CheckCheck />}
              Approve and build
            </Button>
            <span className="text-[12px] text-muted-foreground" aria-live="polite">{saving ? 'Saving your edits…' : 'Edits are saved as you go.'}</span>
          </div>
        </div>
      )}
    </div>
  );
}
