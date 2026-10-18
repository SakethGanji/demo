import { describe, expect, it } from 'vitest';
import {
  barState, barText, compactBarText, composeBar, failureDetail, failureMessage, nodeLabelMap, gridCell, gridGapCount, newTestCase, newTestId, recordRename,
  renameNodeInTests, rowStatus, successMock, summaryText, visibleCategories,
  type Grid, type Scorecard, type TestCase, type TestRunReport, type TestSummary,
} from './tests';

const summary = (p: Partial<TestSummary> = {}): TestSummary => ({
  total: 14, passed: 14, failed: 0, errored: 0, approved_total: 0, approved_failed: 0, ...p,
});

const grid: Grid = {
  categories: ['normal', 'boundary', 'ext_success'],
  rows: [
    { node: 'If', categories: { normal: { status: 'pass', tests: ['t_1'] }, boundary: { status: 'none', tests: [] }, ext_success: { status: 'na', tests: [] } } },
    { node: 'Set', categories: { normal: { status: 'fail', tests: ['t_2'] }, boundary: { status: 'na', tests: [] }, ext_success: { status: 'na', tests: [] } } },
  ],
};

const report = (s = summary()): TestRunReport => ({
  results: [{ test_id: 't_1', name: 'a', status: 'failed', duration_ms: 1, execution_id: null, assertions: [], failures: [], warnings: [], node_status: {}, ports_taken: {}, requests: {}, outputs: {} }],
  summary: s, badges: {}, grid,
});

describe('tests helpers', () => {
  it('makes ids of the contract form', () => {
    expect(newTestId()).toMatch(/^t_[0-9a-f]{8}$/);
  });

  it('builds a node draft', () => {
    const t = newTestCase('If', 'boundary');
    expect(t.id).toMatch(/^t_[0-9a-f]{8}$/);
    expect(t.target).toEqual({ kind: 'node', node: 'If' });
    expect(t.category).toBe('boundary');
    expect(t.status).toBe('draft');
    expect(t.locked).toBe(false);
  });

  it('formats summary and bar text', () => {
    expect(summaryText(summary({ total: 5, passed: 3 }))).toBe('3/5 passing');
    expect(barText(report(), null)).toBe('14/14 passing');
    const sc = { bar: '14/14 passing · 1 gap · 19/20 planted bugs caught' } as Scorecard;
    expect(barText(report(), sc)).toBe(sc.bar);
    expect(barText(null, null)).toBe('');
  });

  it('composes a bar locally', () => {
    const sc = { tests: summary(), gaps: [{ node: 'If', category: 'boundary', reason: '' }], mutation: { total: 20, caught: 19, survived: [] } } as unknown as Scorecard;
    expect(composeBar(sc)).toBe('14/14 passing · 1 gap · 19/20 planted bugs caught');
  });

  it('derives bar state', () => {
    expect(barState(null)).toBe('none');
    expect(barState(report(summary({ total: 0, passed: 0 })))).toBe('none');
    expect(barState(report())).toBe('pass');
    expect(barState(report(summary({ failed: 1, passed: 13 })))).toBe('fail');
    expect(barState(report(summary({ errored: 1, passed: 13 })))).toBe('fail');
  });

  it('reads row status', () => {
    expect(rowStatus('t_1', report())).toBe('failed');
    expect(rowStatus('t_x', report())).toBe('draft-unrun');
    expect(rowStatus('t_1', null)).toBe('draft-unrun');
  });

  it('grid helpers', () => {
    expect(gridCell(grid, 'If', 'boundary')?.status).toBe('none');
    expect(gridCell(grid, 'Nope', 'normal')).toBeUndefined();
    expect(gridGapCount(grid)).toBe(1);
    expect(visibleCategories(grid)).toEqual(['normal', 'boundary']);
  });

  it('rewrites a rename in tests and defaults', () => {
    const t: TestCase = {
      ...newTestCase('Old', 'normal'),
      mocks: { Old: { items: [{ a: '{{ $node["Old"].json.x }}' }] } },
      expect: [{ node: 'Old', op: 'count', value: 1 }, { node: 'Other', op: 'count', value: 1 }],
      target: { kind: 'range', from: 'Old', to: 'Other' },
    };
    const r = renameNodeInTests([t], { mocks: { Old: { timeout: true } } }, 'Old', 'New');
    expect(r.tests[0].target).toEqual({ kind: 'range', from: 'New', to: 'Other' });
    expect(Object.keys(r.tests[0].mocks)).toEqual(['New']);
    expect(JSON.stringify(r.tests[0].mocks)).toContain('$node[\\"New\\"]');
    expect(r.tests[0].expect.map((a) => a.node)).toEqual(['New', 'Other']);
    expect(r.tests[0].covers).toEqual(['New']);
    expect(Object.keys(r.defaults.mocks)).toEqual(['New']);
  });

  it('collapses chained renames', () => {
    let r = recordRename({}, 'a', 'b');
    r = recordRename(r, 'b', 'c');
    expect(r).toEqual({ a: 'c' });
    expect(recordRename(r, 'c', 'a')).toEqual({});
    expect(recordRename({}, 'x', 'x')).toEqual({});
  });

  it('picks the success mock', () => {
    expect(successMock([])).toBeNull();
    expect(successMock([
      { label: '404', category: 'ext_error', mock: { http: { status: 404 } } },
      { label: '200', category: 'ext_success', mock: { http: { status: 200 } } },
    ])).toEqual({ http: { status: 200 } });
  });
});

describe('failureDetail', () => {
  it('adds nothing when the message already says expected and actual', () => {
    expect(failureDetail({ kind: 'assertion', message: 'expected 200, got 500', expected: 200, actual: 500 })).toBe('');
  });
  it('adds the missing parts for terse messages', () => {
    expect(failureDetail({ kind: 'assertion', message: 'status differs', expected: 200, actual: 500 })).toBe('expected 200, got 500');
  });
  it('ignores other kinds', () => {
    expect(failureDetail({ kind: 'unmocked', message: 'x', expected: 1 })).toBe('');
  });
});

describe('nodeLabelMap', () => {
  it('prefers the label and falls back to the name', () => {
    const m = nodeLabelMap([{ data: { name: 'Risk API', label: 'Ask risk API' } }, { data: { name: 'If', label: '' } }]);
    expect(m['Risk API']).toBe('Ask risk API');
    expect(m.If).toBe('If');
  });
});

describe('failureMessage', () => {
  it('strips a leading node prefix', () => {
    expect(failureMessage({ node: 'Risk API', message: 'Risk API: expected 200, got 500' })).toBe('expected 200, got 500');
    expect(failureMessage({ node: null, message: 'boom' })).toBe('boom');
  });
});

describe('compactBarText', () => {
  it('is empty before any run', () => expect(compactBarText(null, null)).toBe(''));
  it('shows passed/total, plus planted bugs once scored', () => {
    const sc = { tests: { passed: 3, total: 4 }, mutation: { total: 19, caught: 11, survived: [] }, bar: 'x' } as unknown as Scorecard;
    expect(compactBarText(null, sc)).toBe('3/4 · 11/19 bugs caught');
    expect(compactBarText(null, { ...sc, mutation: null })).toBe('3/4');
  });
});
