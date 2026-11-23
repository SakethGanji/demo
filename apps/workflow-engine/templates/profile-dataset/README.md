# Profile a dataset

Profiles one of the project's datasets with the analytics service and turns the result into a one-line summary:
row and column counts, duplicate rows and the columns that have missing values.

1. Import a CSV, Parquet or Excel file on the project's Datasets page.
2. Open the **Profile dataset** step and pick it under **Dataset** (the step stores the dataset's name).
3. Run the workflow. **Summary** returns `{ok, rows, columns, duplicate_rows, columns_with_missing_values, summary}`.

If the dataset is missing (renamed or deleted) or the analytics service is down, the run still finishes:
**Explain failure** returns `{ok: false, error}` with the reason.
