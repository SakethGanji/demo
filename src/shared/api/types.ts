// Mirrors backend/app/schemas.py and the profile built in backend/app/profiling.py. Keep them in step.

export type EngineInfo = { name: string; version: string | null; timeseries: string | null }

export type Health = { status: 'ok'; version: string; datasets: number; data_dir: string; runs: number; engine: EngineInfo }

export type ColumnType = 'text' | 'category' | 'number' | 'id'

export type ColumnProfile = {
  name: string
  type: ColumnType
  dtype: string
  role: 'target' | 'feature' | 'ignored'
  missing: number
  missing_share: number
  unique: number
  example: string
  stats?: { min: number; max: number; mean: number; std: number; median: number } | { mean_chars: number; max_chars: number } | null
  top?: { value: string; count: number }[]
}

export type ClassShare = { name: string; count: number; share: number }

export type Splits = {
  seed: number
  target: string | null
  stratified: boolean
  holdout_share: number
  holdout_rows: number
  n_folds: number
  fold_rows: number[]
  holdout_classes?: Record<string, number>
}

export type Profile = {
  rows: number
  n_cols: number
  kind: 'text' | 'tabular'
  target: string | null
  suggested_target: string | null
  label_candidates: string[]
  columns: ColumnProfile[]
  classes: ClassShare[]
  n_classes: number
  imbalance_ratio: number | null
  /** What the target asks for: classification with 2 or more classes, or regression (numeric, many values). */
  task: 'binary' | 'multiclass' | 'regression' | null
  /** Regression targets only: min/max/mean/std/median of the label. */
  target_stats: { min: number; max: number; mean: number; std: number; median: number } | null
  missing: { cells: number; share: number; rows_with_missing: number; columns_with_missing: string[] }
  duplicates: { rows: number; share: number; text: Record<string, number>; conflicting_labels: number; ignoring: string[] }
  warnings: string[]
  splits: Splits
}

export type DatasetSummary = {
  id: string
  name: string
  filename: string
  source: 'sample' | 'upload'
  sample: boolean
  note: string
  created_at: string
  size_bytes: number
  rows: number
  n_cols: number
  kind: 'text' | 'tabular'
  target: string | null
  n_classes: number
}

export type DatasetDetail = DatasetSummary & { profile: Profile }

// ---------------------------------------------------------------------------
// Engine (S2): model catalogue, runs, events, predictions. Mirrors backend/app/engine/*.
// Scores: `score_*` are AutoGluon's higher-is-better values (errors are negated); `*_natural` are in the
// metric's own units, read with `eval_metric.greater_is_better`.
// ---------------------------------------------------------------------------

export type ProblemType = 'binary' | 'multiclass' | 'regression' | 'quantile'

export type ModelFamily = {
  key: string // AutoGluon hyperparameters key, e.g. GBM, TFIDF_LR
  name: string
  kind: string
  description: string
  /** Unicode maths, e.g. "min_w Σᵢ log(1 + exp(−ỹᵢ·wᵀxᵢ)) + (1/2C)‖w‖²" */
  objective: string
  objective_tex: string
  /** Every symbol used in the objective, defined. */
  symbols: Record<string, string>
  problem_types: ProblemType[]
  custom: boolean
  explains?: string
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type RunCreate = {
  dataset_id: string
  label?: string | null
  name?: string | null
  problem_type?: 'auto' | ProblemType
  eval_metric?: string | null
  presets?: string | string[] | null
  time_limit?: number | null
  /** Passed to TabularPredictor.fit unchanged. */
  fit_kwargs?: Record<string, unknown>
  /** Passed to TabularPredictor(...) (not label or path). */
  predictor_kwargs?: Record<string, unknown>
  custom_text_models?: boolean
  drop_columns?: string[] | null
  feature_importance?: boolean
  /** Override inferred column types; the engine passes them to AutoGluon as feature_metadata. */
  column_types?: Record<string, 'number' | 'category' | 'text'> | null
}

export type EvalMetric = { name: string; greater_is_better: boolean; optimum: number | null; scores_note: string }

export type LeaderboardRow = {
  model: string
  family: string | null
  rank: number
  score_val: number | null
  score_test: number | null
  score_val_natural: number | null
  score_test_natural: number | null
  fit_time: number | null
  pred_time_val: number | null
  pred_time_test: number | null
  stack_level: number
  can_infer: boolean
  /** Bagged models only: each fold child's validation score, and their sample std. */
  fold_scores: number[] | null
  fold_scores_natural?: number[] | null
  val_score_std: number | null
  num_models?: number
  /** Weighted ensembles only: base model → weight (sums to 1). */
  weights: Record<string, number> | null
  // GET /api/runs/{id} also returns AutoGluon's extra info: hyperparameters, features, model_type, ancestors, …
  [extra: string]: unknown
}

export type Ensemble = { model: string; stack_level: number; weights: Record<string, number>; best: boolean }

export type FeatureImportance = {
  feature: string; importance: number; stddev: number; p_value: number; n: number; p99_high: number; p99_low: number
}

export type RunData = {
  rows: number; train_rows: number; holdout_rows: number
  dropped_missing_label: number; dropped_missing_label_train: number; dropped_missing_label_holdout: number
  dropped_columns: string[]; features: string[]; label: string
}

export type Holdout = {
  n_rows: number
  model: string
  eval_metric: string
  /** AutoGluon's evaluate(): higher is better. */
  scores: Record<string, number>
  /** The same in natural units (RMSE positive). */
  metrics: Record<string, number>
  classes?: (string | number)[]
  confusion_matrix?: number[][]
}

export type Run = {
  id: string
  name: string
  dataset_id: string
  label: string
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  config: Record<string, unknown>
  predictor_path: string
  n_events: number
  problem_type: ProblemType | null
  eval_metric: EvalMetric | null
  classes: (string | number)[] | null
  positive_class: string | number | null
  best_model: string | null
  fit_seconds: number | null
  elapsed_seconds: number | null
  data: RunData | null
  features: string[] | null
  ensemble: Ensemble | null
  ensembles: Ensemble[] | null
  holdout: Holdout | null
  oof: { available: boolean; models: string[]; note: string | null } | null
  /** model → class → [word, coefficient][] (TF-IDF models). Null in the list. */
  text_models: Record<string, Record<string, [string, number][]>> | null
  feature_importance: FeatureImportance[] | null
  leaderboard: LeaderboardRow[] | null
  /** The exact TabularPredictor(...) and .fit(...) arguments. Null in the list. */
  resolved: { predictor_kwargs: Record<string, unknown>; fit_kwargs: Record<string, unknown>; notes: string[]; path: string } | null
  /** Weighted ensembles fitted later on chosen models (POST /runs/{id}/ensemble). */
  custom_ensembles?: CustomEnsemble[] | null
}

/** One model's scores in a custom-ensemble result. score_* higher is better; *_natural in the metric's units. */
export type EnsembleScores = {
  /** predict_model is the copy that predicts: the model itself, or its refit twin (…_FULL) in refit presets. */
  model: string; predict_model: string; stack_level: number
  score_val: number | null; score_test: number | null; score_val_natural: number | null; score_test_natural: number | null
  fit_time: number | null; pred_time_test: number | null; pred_time_test_per_row: number | null
}

export type Comparison = { beats: boolean; delta: number; delta_natural: number | null } | null

export type CustomEnsemble = {
  model: string; predict_model: string; refit: boolean; base_models: string[]; weights: Record<string, number>; n_selected: number; fit_seconds: number
  created_at: string; eval_metric: string; greater_is_better: boolean; n_holdout: number; validation: string
  scores: EnsembleScores
  best_chosen_single: EnsembleScores | null; best_single: EnsembleScores | null; run_best: EnsembleScores
  vs_best_single: { val: Comparison; holdout: Comparison }
  vs_run_best: { val: Comparison; holdout: Comparison }
  model_best_unchanged: string
}

export type HoldoutPredictions = {
  run_id: string; model: string | null; n_rows: number; rows: number[]
  y_true: (string | number)[]; y_pred: (string | number)[]
  classes: string[] | null; probabilities: number[][] | null

export type RunEventType =
  | 'run_queued' | 'run_started' | 'data_ready' | 'config_resolved' | 'fit_started'
  | 'model_started' | 'model_finished' | 'model_failed' | 'ensemble' | 'fit_finished'
  | 'leaderboard' | 'evaluation' | 'text_models' | 'explaining' | 'feature_importance' | 'log'
  | 'run_succeeded' | 'run_failed' | 'run_cancelled'

/** One event. `phase` is "main" or "dystack" (AutoGluon's dynamic-stacking sub-fit) on fit events. */
export type RunEvent = { seq: number; ts: number; type: RunEventType; phase?: 'main' | 'dystack'; [k: string]: unknown }

export type ModelStartedEvent = RunEvent & {
  type: 'model_started'; model: string; family: string | null; model_type: string; stack_level: number
  stack_name: string; time_limit: number | null; bagged: boolean; order: number
}

export type ModelFinishedEvent = RunEvent & {
  type: 'model_finished'; model: string; family: string | null; stack_level: number
  val_score: number | null; val_score_natural: number | null; fit_time: number | null; predict_time: number | null
  fold_scores: number[] | null; fold_scores_natural: number[] | null; val_score_std: number | null
  n_children: number; elapsed: number | null
}

export type EnsembleEvent = RunEvent & {
  type: 'ensemble'; model: string; stack_level: number; weights: Record<string, number>
  val_score: number | null; val_score_natural: number | null
}

export type Prediction = {
  run_id: string
  model: string
  problem_type: ProblemType
  n_rows: number
  missing_columns: string[]
  predictions: (string | number)[]
  /** Classification: one {class: p} per row. */
  probabilities: Record<string, number>[] | null
  classes?: (string | number)[]
  positive_class?: string | number
}
