import { describe, it, expect } from 'vitest';
import { contractSuggestions, isObservedType, type ShapesByNode } from './contractSuggestions';
import type { Shape } from './shapes';

const obj = (props: Record<string, Shape>): Shape => ({ type: 'object', properties: props });
const shapes: ShapesByNode = {
  Fetch: { inputs: { main: obj({}) }, outputs: { main: obj({ id: { type: 'integer' }, user: obj({ name: { type: 'string' } }) }) } },
  Set: { inputs: { main: obj({ id: { type: 'integer' }, user: obj({ name: { type: 'string' } }) }) }, outputs: { main: obj({ ok: { type: 'boolean' } }) } },
};

describe('contractSuggestions', () => {
  it('is empty when there are no shapes', () => {
    expect(contractSuggestions({ currentNode: 'Set', shapesByNode: {} })).toEqual([]);
  });

  it('falls back to shapes when there is no run data', () => {
    const labels = contractSuggestions({ currentNode: 'Set', shapesByNode: shapes, sampleData: [], allNodeData: {} }).map((s) => s.label);
    expect(labels).toEqual(['$json.id', '$json.user', '$json.user.name', '$node["Fetch"].json.id', '$node["Fetch"].json.user', '$node["Fetch"].json.user.name']);
    expect(contractSuggestions({ currentNode: 'Set', shapesByNode: shapes }).every((s) => s.source === 'contract')).toBe(true);
  });

  it('run data wins: no contract suggestion for nodes that have run data', () => {
    const labels = contractSuggestions({
      currentNode: 'Set',
      shapesByNode: shapes,
      sampleData: [{ id: 1 }],
      allNodeData: { Fetch: [{ id: 1 }] },
    }).map((s) => s.label);
    expect(labels).toEqual([]);
  });

  it('mixes: run data for $json, shapes for a node without run data', () => {
    const labels = contractSuggestions({ currentNode: 'Set', shapesByNode: shapes, sampleData: [{ id: 1 }], allNodeData: {} }).map((s) => s.label);
    expect(labels.every((l) => l.startsWith('$node["Fetch"]'))).toBe(true);
  });
});

describe('isObservedType', () => {
  it('knows the learned node types', () => {
    expect(isObservedType('HttpRequest')).toBe(true);
    expect(isObservedType('Set')).toBe(false);
  });
});
