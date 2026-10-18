import { Code2, Globe, TestTube2 } from 'lucide-react';
import { Callout } from '@/shared/components/ui/surface';
import type { Proposal } from '../types';

/**
 * What a bank reviewer must look at before keeping: new outside addresses the workflow will call, code the AI wrote,
 * and tests that run on made-up answers instead of the real service.
 */
export function ReviewFlags({ proposal }: { proposal: Proposal }) {
  const diff = proposal.diff;
  const dests = diff?.new_destinations ?? [];
  const code = diff?.new_code ?? [];
  const mocks = proposal.report?.mock_warnings ?? [];
  if (!dests.length && !code.length && !mocks.length) return null;
  return (
    <div className="flex flex-col gap-2" data-testid="ai-review-flags">
      {dests.length > 0 && (
        <Callout tone="warn" icon={<Globe />} className="items-start" data-testid="ai-review-flag-destinations">
          <div className="font-medium">Sends data to {dests.length === 1 ? 'a new outside address' : 'new outside addresses'}</div>
          <ul className="mt-0.5 break-all font-mono text-[12px]">{dests.map((d) => <li key={d}>{d}</li>)}</ul>
          <div className="mt-0.5">Check that you trust {dests.length === 1 ? 'it' : 'them'} before you keep this.</div>
        </Callout>
      )}
      {code.length > 0 && (
        <Callout tone="warn" icon={<Code2 />} className="items-start" data-testid="ai-review-flag-code">
          <div className="font-medium">The AI wrote code</div>
          <div>In {code.map((c) => `"${c}"`).join(', ')}. Ask someone who reads code to look at it.</div>
        </Callout>
      )}
      {mocks.length > 0 && (
        <Callout tone="warn" icon={<TestTube2 />} className="items-start" data-testid="ai-review-flag-mocks">
          <div className="font-medium">Some tests use made-up answers</div>
          <ul className="mt-0.5 list-disc pl-4">
            {mocks.slice(0, 5).map((m, i) => (
              <li key={i}>{typeof m.message === 'string' ? m.message : `${m.node ?? 'A step'} uses a stand-in answer.`}</li>
            ))}
          </ul>
          <div className="mt-0.5">Tests with stand-in answers do not prove the real service behaves that way.</div>
        </Callout>
      )}
    </div>
  );
}
