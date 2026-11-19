import { matchTab } from '@/shared/hooks/useHashParam'

/** The run page's tabs and their sub-tabs (kept apart so routes.ts can map old Explain links without loading the pages). */
export const EXPLAIN_SUBS = ['Performance', 'Errors', 'Features & SHAP', 'Fairness', 'Threshold', 'Monotonicity', 'Scorecard', 'Text'] as const
export type ExplainSub = (typeof EXPLAIN_SUBS)[number]

export const TABS = ['Results', 'Explain', 'Doctor'] as const
export type Tab = (typeof TABS)[number]
export const DOCTOR_SUBS = ['Model Doctor', 'Weak Slices', 'Shift', 'Learning Curve', 'Labels'] as const
export type DoctorSub = (typeof DOCTOR_SUBS)[number]
/** Old Explain tab names (links in docs, the agent, the Doctor's actions) → the run page's tab and sub-tab. */
const SUB_ALIASES: Record<string, string> = { doctor: 'Model Doctor', 'Labelling Queue': 'Labels', Annotators: 'Labels', shap: 'Features & SHAP', slices: 'Weak Slices', 'Fair Lending': 'Fairness' }
/** The run page link for a view by name: `models?run=X&tab=Doctor&sub=Shift`. */
export function runView(runId: string, name?: string, extra: Record<string, string> = {}): string {
  const to = name ? tabOfSub(name) ?? (TABS.includes(name as Tab) ? { tab: name as Tab, sub: '' } : null) : null
  return `models?run=${runId}${to ? `&tab=${to.tab}${to.sub ? `&sub=${encodeURIComponent(to.sub)}` : ''}` : ''}${Object.entries(extra).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('')}`
}

export function tabOfSub(raw: string | null): { tab: Tab; sub: string } | null {
  const d = matchTab(DOCTOR_SUBS, raw, SUB_ALIASES)
  if (d) return { tab: 'Doctor', sub: d }
  const e = matchTab(EXPLAIN_SUBS, raw, SUB_ALIASES)
  return e ? { tab: 'Explain', sub: e } : null
}

