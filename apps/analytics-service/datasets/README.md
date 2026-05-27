# PromptLab datasets

Local parquet files consumed by the `/evaluate` endpoint. Each dataset has two columns:

- `input`: JSON-encoded object whose keys are the template variables substituted into the prompt (e.g. `{"text": "I love this"}`).
- `expected`: the gold-standard string the prediction is scored against (e.g. `"positive"`).

## sentiment_v1.parquet

Ten rows of short consumer-style sentences labeled `positive`, `negative`, or `neutral`. Intended as a smoke-test dataset for the sentiment-classification prompt template.
