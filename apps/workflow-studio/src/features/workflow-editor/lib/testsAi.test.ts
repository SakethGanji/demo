import { describe, expect, it } from 'vitest';
import { newTestCase, type TestCase } from './tests';
import {
  aiDraftsToTests, aiErrorInfo, aiRunsById, driftWorkflowIds, findingToTest, isAiDraft, sameSmoke, smokeBody, smokeForm, staleSet, toggleIn,
  type AIDraft, type Finding,
} from './testsAi';

const draft = (over: Partial<AIDraft> = {}): AIDraft => ({
  ...newTestCase('If', 'boundary'), id: 't_aaaa1111', name: 'If: edge', source: 'ai',
  ai_run: { status: 'passed', failures: [] }, ...over,
});

const finding = (test: Partial<TestCase> = {}): Finding => ({
  id: 'f1', kind: 'node_error', node: 'Score', field: 'amount', input: { items: [{ amount: -1 }] },
  error: { message: 'bad amount' }, count: 3, test: { ...newTestCase('Score', 'boundary'), id: 't_ffff0000', source: 'fuzz', ...test },
});

describe('aiDraftsToTests', () => {
  it('inserts drafts as unlocked ai drafts and drops ai_run', () => {
    const [t] = aiDraftsToTests([draft({ locked: true, status: 'approved', approved_by: 'x' })], []);
    expect(t.status).toBe('draft');
    expect(t.locked).toBe(false);
    expect(t.source).toBe('ai');
    expect(t.approved_by).toBeUndefined();
    expect('ai_run' in t).toBe(false);
  });
  it('never reuses an existing id, even between two drafts', () => {
    const out = aiDraftsToTests([draft(), draft()], ['t_aaaa1111']);
    const ids = new Set(['t_aaaa1111', ...out.map((t) => t.id)]);
    expect(ids.size).toBe(3);
  });
  it('maps dry runs to the inserted test ids', () => {
    const drafts = [draft({ ai_run: { status: 'failed', failures: [] } })];
    const tests = aiDraftsToTests(drafts, []);
    expect(aiRunsById(drafts, tests)[tests[0].id].status).toBe('failed');
  });
});

describe('isAiDraft', () => {
  it('is true only for ai drafts', () => {
    expect(isAiDraft({ source: 'ai', status: 'draft' })).toBe(true);
    expect(isAiDraft({ source: 'ai', status: 'approved' })).toBe(false);
    expect(isAiDraft({ source: 'human', status: 'draft' })).toBe(false);
  });
});

describe('findingToTest', () => {
  it('keeps the fuzz source and stays a draft', () => {
    const t = findingToTest(finding({ status: 'approved', locked: true }), []);
    expect(t.source).toBe('fuzz');
    expect(t.status).toBe('draft');
    expect(t.locked).toBe(false);
    expect(t.id).toBe('t_ffff0000');
  });
  it('keeps ai source for ai inputs and renews a taken id', () => {
    const t = findingToTest(finding({ source: 'ai' }), ['t_ffff0000']);
    expect(t.source).toBe('ai');
    expect(t.id).not.toBe('t_ffff0000');
  });
});

describe('drift helpers', () => {
  it('maps stale ids', () => {
    expect(staleSet({ alerts: [], stale_test_ids: ['a', 'b'] }).has('b')).toBe(true);
    expect(staleSet(undefined).size).toBe(0);
  });
  it('lists workflows with open drift only', () => {
    const ids = driftWorkflowIds([
      { workflow_id: 'w1', status: 'open' }, { workflow_id: 'w2', status: 'resolved' }, { workflow_id: 'w1', status: 'open' },
    ]);
    expect([...ids]).toEqual(['w1']);
    expect(driftWorkflowIds(undefined).size).toBe(0);
  });
});

describe('smoke form', () => {
  it('toggles values in a list', () => {
    expect(toggleIn(['a'], 'b', true)).toEqual(['a', 'b']);
    expect(toggleIn(['a', 'b'], 'a', false)).toEqual(['b']);
    expect(toggleIn(['a'], 'a', true)).toEqual(['a']);
  });
  it('drops unknown ids and nodes, and empties a blank schedule', () => {
    const f = { ...smokeForm(null), test_ids: ['t1', 'gone'], live_nodes: ['Risk', 'Old'], schedule: '  ' };
    expect(smokeBody(f, ['t1'], ['Risk'])).toMatchObject({ test_ids: ['t1'], live_nodes: ['Risk'], schedule: null });
  });
  it('trims the schedule and compares configs', () => {
    const a = { ...smokeForm(null), schedule: ' 0 * * * * ' };
    const b = { ...smokeForm(null), schedule: '0 * * * *' };
    expect(sameSmoke(a, b)).toBe(true);
    expect(sameSmoke(a, { ...b, enabled: true })).toBe(false);
  });
});

describe('aiErrorInfo', () => {
  it('names the not-set-up case and passes the model message on 502', () => {
    expect(aiErrorInfo(503, 'x').title).toBe("AI isn't set up");
    expect(aiErrorInfo(502, 'bad json').description).toBe('bad json');
  });
});
