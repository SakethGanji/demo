-- 20260804030000_quality
-- Phase 2 (trust): quality rules, durable validation runs, promotion gates.
-- jobs stay the operational execution record; validation_runs/_rule_results
-- are the durable product result (queryable, retained, gate-able).

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- Quality rules
-- ============================================================
-- A rule targets a dataset, a sheet, a column, or a cross-sheet relationship.
-- Selectors use sheet_key / normalized column names (stable across versions).
-- All rule types compile to single DuckDB queries at validation time.

CREATE TABLE IF NOT EXISTS quality_rules (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id       UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    description      TEXT,
    scope_type       TEXT NOT NULL
                         CHECK (scope_type IN ('dataset', 'sheet', 'column', 'cross_sheet')),
    sheet_selector   TEXT,   -- sheet_key the rule targets (NULL for dataset scope)
    column_selector  TEXT,   -- normalized column name (column/cross_sheet scope)
    rule_type        TEXT NOT NULL
                         CHECK (rule_type IN ('sheet_exists', 'row_count_min', 'not_null',
                                              'unique', 'accepted_values', 'range',
                                              'regex_match', 'foreign_key')),
    parameters       JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity         TEXT NOT NULL DEFAULT 'error' CHECK (severity IN ('error', 'warning')),
    enabled          BOOLEAN NOT NULL DEFAULT true,
    created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_quality_rules_dataset ON quality_rules (dataset_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_rules_dataset_name
    ON quality_rules (dataset_id, name);

-- ============================================================
-- Validation runs (durable results; jobs track execution)
-- ============================================================

CREATE TABLE IF NOT EXISTS validation_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id          UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version_id  UUID NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    job_id              UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed')),
    rules_total         INTEGER,
    rules_passed        INTEGER,
    rules_failed        INTEGER,
    error_failures      INTEGER,   -- failed rules with severity=error
    warning_failures    INTEGER,
    triggered_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS ix_validation_runs_version
    ON validation_runs (dataset_version_id, started_at DESC);

CREATE TABLE IF NOT EXISTS validation_rule_results (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    validation_run_id  UUID NOT NULL REFERENCES validation_runs(id) ON DELETE CASCADE,
    rule_id            UUID REFERENCES quality_rules(id) ON DELETE SET NULL,
    -- Rule snapshot: results stay readable after the rule is edited/deleted.
    rule_name          TEXT NOT NULL,
    rule_type          TEXT NOT NULL,
    scope_type         TEXT NOT NULL,
    sheet_selector     TEXT,
    column_selector    TEXT,
    severity           TEXT NOT NULL,
    status             TEXT NOT NULL
                           CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
    failure_count      BIGINT,
    message            TEXT,
    sample_failures    JSONB
);
CREATE INDEX IF NOT EXISTS ix_validation_rule_results_run
    ON validation_rule_results (validation_run_id);

-- ============================================================
-- Jobs: allow validation + saved-analytics runs
-- ============================================================

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics'));

-- migrate:down

SET search_path TO "accelerator";

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform'));
DROP TABLE IF EXISTS validation_rule_results;
DROP TABLE IF EXISTS validation_runs;
DROP TABLE IF EXISTS quality_rules;
