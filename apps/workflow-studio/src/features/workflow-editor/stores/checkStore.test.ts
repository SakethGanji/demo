import { describe, it, expect, beforeEach } from 'vitest';
import { useCheckStore, paramRoot } from './checkStore';
import type { CheckResult } from '../lib/shapes';

const result: CheckResult = {
  findings: [
    { code: 'WF002', severity: 'error', message: 'no field', node: 'A', param: 'url', path: '$json.x' },
    { code: 'WF003', severity: 'warning', message: 'type', node: 'A', param: 'headers.auth', path: null },
    { code: 'WF005', severity: 'info', message: 'unknown', node: 'B', param: 'x', path: null },
    { code: 'WF008', severity: 'error', message: 'cycle', node: null, param: null, path: null },
  ],
  shapes_by_node: { A: { inputs: {}, outputs: {} } },
  coverage: { refs_total: 3, refs_checked: 2, refs_unknown: 1, nodes_total: 2, nodes_known_output: 1 },
};

describe('checkStore', () => {
  beforeEach(() => useCheckStore.getState().clear());

  it('derives findings by node, counts and coverage', () => {
    useCheckStore.getState().setResult(result);
    const s = useCheckStore.getState();
    expect(s.findingsByNode('A')).toHaveLength(2);
    expect(s.findingsByNode('nope')).toEqual([]);
    expect(s.counts).toEqual({ error: 2, warning: 1, info: 1 });
    expect(s.coverage?.refs_checked).toBe(2);
    expect(s.nodeSeverity('A')).toBe('error');
    expect(s.nodeSeverity('B')).toBeNull();
  });

  it('matches params by first segment', () => {
    useCheckStore.getState().setResult(result);
    const s = useCheckStore.getState();
    expect(s.findingsByParam('A', 'url')).toHaveLength(1);
    expect(s.findingsByParam('A', 'headers')).toHaveLength(1);
    expect(s.findingsByParam('A', 'auth')).toHaveLength(0);
    expect(paramRoot('values[0].name')).toBe('values');
  });

  it('warning-only node is a warning; clear empties', () => {
    useCheckStore.getState().setResult({ ...result, findings: [result.findings[1]] });
    expect(useCheckStore.getState().nodeSeverity('A')).toBe('warning');
    useCheckStore.getState().clear();
    expect(useCheckStore.getState().result).toBeNull();
    expect(useCheckStore.getState().counts.error).toBe(0);
  });
});
