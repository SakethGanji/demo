import type { SpecIssue } from '../types';

export const isErrorIssue = (i: SpecIssue) => (i.level ?? 'error') === 'error';

/** Issues whose path is `prefix` or lies under it (`steps[2]` matches `steps[2].api_ref`). */
export function issuesAt(issues: SpecIssue[], prefix: string): SpecIssue[] {
  return issues.filter((i) => i.path === prefix || i.path.startsWith(`${prefix}.`) || i.path.startsWith(`${prefix}[`));
}
