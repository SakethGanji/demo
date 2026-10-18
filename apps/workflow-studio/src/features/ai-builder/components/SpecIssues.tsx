import { AlertTriangle, Info } from 'lucide-react';
import { isErrorIssue } from '../lib/specIssues';
import type { SpecIssue } from '../types';

/** The issues the plan checker found for one part of the plan, shown right under that part. */
export function IssueList({ issues }: { issues: SpecIssue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="flex flex-col gap-1">
      {issues.map((i, n) => (
        <li
          key={`${i.code}-${i.path}-${n}`}
          data-testid={`ai-spec-issue-${i.code}`}
          data-path={i.path}
          data-level={i.level ?? 'error'}
          className={`flex items-start gap-1.5 text-[12px] leading-4 ${isErrorIssue(i) ? 'text-destructive' : 'text-warning-text'}`}
        >
          {isErrorIssue(i) ? <AlertTriangle className="mt-px size-3 shrink-0" /> : <Info className="mt-px size-3 shrink-0" />}
          <span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}
