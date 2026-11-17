// Deterministic pseudo-random helpers for the mock data.

// Deterministic sample data for the demo workspace.
export function rng(seed: number) {
  let s = seed >>> 0
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32)
}

export function curve(n: number, start: number, end: number, k: number, noise: number, seed: number) {
  const r = rng(seed)
  return Array.from({ length: n }, (_, i) => end + (start - end) * Math.exp(-k * i) + (r() - 0.5) * noise)
}
