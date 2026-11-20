// Metrics recomputed in the browser from the holdout predictions, and seeded bootstrap intervals for them.
// The point estimate must equal what the API reports from AutoGluon's evaluate(); the e2e tests check that.

import { rng } from '@/shared/lib/random'
import type { HoldoutPredictions } from '@/shared/api/types'

type Data = { yTrue: (string | number)[]; yPred: (string | number)[]; probs: number[][] | null; classes: string[] | null; positive: string | null }

const mean = (a: number[]) => a.reduce((s, v) => s + v, 0) / (a.length || 1)

function auc(y: number[], p: number[]) {
  // rank-based (Mann–Whitney U), ties averaged
  const idx = p.map((v, i) => [v, i] as const).sort((a, b) => a[0] - b[0])
  const ranks = new Array(p.length)
  for (let i = 0; i < idx.length;) {
    let j = i
    while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++
    const r = (i + j) / 2 + 1
    for (let k = i; k <= j; k++) ranks[idx[k][1]] = r
    i = j + 1
  }
  const nPos = y.reduce((s, v) => s + v, 0), nNeg = y.length - nPos
  if (!nPos || !nNeg) return NaN
  const sumPos = y.reduce((s, v, i) => s + (v ? ranks[i] : 0), 0)
  return (sumPos - (nPos * (nPos + 1)) / 2) / (nPos * nNeg)
}

/** One metric on rows `ix` of the data, in natural units; null if it can't be computed here. */
export function metricOn(key: string, d: Data, ix: number[]): number | null {
  const yt = ix.map((i) => d.yTrue[i]), yp = ix.map((i) => d.yPred[i])
  const n = ix.length
  if (!n) return null
  switch (key) {
    case 'accuracy': return yt.reduce<number>((s, v, i) => s + (String(v) === String(yp[i]) ? 1 : 0), 0) / n
    case 'balanced_accuracy': {
      const cls = [...new Set(yt.map(String))]
      return mean(cls.map((c) => { const rows = yt.map((v, i) => [String(v), String(yp[i])]).filter(([v]) => v === c); return rows.filter(([, p]) => p === c).length / rows.length }))
    }
    case 'f1': case 'precision': case 'recall': {
      if (!d.positive) return null
      const P = String(d.positive)
      let tp = 0, fp = 0, fn = 0
      yt.forEach((v, i) => { const t = String(v) === P, p = String(yp[i]) === P; if (t && p) tp++; else if (p) fp++; else if (t) fn++ })
      if (key === 'precision') return tp + fp ? tp / (tp + fp) : 0
      if (key === 'recall') return tp + fn ? tp / (tp + fn) : 0
      return 2 * tp + fp + fn ? (2 * tp) / (2 * tp + fp + fn) : 0
    }
    case 'f1_macro': {
      const cls = [...new Set([...yt, ...yp].map(String))]
      return mean(cls.map((c) => {
        let tp = 0, fp = 0, fn = 0
        yt.forEach((v, i) => { const t = String(v) === c, p = String(yp[i]) === c; if (t && p) tp++; else if (p) fp++; else if (t) fn++ })
        return 2 * tp + fp + fn ? (2 * tp) / (2 * tp + fp + fn) : 0
      }))
    }
    case 'roc_auc': {
      if (!d.probs || !d.classes || !d.positive) return null
      const k = d.classes.indexOf(String(d.positive))
      if (k < 0) return null
      return auc(yt.map((v) => (String(v) === String(d.positive) ? 1 : 0)), ix.map((i) => d.probs![i][k]))
    }
    case 'log_loss': {
      if (!d.probs || !d.classes) return null
      const eps = 1e-15
      return -mean(ix.map((i) => Math.log(Math.min(1 - eps, Math.max(eps, d.probs![i][d.classes!.indexOf(String(d.yTrue[i]))] ?? eps)))))
    }
    case 'root_mean_squared_error': return Math.sqrt(mean(yt.map((v, i) => (Number(v) - Number(yp[i])) ** 2)))
    case 'mean_squared_error': return mean(yt.map((v, i) => (Number(v) - Number(yp[i])) ** 2))
    case 'mean_absolute_error': return mean(yt.map((v, i) => Math.abs(Number(v) - Number(yp[i]))))
    case 'median_absolute_error': {
      const a = yt.map((v, i) => Math.abs(Number(v) - Number(yp[i]))).sort((x, y) => x - y)
      return a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2
    }
    case 'r2': {
      const ybar = mean(yt.map(Number))
      const ssr = yt.reduce<number>((s, v, i) => s + (Number(v) - Number(yp[i])) ** 2, 0)
      const sst = yt.reduce<number>((s, v) => s + (Number(v) - ybar) ** 2, 0)
      return sst ? 1 - ssr / sst : null
    }
    default: return null
  }
}

export function toData(h: HoldoutPredictions, positive: string | number | null | undefined): Data {
  return { yTrue: h.y_true, yPred: h.y_pred, probs: h.probabilities, classes: h.classes, positive: positive == null ? null : String(positive) }
}

/**
 * A1 #11: the metric on a resample given as row counts w (wᵢ = times row i was drawn), in O(n) with everything that
 * does not depend on w precomputed once. Same values as metricOn on the expanded index list, without building it:
 * 300 resamples of 40,000 rows took tens of seconds on the main thread; this takes well under one.
 */
function weighted(key: string, d: Data): ((w: Int32Array) => number | null) | null {
  const n = d.yTrue.length
  const yt = d.yTrue.map(String), yp = d.yPred.map(String)
  const codes = (a: string[], dict: Map<string, number>) => Int32Array.from(a, (v) => { if (!dict.has(v)) dict.set(v, dict.size); return dict.get(v)! })
  const wmean = (x: Float64Array) => (w: Int32Array) => { let s = 0, W = 0; for (let i = 0; i < n; i++) { s += w[i] * x[i]; W += w[i] } return W ? s / W : 0 }
  switch (key) {
    case 'accuracy': return wmean(Float64Array.from(yt, (v, i) => (v === yp[i] ? 1 : 0)))
    case 'balanced_accuracy': {
      const dict = new Map<string, number>(); const c = codes(yt, dict); const K = dict.size
      const eq = Uint8Array.from(yt, (v, i) => (v === yp[i] ? 1 : 0))
      return (w) => {
        const hit = new Float64Array(K), tot = new Float64Array(K)
        for (let i = 0; i < n; i++) { tot[c[i]] += w[i]; hit[c[i]] += w[i] * eq[i] }
        let s = 0, m = 0
        for (let k = 0; k < K; k++) if (tot[k] > 0) { s += hit[k] / tot[k]; m++ }
        return m ? s / m : 0
      }
    }
    case 'f1': case 'precision': case 'recall': {
      if (!d.positive) return null
      const P = String(d.positive)
      const t = Uint8Array.from(yt, (v) => (v === P ? 1 : 0)), p = Uint8Array.from(yp, (v) => (v === P ? 1 : 0))
      return (w) => {
        let tp = 0, fp = 0, fn = 0
        for (let i = 0; i < n; i++) { if (t[i] && p[i]) tp += w[i]; else if (p[i]) fp += w[i]; else if (t[i]) fn += w[i] }
        if (key === 'precision') return tp + fp ? tp / (tp + fp) : 0
        if (key === 'recall') return tp + fn ? tp / (tp + fn) : 0
        return 2 * tp + fp + fn ? (2 * tp) / (2 * tp + fp + fn) : 0
      }
    }
    case 'f1_macro': {
      const dict = new Map<string, number>(); const ct = codes(yt, dict), cp = codes(yp, dict); const K = dict.size
      return (w) => {
        const tp = new Float64Array(K), fp = new Float64Array(K), fn = new Float64Array(K), seen = new Uint8Array(K)
        for (let i = 0; i < n; i++) {
          if (!w[i]) continue
          seen[ct[i]] = 1; seen[cp[i]] = 1
          if (ct[i] === cp[i]) tp[ct[i]] += w[i]; else { fp[cp[i]] += w[i]; fn[ct[i]] += w[i] }
        }
        let s = 0, m = 0
        for (let k = 0; k < K; k++) if (seen[k]) { const den = 2 * tp[k] + fp[k] + fn[k]; s += den ? (2 * tp[k]) / den : 0; m++ }
        return m ? s / m : 0
      }
    }
    case 'roc_auc': {
      if (!d.probs || !d.classes || !d.positive) return null
      const k = d.classes.indexOf(String(d.positive))
      if (k < 0) return null
      const P = String(d.positive)
      const pos = Uint8Array.from(yt, (v) => (v === P ? 1 : 0))
      const p = Float64Array.from(d.probs, (r) => r[k])
      const order = Int32Array.from({ length: n }, (_, i) => i).sort((a, b) => p[a] - p[b])
      return (w) => {
        // Mann–Whitney with ties averaged: each tie group's positives beat the negatives below it, half of its own
        let below = 0, sum = 0, nP = 0, nN = 0
        for (let i = 0; i < n;) {
          let j = i, gp = 0, gn = 0
          do { const r = order[j]; if (pos[r]) gp += w[r]; else gn += w[r]; j++ } while (j < n && p[order[j]] === p[order[i]])
          sum += gp * (below + gn / 2); below += gn; nP += gp; nN += gn
          i = j
        }
        return nP && nN ? sum / (nP * nN) : NaN
      }
    }
    case 'log_loss': {
      if (!d.probs || !d.classes) return null
      const eps = 1e-15
      return wmean(Float64Array.from(d.yTrue, (v, i) => -Math.log(Math.min(1 - eps, Math.max(eps, d.probs![i][d.classes!.indexOf(String(v))] ?? eps)))))
    }
    case 'root_mean_squared_error': { const f = wmean(Float64Array.from(d.yTrue, (v, i) => (Number(v) - Number(d.yPred[i])) ** 2)); return (w) => Math.sqrt(f(w) ?? 0) }
    case 'mean_squared_error': return wmean(Float64Array.from(d.yTrue, (v, i) => (Number(v) - Number(d.yPred[i])) ** 2))
    case 'mean_absolute_error': return wmean(Float64Array.from(d.yTrue, (v, i) => Math.abs(Number(v) - Number(d.yPred[i]))))
    case 'median_absolute_error': {
      const a = Float64Array.from(d.yTrue, (v, i) => Math.abs(Number(v) - Number(d.yPred[i])))
      const order = Int32Array.from({ length: n }, (_, i) => i).sort((x, y) => a[x] - a[y])
      return (w) => {
        let W = 0
        for (let i = 0; i < n; i++) W += w[i]
        const at = (k: number) => { let c = 0; for (let j = 0; j < n; j++) { c += w[order[j]]; if (c > k) return a[order[j]] } return a[order[n - 1]] }
        return W % 2 ? at((W - 1) / 2) : (at(W / 2 - 1) + at(W / 2)) / 2
      }
    }
    case 'r2': {
      const y = Float64Array.from(d.yTrue, Number), e2 = Float64Array.from(d.yTrue, (v, i) => (Number(v) - Number(d.yPred[i])) ** 2)
      return (w) => {
        let W = 0, sy = 0, ssr = 0
        for (let i = 0; i < n; i++) { W += w[i]; sy += w[i] * y[i]; ssr += w[i] * e2[i] }
        const ybar = sy / W
        let sst = 0
        for (let i = 0; i < n; i++) sst += w[i] * (y[i] - ybar) ** 2
        return sst ? 1 - ssr / sst : null
      }
    }
    default: return null
  }
}

/** The metric on all holdout rows and a percentile bootstrap 95% interval (B resamples, fixed seed). */
export function withInterval(key: string, d: Data, B = 300, seed = 42): { value: number; lo: number; hi: number; B: number } | null {
  const n = d.yTrue.length
  const all = Array.from({ length: n }, (_, i) => i)
  const value = metricOn(key, d, all)
  if (value == null || !Number.isFinite(value)) return null
  const r = rng(seed)
  const stats: number[] = []
  const f = weighted(key, d)
  const w = new Int32Array(n)
  for (let b = 0; b < B; b++) {
    // the same draws as before (n per resample, same seed), kept as counts
    let v: number | null
    if (f) {
      w.fill(0)
      for (let j = 0; j < n; j++) w[Math.floor(r() * n)]++
      v = f(w)
    } else {
      v = metricOn(key, d, Array.from({ length: n }, () => Math.floor(r() * n)))
    }
    if (v != null && Number.isFinite(v)) stats.push(v)
  }
  stats.sort((a, b) => a - b)
  const q = (p: number) => stats[Math.min(stats.length - 1, Math.max(0, Math.floor(p * (stats.length - 1))))]
  return { value, lo: q(0.025), hi: q(0.975), B: stats.length }
}

/** Residuals rᵢ = yᵢ − ŷᵢ and their summary. */
export function residuals(h: HoldoutPredictions) {
  const y = h.y_true.map(Number), p = h.y_pred.map(Number)
  const r = y.map((v, i) => v - p[i])
  const sorted = [...r].sort((a, b) => a - b)
  const q = (t: number) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(t * (sorted.length - 1))))]
  const m = mean(r)
  const sd = Math.sqrt(r.reduce((s, v) => s + (v - m) ** 2, 0) / Math.max(1, r.length - 1))
  const worst = r.map((v, i) => ({ i, row: h.rows[i], y: y[i], yhat: p[i], r: v })).sort((a, b) => Math.abs(b.r) - Math.abs(a.r)).slice(0, 5)
  return { y, p, r, mean: m, sd, q05: q(0.05), q25: q(0.25), q50: q(0.5), q75: q(0.75), q95: q(0.95), maxAbs: Math.max(...r.map(Math.abs)), worst }
}
