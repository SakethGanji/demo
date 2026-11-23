import { Bug, GitBranch, Repeat, Target } from 'lucide-react';
import type { Scorecard } from '../../lib/tests';
import { CATEGORY_LABELS, type Category } from '../../lib/tests';
import { selectNodeByName } from '../../lib/testActions';
import { useNodeLabels } from '../../hooks/useNodeLabels';
import { cn } from '@/shared/lib/utils';

function NodeLink({ name, label, children }: { name: string; label: string; children?: React.ReactNode }) {
  return (
    <button
      type="button"
      title={name}
      onClick={() => selectNodeByName(name)}
      className="rounded-chip px-1 text-left text-foreground transition-colors hover:bg-(--hover-fill)"
    >
      <span className="font-medium">{label}</span>
      {children}
    </button>
  );
}

function Section({ icon, title, summary, tone, testId, children }: {
  icon: React.ReactNode;
  title: string;
  summary: string;
  tone?: 'ok' | 'warn';
  testId: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1 basis-56" data-testid={testId}>
      <div className="flex items-center gap-1.5 text-[12px] font-medium text-foreground">
        <span className="text-muted-foreground">{icon}</span>
        {title}
        <span className={cn('font-normal', tone === 'warn' ? 'text-warning-text' : 'text-muted-foreground')}>{summary}</span>
      </div>
      {children && <div className="mt-0.5 flex flex-col gap-0.5 pl-5 text-[12px] text-muted-foreground">{children}</div>}
    </div>
  );
}

/** What "Score" found: branches never taken, planted bugs that survived, repeatability and coverage gaps. */
export default function ScorecardView({ scorecard }: { scorecard: Scorecard }) {
  const labels = useNodeLabels();
  const lbl = (n: string) => labels[n] ?? n;
  const cov = scorecard.coverage;
  const mut = scorecard.mutation;
  const rep = scorecard.repeatability;
  const gaps = scorecard.gaps ?? [];
  const survived = mut?.survived ?? [];
  const untaken = cov?.untaken ?? [];

  return (
    <div className="px-3 pt-1 pb-2" data-testid="tests-scorecard">
      <div className="mb-1 text-[12px] font-medium text-foreground" data-testid="tests-scorecard-bar">{scorecard.bar}</div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {cov && (
          <Section
            testId="scorecard-branches"
            icon={<GitBranch size={12} />}
            title="Branches"
            summary={`${cov.branches_taken}/${cov.branches_total} taken`}
            tone={untaken.length > 0 ? 'warn' : 'ok'}
          >
            {untaken.map((u) => (
              <div key={`${u.node}:${u.port}`} data-testid="scorecard-untaken" data-node={u.node}>
                <NodeLink name={u.node} label={lbl(u.node)}>
                  <span className="font-normal text-muted-foreground"> never takes {u.port}</span>
                </NodeLink>
              </div>
            ))}
          </Section>
        )}
        {mut && (
          <Section
            testId="scorecard-mutation"
            icon={<Bug size={12} />}
            title="Planted bugs"
            summary={`${mut.caught}/${mut.total} caught`}
            tone={survived.length > 0 ? 'warn' : 'ok'}
          >
            {survived.map((m) => (
              <div key={m.id} data-testid="scorecard-survivor" data-node={m.node}>
                <NodeLink name={m.node} label={lbl(m.node)}>
                  <span className="font-normal text-muted-foreground"> {m.description}</span>
                </NodeLink>
              </div>
            ))}
          </Section>
        )}
        {rep && (
          <Section
            testId="scorecard-repeat"
            icon={<Repeat size={12} />}
            title="Repeatable"
            summary={rep.identical ? `same result in ${rep.runs} runs` : `${rep.differing_tests.length} tests differ between runs`}
            tone={rep.identical ? 'ok' : 'warn'}
          />
        )}
        {gaps.length > 0 && (
          <Section
            testId="scorecard-gaps"
            icon={<Target size={12} />}
            title="Gaps"
            summary={`${gaps.length}`}
            tone="warn"
          >
            {gaps.slice(0, 6).map((g) => (
              <div key={`${g.node}:${g.category}`} data-testid="scorecard-gap" data-node={g.node}>
                <NodeLink name={g.node} label={lbl(g.node)}>
                  <span className="font-normal text-muted-foreground"> {CATEGORY_LABELS[g.category as Category] ?? g.category}: {g.reason}</span>
                </NodeLink>
              </div>
            ))}
            {gaps.length > 6 && <div>and {gaps.length - 6} more</div>}
          </Section>
        )}
      </div>
    </div>
  );
}
