import { CheckCircle2, CircleDashed, XCircle } from 'lucide-react';
import type { AssertionResult, Failure, TestResult } from '@/features/workflow-editor/lib/tests';
import { shortValue } from '../lib/plainTest';
import type { Proposal } from '../types';
import { WellList, WellRow } from '@/shared/components/ui/surface';

function failedAssertion(a: AssertionResult): string {
  const hasValues = a.expected !== undefined || a.actual !== undefined;
  const where = a.node ? `${a.node}${a.path ? ` / ${a.path}` : ''}: ` : '';
  return hasValues ? `${where}expected ${shortValue(a.expected)}, but got ${a.actual === undefined ? 'nothing' : shortValue(a.actual)}.` : `${where}${a.message}`;
}

function failureLine(f: Failure): string {
  switch (f.kind) {
    case 'unmocked': return `${f.node ?? 'A step'} reached an outside service that the test did not fake.`;
    case 'node_error': return `${f.node ?? 'A step'} failed while the test ran.`;
    case 'contract': return `${f.node ?? 'A step'} received or returned data in an unexpected form.`;
    case 'setup': return 'The test could not be set up.';
    default: return f.message;
  }
}

type Row = { id: string; name: string; status: 'passed' | 'failed' | 'error' | 'not_run'; result?: TestResult };

/** The proposed tests with their results; the ones that fail show what was expected and what happened. */
export function ReviewTests({ proposal }: { proposal: Proposal }) {
  const results = proposal.report?.tests?.results ?? [];
  const byId = new Map(results.map((r) => [r.test_id, r]));
  const rows: Row[] = [];
  for (const t of proposal.spec?.tests ?? []) {
    const r = byId.get(t.id);
    rows.push({ id: t.id, name: t.name, status: r ? r.status : 'not_run', result: r });
    byId.delete(t.id);
  }
  for (const r of byId.values()) rows.push({ id: r.test_id, name: r.name, status: r.status, result: r });
  if (!rows.length) return null;
  return (
    <WellList role="list" data-testid="ai-review-tests">
      {rows.map((row) => {
        const bad = row.status === 'failed' || row.status === 'error';
        return (
          <WellRow key={row.id} role="listitem" className="items-start" data-testid={`ai-review-test-${row.id}`} data-status={row.status}>
            <span className="mt-0.5 shrink-0">
              {row.status === 'passed' ? <CheckCircle2 className="size-3.5 text-success" />
                : bad ? <XCircle className="size-3.5 text-destructive" /> : <CircleDashed className="size-3.5 text-muted-foreground" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] leading-5">{row.name}</div>
              {row.status === 'not_run' && <div className="text-[12px] text-muted-foreground">This test did not run.</div>}
              {bad && row.result && (
                <ul className="mt-0.5 flex list-disc flex-col gap-0.5 pl-4 text-[12px] leading-4 text-destructive">
                  {row.result.assertions.filter((a) => !a.passed).map((a, i) => <li key={`a${i}`}>{failedAssertion(a)}</li>)}
                  {row.result.failures.filter((f) => f.kind !== 'assertion').map((f, i) => <li key={`f${i}`}>{failureLine(f)}</li>)}
                </ul>
              )}
            </div>
          </WellRow>
        );
      })}
    </WellList>
  );
}
