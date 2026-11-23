/**
 * Line-level diff (LCS) used for the AI-diff tint and the diff stat (+N −M).
 * Returns 1-based line numbers of `next` that are new or changed, and the removed count.
 */
export function lineDiff(prev: string, next: string): { added: number[]; addedCount: number; removedCount: number } {
  const a = prev.split('\n')
  const b = next.split('\n')
  // trim common prefix/suffix to keep the table small
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length - 1
  let endB = b.length - 1
  while (endA >= start && endB >= start && a[endA] === b[endB]) {
    endA--
    endB--
  }
  const A = a.slice(start, endA + 1)
  const B = b.slice(start, endB + 1)
  if (A.length * B.length > 4_000_000) {
    // too big: treat the whole middle as replaced
    return { added: B.map((_, i) => start + i + 1), addedCount: B.length, removedCount: A.length }
  }
  const n = A.length
  const m = B.length
  const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const added: number[] = []
  let removed = 0
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      removed++
      i++
    } else {
      added.push(start + j + 1)
      j++
    }
  }
  removed += n - i
  while (j < m) added.push(start + 1 + j++)
  return { added, addedCount: added.length, removedCount: removed }
}
