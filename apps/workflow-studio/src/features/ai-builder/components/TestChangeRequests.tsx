import { MessageSquareWarning } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Callout } from '@/shared/components/ui/surface';
import { describeAssertion } from '../lib/plainTest';
import type { Assertion } from '@/features/workflow-editor/lib/tests';
import type { Proposal } from '../types';

function proposedLines(p: unknown): string[] {
  if (!p || typeof p !== 'object') return [];
  const t = p as { name?: unknown; expect?: unknown };
  const lines: string[] = [];
  if (typeof t.name === 'string') lines.push(t.name);
  if (Array.isArray(t.expect)) for (const a of t.expect as Assertion[]) if (a && typeof a === 'object') lines.push(describeAssertion(a));
  return lines;
}

/**
 * When the AI thinks an approved test is wrong it may only ask. Nothing here applies a change: a person decides,
 * either by asking for a revision or by editing the test in the Tests panel after keeping the workflow.
 */
export function TestChangeRequests({ proposal, onUseInRevise }: { proposal: Proposal; onUseInRevise: (text: string) => void }) {
  const reqs = proposal.test_change_requests ?? [];
  if (!reqs.length) return null;
  const names = new Map((proposal.spec?.tests ?? []).map((t) => [t.id, t.name]));
  return (
    <div className="flex flex-col gap-2" data-testid="ai-review-test-changes">
      {reqs.map((r) => {
        const lines = proposedLines(r.proposed);
        return (
          <Callout key={`${r.test_id}-${r.reason}`} tone="warn" icon={<MessageSquareWarning />} className="items-start" data-testid={`ai-review-test-change-${r.test_id}`}>
            <div className="font-medium">The AI thinks the test "{names.get(r.test_id) ?? r.test_id}" may be wrong</div>
            {r.reason && <div className="mt-0.5">{r.reason}</div>}
            {lines.length > 0 && (
              <div className="mt-1">
                <span className="font-medium">What it would check instead:</span>
                <ul className="list-disc pl-4">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
              </div>
            )}
            <div className="mt-1 text-[12px]">
              Approved tests never change on their own. To act on this, describe the change under "Ask for a change" below, or edit the test in the Tests panel after you keep the workflow.
            </div>
            <Button
              variant="ghost" size="xs" className="mt-1"
              onClick={() => onUseInRevise(`About the test "${names.get(r.test_id) ?? r.test_id}": ${r.reason}`.trim())}
            >Use this in my request</Button>
          </Callout>
        );
      })}
    </div>
  );
}
