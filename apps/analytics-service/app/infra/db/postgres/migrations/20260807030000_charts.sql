-- 20260807030000_charts
-- Wave 2 §14: thin chart definitions. A chart renders an existing data
-- source — a saved analytics definition (pivot/aggregate/...) or a saved
-- view — and owns NO query logic; config is frontend-owned encoding.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS chart_definitions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id     UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    definition_id  UUID REFERENCES analytics_definitions(id) ON DELETE CASCADE,
    view_id        UUID REFERENCES dataset_views(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    description    TEXT,
    chart_type     TEXT NOT NULL
                       CHECK (chart_type IN ('bar', 'line', 'area', 'scatter',
                                             'pie', 'table', 'kpi')),
    config         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, name),
    -- Exactly one data source.
    CHECK ((definition_id IS NULL) <> (view_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_chart_definitions_dataset ON chart_definitions (dataset_id);

-- migrate:down
SET search_path TO "accelerator";

DROP TABLE IF EXISTS chart_definitions;
