import { describe, it, expect } from 'vitest';
import { detectTrigger, filterByToken, applyTrigger } from './expressionTrigger';

describe('detectTrigger', () => {
  it('opens after {{ with an empty token', () => {
    expect(detectTrigger('a {{ ', 5)).toEqual({ start: 5, token: '', insideBraces: true });
    expect(detectTrigger('{{', 2)?.token).toBe('');
  });
  it('opens on $json. and filters by what follows', () => {
    expect(detectTrigger('{{ $json.', 9)).toEqual({ start: 3, token: '$json.', insideBraces: true });
    expect(detectTrigger('{{ $json.al', 11)?.token).toBe('$json.al');
  });
  it('handles $node["X"].json.', () => {
    const t = detectTrigger('{{ $node["My Node"].json.na', 27);
    expect(t?.token).toBe('$node["My Node"].json.na');
  });
  it('stays closed for plain text, bare $json, or closed braces', () => {
    expect(detectTrigger('hello', 5)).toBeNull();
    expect(detectTrigger('{{ $json', 8)).toBeNull();
    expect(detectTrigger('$json.a', 7)?.insideBraces).toBe(false);
    expect(detectTrigger('{{ $json.a }} $json.b', 21)?.insideBraces).toBe(false);
  });
});

describe('filterByToken', () => {
  const items = [{ label: '$json.alpha' }, { label: '$json.beta' }, { label: '$node["A"].json.alpha' }];
  it('prefix-matches case-insensitively', () => {
    expect(filterByToken(items, '$JSON.al')).toEqual([{ label: '$json.alpha' }]);
    expect(filterByToken(items, '$json.')).toHaveLength(2);
  });
  it('keeps all for empty token, drops exact match', () => {
    expect(filterByToken(items, '')).toHaveLength(3);
    expect(filterByToken(items, '$json.beta')).toHaveLength(0);
  });
});

describe('applyTrigger', () => {
  it('replaces the token inside braces', () => {
    const text = '{{ $json.al }}';
    const t = detectTrigger(text, 11)!;
    expect(applyTrigger(text, 11, t, '$json.alpha')).toEqual({ value: '{{ $json.alpha }}', cursor: 14 });
  });
  it('wraps outside braces', () => {
    const t = detectTrigger('x $json.a', 9)!;
    expect(applyTrigger('x $json.a', 9, t, '$json.alpha').value).toBe('x {{ $json.alpha }}');
  });
});
