import { create } from 'zustand';
import type { CheckResult, Coverage, Finding, Shape, Severity } from '../lib/shapes';

/** First segment of a finding's `param`: `headers.x` and `values[0].name` both belong to `headers` / `values`. */
export function paramRoot(param: string): string {
  return param.split(/[.[]/, 1)[0];
}

interface CheckState {
  result: CheckResult | null;
  findings: Finding[];
  /** Keyed by node NAME (what the engine reports), not the canvas id. */
  findingsByNodeMap: Record<string, Finding[]>;
  shapesByNode: CheckResult['shapes_by_node'];
  coverage: Coverage | null;
  counts: Record<Severity, number>;
  setResult: (r: CheckResult) => void;
  clear: () => void;
  findingsByNode: (node: string) => Finding[];
  /** Findings on `node` whose `param` matches `param` (top-level name; nested params match their first segment). */
  findingsByParam: (node: string, param: string) => Finding[];
  /** Worst severity for a node, or null. Info does not count. */
  nodeSeverity: (node: string) => 'error' | 'warning' | null;
}

const emptyCounts = (): Record<Severity, number> => ({ error: 0, warning: 0, info: 0 });

export const useCheckStore = create<CheckState>((set, get) => ({
  result: null,
  findings: [],
  findingsByNodeMap: {},
  shapesByNode: {},
  coverage: null,
  counts: emptyCounts(),

  setResult: (r) => {
    const findings = r.findings ?? [];
    const map: Record<string, Finding[]> = {};
    const counts = emptyCounts();
    for (const f of findings) {
      counts[f.severity] = (counts[f.severity] ?? 0) + 1;
      if (f.node) (map[f.node] ??= []).push(f);
    }
    set({ result: r, findings, findingsByNodeMap: map, shapesByNode: r.shapes_by_node ?? {}, coverage: r.coverage ?? null, counts });
  },

  clear: () => set({ result: null, findings: [], findingsByNodeMap: {}, shapesByNode: {}, coverage: null, counts: emptyCounts() }),

  findingsByNode: (node) => get().findingsByNodeMap[node] ?? [],
  findingsByParam: (node, param) =>
    (get().findingsByNodeMap[node] ?? []).filter((f) => f.param && paramRoot(f.param) === param),
  nodeSeverity: (node) => {
    const fs = get().findingsByNodeMap[node];
    if (!fs) return null;
    if (fs.some((f) => f.severity === 'error')) return 'error';
    if (fs.some((f) => f.severity === 'warning')) return 'warning';
    return null;
  },
}));

export type { Shape };
