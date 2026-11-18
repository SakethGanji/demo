// Mock runs and metrics shared by more than one page.

export const CLASSES = ['Billing', 'Technical', 'Account', 'Security', 'Shipping', 'Other']

export const CLASS_COUNTS = [21840, 26810, 17320, 8960, 12570, 37000]

/** Share of the data held out and never trained on. */
export const HOLDOUT = 0.15

// The error pattern of run_894a, per actual class. Scaled below so each row is that class's share of the holdout.
const CONFUSION_SHAPE = [
  [3812, 61, 44, 6, 21, 28],
  [52, 4410, 37, 118, 9, 40],
  [39, 33, 2964, 41, 8, 19],
  [4, 196, 31, 1271, 2, 9],
  [18, 7, 6, 1, 2104, 22],
  [41, 52, 23, 6, 29, 1688],
]

export const CONFUSION = (() => {
  const want = CLASS_COUNTS.map((n) => Math.round(n * HOLDOUT))
  const total = Math.round(CLASS_COUNTS.reduce((a, b) => a + b, 0) * HOLDOUT)
  want[want.indexOf(Math.max(...want))] += total - want.reduce((a, b) => a + b, 0) // absorb rounding in the largest class
  return CONFUSION_SHAPE.map((row, i) => {
    const sum = row.reduce((a, b) => a + b, 0)
    const out = row.map((v, j) => (j === i ? 0 : Math.round((v / sum) * want[i])))
    out[i] = want[i] - out.reduce((a, b) => a + b, 0)
    return out
  })
})()

/** Recall per class and its mean, from the matrix above. */
export const RECALL = CONFUSION.map((row, i) => row[i] / row.reduce((a, b) => a + b, 0))
export const MEAN_RECALL = RECALL.reduce((a, b) => a + b, 0) / RECALL.length


export type Run = {
  id: string
  model: string
  pipeline: string
  status: 'Training' | 'Completed' | 'Queued' | 'Failed'
  f1: number | null
  spread: number | null
  auc: number | null
  duration: string
  owner: string
  params: string
}

export const RUNS: Run[] = [
  { id: 'run_8a10', model: 'Logistic Regression', pipeline: 'TF-IDF → LogReg (OvR)', status: 'Training', f1: null, spread: null, auc: null, duration: '06m 12s', owner: 'Maya Lin', params: 'C=2.0  ngram=(1,2)' },
  { id: 'run_894a', model: 'Logistic Regression', pipeline: 'TF-IDF → LogReg (OvR)', status: 'Completed', f1: 0.914, spread: 0.006, auc: 0.941, duration: '01m 42s', owner: 'Maya Lin', params: 'C=1.0  ngram=(1,2)' },
  { id: 'run_8931', model: 'Gradient Boosting', pipeline: 'TF-IDF → SVD → GBM', status: 'Completed', f1: 0.902, spread: 0.009, auc: 0.936, duration: '14m 05s', owner: 'Arun Patel', params: 'M=600  η=0.05' },
  { id: 'run_8912', model: 'Linear SVM', pipeline: 'TF-IDF → LinearSVC', status: 'Completed', f1: 0.907, spread: 0.005, auc: 0.928, duration: '02m 18s', owner: 'Maya Lin', params: 'C=0.5  ngram=(1,3)' },
  { id: 'run_88f7', model: 'DistilBERT', pipeline: 'Tokenize → DistilBERT', status: 'Failed', f1: null, spread: null, auc: null, duration: '22m 40s', owner: 'Jo Kim', params: 'η=3e-5  epochs=4' },
  { id: 'run_8b02', model: 'Naive Bayes', pipeline: 'Counts → MultinomialNB', status: 'Queued', f1: null, spread: null, auc: null, duration: '—', owner: 'Arun Patel', params: 'α=0.1' },
  { id: 'run_8855', model: 'Linear SVM', pipeline: 'TF-IDF → LinearSVC', status: 'Completed', f1: 0.899, spread: 0.006, auc: 0.921, duration: '01m 58s', owner: 'Jo Kim', params: 'C=0.1  ngram=(1,2)' },
  { id: 'run_8841', model: 'Naive Bayes', pipeline: 'Counts → MultinomialNB', status: 'Completed', f1: 0.871, spread: 0.008, auc: 0.905, duration: '00m 24s', owner: 'Arun Patel', params: 'α=1.0' },
  { id: 'run_8870', model: 'Logistic Regression', pipeline: 'TF-IDF → LogReg (OvR)', status: 'Completed', f1: 0.893, spread: 0.007, auc: 0.925, duration: '01m 31s', owner: 'Maya Lin', params: 'C=0.1  ngram=(1,1)' },
]

export const LOG_LINES: [string, string][] = [
  ['lt', '[INFO] loading zendesk_tickets_2026_q3.parquet · 124,500 rows'],
  ['lt', '[INFO] split stratified k=5 seed=42'],
  ['li', '[FIT ] fold=1 tfidf vocab=150,420 sparse ℝⁿˣ¹⁵⁰⁴²⁰'],
  ['lm', '[METRIC] fold=1 macro_f1=0.911 loss=0.284'],
  ['li', '[FIT ] fold=2 lbfgs iter=212 converged'],
  ['lm', '[METRIC] fold=2 macro_f1=0.919 loss=0.271'],
  ['lw', '[WARN] fold=3 class=Security recall=0.83 below 0.90'],
  ['lm', '[METRIC] fold=3 macro_f1=0.908 loss=0.290'],
  ['li', '[FIT ] fold=4 lbfgs iter=198 converged'],
  ['lm', '[METRIC] fold=4 macro_f1=0.914 loss=0.279'],
  ['li', '[FIT ] fold=5 lbfgs iter=205 converged'],
  ['lm', '[METRIC] fold=5 macro_f1=0.917 loss=0.276'],
  ['lt', '[INFO] writing artifacts s3://ensemble/runs/run_8a10/'],
]

// d features (thousands), fit seconds, AUC, Security recall, macro F1
export const PC: Record<string, { d: number; t: number; auc: number; rec: number; f1: number }> = {
  run_894a: { d: 150, t: 102, auc: 0.941, rec: 0.84, f1: 0.914 },
  run_8931: { d: 0.3, t: 845, auc: 0.936, rec: 0.87, f1: 0.902 },
  run_8912: { d: 412, t: 138, auc: 0.928, rec: 0.82, f1: 0.907 },
  run_8855: { d: 150, t: 118, auc: 0.921, rec: 0.79, f1: 0.899 },
  run_8841: { d: 61, t: 24, auc: 0.905, rec: 0.71, f1: 0.871 },
  run_8870: { d: 38, t: 91, auc: 0.925, rec: 0.80, f1: 0.893 },
}
