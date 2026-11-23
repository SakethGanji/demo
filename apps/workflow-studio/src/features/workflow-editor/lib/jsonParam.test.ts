import { describe, expect, it } from 'vitest';
import { isExtensionOwnedParam, jsonFieldText } from './jsonParam';

describe('jsonFieldText', () => {
  it('passes strings through', () => expect(jsonFieldText('{"a":1}')).toBe('{"a":1}'));
  it('pretty-prints objects and arrays', () => {
    expect(jsonFieldText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(jsonFieldText([1, 2])).toBe('[\n  1,\n  2\n]');
  });
  it('never returns a non-string for null, numbers, booleans or object defaults', () => {
    expect(jsonFieldText(null)).toBe('');
    expect(jsonFieldText(42)).toBe('42');
    expect(jsonFieldText(false)).toBe('false');
    expect(jsonFieldText(undefined, {})).toBe('{}');
    expect(jsonFieldText(undefined, null)).toBe('');
    expect(jsonFieldText(undefined)).toBe('');
  });
  it('survives circular values', () => {
    const o: Record<string, unknown> = {};
    o.self = o;
    expect(typeof jsonFieldText(o)).toBe('string');
  });
});

describe('isExtensionOwnedParam', () => {
  it('hides the catalog params of HttpRequest only', () => {
    expect(isExtensionOwnedParam('HttpRequest', 'api_ref')).toBe(true);
    expect(isExtensionOwnedParam('HttpRequest', 'path_params')).toBe(true);
    expect(isExtensionOwnedParam('HttpRequest', 'url')).toBe(false);
    expect(isExtensionOwnedParam('Set', 'api_ref')).toBe(false);
  });
  it('hides method and url only while api_ref is set', () => {
    const ref = { api_ref: { spec_id: 's', operation_id: 'o' } };
    expect(isExtensionOwnedParam('HttpRequest', 'url', ref)).toBe(true);
    expect(isExtensionOwnedParam('HttpRequest', 'method', ref)).toBe(true);
    expect(isExtensionOwnedParam('HttpRequest', 'url', { api_ref: null })).toBe(false);
    expect(isExtensionOwnedParam('Set', 'url', ref)).toBe(false);
  });
});
