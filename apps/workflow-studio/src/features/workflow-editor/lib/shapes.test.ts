import { describe, it, expect } from 'vitest';
import { lookupShape, shapeFields, UNKNOWN, type Shape } from './shapes';

const obj = (properties: Record<string, Shape>): Shape => ({ type: 'object', properties });
const str: Shape = { type: 'string' };

describe('lookupShape', () => {
  const shape = obj({ a: obj({ b: str }), list: { type: 'array', items: obj({ id: { type: 'integer' } }) }, free: { type: 'object' } });
  it('empty path returns the shape', () => expect(lookupShape(shape, [])).toBe(shape));
  it('finds nested', () => expect(lookupShape(shape, ['a', 'b'])).toEqual(str));
  it('null when a known object lacks the property', () => {
    expect(lookupShape(shape, ['nope'])).toBeNull();
    expect(lookupShape(shape, ['a', 'zzz'])).toBeNull();
  });
  it('unknown when it cannot tell', () => {
    expect(lookupShape(UNKNOWN, ['x', 'y'])).toEqual(UNKNOWN);
    expect(lookupShape(shape, ['free', 'x'])).toEqual(UNKNOWN);
    expect(lookupShape(shape, ['a', 'b', 'lower'])).toEqual(UNKNOWN);
    expect(lookupShape(undefined, ['x'])).toEqual(UNKNOWN);
  });
  it('arrays by index', () => {
    expect(lookupShape(shape, ['list', 0, 'id'])).toEqual({ type: 'integer' });
    expect(lookupShape(shape, ['list', 0, 'missing'])).toBeNull();
    expect(lookupShape(shape, ['list', 'length'])).toEqual(UNKNOWN);
  });
});

describe('shapeFields', () => {
  it('lists nested paths with types', () => {
    const s = obj({ a: obj({ b: str }), n: { type: 'number' }, list: { type: 'array', items: obj({ id: str }) } });
    expect(shapeFields(s)).toEqual([
      { path: 'a', type: 'object' },
      { path: 'a.b', type: 'string' },
      { path: 'n', type: 'number' },
      { path: 'list', type: 'array' },
      { path: 'list[0].id', type: 'string' },
    ]);
  });
  it('limits depth', () => {
    const s = obj({ a: obj({ b: obj({ c: str }) }) });
    expect(shapeFields(s, 2).map((f) => f.path)).toEqual(['a', 'a.b']);
  });
  it('empty for unknown / undefined', () => {
    expect(shapeFields(UNKNOWN)).toEqual([]);
    expect(shapeFields(null)).toEqual([]);
  });
});
