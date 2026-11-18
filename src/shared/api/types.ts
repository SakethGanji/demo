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
  kind: 'text' | 'tabular' | 'timeseries' | 'transactions'
  target: string | null
  n_classes: number
  /** Which flows suit the dataset: tabular, forecast, anomaly. */
  flows?: string[]
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
  /** Binary: calibrate the decision threshold after the fit for this metric ('auto': f1 when the minority class < 20%). */
  calibrate_threshold?: string | null
  /** Pseudo-labelling with unlabeled rows (another dataset): fit_pseudolabel after the fit, or fit(unlabeled_data=…). */
  pseudolabel?: { dataset_id: string; method?: 'fit_pseudolabel' | 'unlabeled_data'; max_iter?: number } | null
  /** predictor.distill(**distill) after the fit. */
  distill?: Record<string, unknown> | null
  parent_run_id?: string | null
  patch?: { id: string; title?: string; changes: PatchChange[] } | null
  source?: string | null
}

// -- Training Gym (S6) -------------------------------------------------------------------------------------------

export type GymParam = {
  key: string; sym: string; name: string; help: string; default: number | string
  min?: number; max?: number; log?: boolean; integer?: boolean; choices?: (string | number)[]
  default_source?: 'autogluon' | 'library'; autogluon_default?: unknown
}
export type GymFamily = {
  key: string; name: string; kind: string; description: string; objective: string; symbols: Record<string, string>
  custom: boolean; problem_types: ProblemType[]; params: GymParam[]; in_default_zoo: boolean
}
export type GymSchema = {
  engine: { name: string; version: string }
  families: GymFamily[]
  presets: { key: string; name: string; note: string }[]
  preset_values: Record<string, Record<string, unknown>>
  metrics: Record<string, string[]>
  hpo: { schedulers: string[]; searchers: string[]; note: string }
  gpu: { count: number; names: string[]; note: string | null }
  fit: { keys: string[]; defaults: Record<string, unknown>; named: string[]; public: string[] }
  default_zoo: Record<string, unknown>
  calibration_metrics: string[]
}
export type GymIssue = { path: string; message: string }
export type GymValidation = {
  ok: boolean; errors: GymIssue[]; warnings: GymIssue[]; info: GymIssue[]
  problem_type?: ProblemType | null; label?: string
  data?: { rows: number; train_rows: number; features: string[]; d: number; dropped: string[]; text_columns: string[]; holdout_share: number }
  resolved?: { predictor_kwargs: Record<string, unknown>; fit_kwargs: Record<string, unknown>; post_fit: { call: string; kwargs: Record<string, unknown> }[]; notes: string[]; preset_expansion: Record<string, unknown> }
  call?: string
  diff?: { key: string; value: unknown; default: unknown; origin: 'you' | 'preset' | 'ensemble'; changed: boolean }[]
  effective?: { num_bag_folds: number; num_bag_sets: number; num_stack_levels: number; dynamic_stacking: boolean; use_bag_holdout?: boolean; holdout_frac: number | null; refit_full: boolean; source: string }
  estimate?: { seconds: number; unbounded_seconds: number; budget: number | null; budget_binds: boolean; overhead: number; n: number; d: number; k: number; s: number; L: number
    families: { family: string; variants: number; rate: number; rate_source: string; seconds: number; trials: number }[]; formula: string; note: string }
  families?: Record<string, number>
}

// -- Vela (S6) ---------------------------------------------------------------------------------------------------

export type PatchChange = { path: string; from: unknown; to: unknown; label: string }
export type VelaPatch = { id: string; title: string; changes: PatchChange[]; body: RunCreate }
export type VelaFinding = {
  id: string; kind: string; severity: 'info' | 'warning' | 'critical'; title: string; text: string; computed_text: string
  numbers: Record<string, unknown>; formula?: string; patch: VelaPatch
}
export type VelaWording = { source: 'claude-cli' | 'anthropic-api' | 'computed'; model: string | null; texts: Record<string, string>; kept: Record<string, boolean>
  attempts: { source: string; ok: boolean; error?: string; seconds: number }[]; seconds: number; disabled?: boolean; key: string }
export type VelaResponse = { run_id: string; run_name: string; findings: VelaFinding[]; errors: string[]; base: RunCreate; wording: VelaWording | null; key: string }

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
  mlflow?: { experiment_id: string; run_id: string; artifact_uri: string } | null
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
  /** Binary runs: the decision threshold the predictor uses (S4). */
  threshold?: ThresholdRecord | null
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
}

export type RunEventType =
  | 'run_queued' | 'run_started' | 'data_ready' | 'config_resolved' | 'fit_started'
  | 'model_started' | 'model_finished' | 'model_failed' | 'ensemble' | 'fit_finished'
  | 'leaderboard' | 'evaluation' | 'text_models' | 'explaining' | 'feature_importance' | 'log' | 'calibrating' | 'threshold' | 'tracked' | 'post_fit' | 'forecast_ready' | 'importing'
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

// ---------------------------------------------------------------------------
// Understand (S4): threshold, explain, fairness, SHAP, status. Mirrors backend/app/engine/{explain,threshold}.py.
// ---------------------------------------------------------------------------

/** Confusion counts and threshold metrics at one t (predict positive when p > t). */
export type ThresholdPoint = {
  threshold: number; tp: number; fp: number; fn: number; tn: number; n: number
  precision: number; recall: number; f1: number; specificity: number; fpr: number; balanced_accuracy: number
  accuracy: number; mcc: number; predicted_positive_rate: number; cost?: number; cost_per_row?: number
}

/** The decision threshold a binary run uses, and where it came from. */
export type ThresholdRecord = {
  value: number; default: number
  source: 'default' | 'calibrated' | 'manual' | 'autogluon_auto' | 'fit_kwargs' | 'none'
  metric: string | null; seconds: number | null; positive_class: string | number; rule: string; set_at: string
  holdout_at_default: ThresholdPoint; holdout_at_threshold: ThresholdPoint
}

export type ThresholdAnalysis = {
  run_id: string; model: string; threshold: ThresholdRecord | null
  positive_class: string | number; n: number; n_positive: number; prevalence: number
  cost_fp: number; cost_fn: number; rule: string
  current: ThresholdPoint; default: ThresholdPoint; at: ThresholdPoint | null
  best: Record<'f1' | 'balanced_accuracy' | 'mcc' | 'cost', { threshold: number; value: number }>
  bayes_threshold: number
  grid: ThresholdPoint[]
  score_histogram: { edges: number[]; positive: number[]; negative: number[] }
}

export type RocCurve = { fpr: number[]; tpr: number[]; thresholds: (number | null)[]; auc: number; n_pos: number; n_neg: number }
export type PrCurve = { precision: number[]; recall: number[]; thresholds: number[]; average_precision: number; auc_trapezoid: number; prevalence: number }
export type Calibration = {
  kind: 'binary' | 'top_label'; class?: string; n_bins: number; strategy: string; edges: number[]
  mean_predicted: number[]; fraction_positive: number[]; counts: number[]; bins: number[]; brier: number; ece: number; brier_reference?: number
}
export type GroupCandidate = { column: string; type: 'category' | 'number'; groups: number }

type ExplainBase = {
  run_id: string; model: string; problem_type: ProblemType; eval_metric: EvalMetric | null; label: string
  threshold: ThresholdRecord | null; group_candidates: GroupCandidate[]; text_columns: string[]; n: number
}

export type WorstClassified = {
  row: number; y_true: string; y_pred: string; p_true: number; p_pred: number | null
  probabilities: Record<string, number>; features: Record<string, unknown>
}

export type ExplainClassification = ExplainBase & {
  kind: 'classification'; classes: string[]; confusion_matrix: number[][]
  per_class: { class: string; precision: number; recall: number; f1: number; support: number }[]
  accuracy: number; log_loss: number; positive_class?: string
  roc: Record<string, RocCurve>; pr: Record<string, PrCurve>; roc_macro_auc?: number | null
  calibration: Calibration; n_wrong: number; worst: WorstClassified[]
}

export type ExplainRegression = ExplainBase & {
  kind: 'regression'; rmse: number; mae: number; r2: number; mean_residual: number; sd_residual: number
  quantiles: Record<'q05' | 'q25' | 'q50' | 'q75' | 'q95', number>
  points: { row: number[]; y_pred: number[]; residual: number[]; y_true: number[]; sampled: boolean }
  histogram: { edges: number[]; counts: number[]; normal: number[] | null }
  worst: { row: number; y_true: number; y_pred: number; residual: number; abs_error: number; features: Record<string, unknown> }[]
}

export type Explain = ExplainClassification | ExplainRegression

export type Fairness = {
  run_id: string; model: string; group: string; binning: 'values' | 'quartiles'; n: number; problem_type: ProblemType
  threshold?: number; positive_class?: string
  gaps: Record<string, number>; metrics: string[]
  overall: Record<string, number>; by_group: ({ group: string } & Record<string, number | null>)[]
  difference: Record<string, number | null>; ratio: Record<string, number | null>
}

export type ShapResult = {
  run_id: string; row: number; model: string; problem_type: ProblemType; target: string | null
  output: 'probability' | 'prediction'; base_value: number; fx: number; sum_phi: number; additivity_error: number
  contributions: ShapContribution[]
  background_size: number; nsamples: number; seconds: number; classes: string[] | null; y_true: unknown; cached: boolean; method: string
}

export type ShapContribution = { feature: string; value: unknown; phi: number }

export type FeatureImportanceCI = FeatureImportance & { ci_low: number | null; ci_high: number | null; ci_level: number; t_crit: number | null }

export type ImportanceResponse = {
  run_id: string; features: FeatureImportanceCI[]; meta: { num_shuffle_sets: number; subsample_size: number | null }
  method: string; eval_metric: string | null
}

export type ApiStatus = {
  version: string; engine: EngineInfo; cpu_count: number | null; memory_gb: number | null; python: string; host: string
  active: null | {
    id: string; name: string; status: RunStatus; started_at: string | null; time_limit: number | null; presets: unknown
    eval_metric: string | null; models_started: number; models_finished: number; models_failed: number
    best_val: number | null; stage: string
  }
  queued: number; runs: number; max_parallel_runs: number | null
  registry?: RegistryCounts; pool?: { workers: number; active_jobs: number; submitted: number }
}

// ---------------------------------------------------------------------------
// Registry and use (S5). Mirrors backend/app/registry/*.
// ---------------------------------------------------------------------------

export type Stage = 'None' | 'Staging' | 'Production' | 'Archived'
export type Approval = 'pending' | 'approved' | 'rejected'

export type Latency = {
  batch_rows: number; batch_repeats: number; us_per_row_p50: number; us_per_row_p95: number; us_per_row: number[]
  single_calls: number; single_ms_p50: number | null; single_ms_p95: number | null; throughput_rows_per_s: number | null
}

export type MetricCI = { value: number; ci_low: number | null; ci_high: number | null; formula: string; B: number }
export type HoldoutCI = { metrics: Record<string, MetricCI>; n: number; B: number; seed: number; method: string }

export type BuildStep = { name: string; status: 'running' | 'done' | 'skipped' | 'warning' | 'failed'; ts: string; seconds?: number; [k: string]: unknown }

export type InferCandidate = {
  model: string; refit_parent: string | null; score_val: number | null; score_val_natural: number | null; us_per_row: number | null
  ag_predict_1_time_us: number | null; n_models: number; time_used_us: number | null; within: boolean
}

export type VersionResult = {
  source: { run_id: string; best_model: string; size_bytes: number; latency: Latency; n_models: number; holdout_ci: HoldoutCI | null; holdout: Record<string, number> | null }
  version: {
    best_model: string; size_bytes: number; latency: Latency; n_models: number; models: string[]; dependencies: string[]
    holdout: Record<string, number>; confusion_matrix?: number[][]; classes?: string[]; holdout_ci: HoldoutCI; problem_type: ProblemType
    eval_metric: string; positive_class: string | null; decision_threshold: number | null; features: string[]
  }
  tradeoff: { size_ratio: number | null; latency_ratio_p50: number | null; single_ratio_p50: number | null }
  refit: { skipped?: boolean; reason?: string; model?: string; mapping?: Record<string, string>; seconds?: number; best?: string } | null
  infer_limit: { infer_limit_s: number; infer_limit_us: number; batch_size: number; candidates: InferCandidate[]; chosen: string; met: boolean; seconds: number; rule: string } | null
  hashes: { model_sha256: string; data_sha256: string; splits_sha256: string; data_file: string }
  path: string
  mlflow?: { name: string; version: number; source: string; run_id: string; stage: string; experiment_id: string }
}

export type ModelVersion = {
  id: string; model_name: string; version: number; run_id: string; status: 'building' | 'ready' | 'failed'; stage: Stage; approval: Approval
  created_at: string; created_by: string
  options: { refit_full: boolean; infer_limit: number | null; infer_limit_batch_size: number | null; clone_for_deployment: boolean; description: string }
  progress: { steps: BuildStep[] } | null; error: string | null; path: string | null; model_hash: string | null
  summary: {
    best_model: string | null; size_bytes: number | null; n_models: number | null; us_per_row_p50: number | null; us_per_row_p95: number | null
    single_ms_p50: number | null; holdout: Record<string, number> | null; eval_metric: string | null; problem_type: ProblemType | null
    source_size_bytes: number | null; source_us_per_row_p50: number | null; mlflow: VersionResult['mlflow'] | null; data_sha256: string | null
  }
  result: VersionResult | null
}

export type RegisteredModel = {
  name: string; created_at: string; description: string; dataset_id: string; label: string
  versions: number; latest: number | null; production: number | null; staging: number | null
}

export type RegistryCounts = {
  models: number; versions: number; building: number; stages: Record<Stage, number>; approvals: Record<Approval, number>; batches: number
}

export type AuditEntry = {
  id: number; ts: string; actor: string; action: 'registered' | 'stage_changed' | 'approved' | 'rejected' | 'card_edited' | 'batch_scored'
  model_name: string | null; version: number | null; version_id: string | null; run_id: string | null
  from_stage: string | null; to_stage: string | null; comment: string; model_hash: string | null; details: Record<string, unknown> | null
}

export type RegisterRequest = {
  created_by: string; model_name?: string; description?: string; refit_full?: boolean; infer_limit?: number | null
  infer_limit_batch_size?: number | null; clone_for_deployment?: boolean
}

export type LatencyModel = {
  model: string; us_per_row: number; us_per_row_all: number[]; score_val_natural: number | null; score_test_natural: number | null
  n_models: number; size_bytes: number; stack_level: number; refit_full: boolean; best: boolean; ag_predict_1_time_us: number | null
}
export type LatencyProfile = {
  run_id: string; batch_rows: number; models: LatencyModel[]; eval_metric: string; greater_is_better: boolean; best_model: string
  total_size_bytes: number; bagged: boolean; cached: boolean
}

export type TryResult = {
  version_id: string; model_name: string; version: number; model: string; problem_type: ProblemType; missing_columns: string[]; features: string[]
  classes?: string[]; prediction: unknown; probabilities: Record<string, number> | null; positive_class?: string; threshold?: number; predict_ms: number
  shap?: ShapResult & { reasons: (ShapContribution & { direction: 'raises' | 'lowers' })[] }
}

export type PsiBin = { label: string; expected_count: number; actual_count: number; expected: number; actual: number; term: number }
export type PsiFeature = {
  feature: string; kind: string; psi: number | null; band: 'stable' | 'watch' | 'shift' | 'missing'; n_expected?: number; n_actual?: number
  bins?: PsiBin[]; edges?: number[] | null; mean_expected?: number | null; mean_actual?: number | null; std_expected?: number | null
  std_actual?: number | null; categories_unseen?: string[]; note?: string; output?: string; ks?: { statistic: number; p_value: number }
}
export type DriftReport = {
  features: PsiFeature[]; prediction: PsiFeature; counts: { stable: number; watch: number; shift: number }; eps: number; max_psi: number | null
  bands: { band: string; rule: string }[]; formula: string; n_expected: number; n_actual: number; reference: string; prediction_reference: string
}

export type BatchJob = {
  id: string; version_id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; created_at: string; finished_at: string | null
  created_by: string; filename: string; n_rows: number | null; progress: number; stage: string; error: string | null
  result: {
    summary: {
      n_rows: number; seconds: number; score_seconds: number; us_per_row: number; missing_columns: string[]; extra_columns: string[]; model: string
      problem_type: ProblemType; predicted?: Record<string, number>; classes?: string[]; positive_class?: string; mean_p?: number
      mean_p_reference?: number | null; flagged_share?: number; threshold?: number; mean_prediction?: number; std_prediction?: number
      histogram?: { edges: number[]; actual: number[]; expected: number[] }
    }
    drift: DriftReport; preview: Record<string, unknown>[]; columns: string[]; output_file: string
  } | null
}
export type BatchListItem = Omit<BatchJob, 'result'> & { summary: NonNullable<BatchJob['result']>['summary'] | null; drift_counts: DriftReport['counts'] | null; max_psi: number | null; prediction_psi: number | null }

export type CardField = { key: string; label: string; hint: string; value: string }
export type ModelCard = {
  title: string; model_name: string; version: number; version_id: string; generated_note: string
  summary: Record<string, unknown> & { stage: Stage; approval: Approval; model_sha256: string; best_model: string; source_run: string; eval_metric: string; problem_type: ProblemType; label: string; positive_class: string | null; registered_by: string; registered_at: string; mlflow: VersionResult['mlflow'] | null }
  data: Record<string, unknown> & { rows: number; columns: number; features: number; train_rows: number; holdout_rows: number; sha256: string; splits_sha256: string; name: string; source: string; filename: string; split: string; dropped_missing_label: number; dropped_columns: string[]; warnings: string[]
    label_balance: { class: string; train: number; train_share: number; holdout: number; holdout_share: number }[] | { mean: number; std: number; min: number; median: number; max: number } | null
    feature_table: { name: string; type: string; missing_share: number; unique: number }[] }
  training: { call: string; notes: string[]; fit_seconds: number; n_models: number; presets: unknown; time_limit: number | null; registration: Record<string, unknown>; refit: VersionResult['refit'] }
  validation: { holdout_ci: HoldoutCI | null; source_holdout_ci: HoldoutCI | null; threshold: ThresholdRecord | null; version_threshold: number | null; confusion_matrix: number[][] | null; classes: string[] | null; validation_score: number | null }
  importance: { features: FeatureImportanceCI[]; method: string }
  fairness: { reports: Record<string, { gaps: Record<string, number>; threshold: number | null; computed_at: string; binning: string; by_group: Record<string, unknown>[] }>; note: string | null }
  ensemble: { model: string | null; weights: Record<string, number> | null; stack_level: number | null; version_best: string; dependencies: string[]; leaderboard: Record<string, unknown>[] }
  deployment: { source: { best_model: string; size_bytes: number; n_models: number; latency: Latency }; version: { best_model: string; size_bytes: number; n_models: number; latency: Latency }; tradeoff: VersionResult['tradeoff']; infer_limit: VersionResult['infer_limit'] }
  use: { fields: CardField[] }
  monitoring: { batches: { id: string; created_at: string; n_rows: number; max_psi: number | null; prediction_psi: number | null; counts: DriftReport['counts'] | null }[]; bands: string }
  approval: { stage: Stage; approval: Approval; rules: string; reviews: AuditEntry[]; audit: AuditEntry[] }
}

// ---------------------------------------------------------------------- Autopilot (S7)

export type ApRequirement = { metric: string; op: '>=' | '<='; value: number }
export type ApCheck = ApRequirement & { observed: number | null; ok: boolean; gap: number | null; name: string; sym: string }
export type ApLatency = { n?: number; p50_ms?: number; p95_ms?: number; mean_ms?: number; model?: string; method?: string; error?: string }
export type ApDecision = {
  at: string; after_step: string; menu: string[]; action: string; rationale: string; layer: 'layer-1' | 'layer-2'
  source: string; model: string | null; seconds?: number; attempts?: { source: string; ok: boolean; error?: string; seconds: number }[]
  layer1_would?: string; layer2_error?: string; time_limit?: number; cost_usd?: number
}
export type ApStep = {
  id: string; n: number; kind: string; title: string; status: 'running' | 'done' | 'dropped' | 'skipped' | 'failed' | 'interrupted'
  started_at: string; finished_at?: string; run_id?: string; dataset_id?: string; score?: number | null; score_own?: number | null
  dataset_version?: number; metrics?: Record<string, number>; checks?: ApCheck[]; met?: boolean; latency?: ApLatency
  seconds?: number; best_model?: string; n_models?: number; kept?: boolean; change?: string | null; decision?: ApDecision | null
  body?: Record<string, unknown>; relabelled?: number; dropped?: number; confirmed?: number; skipped?: number; synthetic?: number
  stats?: Record<string, unknown>; kinds?: Record<string, number>; text?: string; facts_text?: string
}
export type ApRecall = {
  planted: number; found: number; recall: number; top_k: number; precision: number; suggestion_correct: number
  suggestion_accuracy: number; recall_at_planted: number; fixed: number; source: string
}
export type ApInboxSummary = {
  n_items: number; answers: Record<string, number>; stats: Record<string, number | string>; kinds?: Record<string, number>
  recall: ApRecall | null; run_id?: string; model?: string; applied?: boolean; submitted?: boolean; seconds?: number
}
export type ApInboxItem = {
  id: string; row: number; kind: 'label' | 'conflict' | 'duplicate' | 'outlier'; split: 'train' | 'holdout'
  given: string; suggested: string | null; suggestion: 'relabel' | 'drop' | 'skip'; confidence: number
  p_given?: number; margin?: number; quality?: number; of_row?: number; similarity?: number; ood_score?: number
  why: string; text: string | null; features: Record<string, unknown>
  answer: 'confirm' | 'relabel' | 'drop' | 'skip' | null; answer_label?: string; answered_by?: string
}
export type ApDatasetVersion = { id: string; name: string; parent: string; version: number; rows: number; relabelled: number; dropped: number; synthetic: number; relabelled_holdout: number; synthetic_class?: string; synthetic_source?: string; created_at: string; synthetic_examples?: Record<string, string>[] }
export type ApFacts = {
  dataset_id: string; dataset_name: string; rows: number; label: string; problem_type: string; features: string[]
  text_columns: string[]; class_shares: Record<string, number> | null; minority_class: string | null; minority_share: number | null; holdout_rows: number
}
export type Autopilot = {
  id: string; created_at: string; updated_at?: string; finished_at?: string
  status: 'queued' | 'running' | 'awaiting_inbox' | 'finished' | 'failed' | 'cancelled'; error: string | null
  brief: { dataset_id: string; label: string; goal: string; requirements: ApRequirement[]; budget_seconds: number; autonomy: 'ask' | 'auto'; auto_confidence: number; decider: 'auto' | 'layer1'; allow_synthetic: boolean }
  facts: ApFacts | null; primary?: string; truth_dataset?: string; steps: ApStep[]; decisions: ApDecision[]
  log: { t: string; text: string; layer: string; source?: string | null; model?: string | null; action?: string; step?: string }[]
  inbox: ApInboxSummary | null; dataset_versions: ApDatasetVersion[]; current: string | null
  verdict: { met: boolean; checks: ApCheck[]; best_step: string | null; best_run_id: string | null; score: number | null; baseline_score: number | null; delta: number | null; spent_seconds: number; budget_seconds: number; dataset_id: string } | null
  budget: { total: number; spent: number; remaining: number }
}
export type ApInbox = ApInboxSummary & { autopilot_id: string; status: Autopilot['status']; total: number; items: ApInboxItem[] }
export type VelaAnswer = { answer: string | null; source: string; model: string | null; seconds: number; message?: string; attempts: { source: string; ok: boolean; error?: string }[]; facts: string[] }
