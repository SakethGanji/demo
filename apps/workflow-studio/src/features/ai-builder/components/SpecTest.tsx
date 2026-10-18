import { WellRow } from '@/shared/components/ui/surface';
import { useState } from 'react';
import { Lock, Pencil, Trash2 } from 'lucide-react';
import type { TestCase } from '@/features/workflow-editor/lib/tests';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Tip } from '@/shared/components/ui/tooltip';
import {
  categoryWords, describeAssertion, describeMock, flattenInput, parseLike, setAtPath, shortValue, type InputField,
} from '../lib/plainTest';
import type { SpecIssue } from '../types';
import { IssueList } from './SpecIssues';

/**
 * One proposed test in plain language: what goes in, what is faked, what must be true. Edit turns the facts into boxes.
 * Approving the plan locks every test here; after that the AI can ask for a change but never makes one.
 */
export function SpecTest({ test, ruleIds, issues, readOnly, locked, onChange, onRemove }: {
  test: TestCase;
  ruleIds: string[];
  issues: SpecIssue[];
  readOnly?: boolean;
  /** the test is approved and locked */
  locked?: boolean;
  onChange: (t: TestCase) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const canEdit = !readOnly && !locked;
  const inputs = flattenInput(test.input.items[0]);
  const mocks = Object.entries(test.mocks ?? {});

  const setInput = (f: InputField, text: string) => {
    const first = setAtPath(test.input.items[0] ?? {}, f.path, parseLike(text, f.value));
    onChange({ ...test, input: { ...test.input, items: [first, ...test.input.items.slice(1)] } });
  };
  const setExpectValue = (i: number, text: string) => {
    const a = test.expect[i];
    onChange({ ...test, expect: test.expect.map((x, j) => (j === i ? { ...a, value: parseLike(text, typeof a.value === 'number' ? 0 : '') } : x)) });
  };

  return (
    <WellRow className="flex-col items-stretch gap-2 py-2.5" data-testid={`ai-spec-test-${test.id}`} data-locked={locked ? 'true' : 'false'}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {editing && canEdit ? (
            <Input value={test.name} aria-label="Test name" onChange={(e) => onChange({ ...test, name: e.target.value })} />
          ) : (
            <div className="text-[13px] font-medium leading-5">{test.name}</div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge variant="neutral">{categoryWords(test)}</Badge>
            {ruleIds.map((r) => <Badge key={r} variant="ai">Rule {r}</Badge>)}
            {locked && <Badge variant="success"><Lock /> Approved</Badge>}
          </div>
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-0.5">
            <Tip label={editing ? 'Done editing' : 'Edit this test'}>
              <Button
                variant={editing ? 'secondary' : 'ghost'} size="icon-sm" aria-label={editing ? 'Done editing' : 'Edit this test'}
                onClick={() => setEditing((v) => !v)} data-testid={`ai-spec-test-edit-${test.id}`}
              ><Pencil /></Button>
            </Tip>
            <Tip label="Remove this test">
              <Button variant="ghost" size="icon-sm" aria-label="Remove this test" onClick={onRemove}><Trash2 /></Button>
            </Tip>
          </div>
        )}
      </div>

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px] leading-[18px]">
        <dt className="text-muted-foreground">When it gets</dt>
        <dd className="flex min-w-0 flex-col gap-1">
          {inputs.length === 0 && <span className="text-muted-foreground">No input values</span>}
          {inputs.map((f) => (
            <span key={f.path} className="flex min-w-0 items-center gap-1.5">
              <span className="shrink-0 text-muted-foreground">{f.path.replace(/\./g, ' / ')}:</span>
              {editing && canEdit && typeof f.value !== 'object' ? (
                <Input
                  value={f.value === null ? '' : String(f.value)} aria-label={f.path} className="h-7 min-w-0 flex-1"
                  onChange={(e) => setInput(f, e.target.value)}
                />
              ) : (
                <span className="min-w-0 break-words font-medium">{shortValue(f.value)}</span>
              )}
            </span>
          ))}
        </dd>
        {mocks.length > 0 && (
          <>
            <dt className="text-muted-foreground">Pretending that</dt>
            <dd className="flex flex-col gap-0.5">{mocks.map(([step, m]) => <span key={step}>{describeMock(step, m)}</span>)}</dd>
          </>
        )}
        <dt className="text-muted-foreground">It must be true that</dt>
        <dd className="flex min-w-0 flex-col gap-1">
          {test.expect.length === 0 && <span className="text-warning-text">Nothing is checked yet</span>}
          {test.expect.map((a, i) => {
            const editableValue = editing && canEdit && a.subject !== 'node' && !['exists', 'not_exists', 'status', 'count'].includes(a.op)
              && a.value !== undefined && typeof a.value !== 'object';
            return (
              <span key={i} className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span>{describeAssertion(editableValue ? { ...a, value: undefined } : a)}</span>
                {editableValue && (
                  <Input
                    value={String(a.value)} aria-label="Expected value" className="h-7 w-28"
                    onChange={(e) => setExpectValue(i, e.target.value)}
                  />
                )}
                {editing && canEdit && (
                  <Button
                    variant="ghost" size="icon-xs" aria-label="Remove this check"
                    onClick={() => onChange({ ...test, expect: test.expect.filter((_, j) => j !== i) })}
                  ><Trash2 /></Button>
                )}
              </span>
            );
          })}
        </dd>
      </dl>
      <IssueList issues={issues} />
    </WellRow>
  );
}
