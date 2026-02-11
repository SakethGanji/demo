/**
 * Reusable equality functions for Zustand selectors.
 *
 * Pass as the second argument to useStore(selector, equalityFn)
 * to prevent re-renders when the derived value hasn't meaningfully changed.
 */

/** Deep-compare small objects via JSON serialization. */
export function jsonEqual<T>(a: T, b: T): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Compare two Sets by size and membership. */
export function setEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) {
    if (!b.has(v)) return false;
  }
  return true;
}

/** Shallow-compare an object's keys and value references (===). */
export function shallowObjectEqual<T extends Record<string, unknown>>(a: T, b: T): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((k) => a[k] === b[k]);
}
