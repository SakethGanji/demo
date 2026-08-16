"""Individual sampling method implementations."""

from __future__ import annotations

import os
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident


def duckdb_set_seed(conn: duckdb.DuckDBPyConnection, seed: int | None) -> None:
    """Set DuckDB random seed (normalised to 0-1 range)."""
    if seed is not None:
        conn.execute(f"SELECT setseed({(abs(seed) % 2147483647) / 2147483647.0})")


def resolve_target(
    row_count: int,
    n: int | None,
    frac: float | None,
    replace: bool = False,
    method_name: str = "sampling",
) -> int:
    """Resolve sample_size / sample_fraction to an integer target count."""
    if n is not None:
        return n if replace else min(n, row_count)
    if frac is not None:
        effective_frac = frac if replace else min(frac, 1.0)
        return int(row_count * effective_frac)
    raise HTTPException(400, f"Either sample_size or sample_fraction required for {method_name}")


def sample_random(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    n: int | None,
    frac: float | None,
    seed: int | None,
    replace: bool = False,
) -> pd.DataFrame:
    """Random sample, with or without replacement."""
    row_count: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    target = resolve_target(row_count, n, frac, replace, "random")
    duckdb_set_seed(conn, seed)

    if not replace:
        return conn.execute(f"SELECT * FROM {table} ORDER BY random() LIMIT {target}").fetchdf()

    # With replacement: generate random row picks
    return conn.execute(f"""
        WITH numbered AS (
            SELECT *, ROW_NUMBER() OVER () AS _rn FROM {table}
        ),
        picks AS (
            SELECT (floor(random() * {row_count})::INT + 1) AS _rid
            FROM generate_series(1, {target})
        )
        SELECT numbered.* EXCLUDE (_rn)
        FROM picks JOIN numbered ON numbered._rn = picks._rid
    """).fetchdf()


def sample_stratified(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    stratify_column: str,
    n: int | None,
    frac: float | None,
    class_targets: dict[str, int] | None,
    seed: int | None,
    replace: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Stratified sample. Returns (sampled_df, per_class_counts)."""
    qcol = quote_ident(stratify_column)
    duckdb_set_seed(conn, seed)

    if class_targets:
        parts = []
        actual_counts: dict[str, int] = {}
        for class_val, count in class_targets.items():
            available = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE CAST({qcol} AS VARCHAR) = ?",
                [class_val],
            ).fetchone()[0]
            effective = count if replace else min(count, available)
            if not replace:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE CAST({qcol} AS VARCHAR) = ? ORDER BY random() LIMIT {int(effective)}",
                    [class_val],
                ).fetchdf()
            else:
                rows = conn.execute(f"""
                    WITH src AS (
                        SELECT *, ROW_NUMBER() OVER (ORDER BY random()) AS _rn
                        FROM {table} WHERE CAST({qcol} AS VARCHAR) = ?
                    ),
                    picks AS (
                        SELECT (floor(random() * {available})::INT + 1) AS _rid
                        FROM generate_series(1, {count})
                    )
                    SELECT src.* EXCLUDE (_rn) FROM picks JOIN src ON src._rn = picks._rid
                """, [class_val]).fetchdf()
            parts.append(rows)
            actual_counts[class_val] = len(rows)
        result = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        return result, actual_counts

    if frac is not None:
        effective_frac = frac if replace else min(frac, 1.0)
        result = conn.execute(f"""
            SELECT * EXCLUDE (_rn, _gsize) FROM (
                SELECT *,
                    ROW_NUMBER() OVER (PARTITION BY {qcol} ORDER BY random()) AS _rn,
                    COUNT(*) OVER (PARTITION BY {qcol}) AS _gsize
                FROM {table}
            ) sub
            WHERE _rn <= GREATEST(1, CEIL(_gsize * {effective_frac}))
        """).fetchdf()
        counts = result[stratify_column].astype(str).value_counts().to_dict() if not result.empty else {}
        return result, counts

    if n is not None:
        result = conn.execute(f"""
            SELECT * EXCLUDE (_rn, _glimit) FROM (
                SELECT t.*,
                    ROW_NUMBER() OVER (PARTITION BY t.{qcol} ORDER BY random()) AS _rn,
                    GREATEST(1, CEIL({n} * gs._gfrac))::INT AS _glimit
                FROM {table} t
                JOIN (
                    SELECT {qcol} AS _gkey,
                           COUNT(*) * 1.0 / (SELECT COUNT(*) FROM {table}) AS _gfrac
                    FROM {table} GROUP BY {qcol}
                ) gs ON t.{qcol} = gs._gkey
            ) sub
            WHERE _rn <= _glimit
        """).fetchdf()
        counts = result[stratify_column].astype(str).value_counts().to_dict() if not result.empty else {}
        return result, counts

    raise HTTPException(400, "Stratified sampling requires sample_size, sample_fraction, or class_targets")


def sample_systematic(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    n: int | None,
    frac: float | None,
) -> pd.DataFrame:
    """Systematic (every-nth-row) sample."""
    row_count: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    target = resolve_target(row_count, n, frac, False, "systematic")
    step = max(1, row_count // target) if target > 0 else row_count
    return conn.execute(f"""
        SELECT * EXCLUDE (_rn) FROM (
            SELECT *, ROW_NUMBER() OVER () AS _rn FROM {table}
        ) sub
        WHERE (_rn - 1) % {step} = 0
        LIMIT {int(target)}
    """).fetchdf()


def sample_cluster(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    cluster_column: str,
    num_clusters: int | None,
    seed: int | None,
) -> pd.DataFrame:
    """Cluster sample: select random groups and return all rows in those groups."""
    qcol = quote_ident(cluster_column)
    all_clusters = [r[0] for r in conn.execute(f"SELECT DISTINCT {qcol} FROM {table}").fetchall()]
    if num_clusters is None or num_clusters >= len(all_clusters):
        selected = all_clusters
    else:
        import random as rng
        if seed is not None:
            rng.seed(seed)
        selected = rng.sample(all_clusters, num_clusters)
    placeholders = ", ".join(["?"] * len(selected))
    return conn.execute(
        f"SELECT * FROM {table} WHERE {qcol} IN ({placeholders})", selected,
    ).fetchdf()


def sample_weighted(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    weight_column: str,
    n: int | None,
    frac: float | None,
    seed: int | None,
    replace: bool = False,
) -> pd.DataFrame:
    """Weighted random sample. Higher weight = more likely to be picked."""
    qw = quote_ident(weight_column)
    row_count: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    target = resolve_target(row_count, n, frac, replace, "weighted")
    duckdb_set_seed(conn, seed)

    # Use exponential sort trick: ORDER BY random() ^ (1/weight) DESC
    # This gives correct weighted sampling without replacement
    return conn.execute(f"""
        SELECT * EXCLUDE (_wscore) FROM (
            SELECT *, power(random(), 1.0 / GREATEST({qw}, 1e-10)) AS _wscore
            FROM {table}
            WHERE {qw} IS NOT NULL AND {qw} > 0
        ) sub
        ORDER BY _wscore DESC
        LIMIT {target}
    """).fetchdf()


def sample_time_stratified(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    time_column: str,
    n: int | None,
    frac: float | None,
    time_bins: int,
    seed: int | None,
) -> pd.DataFrame:
    """Sample evenly across time bins. Divides the time range into equal bins and samples proportionally."""
    qtc = quote_ident(time_column)
    row_count: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    target = resolve_target(row_count, n, frac, False, "time_stratified")
    per_bin = max(1, target // time_bins)
    duckdb_set_seed(conn, seed)

    return conn.execute(f"""
        SELECT * EXCLUDE (_time_bin, _rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY _time_bin ORDER BY random()) AS _rn
            FROM (
                SELECT *, NTILE({time_bins}) OVER (ORDER BY {qtc}) AS _time_bin
                FROM {table}
                WHERE {qtc} IS NOT NULL
            ) binned
        ) ranked
        WHERE _rn <= {per_bin}
    """).fetchdf()


# =============================================================================
# LLM Semantic Sampling
# =============================================================================


async def _get_embeddings_raw(
    texts: list[str],
    api_url: str,
    api_key: str,
    model: str,
    batch_size: int = 100,
) -> list[list[float]]:
    """Fetch embeddings from an OpenAI-compatible embeddings API (raw HTTP).

    Used as fallback when llm_api_url/llm_api_key are explicitly provided,
    bypassing the unified LLM provider.
    """
    import httpx

    all_embeddings: list[list[float]] = []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = await client.post(
                api_url,
                headers=headers,
                json={"input": batch, "model": model},
            )
            if resp.status_code != 200:
                raise HTTPException(
                    502, f"Embeddings API error ({resp.status_code}): {resp.text[:500]}",
                )
            data = resp.json()
            batch_embeddings = [item["embedding"] for item in data["data"]]
            all_embeddings.extend(batch_embeddings)

    return all_embeddings


async def sample_llm_semantic(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    text_column: str,
    n: int,
    strategy: str = "diverse",
    provider: str = "openai",
    api_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    query: str | None = None,
) -> pd.DataFrame:
    """Sample rows based on semantic content using LLM embeddings.

    Strategies:
      - diverse:           KMeans clustering, pick nearest-to-centroid per cluster
      - edge_cases:        IsolationForest anomaly detection, pick most anomalous
      - similarity_search: Cosine similarity to a query text, pick top-N most similar
    """
    from sklearn.cluster import KMeans
    from sklearn.ensemble import IsolationForest

    qcol = quote_ident(text_column)

    rows = conn.execute(
        f"SELECT ROW_NUMBER() OVER () AS _sem_rid, {qcol} AS _text, * FROM {table}"
    ).fetchdf()

    if rows.empty:
        return pd.DataFrame()

    mask = rows["_text"].notna() & (rows["_text"].astype(str).str.strip() != "")
    valid_rows = rows[mask].copy()
    if valid_rows.empty:
        raise HTTPException(400, f"No non-empty text values in column '{text_column}'")

    texts = valid_rows["_text"].astype(str).tolist()
    n = min(n, len(valid_rows))

    resolved_model = model or os.environ.get("EMBEDDINGS_MODEL", "text-embedding-3-small")

    # Determine embedding source: explicit API URL/key or unified provider
    if api_url and api_key:
        # Raw HTTP mode (backward compat / custom endpoints)
        all_texts = texts if strategy != "similarity_search" else texts + [query]
        embeddings = await _get_embeddings_raw(all_texts, api_url, api_key, resolved_model)
    else:
        # Use unified LLM provider
        from app.infra.llm.llm_provider import get_embeddings_batch

        resolved_provider = provider or "openai"
        all_texts = texts if strategy != "similarity_search" else texts + [query]
        embeddings = await get_embeddings_batch(
            all_texts, provider=resolved_provider, model=resolved_model,
        )

    if strategy == "similarity_search":
        # Last embedding is the query; rest are the rows
        query_vec = np.array(embeddings[-1])
        embedding_matrix = np.array(embeddings[:-1])
    else:
        embedding_matrix = np.array(embeddings)

    if strategy == "diverse":
        n_clusters = min(n, len(valid_rows))
        kmeans = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
        kmeans.fit(embedding_matrix)
        selected_indices = []
        for center_idx in range(n_clusters):
            cluster_mask = kmeans.labels_ == center_idx
            cluster_indices = np.where(cluster_mask)[0]
            if len(cluster_indices) == 0:
                continue
            cluster_embeddings = embedding_matrix[cluster_indices]
            center = kmeans.cluster_centers_[center_idx]
            distances = np.linalg.norm(cluster_embeddings - center, axis=1)
            closest = cluster_indices[np.argmin(distances)]
            selected_indices.append(closest)

    elif strategy == "edge_cases":
        iso = IsolationForest(
            n_estimators=100,
            contamination=min(0.5, n / len(valid_rows)),
            random_state=seed,
        )
        scores = iso.fit(embedding_matrix).decision_function(embedding_matrix)
        selected_indices = np.argsort(scores)[:n].tolist()

    elif strategy == "similarity_search":
        if not query:
            raise HTTPException(400, "similarity_search strategy requires 'llm_query' parameter")
        # Cosine similarity between each row and the query
        row_norms = np.linalg.norm(embedding_matrix, axis=1)
        query_norm = np.linalg.norm(query_vec)
        # Avoid division by zero
        row_norms = np.where(row_norms == 0, 1e-10, row_norms)
        query_norm = max(query_norm, 1e-10)
        similarities = np.dot(embedding_matrix, query_vec) / (row_norms * query_norm)
        selected_indices = np.argsort(similarities)[-n:][::-1].tolist()

    else:
        raise HTTPException(
            400,
            f"Unknown LLM strategy: {strategy}. Use 'diverse', 'edge_cases', or 'similarity_search'.",
        )

    result = valid_rows.iloc[selected_indices].drop(columns=["_sem_rid", "_text"], errors="ignore")
    return result
