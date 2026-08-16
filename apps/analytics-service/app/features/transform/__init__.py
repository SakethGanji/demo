"""Transformation pipelines (ROADMAP §19–§21).

Saved, declarative pipelines over one logical sheet: a discriminated union of
steps compiled to a single DuckDB statement (one CTE per step), run through the
job worker, materialized as a ``transform_output`` artifact, auto-profiled
against the source, and publishable as a new dataset or version.
"""
