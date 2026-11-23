import { describe, expect, it } from 'vitest';
import { buildLog, humanType } from './buildLog';
import type { AiEvent } from '../types';

const ev = (seq: number, event: string, payload: Record<string, unknown>): AiEvent => ({ seq, at: '2026-10-18T10:00:00Z', event, payload, actor: null });
const node = (name: string, type: string, x: number) => ({ name, type, position: { x, y: 0 } });

describe('buildLog', () => {
  it('turns a round into added steps, a link row, a check and per-test rows', () => {
    const log = buildLog([
      ev(1, 'round_started', { round: 0 }),
      ev(2, 'round_shape', {
        round: 0, nodes: [node('Receive', 'FormTrigger', 0), node('Pay', 'HttpRequest', 280)],
        connections: [['Receive', 'main', 'Pay', 'main']], changed: [], removed: [],
      }),
      ev(3, 'ops_applied', { round: 0, ok: 4, rejected: 0, auto_fixed: 0 }),
      ev(4, 'checked', { round: 0, errors: 0, warnings: 0 }),
      ev(5, 'tested', {
        round: 0, passed: 1, failed: 1, errored: 0,
        results: [
          { test_id: 'a', name: 'Small refund', status: 'passed', path: ['Receive', 'Pay'], failed_nodes: [] },
          { test_id: 'b', name: 'Large refund', status: 'failed', path: ['Receive', 'Pay'], failed_nodes: ['Pay'] },
        ],
      }),
    ]);
    expect(log).toHaveLength(1);
    expect(log[0].rows.map((r) => [r.kind, r.text, r.node])).toEqual([
      ['add', 'Added Receive', 'Receive'],
      ['add', 'Added Pay', 'Pay'],
      ['link', 'Linked 1 pair of steps', undefined],
      ['check', 'Checked for mistakes', undefined],
      ['test', 'Small refund', 'Pay'],
      ['test', 'Large refund', 'Pay'],
    ]);
    expect(log[0].rows[5]).toMatchObject({ tone: 'bad', detail: 'failed at Pay' });
    expect(log[0].rows[1].detail).toBe('Http request');
  });

  it('describes round 1 as a change against round 0', () => {
    const log = buildLog([
      ev(1, 'round_started', { round: 0 }),
      ev(2, 'round_shape', { round: 0, nodes: [node('A', 'X', 0)], connections: [], changed: [], removed: [] }),
      ev(3, 'round_started', { round: 1 }),
      ev(4, 'round_shape', { round: 1, nodes: [node('A', 'X', 0), node('B', 'Y', 280)], connections: [], changed: [{ name: 'A', fields: ['conditions'] }], removed: [] }),
    ]);
    expect(log.map((r) => r.rows.map((x) => x.text))).toEqual([['Added A'], ['Added B', 'Changed A']]);
    expect(log[1].rows[1].detail).toBe('conditions');
  });

  it('measures a modify against the base graph, so existing steps are not "added"', () => {
    const base = { nodes: [{ name: 'A', type: 'X', position: { x: 0, y: 0 } }], connections: [] };
    const log = buildLog([
      ev(1, 'round_started', { round: 0 }),
      ev(2, 'round_shape', { round: 0, nodes: [node('A', 'X', 0), node('B', 'Y', 280)], connections: [], changed: [], removed: [] }),
    ], base);
    expect(log[0].rows.map((r) => r.text)).toEqual(['Added B']);
  });

  it('falls back to a summary row when the engine sent no per-test results', () => {
    const log = buildLog([ev(1, 'round_started', { round: 0 }), ev(2, 'tested', { round: 0, passed: 2, failed: 1, errored: 0 })]);
    expect(log[0].rows).toEqual([expect.objectContaining({ kind: 'test', text: 'Ran the tests', detail: '2 passing, 1 failing', tone: 'bad' })]);
  });

  it('humanises node type names', () => {
    expect(humanType('HttpRequest')).toBe('Http request');
    expect(humanType('send_email')).toBe('Send email');
  });
});
