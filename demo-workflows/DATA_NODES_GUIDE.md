# Data Analysis Nodes — User Guide

## Prerequisites

The analytics service must be running alongside the workflow engine:

```bash
cd apps/analytics-service && python3 main.py    # port 8001
cd apps/workflow-engine && python3 -m src.main   # port 8000
cd apps/workflow-studio && npm run dev            # port 5173
```

---

## Supported File Formats

All data nodes accept: `.csv`, `.tsv`, `.xlsx`, `.xls`, `.parquet`

---

## Node Reference

### 1. Report Node

Generates a full HTML report from any tabular data — column stats, distributions, top values, correlations, data preview.

**How to use in UI:**

1. Add a **Start** node (trigger)
2. Add a **Report** node, connect Start → Report
3. Open Report node (double-click), configure:
   - **Source Type** → `From File`
   - **File Path** → absolute path to your file (e.g. `/home/you/data/sales.xlsx`)
   - **Title** → your report title
   - Toggles: Overview, Column Stats, Distributions, Top Values, Correlations, Data Preview (all ON by default)
   - **Preview Rows** → how many rows to show in the data preview table (default: 10)
   - **Top N** → how many top frequent values per column (default: 10)
   - **Output Format** → `html` (default), `markdown`, or `pdf`
4. Add an **Output** node, connect Report → Output
5. Open Output node, configure:
   - **Source** → `From Input`
   - **Format** → `html`
   - **Content Field** → `html`
6. Run the workflow. The report appears in the bottom panel (UI tab).

**Using with input data instead of file:**
- Set **Source Type** → `From Previous Node`
- Set **Data Field** → the field name containing your array of objects (default: `data`)
- Connect any node that outputs `{data: [{...}, {...}, ...]}` into the Report node

---

### 2. Profile Node

Profiles every column in your dataset — types, nulls, min/max/mean/median, unique counts, histograms, correlations.

**How to use in UI:**

1. Add **Start** → **Profile**
2. Open Profile node, configure:
   - **Source Type** → `From File`
   - **File Path** → path to your file
   - **Columns** → leave empty for all columns, or comma-separated list (e.g. `Revenue,Profit,Region`)
   - **Include Histograms** → ON (default)
   - **Include Correlations** → OFF by default, turn ON for numeric correlation matrix
   - **Top N** → frequent values to show per column (default: 10)
3. Run. Results appear in the node's output panel (click the node, check Output tab).

**Output structure:**
```
row_count, column_count, duplicate_row_count, memory_usage_bytes
columns: [
  {
    name, dtype, non_null_count, null_count, null_percentage,
    unique_count, unique_percentage,
    mean, std, min, max, median,     (numeric only)
    histogram: {bins, counts},        (if enabled)
    top_values: [{value, count}, ...]
  }
]
correlations: {col1: {col2: 0.85, ...}}  (if enabled)
```

---

### 3. Aggregate Node

Groups data by one or more columns and computes aggregations — sum, mean, count, min, max, median, std, nunique, first, last.

**How to use in UI:**

1. Add **Start** → **Aggregate** → **Output**
2. Open Aggregate node, configure:
   - **Source Type** → `From File`
   - **File Path** → path to your file
   - **Group By** → comma-separated column names (e.g. `Region` or `Product,Channel`)
   - **Aggregations** → click "Add Item" for each metric:
     - **Column** → which column to aggregate (e.g. `Revenue`)
     - **Function** → `sum`, `mean`, `median`, `count`, `min`, `max`, `std`, `nunique`, `first`, `last`
     - **Alias** → output column name (e.g. `Total Revenue`). Leave empty for auto-naming (`Revenue_sum`)
   - **Sort By** → column to sort results by (e.g. `Total Revenue`)
   - **Sort Order** → `desc` or `asc`
   - **Limit** → max number of groups to return (leave empty for all)
   - **Filter Expression** → optional pandas-style filter applied before aggregation (e.g. `Revenue > 1000`)
3. Open Output node:
   - **Source** → `From Input`
   - **Format** → `table`
4. Run. Table appears in the bottom panel.

**Saving results to file:**
- Set **Output Path** → e.g. `/tmp/aggregated.csv`
- Set **Output Format** → `csv`, `xlsx`, or `parquet`
- The file is written AND the data still flows to the next node.

**Multi-group example:**
- Group By: `Product,Region`
- This creates one row per unique Product+Region combination.

---

### 4. Sample Node

Extracts a subset of rows using various sampling strategies.

**How to use in UI:**

1. Add **Start** → **Sample** → **Output**
2. Open Sample node, configure:
   - **Source Type** → `From File`
   - **File Path** → path to your file
   - **Method** → pick one:
     - `random` — pure random sample
     - `stratified` — proportional sample per group (set **Stratify Column**)
     - `systematic` — every Nth row
     - `cluster` — select entire groups (set **Cluster Column** + **Num Clusters**)
     - `first_n` — first N rows
     - `last_n` — last N rows
   - **Sample Size** → number of rows (e.g. `50`)
   - **Sample Fraction** → alternative to size, 0-1 (e.g. `0.1` for 10%)
   - **Seed** → random seed for reproducibility (e.g. `42`)
   - **Replace** → sample with replacement (default: OFF)
3. Open Output node:
   - **Source** → `From Input`
   - **Format** → `table`
4. Run.

**Stratified sampling:**
- Set Method → `stratified`
- Set Stratify Column → e.g. `Department`
- This ensures each Department is proportionally represented in the sample.

**Multi-round sampling:**
- Set **Rounds** → e.g. `3`
- Set **Round Sample Size** → rows per round
- Useful for bootstrap-style repeated sampling.

---

### 5. Output Node

Displays data in the UI — tables, HTML, markdown, or PDF. Can read from the previous node or directly from a file.

**Mode 1 — Display a file directly:**

1. Add **Start** → **Output**
2. Open Output node:
   - **Source** → `From File`
   - **File Path** → path to your file
3. Format is auto-detected from extension:
   - `.csv`, `.xlsx`, `.tsv`, `.parquet` → sortable table
   - `.html` → rendered HTML
   - `.md` → rendered markdown
   - `.pdf` → embedded PDF viewer

**Mode 2 — Display output from previous node:**

1. Connect any data node → **Output**
2. Open Output node:
   - **Source** → `From Input`
   - **Format** → pick one:
     - `table` — renders `data` field as sortable table
     - `html` — renders HTML content
     - `markdown` — renders markdown
     - `pdf` — renders base64-encoded PDF
   - **Content Field** → which field contains the content (auto-detected per format, usually `html`, `markdown`, `pdf_base64`, or `data`)

---

## Common Patterns

### Pattern 1: Quick File Preview
```
Start → Output (file mode, point at your .xlsx)
```
Fastest way to see what's in a file.

### Pattern 2: Full Data Report
```
Start → Report (file mode) → Output (html)
```
One-click executive dashboard from any file.

### Pattern 3: Aggregate then Visualize
```
Start → Aggregate (file mode, group by X, sum Y) → Output (table)
```
Answer specific business questions.

### Pattern 4: Aggregate then Report
```
Start → Aggregate → Report (input mode, dataField="data") → Output (html)
```
Aggregate first, then get a visual report of the aggregated results.

### Pattern 5: Sample then Profile
```
Start → Sample (file mode, 100 rows) → Profile (input mode, dataField="data")
```
For large files — sample first, then profile the sample.

### Pattern 6: Profile → Conditional → Different Reports
```
Start → Profile → If (check some condition) → Report A / Report B
```
Branch your pipeline based on data characteristics.

---

## Tips

- **File paths must be absolute** (e.g. `/home/user/data/file.xlsx`, not `~/data/file.xlsx`)
- **Column names are case-sensitive** — check your file headers
- **Large files** work fine — DuckDB handles millions of rows efficiently. But Report/Profile output can be large HTML, so keep Preview Rows reasonable (10-20)
- **Chaining nodes**: when the previous node outputs `{data: [...]}`, set Data Field to `data` in the next node
- **Output node auto-detects** the render format when reading from a file — just set the file path
- **Re-run after config changes** — the workflow doesn't auto-run when you change parameters
- **Check the Output tab** in the node details panel (double-click a node after running) to see raw JSON output
