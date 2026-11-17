import type { ReactNode } from 'react'
import type { Dataset } from './datasets'

export type Recipe = { id: 'ovr' | 'xgb' | 'svm'; name: string; pipeline: (d: Dataset) => string; stages: (d: Dataset) => string[]; eq: ReactNode; where: string; f1: number; secs: number; viz: 'strings' | 'boost'; best: 'text' | 'tabular'; chips: [string, string][] }

export const RECIPES: Recipe[] = [
  {
    id: 'ovr', name: 'TF-IDF → LogReg (OvR)', viz: 'strings', best: 'text', f1: 0.914, secs: 102, chips: [['C', '1.0'], ['ngram', '(1,2)']],
    pipeline: () => 'TF-IDF → LogReg (OvR)', stages: () => ['Load', 'Split', 'TF-IDF', 'LogReg × K', 'Evaluate'],
    eq: <><i>ŷ</i> = argmax<sub><i>k</i></sub> σ(<i>w</i><sub><i>k</i></sub><sup>⊤</sup><i>x</i> + <i>b</i><sub><i>k</i></sub>)</>, where: 'One linear classifier per class; each one separates its class from the rest.',
  },
  {
    id: 'xgb', name: 'XGBoost', viz: 'boost', best: 'tabular', f1: 0.902, secs: 845, chips: [['M', '600'], ['η', '0.05']],
    pipeline: (d) => (d.kind === 'text' ? 'TF-IDF → SVD → XGBoost' : 'Encode → XGBoost'), stages: (d) => ['Load', 'Split', d.kind === 'text' ? 'TF-IDF → SVD' : 'Encode', 'Boost × M', 'Evaluate'],
    eq: <><i>F</i><sub><i>M</i></sub>(<i>x</i>) = Σ<sub><i>m</i>=1</sub><sup><i>M</i></sup> <i>η</i> <i>h</i><sub><i>m</i></sub>(<i>x</i>)</>, where: 'M small trees added one at a time; each one fits what the trees before it got wrong.',
  },
  {
    id: 'svm', name: 'TF-IDF → LinearSVC', viz: 'strings', best: 'text', f1: 0.907, secs: 138, chips: [['C', '0.5'], ['ngram', '(1,3)']],
    pipeline: () => 'TF-IDF → LinearSVC', stages: () => ['Load', 'Split', 'TF-IDF', 'SVM × K', 'Evaluate'],
    eq: <>min ½‖<i>w</i>‖² + <i>C</i> Σ max(0, 1 − <i>y</i><i>w</i><sup>⊤</sup><i>x</i>)</>, where: 'One max-margin classifier per class. Fast, strong on sparse text.',
  },
]

/* Phase boundaries on the run's progress t ∈ [0, 1]: load, split, vectorize, fit (5 folds), evaluate. */

export const PH = [0, 0.06, 0.12, 0.28, 0.9, 1]

export const FOLDS = 5

export const phaseOf = (t: number) => (t >= 1 ? 5 : PH.findIndex((_, i) => t < PH[i + 1]))

export const fitFrac = (t: number) => Math.min(1, Math.max(0, (t - PH[3]) / (PH[4] - PH[3])))

export const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}m ${String(Math.round(s % 60)).padStart(2, '0')}s`
