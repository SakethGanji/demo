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
  /** F4: splits are stored per (target, strategy); only "random-stratified" so far. */
  strategy?: string
  /** File key of the (target, strategy) split. */
  key?: string
  /** SHA-256 of the holdout rows and fold assignment: equal hashes mean the same split. */
  sha256?: string
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
  /** given: named on upload (or a sample's label) · guessed: the profiler's pick, not yet confirmed · confirmed: by the user */
  target_source?: 'given' | 'guessed' | 'confirmed'
  target_history?: { target: string; at: string }[]
}

/** The split a run used (F4): its target's holdout and folds, recorded at run creation. */
export type RunSplit = {
  path: string; key: string; target: string; strategy: string; seed: number; stratified: boolean
  holdout_share: number; n_folds: number; holdout_rows: number; sha256: string; drawn_for_this_run: boolean
  /** F6: group / time / time-group splits. */
  params?: SplitParams; tuning_rows?: number; excluded_rows?: number; cutoff?: string | number | null; tuning_cutoff?: string | number | null
}

/** F6: how the holdout is drawn. */
export type SplitStrategy = 'random-stratified' | 'group' | 'time' | 'time-group'
export type SplitParams = { group_column?: string; time_column?: string; cutoff?: string | null; tuning_share?: number }
export type SplitSpec = SplitParams & { strategy: SplitStrategy }
export type SplitOptions = {
  dataset_id: string; target: string | null; strategies: SplitStrategy[]
  date_columns: { column: string; from: string; to: string; distinct: number; parsed_share: number }[]
  group_columns: { column: string; groups: number; rows_per_group: number; max_rows: number; id_like_name: boolean }[]
  warnings: string[]; suggested: SplitStrategy; holdout_share: number; tuning_share: number
  suggested_params: SplitParams
}
export type SplitRole = { rows: number; groups?: number; positive_rate?: number; from?: string; to?: string }
export type SplitDiagram = {
  roles: { train: SplitRole; tuning: SplitRole; holdout: SplitRole; excluded: SplitRole }
  positive_label: string | null
  shared_groups?: { train_holdout: number; train_tuning: number; tuning_holdout: number }
  rows_per_group?: { mean: number; max: number; hist: Record<string, number> }
  fold_groups?: number[]
  timeline?: ({ period: string; train: number; tuning: number; holdout: number; excluded: number; positive_rate?: number })[]
}
export type SplitDescription = Splits & {
  dataset_id: string; drawn_now: boolean; params?: SplitParams; train_rows?: number; tuning_rows?: number; excluded_rows?: number
  cutoff?: string | number | null; tuning_cutoff?: string | number | null; diagram?: SplitDiagram
}
export type SplitComparisonRow = {
  run_id: string; name: string; created_at: string; key: string; strategy: SplitStrategy; params: SplitParams | null
  cutoff: string | null; tuning_cutoff: string | null; holdout_rows: number | null; train_rows: number | null
  tuning_rows: number; excluded_rows: number; score: number; same_config: boolean; this_run: boolean
  best_model: string | null; optimism: number | null
}
export type SplitComparison = {
  run_id: string; dataset_id: string; label: string; metric: string | null; greater_is_better: boolean
  random_run_id: string | null; runs: SplitComparisonRow[]; strategies_missing: SplitStrategy[]
}

export type DatasetSplits = { dataset_id: string; target: string | null; current: string | null; splits: (Splits & { created_at?: string; adopted_from?: string | null })[] }

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

/** G0 Smart Intake: a column's role, how sure the detector is, and why. `also`: other roles the column could have. */
export type IntakeRole = { role: string; confidence: number; evidence: string[] }
export type IntakeColumn = IntakeRole & { name: string; type: string; unique: number; also: IntakeRole[]; overridden?: boolean }
/** One thing the table can build, with the reason and the pre-fill its flow (`route`) opens with. */
export type IntakeSuggestion = {
  id: string; title: string; route: string; score: number; reason: string
  prefill: { target?: string; drop?: string[]; split?: 'time' | 'group' | 'time-group'; time?: string; group?: string; metric?: string } & Record<string, unknown>
}
export type Intake = {
  dataset_id: string; rows: number; columns: IntakeColumn[]; roles: Record<string, string[]>
  suggestions: IntakeSuggestion[]; role_names: Record<string, string>
}

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
  /** F6: the split strategy (default random-stratified) and its columns. */
  split_spec?: SplitSpec | null
  /** F7: a business cost as the eval_metric ('expected_cost'). */
  cost?: CostRequest | null
  /** F8: monotone constraints on numeric features (binary and regression). */
  monotone?: MonotoneRequest | null
  /** F9: predict a range: the quantile levels τ (problem_type 'quantile'). */
  quantile_levels?: number[] | null
  /** F11: the scorecard challenger (binary): null/true on with defaults, false off, or its binning and points scaling. */
  scorecard?: boolean | Partial<ScorecardParams> | null
  /** F9: conformal recalibration (AutoGluon's calibrate): true, false, or null = AutoGluon's 'auto'. */
  conformal?: boolean | null
}

// -- Quantile regression (F9) -----------------------------------------------------------------------------------

export type QuantileLevelEval = { tau: number; pinball: number; below: number; below_ci: [number | null, number | null]; gap: number }
export type QuantileInterval = {
  lo: number; hi: number; nominal: number; symmetric: boolean; coverage: number; coverage_ci: [number | null, number | null]
  inside: number; n: number; below_lo: number; above_hi: number; mean_width: number; median_width: number
}
export type QuantileEval = { n: number; pinball: number; levels: QuantileLevelEval[]; intervals: QuantileInterval[]; crossing_share: number; crossing_rows: number }
export type QuantileReport = {
  levels: number[]; centre: number; main_interval: { lo: number; hi: number; nominal: number } | null; model: string
  holdout: QuantileEval; raw: QuantileEval | null; calibrated: QuantileEval | null; served: 'raw' | 'calibrated'
  conformal: { model: string; applied: boolean; offsets?: Record<string, number>; source?: string; n_val?: number; error?: string; method: string }
  models: { model: string; pinball: number; levels: { tau: number; pinball: number; below: number }[]; coverage: number | null; nominal: number | null; mean_width: number | null; crossing_share: number; conformalized: boolean }[]
  fan: { rank: number[]; row: number[]; y: number[]; q: Record<string, number[]>; n: number; sampled: boolean; sorted_by: string }
  formulas: Record<string, string>
  error?: string
}

// -- Scorecard challenger (F11) ---------------------------------------------------------------------------------

export type ScorecardParams = { pdo: number; base_score: number; base_odds: number; max_bins: number; fine_bins: number; min_bin_share: number; min_iv: number; monotone_bins: 'auto' | 'none'; C: number }
export type ScorecardBin = { feature: string; bin: number; label: string; n: number; share: number; events: number; event_rate: number | null; woe: number; iv: number; points: number | null; points_rounded: number | null; in_model: boolean }
export type ScorecardFeature = { feature: string; kind: 'numeric' | 'categorical'; bins: number; iv: number; in_model: boolean; coefficient: number | null; dropped: string | null; direction: number | null; constraint: number | null; cuts: number[] | null; has_missing: boolean; points_range: [number, number] | null }
export type ScorecardAuc = { auc: number; gini: number; ks?: number; model?: string }
export type DeltaAuc = { delta: number; ci: [number, number]; se: number; p_le_0: number; B: number; level: number; method: string }
export type ScorecardReport = {
  run_id: string; available: boolean; model: string; served_model: string; positive_class: string; params: ScorecardParams
  monotone: Record<string, number>; n_train: number; n_holdout: number; export: string
  table: { rows: ScorecardBin[]; features: ScorecardFeature[]; intercept: number; n_features: number; n_train: number; event_rate: number
    scaling: { pdo: number; base_score: number; base_odds: number; factor: number; offset: number } }
  holdout: { scorecard: ScorecardAuc; served: ScorecardAuc & { model: string }; scorecard_in_run: ScorecardAuc & { model: string; score_val: number | null; bagged: boolean }
    delta_auc: DeltaAuc; delta_auc_in_run: DeltaAuc; bands: { lo: number; hi: number; n: number; events: number; event_rate: number }[]; proba_check: number
    score: { min: number; max: number; mean_event: number | null; mean_non_event: number | null } }
  monotone_check: Record<string, { sign: number; in_model: boolean; monotone: boolean; points: number[] }>
  cost?: { threshold_scorecard: number; threshold_served: number; scorecard_per_1000: number; served_per_1000: number; c_fp: number; c_fn: number; g_tp: number }
  family?: ModelFamily | null
}
export type ScorecardRowScore = {
  score: number; p_event: number; served_p_event: number; served_adverse: boolean; truth?: string
  points: { feature: string; value: unknown; bin: string; points: number; max_points: number; points_lost: number }[]
  reasons: { feature: string; points_lost: number }[]
}
export type ScorecardScore = { run_id: string; rows: ScorecardRowScore[]; scaling: { factor: number; offset: number; pdo: number; base_score: number; base_odds: number }
  positive_class: string; served_model: string; served_threshold: number; top_k: number }

// -- Monotone constraints (F8) -----------------------------------------------------------------------------------

export type MonotoneDirection = 'increasing' | 'decreasing'
/** What the Gym sends; the API stores {constraints: {column: ±1}, compliant_only, stack}. */
export type MonotoneRequest = { constraints: Record<string, MonotoneDirection | 1 | -1 | 0 | 'none'>; compliant_only?: boolean; stack?: boolean }
/** ICE / PD of one model for one constrained column, on raw holdout rows. */
export type MonotoneColumnCheck = {
  sign: number; grid?: number[]; pd?: number[]; n_curves: number; n_steps: number; curves_violating: number; steps_violating: number
  max_violation: number; pd_monotone: boolean; pd_max_violation: number; pd_range: number; monotone: boolean
  ice?: number[][]; ice_rows?: number[]; error?: string
}
export type MonotoneModel = {
  model: string; class: string; family: string | null; stack_level: number; kind: 'model' | 'weighted_ensemble'
  constrained: boolean | null; applied?: Record<string, number>; internal?: Record<string, number>; n_columns?: number | null
  weights?: Record<string, number>; monotone_by_construction: boolean; monotone_checked?: boolean
  check?: Record<string, MonotoneColumnCheck>; check_error?: string
}
/** The worker's report after the fit (Run.monotone). */
export type MonotoneReport = {
  constraints: Record<string, number>; compliant_only: boolean; stack: boolean
  columns?: Record<string, { features: string[]; models: string[]; status: string }>
  models?: MonotoneModel[]; best_model?: string; served?: Record<string, MonotoneColumnCheck>; best_monotone?: boolean
  unconstrained_models?: string[]; grid_points?: number; eps?: number; error?: string
}
/** GET /runs/{id}/monotone and /registry/versions/{id}/monotone. */
export type MonotoneCheck = {
  run_id: string; version_id?: string; served_model?: string; constraints: Record<string, number>
  request?: MonotoneReport | null; report?: MonotoneReport | null
  model: string; best_model: string; problem_type: ProblemType; score: string; rows: number
  columns: Record<string, MonotoneColumnCheck>; monotone: boolean; eps: number
}

// -- Business cost (F7) ------------------------------------------------------------------------------------------

export type CostRequest = { c_fp: number; c_fn: number; g_tp?: number } | { classes: string[]; matrix: number[][] }
/** The cost as the run stored it (normalised by the API). */
export type RunCost =
  | { kind: 'binary'; c_fp: number; c_fn: number; g_tp: number; classes: string[]; positive_class: string; bayes_threshold: number }
  | { kind: 'multiclass'; classes: string[]; matrix: number[][] }
/** Expected cost per 1,000 decisions with its 95% percentile-bootstrap interval. */
export type CostStat = { per_decision: number; per_1000: number; ci_per_1000: [number, number]; n: number; B: number; level: number }
export type CostPoint = CostStat & { t: number; tp: number; fp: number; fn: number; tn: number }
export type CostAnalysis = {
  run_id: string; model: string | null; eval_metric: string | null; trained_on_cost: boolean; threshold: ThresholdRecord | null
  cost: RunCost; n: number; per: number; B: number; level: number
  // binary
  positive_class?: string | number; prevalence?: number; n_positive?: number; bayes_threshold?: number; rule?: string
  deployed: CostStat & Partial<CostPoint>; bayes: CostStat & Partial<CostPoint>; default?: CostPoint; validation?: CostPoint | null
  hindsight?: { t: number; per_1000: number; note: string }; flag_none?: CostStat; flag_all?: CostStat
  curve?: { t: number; per_1000: number; tp: number; fp: number; fn: number; flagged: number }[]
  // multiclass
  counts?: number[][]; cost_by_cell?: number[][]; best_constant?: CostStat & { class: string }
}
export type CostDelta = { per_1000: number; ci_per_1000: [number, number]; B: number; level: number; share_positive: number }
export type CostComparisonRow = {
  run_id: string; name: string; this_run: boolean; created_at: string; eval_metric: string | null; trained_on_cost: boolean
  best_model: string | null; holdout_rows: number
  threshold: { value: number; source: string | null; metric: string | null } | null
  validation_threshold: number | null; deployed: CostStat; validation: CostPoint | null; bayes: CostStat
  roc_auc: number | null; accuracy: number | null; curve: [number, number][]
  delta: CostDelta | null; delta_validation: CostDelta | null
}
export type CostComparison = {
  run_id: string; cost: RunCost; per: number; B: number; level: number; problem_type: ProblemType | null
  runs: CostComparisonRow[]; trained_on_cost_present: boolean; metrics_missing: string[]
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
  /** F8: the normalised constraints and what they do to the zoo. */
  monotone?: { constraints: Record<string, number>; compliant_only: boolean; stack: boolean; compliant: string[]; unconstrained: string[]; left_out: string[]; families: Record<string, string> } | null
  /** F11: the scorecard's hyperparameters (on), false (switched off) or null (not a binary label). */
  scorecard?: ScorecardParams | false | null
}

// -- Vela (S6) ---------------------------------------------------------------------------------------------------

export type PatchChange = { path: string; from: unknown; to: unknown; label: string }
export type VelaPatch = { id: string; title: string; changes: PatchChange[]; body: RunCreate }
export type VelaFinding = {
  id: string; kind: string; severity: 'info' | 'warning' | 'critical'; title: string; text: string; computed_text: string
  numbers: Record<string, unknown>; formula?: string; patch: VelaPatch
}
export type VelaWording = { source: 'anthropic-sdk' | 'claude-cli' | 'anthropic-api' | 'computed'; decider?: string; cached?: boolean; model: string | null; texts: Record<string, string>; kept: Record<string, boolean>
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
  /** tabular | import | forecast | anomaly */
  kind?: string
  /** F4: the (label, strategy) split this run trained and scored on; absent on runs made before F4. */
  split?: RunSplit | null
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
  /** F8: monotone constraints, where they landed and the ICE / PD check of every model. */
  monotone?: MonotoneReport | null
  /** F11: the scorecard challenger's report (read in full from GET /runs/{id}/scorecard). */
  scorecard?: { available: boolean; error?: string; reason?: string } | null
  /** F12: the learning curve's headline (status and verdict); the full curve is GET /runs/{id}/learning-curve. */
  learning_curve?: { status: LearningCurveStatus; fits_done?: number; fits_total?: number; error?: string | null; verdict?: LearningCurveVerdict | null } | null
  active_learning?: { status: ActiveLearningStatus; fits_done?: number; fits_total?: number; error?: string | null; summary?: ActiveLearningSummary | null } | null
  annotations?: { schema: 'annotations/1'; status: 'computing' | 'ready' | 'failed'; error?: string | null; summary?: AnnotationsSummary | null; updated_at?: string } | null
  /** F15: the latest shift check's summary (shift/1), for the Model Doctor. */
  shift?: ShiftSummary | null
  /** F16: the latest weak-slice search's summary (slices/1), for the Model Doctor. */
  slices?: SliceSummary | null
  /** F17: the Model Doctor's last diagnosis (doctor/1 summary); the full one is GET /runs/{id}/doctor. */
  doctor?: DoctorSummary | null
  /** F9: quantile runs: the levels, and the holdout report (the list keeps only levels, pinball and coverage). */
  quantile_levels?: number[] | null
  quantile?: QuantileReport | null
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
  | 'leaderboard' | 'evaluation' | 'text_models' | 'explaining' | 'feature_importance' | 'log' | 'calibrating' | 'threshold' | 'tracked' | 'post_fit' | 'forecast_ready' | 'importing' | 'el_model_started' | 'el_model_finished' | 'seg_model_started' | 'seg_model_finished'
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
  /** F9: quantile runs: every level per row; predictions are the centre level. */
  quantile_levels?: number[]; centre?: number; quantiles?: Record<string, number>[]
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
  threshold: ThresholdRecord | null; group_candidates: GroupCandidate[]; text_columns: string[]; n: number; bisg_surname?: string | null
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
  output: 'probability' | 'prediction'; quantile_level?: number | null; base_value: number; fx: number; sum_phi: number; additivity_error: number
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
  ag_predict_1_time_us?: number | null; n_models: number; time_used_us: number | null; within: boolean
  /** batch size 1: one-row p50 / p95 as served (pipeline pruned, persisted). */
  single_ms_p50?: number | null; single_ms_p95?: number | null; pipeline_pruned?: boolean
}

/** One-row and batch latency of a version as served (F3). */
export type BatchLatency = { batch: number; calls: number; ms_p50: number | null; ms_p95: number | null; us_per_row_p50: number | null; rows_per_s: number | null }
export type OneRow = { calls: number; ms_p50: number | null; ms_p95: number | null; ms_max: number | null }
export type PipelinePrune = {
  model?: string; pruned: boolean; kept?: boolean; reason?: string; features_in: string[]; dropped_in?: string[]
  n_features_out: number; n_features_out_before?: number | null; generators?: number | null; generators_before?: number | null; max_abs_diff?: number
}
export type Serving = {
  cold_first_call_ms: number; warm: { persisted: string[]; warm_calls_ms: number[]; seconds: number } | null
  one_row: OneRow; batches: BatchLatency[]; pipeline: PipelinePrune | null; cpus: number; where: string
}
export type ServingBenchmark = {
  model: string; one_row: OneRow; batches: BatchLatency[]; warm_on_load: Serving['warm']; cache_load_s: number
  features_in: string[]; n_features: number; where: string
}
export type ScoreResult = {
  version_id: string; model: string; problem_type: ProblemType; n_rows: number; predictions: unknown[]
  probabilities: Record<string, number>[] | null; classes?: string[]; predict_ms: number; worker_ms: number; server_ms: number; where: 'api' | 'worker'; warm_before_call: boolean | null
  /** F9: quantile versions: every level per row (predictions are the centre level). */
  quantile_levels?: number[]; quantiles?: Record<string, number>[]
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
  infer_limit: { infer_limit_s?: number; infer_limit_us?: number; batch_size?: number; candidates?: InferCandidate[]; chosen: string; met: boolean; seconds: number; rule: string; serve_model?: string; serve_fastest?: boolean } | null
  distill?: { teacher: string; students: string[]; served?: string; seconds: number; method: string; teacher_score_test: number | null; scores: Record<string, { score_val: number | null; score_test: number | null }> } | null
  serving?: Serving
  serving_benchmark?: ServingBenchmark
  hashes: { model_sha256: string; data_sha256: string; splits_sha256: string; data_file: string }
  path: string
  mlflow?: { name: string; version: number; source: string; run_id: string; stage: string; experiment_id: string }
  /** F9: the served model's range on the holdout. */
  quantile?: QuantileReport | null
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
  id: number; ts: string; actor: string; action: 'registered' | 'stage_changed' | 'approved' | 'rejected' | 'card_edited' | 'batch_scored' | 'labels_attached' | 'monitoring_configured' | 'retrain_launched' | 'reason_codes_edited'
  model_name: string | null; version: number | null; version_id: string | null; run_id: string | null
  from_stage: string | null; to_stage: string | null; comment: string; model_hash: string | null; details: Record<string, unknown> | null
}

export type RegisterRequest = {
  created_by: string; model_name?: string; description?: string; refit_full?: boolean; infer_limit?: number | null
  infer_limit_batch_size?: number | null; clone_for_deployment?: boolean
  /** Serve this model of the run (set_model_best) instead of AutoGluon's best. */
  serve_model?: string | null
  /** F3: serve the model with the lowest measured one-row p95; distil into single students; prune the pipeline. */
  serve_fastest?: boolean; distill?: boolean; distill_time_limit?: number; prune_pipeline?: boolean
}

export type LatencyModel = {
  model: string; us_per_row: number; us_per_row_all: number[]; score_val_natural: number | null; score_test_natural: number | null
  n_models: number; size_bytes: number; stack_level: number; refit_full: boolean; best: boolean; ag_predict_1_time_us: number | null
  /** One-row calls (what Try It or an online API sees): ms per call, p50 / p95, and how many calls were timed. */
  single_ms_p50?: number | null; single_ms_p95?: number | null; single_calls?: number
  /** As served (F3): feature pipeline pruned to what the model reads, models persisted. */
  served_ms_p50?: number | null; served_ms_p95?: number | null; served_calls?: number; on_frontier?: boolean; score_rank?: number | null
  pipeline?: { pruned: boolean; features_in: string[]; n_features_out: number; n_features_out_before?: number | null; generators?: number | null; generators_before?: number | null }
}
export type LatencyProfile = {
  run_id: string; batch_rows: number; models: LatencyModel[]; eval_metric: string; greater_is_better: boolean; best_model: string
  total_size_bytes: number; bagged: boolean; cached: boolean
  /** F3: models no other model beats on both served one-row p95 and validation score. */
  frontier?: string[]; natural_greater_is_better?: boolean; cpus?: number
}

export type TryResult = {
  version_id: string; model_name: string; version: number; model: string; problem_type: ProblemType; missing_columns: string[]; features: string[]
  classes?: string[]; prediction: unknown; probabilities: Record<string, number> | null; positive_class?: string; threshold?: number; predict_ms: number
  /** F9: quantile versions: every level, the centre level (the prediction) and the main interval. */
  quantile_levels?: number[]; quantiles?: Record<string, number>; centre?: number; interval?: { lo: number; hi: number; nominal: number } | null
  shap?: ShapResult & { reasons: (ShapContribution & { direction: 'raises' | 'lowers' })[] }
  /** F10: the decision at the version's threshold and its reason codes (the same estimator as batch and /score). */
  reasons?: TryReasons | { error: string }
  /** G5: a routed (segmented) version: the row's segment and the model that scored it. */
  segment?: { column: string; value: string; model: 'segment' | 'global'; routed_to: string; features: string[] }
}

/** F10: reason codes for adverse decisions. */
export type SegCardCmp = { n: number; global: Record<string, number>; routed: Record<string, number>; delta: number | null; ci: [number, number] | null; verdict: string }
export type ReasonCode = { rank: number; code: string; feature: string; statement: string; side: 'high' | 'low' | 'category' | 'missing'; value: unknown; phi: number; push: number }
export type ReasonMapping = { feature: string; code: string; kind: 'number' | 'category'; constraint: number; high?: string; low?: string; category?: string; missing: string; edited?: boolean }
export type ReasonAttribution = { method: 'tree' | 'kernel'; name: string; background: number; coalitions?: number; exact: boolean; why: string; d: number; evals_per_row: number | null; models?: string[]; seed?: number }
export type ReasonMonotone = Record<string, { sign: number; rows_above_background: number; rows_below_background: number; violations: number; cited_side: 'high' | 'low' }>
export type ReasonsBase = {
  adverse_class: string; positive_class: string; threshold: number; top_k: number; s: number; rule: string
  reference: 'approved' | 'population'; reference_pool: { rows: number; of?: number; kind: string }; constraints: Record<string, number>; attribution: ReasonAttribution
}
export type TryReasons = ReasonsBase & { adverse: boolean; p_positive: number; codes: ReasonCode[] | null; method: 'tree' | 'kernel'; note: string | null; reasons_ms: number; additivity_max: number; base_value: number | null; monotone: ReasonMonotone }
export type ReasonStability = {
  rows: number; repeats: number; noise: number; same_set: number | null; same_top1: number | null; same_order: number | null; mean_jaccard: number | null
  decision_flips: number; comparisons: number; seconds: number; holdout_rows: number; computed_at: string; error?: string
  vs_reference?: { coalitions: number; exact: boolean; same_set: number; mean_overlap: number; cited_positive: number | null; what: string }
  method: ReasonAttribution
}
/** G1: counterfactuals ("what would change the decision"). */
export type RecourseAction = 'fixed' | 'any' | 'increase' | 'decrease'
export type RecourseChange = { feature: string; from: unknown; to: unknown; cost: number; kind: 'number' | 'category'; delta: number | null }
export type RecourseOption = { changes: RecourseChange[]; n_changes: number; distance: number; plausibility: number | null; p: number; valid: boolean; text: string }
export type RecourseResult = { adverse: boolean; p: number; options: RecourseOption[] }
export type RecourseCheck = {
  rows: number; adverse: number; with_valid: number; share_valid: number | null; options: number; valid_options: number; mean_options: number | null
  median_distance: number | null; median_changes: number | null; median_plausibility: number | null; fixed_changed: number; out_of_range: number
  wrong_direction: number; features_used: Record<string, number>; seconds: number; actions: Record<string, RecourseAction>
  holdout_rows: number; holdout_adverse: number; threshold: number; adverse_class: string; computed_at: string; examples: RecourseResult[]
}
export type RecourseFeature = { feature: string; action: RecourseAction; detected: RecourseAction; effective: RecourseAction; why: string; source: 'detected' | 'you'; monotone?: string; kind: 'number' | 'category'; range: { min: number; max: number; scale: number } | null }
export type RecourseConfig = {
  version_id: string; model_name: string; options: number; max_changes: number; grid: number; features: RecourseFeature[]; actionable: string[]
  categories: Record<string, string[]>; ranges: Record<string, { min: number; max: number; scale: number }>; reference_rows: number
  threshold: number; adverse_class: string; positive_class: string; s: number; updated_by?: string; updated_at?: string; check: RecourseCheck | null
}
export type RecourseRows = { version_id: string; results: RecourseResult[]; seconds: number; threshold: number; adverse_class: string; positive_class: string; s: number }
export type BatchRecourse = { k: number; rows_searched: number; with_valid: number; seconds: number; rows: { row: number; p: number; options: RecourseOption[] }[] } | { error: string }

export type ReasonsConfig = ReasonsBase & { version_id: string; model_name: string; mapping: ReasonMapping[]; features: string[]; classes: string[]; saved: boolean; updated_by?: string; updated_at?: string; stability: ReasonStability | null }
export type BatchReasons = ReasonsBase & {
  n_adverse: number; adverse_share: number; n_explained: number; seconds: number; setup_seconds: number; rows_per_s: number | null; methods: string[]; notes: string[]
  additivity_max: number; codes_per_row: Record<string, number>; signs_consistent: boolean; monotone: ReasonMonotone; rows_fewer_than_k: number
  top_codes: { code: string; feature: string | null; rows: number; share: number }[]; columns: string[]
  recourse?: BatchRecourse
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
  data_date?: string | null
  id: string; version_id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; created_at: string; finished_at: string | null
  created_by: string; filename: string; n_rows: number | null; progress: number; stage: string; error: string | null
  result: {
    summary: {
      n_rows: number; seconds: number; score_seconds: number; us_per_row: number; missing_columns: string[]; extra_columns: string[]; model: string
      problem_type: ProblemType | 'anomaly' | 'forecast'; predicted?: Record<string, number>; classes?: string[]; positive_class?: string; mean_p?: number
      mean_p_reference?: number | null; flagged_share?: number; threshold?: number; mean_prediction?: number; std_prediction?: number
      histogram?: { edges: number[]; actual: number[]; expected: number[] }
      /** Forecast and anomaly versions. */
      quantile_levels?: number[]; prediction_is?: string; interval?: { lo: number; hi: number; nominal: number; mean_width: number; median_width: number; mean_width_reference?: number }
      flag_rule?: string; expected_share?: number; n_flagged?: number; prediction_symbol?: string; items?: number; seg_segments?: { value: string; n: number; model: string; mean_prediction: number; adverse_share?: number }[]; seg_global_rows?: number; reasons_rows?: number; forecast_rows?: number; known_covariates_source?: string
      /** F10: reason codes for the adverse decisions. */
      reasons?: BatchReasons
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
  fairness: { reports: Record<string, { gaps: Record<string, number>; threshold: number | null; computed_at: string; binning: string; by_group: Record<string, unknown>[] }>; note: string | null
    fair_lending?: { tables: string; surname: string; zip: string | null; n: number; threshold: number; approval_rate: number; air: Record<string, number | null>; below_080: string[]
      lowest: { group: string; air: number; air_ci: [number, number] } | null; proxy_accuracy: number | null; proxy_expected_accuracy: number; max_abs_delta_vs_truth: number | null } | null }
  ensemble: { model: string | null; weights: Record<string, number> | null; stack_level: number | null; version_best: string; dependencies: string[]; leaderboard: Record<string, unknown>[] }
  deployment: { source: { best_model: string; size_bytes: number; n_models: number; latency: Latency }; version: { best_model: string; size_bytes: number; n_models: number; latency: Latency }; tradeoff: VersionResult['tradeoff']; infer_limit: VersionResult['infer_limit'] }
  use: { fields: CardField[] }
  monitoring: { batches: { id: string; created_at: string; n_rows: number; max_psi: number | null; prediction_psi: number | null; counts: DriftReport['counts'] | null }[]; bands: string }
  approval: { stage: Stage; approval: Approval; rules: string; reviews: AuditEntry[]; audit: AuditEntry[] }
  /** F8: the monotone constraints and the served model's ICE check. */
  reasons?: {
    adverse_class: string; positive_class: string; threshold: number; top_k: number; mapping: ReasonMapping[]; constraints: Record<string, number>; reference: string
    stability: ReasonStability | null; method: ReasonAttribution | null; rule: string; monotone_note: string
    last_batch: Pick<BatchReasons, 'n_adverse' | 'rows_per_s' | 'methods' | 'additivity_max' | 'signs_consistent' | 'codes_per_row' | 'top_codes' | 'monotone'> | null
  } | null
  /** G1: counterfactuals: the user's overrides and the stored acceptance check. */
  recourse?: { method: string; overrides: Record<string, RecourseAction>; updated_by?: string | null; check: RecourseCheck | null } | null
  credit_report?: { last: { headline: CreditReport['headline']; on: string; computed_at: string } | null; links: { html: string; md: string } } | null
  /** G5: a routed version's segments and the paired comparison with the global model. */
  segmented?: { segment_column: string; problem_type: string; headline: string; threshold: number | null; routing_rule: string; routes: string[]
    overall: SegCardCmp | null; segments: { value: string; train_rows: number; holdout_rows: number; routed: boolean; why: string; own: SegCardCmp | null }[] } | null
  /** G3: an expected-loss version's holdout check and stress scenarios. */
  expected_loss?: { columns: { pd: string; lgd: string; ead: string }; portfolio: { el: number; realised: number; ratio: number | null; ratio_ci: [number, number]; n: number; defaults: number; within_10pct: boolean }; scenarios: { name: string; delta: number; delta_pct: number | null; as_expected: boolean | null }[] } | null
  /** F19: what the source run's later checks found (F6 split, F7 cost, F12, F14, F15, F16, F17), as summaries. */
  diagnostics?: {
    split: { strategy: string; key: string | null; group_column: string | null; time_column: string | null; cutoff: string | null }
    cost: { kind: string; c_fp: number; c_fn: number; g_tp: number | null } | null
    learning_curve: { verdict: string; reason: string; metric: string; slope: number | null; slope_ci: [number, number] | null; x2_score: number | null; x2_ci: [number, number] | null } | null
    shift: { reference: string; new: string; auc: number; ci: [number, number] | null; verdict: string; top: string | null } | null
    slices: { loss: string; n_tested: number; n_weak: number; alpha: number; top: { label: string; n: number; delta: number; ci: [number, number]; q: number }[] } | null
    annotators: { worst: string | null; fleiss_kappa: number | null; changed_vs_given: number | null; mean_quality: number | null } | null
    doctor: { headline: string; computed_at: string; primary: { id: string; title: string; severity: string; reason: string } | null; secondary: string[]; missing: string[] } | null
  } | null
  /** F11: the scorecard benchmark of the source run. */
  scorecard?: { model: string; served: ScorecardAuc & { model: string }; scorecard: ScorecardAuc; delta_auc: DeltaAuc; n_features: number
    scaling: { pdo: number; base_score: number; base_odds: number }; iv: { feature: string; iv: number; in_model: boolean }[]
    cost?: { scorecard_per_1000: number; served_per_1000: number; c_fp: number; c_fn: number } | null; method: string } | null
  constraints?: {
    columns: { column: string; direction: MonotoneDirection; sign: number; features: string[] | null; status: string | null; monotone: boolean | null
      pd_monotone: boolean | null; curves_violating: number | null; n_curves: number | null; grid_points: number; max_violation: number | null; pd_range: number | null }[]
    compliant_only: boolean; served_model: string | null; monotone: boolean | null; unconstrained_models: string[] | null; method: string
  } | null
  /** F9: quantile regression: levels, pinball per level, coverage raw / calibrated. */
  quantile?: {
    levels: number[]; centre: number; served_model: string; served: 'raw' | 'calibrated'; pinball: number
    per_level: { tau: number; pinball: number; below: number; below_ci: [number | null, number | null] }[]
    intervals: CardInterval[]; raw: CardInterval[]; calibrated: CardInterval[]; crossing_share: number | null
    conformal: { applied: boolean; offsets: Record<string, number> | null; source: string | null }; requested_calibrate: boolean | null; method: string
  } | null
}
export type CardInterval = { lo: number; hi: number; nominal: number; coverage: number; coverage_ci: [number | null, number | null]; mean_width: number; below_lo: number; above_hi: number }

// ---------------------------------------------------------------------- Autopilot (S7)

export type ApRequirement = { metric: string; op: '>=' | '<='; value: number }
export type ApCheck = ApRequirement & { observed: number | null; ok: boolean; gap: number | null; name: string; sym: string }
export type ApLatency = { n?: number; p50_ms?: number; p95_ms?: number; mean_ms?: number; model?: string; method?: string; error?: string }
export type ApDecision = {
  at: string; after_step: string; menu: string[]; action: string; rationale: string; layer: 'layer-1' | 'layer-2'
  source: string; model: string | null; seconds?: number; attempts?: { source: string; ok: boolean; error?: string; seconds: number }[]
  layer1_would?: string; layer2_error?: string; time_limit?: number; cost_usd?: number
  /** F17: the Model Doctor's diagnosis of the best run when this decision was taken. */
  doctor?: { run_id: string; headline: string; primary: { id: string; title: string; severity: string; reason: string } | null; secondary: { id: string; severity: string }[] } | null
  prescription_id?: string
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
/** F5: one attempt of the language-model router (app/llm.py), in route order. */
export type LlmAttempt = { source: string; ok: boolean; error?: string; seconds: number; timeout_s?: number; timed_out?: boolean; skipped?: boolean }
export type VelaAnswer = { answer: string | null; source: string; decider: string; model: string | null; seconds: number; message?: string
  attempts: LlmAttempt[]; facts: string[]; cached: boolean; original_seconds?: number | null; key?: string; prompt_sha256?: string; facts_sha256?: string | null
  guard: { ok: boolean; unsupported: string[]; checked: number } | null }
export type LlmSettings = { enabled: boolean; model: string; sdk_timeout: number; cli_timeout: number; total_timeout: number; cache_ttl: number; use_sdk: boolean; use_cli: boolean }
export type LlmLatency = { n: number; p50_s: number | null; p95_s: number | null; last_s: number | null }
export type LlmStatus = { settings: LlmSettings; route: string[]; labels: Record<string, string>
  sdk: { available: boolean; why: string; base_url: string | null }; cli: { path: string | null }
  latency: Record<string, LlmLatency>; stats: { calls: number; cache_hits: number; shared_in_flight: number; by_source: Record<string, number>; cache_size: number } }

/** F12 · learning curves. */
export type LearningCurveStatus = 'none' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type LearningCurveVerdict = {
  schema: string
  label: 'data-limited' | 'saturated' | 'noisy'
  reason: string
  metric: string
  greater_is_better: boolean
  slope_per_doubling: { value: number; ci: [number, number]; level: number; units: string }
  extrapolation: {
    n_full: number; score_at_n: number
    x2: { n: number; score: number; ci: [number, number]; gain: number }
    x4: { n: number; score: number; ci: [number, number]; gain: number }
  }
  evidence: {
    min_gain: number; min_gain_rule: string; seed_sd: number | null
    observed_last_step: { from_fraction: number; from_n: number; to_n: number; doublings: number; gain: number; gain_per_doubling: number | null; ci: [number, number] } | null
    monotone_within_noise: boolean; n_points: number; fractions: number[]; seeds: number; fit_rmse: number
    power_law: { A: number; b: number; c: number; asymptote: number }
    B: number; se_means: number[]; sd_population_full: number; interval_method: string
  }
}
export type LearningCurvePoint = {
  fraction: number; seed: number | null; n: number; score: number; source: 'run' | 'subsample'
  best_model?: string; n_models?: number; models?: string[]; fit_seconds?: number | null; seconds?: number
  holdout_sha: string; rows_sha: string; classes?: number | null; groups?: number | null
}
export type LearningCurveRequest = { fractions?: number[]; seeds?: number; time_limit?: number | null; min_gain?: number | null; reuse_full?: boolean }
export type LearningCurve = {
  run_id: string
  status: LearningCurveStatus
  defaults: { fractions: number[]; seeds: number; time_limit: number | null; n_full: number | null; fits: number; run_fit_seconds: number | null; max_seconds: number | null; estimate_seconds: number | null }
  schema?: string; error?: string | null
  request?: Required<LearningCurveRequest>
  fits_done?: number; fits_total?: number; current?: { fraction: number; seed: number }
  started_at?: string; finished_at?: string
  metric?: { name: string; greater_is_better: boolean; optimum: number | null }
  problem_type?: string; n_full?: number; holdout_rows?: number; holdout_sha?: string; tuning_rows?: number
  split?: { key: string; sha256: string; strategy: string; stratified: boolean; group_column: string | null; subsample: string }
  nested?: boolean; groups_whole?: boolean | null
  fit_call?: { predictor_kwargs: Record<string, unknown>; fit_kwargs: Record<string, unknown>; notes: string[]; time_limit: number | null }
  points?: LearningCurvePoint[]
  summary?: { fraction: number; n: number; k: number; mean: number; sd: number | null; ci: [number, number] | null; scores: number[] }[]
  fit?: { form: string; A: number; b: number; c: number; sse: number; sign: number; u_optimum: number | null; x: number[] }
  curve?: [number, number][]
  band?: [number, number, number][]
  verdict?: LearningCurveVerdict | null
}

/** F13 · active-learning labelling queue. */
export type ActiveLearningStatus = 'none' | 'queued' | 'running' | 'awaiting_labels' | 'succeeded' | 'failed' | 'cancelled'
export type ALRequest = {
  pool?: 'hide' | 'missing'; hide_share?: number; budget?: number; rounds?: number
  strategy?: 'margin' | 'entropy' | 'least_confident'; diversity?: 'cluster' | 'kcenter' | 'none'
  candidates_factor?: number; compare_random?: boolean; seeds?: number; time_limit?: number | null
  /** F16: pre-filter the pool to a weak slice of the run's latest slice search. */
  slice_id?: string | null
}
export type ALPoint = {
  round: number; n: number; score: number; metrics: Record<string, number>; best_model: string; models: string[]
  fit_seconds: number; seconds: number; holdout_sha: string; rows_sha: string; classes: number; added: number
  answered_by?: Record<string, number>; dataset_id?: string | null; agreement_with_hidden?: number | null
}
export type ALItem = {
  id: string; round: number; row: number; split: string; text: string | null; features: Record<string, unknown>
  suggested: string; p_top: [string, number][]; margin: number; entropy: number; uncertainty: number; rank: number
  cluster: number | null; confidence: number; why: string
  answer?: 'confirm' | 'relabel' | 'skip'; answer_label?: string; answered_by?: string
}
export type ALSelection = { round: number; candidates: number; u_mean_picked?: number; u_mean_pool?: number; n_pool?: number; classes?: string[] }
export type ALArm = { kind: 'active' | 'random' | 'queue'; seed: number; points: ALPoint[]; done: boolean; n_labelled: number; selection?: ALSelection[] }
export type ALGap = { mean: number; sd: number | null; ci: [number, number] | null; level: number; wins: number; ties: number; n_seeds: number; per_seed: number[]; units: string }
export type ALCompareRound = {
  round: number; n: number[]; n_random: number[]
  active: { mean: number; sd: number | null; scores: number[] }; random: { mean: number; sd: number | null; scores: number[] }; gap: ALGap
}
export type ActiveLearningSummary = {
  schema: string; metric: string; greater_is_better: boolean; budget: number; rounds: number; strategy: string; diversity: string
  pool: { source: 'hide' | 'missing'; hide_share: number | null; n_pool: number; n_initial: number }
  labels_used: number[]; queue: { round: number; n_labels: number; score: number }[]
  comparison: { simulated: true; labeller: string; seeds: number; rounds: ALCompareRound[] } | null
  verdict: { label: 'active-better' | 'no-clear-difference' | 'random-better' | 'pending' | 'not-compared'; reason: string
    round?: number; n?: number; gap?: number; ci?: [number, number] | null; wins?: number; n_seeds?: number }
}
export type ActiveLearning = {
  run_id: string
  status: ActiveLearningStatus
  defaults: Required<Omit<ALRequest, 'time_limit'>> & { n_train: number; n_initial: number; fits: number; estimate_seconds: number | null; missing_labels: number }
  schema?: string; error?: string | null; request?: Required<ALRequest>
  fits_done?: number; fits_total?: number | null; current?: { task: string; seed?: number; round?: number }
  metric?: { name: string; greater_is_better: boolean; optimum: number | null }
  classes?: string[]; compare?: boolean; holdout_rows?: number; holdout_sha?: string; holdout_in_pool?: number; tuning_rows?: number
  pool?: { source: 'hide' | 'missing'; hide_share: number | null; n_train: number; n_pool: number; n_initial: number; initial_classes: Record<string, number>; rule: string }
  /** F16: the weak slice the pool is pre-filtered to. */
  slice?: { id: string; label: string; conditions: SliceCondition[]; n_train_in_slice: number; n_pool: number } | null
  embedding?: { text: string[]; numeric: string[]; categorical: string[]; dims: number; seconds: number; method: string }
  split?: { key: string; sha256: string; strategy: string }
  fit_call?: { predictor_kwargs: Record<string, unknown>; fit_kwargs: Record<string, unknown>; notes: string[]; time_limit: number | null }
  arms?: Record<string, ALArm>
  queue?: ALArm & { queue: ALItem[]; queue_round: number | null; open: boolean; submitted_pending: boolean; answered: number
    history?: { round: number; items: number; labelled: number; skipped: number; answered_by: Record<string, number>; dataset_id: string; submitted_at: string }[] }
  versions?: { id: string; name: string; round: number; labelled_this_round: number; labelled_total: number; rows: number; created_at: string }[]
  summary?: ActiveLearningSummary
}

// F14: several annotators per row (GET /api/runs/{id}/annotations)
export type AnnAcc = { accuracy: number; ci: [number, number] | null; correct: number }
export type AnnotatorStat = {
  name: string; n: number; quality: number | null; agreement_with_consensus: number; weight: number | null; kappa_mean: number | null
  worst_class: string | null
  top_confusion: { consensus: string; given: string; n: number; share_of_class: number; of_class_rows: number } | null
  confusion_vs_consensus: number[][]
  truth?: { accuracy: number; ci: [number, number] | null; n: number; top_error?: { true: string; given: string; n: number }; profile?: { name: string; kind: string; accuracy: number; profile: string; pair?: string[]; p_swap?: number } }
}
export type AnnotationsSummary = {
  schema: 'annotations/1'; n_rows: number; n_annotations: number; n_annotators: number
  annotations_per_row: { mean: number; min: number; max: number }; classes: string[]
  probs: { source: 'run-oof' | 'quick-bagged'; model: string; note: string; n_oof: number; n_predicted: number; fit_seconds?: number | null }
  agreement: { fleiss_kappa: { value: number; ci: [number, number]; n_rows: number } | null; cohen_kappa_mean: number | null; raw_agreement: number | null; interpretation: string | null }
  annotators: { name: string; n: number; quality: number | null; agreement_with_consensus: number; weight: number | null; kappa_mean: number | null; worst_class: string | null; truth_accuracy?: number }[]
  worst_annotator: { name: string; quality: number; by: string; min_labels: number } | null
  model_weight: number | null
  crowdlab: { pi: number | null; model_weight: number | null; annotator_weights: Record<string, number> | null; consensus_is_argmax: boolean; weights_from: string }
  consensus: { method: string; disagreements: number; low_quality: number; queue: number; queue_threshold: number; consensus_vs_majority_vote_differ: number; majority_vote_ties: number; adjudicated: number; changed_vs_given: number | null; mean_quality: number }
  truth: null | {
    simulated: true; source: string; n: number
    accuracy: { given: AnnAcc | null; majority_vote: AnnAcc; majority_vote_cleanlab: AnnAcc; consensus: AnnAcc; final: AnnAcc; model: AnnAcc }
    paired: { consensus_right_mv_wrong: number; mv_right_consensus_wrong: number; gain: number; mcnemar_p: number }
    worst_by_truth: { name: string; accuracy: number } | null; worst_identified: boolean
  }
}
export type AnnItem = {
  row: number; fields: Record<string, string | null>; given: string | null
  votes: { annotator: string; label: string }[]
  p_model: { label: string; p: number }[]; posterior: { label: string; p: number }[]
  consensus: string; consensus_quality: number; agreement: number; majority_vote: string; final: string; adjudicated: boolean
}
export type Annotations = {
  run_id: string; status: 'none' | 'computing' | 'ready' | 'failed'; error?: string | null; classes: string[]
  sample_available: boolean; sample_defaults: { n_rows: number; per_row: number; seed: number }
  sample_annotators: { name: string; profile: string }[]
  versions: { id: string; name: string; rows: number; relabelled: number; annotated_rows: number; adjudicated: number; holdout_unchanged: boolean; created_at: string }[]
  source?: 'sample' | 'upload'; n_raw?: number; load?: Record<string, unknown>
  summary?: AnnotationsSummary; annotators?: AnnotatorStat[]
  pairwise?: { a: string; b: string; n: number; kappa: number | null; ci: [number, number] | null; agreement: number | null }[]
  fleiss?: { value: number; ci: [number, number]; n_rows: number; P_bar: number; P_e: number; p: Record<string, number>; raters_per_row: { min: number; max: number; mean: number } } | null
  probs?: AnnotationsSummary['probs'] & { classes: string[]; seconds: number }
  queue?: { items: AnnItem[]; total: number; offset: number; limit: number; include: 'queue' | 'all' }
  show_columns?: string[]; simulated?: boolean
  simulation?: { annotators: { name: string; kind: string; accuracy: number; profile: string; pair?: string[]; p_swap?: number }[]; params: { n_rows: number; per_row: number; seed: number } } | null
}

// ---------------------------------------------------------------- F15 · adversarial validation (shift check)

export type ShiftSource = 'holdout' | 'time' | 'batch'
export type ShiftVerdictLabel = 'no shift' | 'slight' | 'moderate' | 'strong'

/** The small JSON (`shift/1`) on the run payload, read by F17's Model Doctor. */
export interface ShiftSummary {
  schema: 'shift/1'; key: string; source: ShiftSource; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  error?: string | null; finished_at?: string | null; reference?: string; new?: string; n_reference?: number; n_new?: number
  auc?: number | null; ci?: [number, number] | null; level?: number
  null?: { permutation_p: number; permutation_q95: number; refit_aucs: number[] | null; refit_mean: number | null } | null
  verdict?: ShiftVerdictLabel | null; verdict_reason?: string | null
  top_features?: { feature: string; importance: number; ci: [number, number] | null; psi: number | null; significant: boolean }[]
  excluded?: string[]; ess_share?: number | null
  created_at?: string; stage?: string | null
}

export interface ShiftFeature {
  feature: string; kind: string; importance: number; sd: number; n: number; ci: [number, number] | null
  ci_excludes_zero: boolean; significant: boolean; model_features: number; psi: number | null; psi_band: string | null
  mean_expected?: number; mean_actual?: number; std_expected?: number; std_actual?: number; rank: number
}

export interface ShiftAction {
  id: string; kind: 'split-twin' | 'gym-patch' | 'weights-version'; title: string; why: string; available?: boolean
  split_spec?: SplitSpec; group_spec?: SplitSpec | null; endpoint?: string; feature?: string
  patch?: { id: string; title: string; changes: { path: string; from: unknown; to: unknown; label: string }[]; body: Record<string, unknown> }
  column?: string; ess?: number; ess_share?: number
}

export interface ShiftCheck {
  run_id: string; schema: 'shift/1'; key: string; source: ShiftSource; status: ShiftSummary['status']
  error?: string | null; stage?: string | null; created_at?: string; started_at?: string; finished_at?: string | null
  request?: Record<string, unknown>
  sets: Record<'reference' | 'new', { name: string; rows: number; what: string; batch_id?: string; version_id?: string; max_psi?: number | null }>
  features: string[]; kinds: Record<string, string>; excluded: { column: string; reason: string }[]
  extra: { time_column?: string; cutoff?: string; cutoff_source?: string; from?: string; to?: string; undated_rows?: number }
  fit?: { model: string; models: string[]; fit_seconds: number; fit_kwargs: Record<string, unknown>; call: string; num_bag_folds: number; oof: string }
  result?: {
    auc: { value: number; se: number; ci: [number, number]; level: number; z: number; m: number; n: number; s10: number; s01: number; method: string }
    verdict: { label: ShiftVerdictLabel; reason: string; shift: boolean; beyond_refit_null: boolean; rule: string }
    features: ShiftFeature[]
    null: {
      permutation: { B: number; p_value: number; mean: number; sd: number; q025: number; q975: number; q95: number; max: number; test: string; observed: number; hist: number[]; hist_edges: number[] }
      refit: { R: number; aucs: number[]; mean: number; max: number; min: number; seconds: number[]; what: string } | null
    }
    roc: [number, number][]
    score_hist: { reference: number[]; new: number[]; edges: number[] }
    weights: {
      column: string; formula: string; n_ref: number; n_new: number; prior_ratio: number; clip_quantile: number; clip_bounds_raw: [number, number]
      clipped_rows: number; min: number; max: number; median: number; q01: number; q99: number; ess: number; ess_share: number
      ess_unclipped: number; ess_unclipped_share: number; sum_w: number; sum_w2: number; hist_log10: { counts: number[]; edges: number[] }
      balance: { feature: string; mean_reference: number; mean_new: number; mean_reference_weighted: number }[]
    }
    timing: { fit_seconds: number; importance_seconds: number; total_seconds: number }
    importance_method: string
  } | null
  versions?: { id: string; name: string; rows: number; column: string; rows_weighted: number; ess: number; ess_share: number; replaces_sample_weight: unknown; created_at: string }[]
  summary: ShiftSummary | null
  actions: ShiftAction[]
}

export interface ShiftList {
  run_id: string; checks: ShiftSummary[]; latest: ShiftSummary | null
  options: {
    date_columns: { column: string; from: string; to: string }[]; time_column: string | null; cutoff: string | null
    batches: { id: string; version_id: string; filename: string; created_at: string; rows?: number; max_psi?: number | null }[]
    defaults: { null_refits: number; permutations: number; shuffle_sets: number; clip: number; num_bag_folds: number; hyperparameters: Record<string, unknown> }
  }
}

export interface ShiftRequest {
  source: ShiftSource; batch_id?: string; time_column?: string; cutoff?: string; exclude?: string[]; include?: string[]
  null_refits?: number; permutations?: number; shuffle_sets?: number; clip?: number; time_limit?: number; fit_kwargs?: Record<string, unknown>
}

// F16: weak-slice discovery (GET/POST /api/runs/{id}/slices)
export type SliceCondition = { column: string; op: 'in' | 'not_in' | 'range' | 'missing'; values?: string[]; lo?: number | null; hi?: number | null; missing?: boolean; date?: boolean }
export type SliceLoss = { name: 'cost' | 'pinball' | 'absolute_error' | 'squared_error' | 'error' | 'log_loss'; formula: string; unit: string; symbol: string }
export type WeakSlice = {
  id: string; rank: number; label: string; conditions: SliceCondition[]; columns: string[]; order: 1 | 2
  n: number; share: number; mean: number; mean_lo: number; mean_rest: number; sd: number; delta: number; ci: [number, number]; se: number
  t: number | null; df: number; p: number; q: number; ratio: number | null
  parents: { label: string; mean: number | null; n: number | null; q: number | null }[]
  fairness_columns: string[]
}
export type SliceSummary = {
  schema: 'slices/1'; computed_at: string; loss: string; overall: number; n: number; n_tested: number; n_significant: number; n_weak: number
  alpha: number; min_support: number; null_mean: number | null
  top: { id: string; label: string; columns: string[]; n: number; mean: number; delta: number; ci: [number, number]; ratio: number | null; q: number }[]
}
export type SliceRequest = { min_support?: number | null; bins?: number; max_levels?: number; alpha?: number; pairs?: boolean; null_permutations?: number; loss?: SliceLoss['name'] | 'auto'; columns?: string[] | null }
export type SliceSearch = {
  schema: 'slices/1'; run_id: string; model: string; problem_type: string; eval_metric: string; label: string; threshold: number | null
  n: number; loss: SliceLoss; overall: number; overall_sd: number
  params: { min_support: number; min_support_share: number; min_support_floor: number; bins: number; max_levels: number; alpha: number; pairs: boolean; null_permutations: number }
  columns: { column: string; how: string; type: string; counts: number[]; levels: string[]; conditions: SliceCondition[] }[]
  skipped: { column: string; reason: string }[]
  tested: { total: number; single: number; pairs: number; features: number; feature_pairs: number }
  slices: WeakSlice[]; n_significant: number; n_weak?: number; n_explained_by_parent: number
  null: { permutations: number; weak_found: number[]; mean: number | null; max: number | null } | null
  holdout_sha: string; computed_at: string; seconds: number; request: SliceRequest; summary: SliceSummary
}
export type SliceRows = {
  run_id: string; slice: WeakSlice; loss: SliceLoss; total: number; offset: number
  rows: { row: number; y_true: unknown; y_pred: unknown; loss: number; p: Record<string, number> | null; features: Record<string, unknown> }[]
}
export type SliceShap = {
  run_id: string; slice: WeakSlice; model: string; target: string | null; output: 'probability' | 'prediction'
  rows_slice: number[]; rows_rest: number[]; base_value: number; fx_slice: number | null; fx_rest: number | null
  features: { feature: string; phi_slice: number; phi_rest: number; abs_slice: number; abs_rest: number; diff: number; in_slice_definition: boolean }[]
  per_row: { row: number; in_slice: boolean; fx: number; y_true: unknown; cached: boolean }[]
  background_size: number; nsamples: number; seconds: number; method: string
}
export type SlicePool = { run_id: string; slice_id: string; label: string; conditions: SliceCondition[]; train_rows: number; in_slice: number; labelled_in_slice: number }

/** F17 · Model Doctor (GET /api/runs/{id}/doctor, schema doctor/1). */
export type DoctorStatus = 'finding' | 'clear' | 'no-evidence' | 'missing' | 'n/a'
export type DoctorSeverity = 'critical' | 'major' | 'minor'
export type DoctorAction = {
  kind: 'gym_patch' | 'split_twin' | 'run_check' | 'labelling_queue' | 'shift_weights' | 'register' | 'inbox' | 'open_slice' | 'info' | 'retrain'
  title: string; why?: string; endpoint?: string; method?: 'GET' | 'POST'; body?: Record<string, unknown>; check?: string
  run_id?: string; slice_id?: string; version_id?: string
  patch?: { id: string; title: string; changes: { path: string; from: unknown; to: unknown; label: string }[]; body: Record<string, unknown> }
}
export type DoctorCheck = {
  id: string; title: string; status: DoctorStatus; severity: DoctorSeverity | null; reason: string
  evidence: Record<string, unknown>; prescription: DoctorAction | null; offer: DoctorAction | null; formula: string | null
  source: string | null; rule: string | null
}
export type Doctor = {
  schema: 'doctor/1'; procedure: string; run_id: string; computed_at: string; problem_type: string
  primary: DoctorCheck | null; secondary: DoctorCheck[]; checks: DoctorCheck[]; offers: DoctorAction[]; headline: string
  precedence: string[]; severity_order: DoctorSeverity[]; rules: Record<string, string>
  inputs: { latency_budget_ms: number | null; measured_errors: string[]; vela_errors: string[] }
}
export type DoctorSummary = {
  schema: 'doctor/1'; computed_at: string; latency_budget_ms: number | null; headline: string; missing: string[]; no_evidence: string[]
  primary: { id: string; title: string; severity: DoctorSeverity; reason: string; prescription: { kind: string; title: string } | null } | null
  secondary: { id: string; title: string; severity: DoctorSeverity; reason: string }[]
}

// --------------------------------------------------------------------------- F18 · monitoring over time

export type MonitoringThresholds = { feature_psi: number; feature_psi_watch: number; prediction_psi: number; adversarial_auc: number; auc_drop: number; calibration_z: number; min_labelled: number }
export type MonitoringConfig = { thresholds: MonitoringThresholds; challenger_version_id: string | null; id_column: string | null; label_column: string | null; retrain_window_months: number; updated_by: string | null; updated_at: string | null }
export type MonitoringPerf = {
  n: number; positives: number; observed_rate: number; observed_ci: [number, number]; mean_p: number; calibration_z: number | null
  log_loss: number; brier: number; auc: number | null; auc_ci: [number, number] | null; auc_se: number | null
  threshold: number; flagged_share: number; recall: number | null; precision: number | null; cost_per_1000?: number | null
}
export type MonitoringAlert = { signal: string; kind: 'covariate' | 'score' | 'performance'; severity: string; value: number; threshold: number; rule: string; text: string; features?: string[]; ci?: [number, number]; baseline?: number; lag_days?: number | null; batch_id?: string; period?: string; visible_on?: string }
export type PairedAuc = { auc_champion: number; auc_challenger: number; delta: number; se: number; ci: [number, number]; z: number | null; p_value: number | null; corr: number | null; m: number; n: number; method: string }
export type PairedLoss = { delta: number; ci: [number, number]; se: number; B: number; n: number; method: string }
export type ShadowSummary = { version_id: string; label: string | null; model?: string; error?: string; n: number; psi: number; band: string; ks: number; spearman: number; agreement: number; kappa: number | null
  mean_p_champion: number; mean_p_challenger: number; flagged_champion: number; flagged_challenger: number; threshold_champion: number; threshold_challenger: number; disagree: { champion_only: number; challenger_only: number } }
export type MonitoringPoint = {
  batch_id: string; filename: string; data_date: string | null; period: string | null; scored_at: string | null; n_rows: number
  max_psi: number | null; features: { feature: string; psi: number | null; band: string }[]; prediction_psi: number | null; prediction_ks: number | null
  mean_p: number | null; mean_p_reference: number | null; flagged_share: number | null; score_quantiles: Record<'q10' | 'q25' | 'q50' | 'q75' | 'q90', number> | null; mean_prediction: number | null
  adversarial: { status: string; auc: number | null; ci: [number, number] | null; verdict: string | null; top_features: { feature: string; significant: boolean }[] | null } | null
  performance: MonitoringPerf | null; labels_date: string | null; label_lag_days: number | null; label_coverage: number | null
  shadow: ShadowSummary | null; paired: { n: number; auc?: PairedAuc; log_loss: PairedLoss; challenger_performance?: MonitoringPerf } | null
  alerts: MonitoringAlert[]
}
export type FirstAlert = { batch_id: string; data_date: string | null; period: string | null; visible_on: string | null; signals: string[]; text: string } | null
export type MonitoringState = {
  schema: 'monitoring/1'; version_id: string; label: string; stage: Stage; model: string; run_id: string; problem_type: string | null
  config: MonitoringConfig; rules: Record<string, string>; baseline: (MonitoringPerf & { source: string }) | null
  points: MonitoringPoint[]; running: { batch_id: string; status: string; stage: string; data_date: string | null }[]
  alerts: { first_covariate: FirstAlert; first_score: FirstAlert; first_performance: FirstAlert; active: MonitoringAlert[]
    latest_labelled: { batch_id: string; period: string; data_date: string; labels_date: string; label_lag_days: number } | null; unlabelled_since: string | null; n_points: number; n_labelled: number }
  challenger: { version_id: string; label: string; stage: Stage; approval: string; run_id: string; batches: number; agreement_mean: number | null; psi_mean: number | null; spearman_mean: number | null
    pooled: { n: number; auc?: PairedAuc; log_loss: PairedLoss; batches: string[] } | null; decision: string; promote: { review: string; transition: string } } | null
  stream: { dataset: string; fed_months: number[]; next_month: number | null } | null
  retrain: { endpoint: string; suggested: boolean }
  formulas: Record<string, string>
}
export type RetrainProposal = {
  version_id: string; label: string; champion_run_id: string; window_months: number; window: { start: string | null; end: string | null }
  batches: { batch_id: string; filename: string; data_date: string; rows: number; label_date: string }[]; rows: number; rows_window: number; rows_reference: number
  include_reference: boolean; body: Record<string, unknown>; split_note: string; why: string[]; ready: boolean; blocked: string | null; note: string
}
export type StreamSpec = { dataset: string; months: { month: number; label: string; start: string; end: string; rows: number; labels_arrive: string; covariate_drift: Record<string, number>; concept_change: boolean }[]
  rows_per_month: number; drift_month: number; label_lag_months: number; label_rule: string; covariate_drift: Record<string, unknown>; concept_change: Record<string, unknown>; id_column: string; date_column: string }
export type StreamFeedResult = { month: number; period: string; batch: BatchJob; data_date: string; labels_delivered: { month: number; batch_id: string; label_date: string; lag_days: number; n: number }[]; labels_pending: unknown[]; labels_due: { month: number; arrive: string } }

/** G2 · credit-risk model report (GET /registry/versions/{id}/credit-report). */
export type CreditRank = {
  n: number; bads: number; bad_rate: number | null; bad_rate_ci: [number, number] | null; mean_p: number | null
  auc?: number; auc_ci?: [number, number]; gini?: number; gini_ci?: [number, number]; ks?: number; ks_ci?: [number, number] | null
  ks_at_p?: number | null; ks_at_score?: number | null; small?: boolean; note?: string
}
export type CreditPeriod = CreditRank & { period: string; source: 'holdout' | 'batch'; batches?: string[] }
export type CreditBand = {
  band: string; n: number; share: number | null; bads: number; bad_rate: number | null; bad_rate_ci: [number, number] | null; mean_p: number | null
  cum_share: number | null; cum_capture: number | null; lift: number | null; cum_lift: number | null; decision: 'approve' | 'decline' | 'mixed' | null
}
export type CreditDecile = { decile: number; n: number; bads: number; bad_rate: number | null; bad_rate_ci: [number, number] | null; mean_p: number | null; lift: number | null; cum_capture: number | null }
export type CreditDetected = { column: string; confidence: number; evidence: string[] } | null
export type CreditCohort = { cohort: string; n: number; observed_mob: number; curve: { mob: number; cum_bad_rate: number; defaults: number }[]; bad_rate_12: number | null }
export type CreditReport = {
  schema: string; version_id: string; label: string; model: string; positive_class: string; threshold: number | null; title: string
  detected: { date: CreditDetected; id: CreditDetected; date_candidates: string[]; label: string }
  on: 'holdout' | 'batches'
  overall: { holdout: CreditRank; batches?: CreditRank }
  periods: CreditPeriod[]
  pending: { period: string; batch_id: string; n: number; filename: string }[]
  bands: { rows: CreditBand[]; n: number; bads: number; bad_rate: number; threshold_score: number | null; scale: { pdo: number; base_score: number; base_odds: number; factor: number; offset: number } }
  deciles: CreditDecile[]
  stability: {
    score_psi: { period: string; psi: number | null; n: number | null; mean_p: number | null }[]
    csi: { periods: string[]; features: { feature: string; csi: (number | null)[]; max: number | null; first_shift: string | null; stays_flagged: boolean }[]; shift: number; watch: number }
    first_flag: { feature: string; period: string; stays_flagged: boolean } | null
    flag_bins: { feature: string; period: string; csi: number; bins: { label: string; expected: number; actual: number; term: number }[] } | null
    reference: string
  }
  vintage: null | {
    cohort_by: 'month' | 'quarter'; cohorts: CreditCohort[]; max_mob: number; loans: number; as_of: string | null
    source: { kind: 'sample' | 'uploaded'; file?: string; by?: string; note?: string; filtered_to?: string; mapping: { open: string | null; default: string | null; until: string | null; id: string | null; evidence: Record<string, string> } }
  }
  formulas: Record<string, string>
  headline: { gini: number | null; gini_ci: [number, number] | null; ks: number | null; bad_rate: number | null; months: number; first_csi_flag: string | null; cohorts: number }
}

/** G4 · GET /runs/{id}/fair-lending: BISG proxy groups, adverse impact ratios with CIs, the proxy's error. */
export type FairLendingCol = { column: string; source: 'detected' | 'set by you'; evidence: string[]; note?: string } | null
export type FairLendingGroup = {
  group: string; n_eff: number; separability: number; reference: boolean; judged: boolean; flag: string | null
  approval: number; approval_ci: [number, number]; air: number | null; air_ci: [number, number]; diff: number; diff_ci: [number, number]
  plain_approval: number; plain_air: number | null; plain_diff: number
}
export type FairLending = {
  run_id: string; label: string; ready: boolean; reason?: string
  detected: { surname: FairLendingCol; zip: FairLendingCol; truth: FairLendingCol }
  candidates: { surname: string[]; zip: string[]; truth: string[] }
  tables: { source: 'synthetic' | 'census'; label: string; surnames: number; zips: number }
  groups: string[]; group_labels: Record<string, string>; reference: string
  n: number; threshold: number; positive_class: string
  favourable: { adverse_label: boolean; approved_when: string; approval_rate: number }
  coverage: { surname: number; zip: number | null }
  air: FairLendingGroup[]
  metricframe: { metrics: string[]; overall: Record<string, number | null>; by_group: ({ group: string } & Record<string, number | null>)[]; difference: Record<string, number | null> }
  proxy: {
    expected_accuracy: number; estimated_shares: Record<string, number>; accuracy?: number; brier?: number
    by_group?: { group: string; n_true: number; true_share: number; estimated_share: number; recall: number | null; precision: number | null; mean_p_true: number | null }[]
    confusion?: number[][]
  }
  limits: { min_eff_n: number; min_separability: number; bootstrap: number }
  truth?: {
    column: string; max_abs_delta: number | null; max_abs_plain_delta: number | null; within_005: boolean | null
    comparison: { group: string; judged: boolean; air: number | null; plain_air: number | null; true_air: number | null; true_air_ci: [number, number]; n_true: number; delta: number | null; plain_delta: number | null }[]
  }
}
