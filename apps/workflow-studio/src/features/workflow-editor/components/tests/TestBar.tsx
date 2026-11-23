import { FlaskConical } from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useTestStore } from '../../stores/testStore';
import { barState, barText, compactBarText } from '../../lib/tests';
import { openTestsTab } from '../../lib/testActions';
import { Tip } from '@/shared/components/ui/tooltip';
import { StatusDot } from '@/shared/components/ui/status-dot';

/** Navbar summary of the last test run (or the scorecard's bar once scored). Click opens the Tests tab. */
export default function TestBar() {
  const hasTests = useWorkflowStore((s) => s.tests.length > 0);
  const report = useTestStore((s) => s.report);
  const scorecard = useTestStore((s) => s.scorecard);
  if (!hasTests) return null;

  const state = barState(report);
  const full = barText(report, scorecard) || 'Tests';
  const text = compactBarText(report, scorecard) || 'Tests';
  const tone = state === 'pass' ? 'ok' : state === 'fail' ? 'err' : 'idle';
  return (
    <Tip label={`${full} · open the Tests tab`}>
      <button
        type="button"
        onClick={openTestsTab}
        data-testid="test-bar"
        data-state={state}
        aria-label={full}
        className="hidden h-7 shrink-0 items-center gap-1.5 rounded-ctl px-2 text-[12px] whitespace-nowrap text-muted-foreground transition-colors hover:bg-(--hover-fill) hover:text-foreground xl:inline-flex"
      >
        <FlaskConical size={12} className="shrink-0" />
        <StatusDot tone={tone} flat />
        <span className="truncate">{text}</span>
      </button>
    </Tip>
  );
}
