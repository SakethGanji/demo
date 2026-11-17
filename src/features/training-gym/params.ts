import { sup } from '@/shared/lib/format'

export type Params = { C: number; maxIter: number; tol: number; ngram: number; minDf: number; classWeight: 'None' | 'balanced' }

export const DEFAULTS: Params = { C: 1.0, maxIter: 1000, tol: 1e-4, ngram: 2, minDf: 3, classWeight: 'None' }

export const STEPS = ['Data Source', 'Schema', 'Split', 'Preprocessing', 'Features', 'Model', 'Hyperparameters', 'Validation', 'Review', 'Train']

export const HYPER = 6

// Illustrative response surface for the live preview: validation F1 peaks near C = 2, train keeps rising.
export const logC = (i: number) => -3 + i / 10

export const trainF1 = (x: number) => 0.80 + 0.195 / (1 + Math.exp(-1.5 * (x + 0.9)))

export const valF1 = (x: number, p: Params) => 0.916 - 0.017 * (x - 0.3) ** 2 + (p.ngram === 1 ? -0.012 : p.ngram === 3 ? -0.002 : 0) + (p.classWeight === 'balanced' ? 0.004 : 0) - 0.0006 * Math.abs(p.minDf - 3)

export const fmtSci = (v: number) => {
  if (v >= 0.01) { const r = Number(v.toPrecision(2)); return Number.isInteger(r) && r < 100 ? r.toFixed(1) : String(r) }
  const e = Math.floor(Math.log10(v)); const m = v / 10 ** e
  return `${Number(m.toPrecision(2))}e${e}`
}

export const pow10 = (v: number) => `10${sup(Math.round(Math.log10(v)))}`
