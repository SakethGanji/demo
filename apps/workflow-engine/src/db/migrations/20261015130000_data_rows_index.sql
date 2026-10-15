-- Phase 7 (BACKEND-DESIGN §2.2 M4): make data_table_rows queryable.
CREATE INDEX IF NOT EXISTS idx_data_table_rows_gin ON data_table_rows USING GIN (data jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_data_table_rows_table_created ON data_table_rows (table_id, id DESC);

-- migrate:down
DROP INDEX IF EXISTS idx_data_table_rows_table_created;
DROP INDEX IF EXISTS idx_data_table_rows_gin;
