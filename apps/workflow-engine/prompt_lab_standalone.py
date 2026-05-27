"""PromptLab — single-file standalone service (TEMP).

Self-contained FastAPI app exposing two routes:

  POST /prompt-lab/datasets   multipart upload (csv/parquet/xlsx)
                              returns dataset_id + sidecar metadata

  POST /prompt-lab/evaluate   JSON body — runs a classification eval
                              against an uploaded dataset, persists
                              the run to MongoDB, returns metrics

Run:
  cd apps/workflow-engine
  source venv/bin/activate
  python prompt_lab_standalone.py
  # or: uvicorn prompt_lab_standalone:app --host 127.0.0.1 --port 8001

Env vars:
  PROMPTLAB_MONGO_URL         default mongodb://localhost:27017
  PROMPTLAB_MONGO_DB          default promptlab
  PROMPTLAB_STORAGE_DIR       default /tmp/promptlab
  GEMINI_API_KEY              required for gemini-* models
  ANTHROPIC_API_KEY           required for claude-* models

Why standalone: lets you ship/move the prompt-lab capability as one
artifact during the POC. Sidesteps the analytics-service for now.
Long-term home is back inside analytics-service with proper modules.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import secrets
import statistics
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from anthropic import AsyncAnthropic
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from sklearn.metrics import precision_recall_fscore_support

logger = logging.getLogger("promptlab")

# Honor PROMPTLAB_LOG_LEVEL (DEBUG/INFO/WARNING) even when imported as a module.
_log_level = os.environ.get("PROMPTLAB_LOG_LEVEL", "INFO").upper()
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
logger.setLevel(_log_level)


# ===========================================================================
# Config
# ===========================================================================

MONGO_URL = os.environ.get("PROMPTLAB_MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("PROMPTLAB_MONGO_DB", "promptlab")
STORAGE_DIR = Path(os.environ.get("PROMPTLAB_STORAGE_DIR", "/tmp/promptlab"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get(
    "ACCELERATOR_GEMINI_API_KEY"
) or os.environ.get("WORKFLOW_GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
    "ACCELERATOR_ANTHROPIC_API_KEY"
) or os.environ.get("WORKFLOW_ANTHROPIC_API_KEY")


# ===========================================================================
# Mongo client lifecycle
# ===========================================================================

_mongo_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    if _mongo_client is None:
        raise RuntimeError("mongo not initialised")
    return _mongo_client[MONGO_DB]


async def init_mongo() -> None:
    global _mongo_client
    _mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        await _mongo_client.admin.command("ping")
        logger.info("mongo connected: %s (db=%s)", MONGO_URL, MONGO_DB)
    except Exception:
        logger.exception("mongo ping failed at %s", MONGO_URL)


async def dispose_mongo() -> None:
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None


# ===========================================================================
# Mongo collections + indexes
# ===========================================================================

SESSIONS = "sessions"
RUNS = "prompt_runs"
EVAL_CACHE = "eval_cache"

SESSION_TTL_S = 7 * 86400
RUN_TTL_S = 14 * 86400
CACHE_TTL_S = 86400


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def ensure_indexes() -> None:
    db = get_db()
    try:
        await db[SESSIONS].create_index(
            [("last_accessed_at", 1)],
            expireAfterSeconds=SESSION_TTL_S,
            name="ttl_last_accessed_at",
        )
        await db[RUNS].create_index(
            [("created_at", 1)],
            expireAfterSeconds=RUN_TTL_S,
            name="ttl_created_at",
        )
        await db[RUNS].create_index(
            [("session_id", 1), ("created_at", -1)],
            name="ix_session_created",
        )
        await db[RUNS].create_index(
            [("session_id", 1), ("prompt_hash", 1)],
            name="ix_session_prompt_hash",
        )
        # Lets the workflow agent's "last 3 full runs" query stay fast.
        await db[RUNS].create_index(
            [("session_id", 1), ("stage", 1), ("created_at", -1)],
            name="ix_session_stage_created",
        )
        await db[RUNS].create_index(
            [("session_id", 1), ("parent_run_id", 1)],
            name="ix_session_parent_run",
        )
        await db[EVAL_CACHE].create_index(
            [("created_at", 1)],
            expireAfterSeconds=CACHE_TTL_S,
            name="ttl_created_at",
        )
        await db[EVAL_CACHE].create_index(
            [("cache_key", 1)], unique=True, name="ux_cache_key"
        )
        logger.info("indexes ensured")
    except Exception:
        logger.exception("ensure_indexes failed (probably already exist)")


# ===========================================================================
# Mongo CRUD
# ===========================================================================

def _new_session_id() -> str:
    return f"sess_{secrets.token_hex(8)}"


def _new_run_id() -> str:
    return f"run_{secrets.token_hex(8)}"


async def upsert_session_minimal(session_id: str, *, max_cost_usd: float) -> dict:
    now = _utc_now()
    doc = await get_db()[SESSIONS].find_one_and_update(
        {"session_id": session_id},
        {
            "$setOnInsert": {
                "session_id": session_id,
                "name": session_id,
                "description": "",
                "max_cost_usd": float(max_cost_usd),
                "budget_spent_usd": 0.0,
                "n_runs": 0,
                "best_run_id": None,
                "best_macro_f1": None,
                "status": "active",
                "created_at": now,
            },
            "$set": {"last_accessed_at": now},
        },
        upsert=True,
        return_document=True,
    )
    return doc


async def get_budget(session_id: str) -> tuple[float, float]:
    doc = await get_db()[SESSIONS].find_one(
        {"session_id": session_id},
        {"budget_spent_usd": 1, "max_cost_usd": 1},
    )
    if not doc:
        return 0.0, 0.0
    return float(doc.get("budget_spent_usd", 0.0)), float(doc.get("max_cost_usd", 0.0))


async def check_budget(session_id: str, projected: float) -> tuple[bool, float, float]:
    spent, cap = await get_budget(session_id)
    if cap <= 0:
        return True, spent, cap
    return (spent + projected) <= cap, spent, cap


async def add_spend(session_id: str, cost: float) -> float:
    doc = await get_db()[SESSIONS].find_one_and_update(
        {"session_id": session_id},
        {"$inc": {"budget_spent_usd": float(cost)},
         "$set": {"last_accessed_at": _utc_now()}},
        return_document=True,
        projection={"budget_spent_usd": 1},
    )
    return float(doc.get("budget_spent_usd", 0.0)) if doc else 0.0


async def insert_run(
    *, session_id: str, prompt_hash: str, prompt_system: str,
    prompt_user_template: str, model: str, response: dict,
    strategy: str | None = None,
    parent_run_id: str | None = None,
    stage: str | None = None,
) -> dict:
    now = _utc_now()
    run_id = _new_run_id()
    doc = {
        "run_id": run_id,
        "session_id": session_id,
        "prompt_hash": prompt_hash,
        "prompt_system": prompt_system,
        "prompt_user_template": prompt_user_template,
        "model": model,
        "strategy": strategy,
        "parent_run_id": parent_run_id,
        "stage": stage,
        "response": response,
        "created_at": now,
    }
    await get_db()[RUNS].insert_one(doc)
    await get_db()[SESSIONS].update_one(
        {"session_id": session_id},
        {"$inc": {"n_runs": 1}, "$set": {"last_accessed_at": now}},
    )
    logger.debug("insert_run session=%s run_id=%s stage=%s strategy=%s cached=%s",
                 session_id, run_id, stage, strategy, response.get("cached"))
    return doc


async def update_session_best(session_id: str, *, run_id: str, macro_f1: float) -> None:
    # Skip degenerate runs — a 0-score run is not a meaningful "best".
    if macro_f1 <= 0:
        logger.info("update_session_best skipped session=%s run=%s macro_f1=%.4f (not > 0)",
                    session_id, run_id, macro_f1)
        return
    result = await get_db()[SESSIONS].update_one(
        {
            "session_id": session_id,
            "$or": [
                {"best_macro_f1": None},
                {"best_macro_f1": {"$lt": macro_f1}},
            ],
        },
        {"$set": {
            "best_run_id": run_id,
            "best_macro_f1": float(macro_f1),
            "last_accessed_at": _utc_now(),
        }},
    )
    if result.modified_count:
        logger.info("update_session_best session=%s new_best run=%s macro_f1=%.4f",
                    session_id, run_id, macro_f1)
    else:
        logger.debug("update_session_best session=%s run=%s macro_f1=%.4f (existing best is higher)",
                     session_id, run_id, macro_f1)


def make_cache_key(
    *, prompt_hash: str, dataset_id: str, model: str,
    intent_classes: list[str], evaluation_splits: list[str],
    target_column: str, input_columns: list[str] | None,
    sample: SampleSpec | None = None, stage: str | None = None,
) -> str:
    payload = {
        "p": prompt_hash, "d": dataset_id, "m": model,
        "c": sorted(intent_classes), "s": sorted(evaluation_splits),
        "t": target_column,
        "i": sorted(input_columns) if input_columns else None,
        # smoke/quick/full and their sample params must differentiate cache
        # entries — same prompt at n=10 random sample is NOT the same result
        # as the same prompt at full dataset.
        "samp": ({"n": sample.n, "st": sample.strategy, "sd": sample.seed}
                 if sample else None),
        "stg": stage,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def get_cached_eval(cache_key: str) -> dict | None:
    doc = await get_db()[EVAL_CACHE].find_one(
        {"cache_key": cache_key}, {"_id": 0, "response": 1}
    )
    return doc.get("response") if doc else None


async def put_cached_eval(cache_key: str, response: dict) -> None:
    await get_db()[EVAL_CACHE].update_one(
        {"cache_key": cache_key},
        {"$set": {"cache_key": cache_key, "response": response, "created_at": _utc_now()}},
        upsert=True,
    )


# ===========================================================================
# Dataset file storage
# ===========================================================================

_ALLOWED_SUFFIXES = (".parquet", ".csv", ".xlsx", ".xls")
_GT_CANDIDATES = ("gt", "GT", "label", "expected")
_SPLIT_CANDIDATES = ("split", "Split", "SPLIT")
_MIN_ROWS = 10
_MAX_UNIQUE_CLASSES = 50
_DATASET_ID_RE = re.compile(r"^ds_[0-9a-f]{12}$")


def datasets_dir() -> Path:
    d = STORAGE_DIR / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_dataset_id(dataset_id: str) -> str:
    if not _DATASET_ID_RE.fullmatch(dataset_id or ""):
        raise HTTPException(400, f"invalid dataset_id: {dataset_id!r}")
    return dataset_id


def dataset_path(dataset_id: str) -> Path:
    return datasets_dir() / f"{_validate_dataset_id(dataset_id)}.parquet"


def dataset_meta_path(dataset_id: str) -> Path:
    return datasets_dir() / f"{_validate_dataset_id(dataset_id)}.meta.json"


def load_dataset_meta(dataset_id: str) -> dict | None:
    p = dataset_meta_path(dataset_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _detect_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _read_dataset(blob: bytes, suffix: str) -> pd.DataFrame:
    if suffix == ".parquet":
        return pd.read_parquet(io.BytesIO(blob))
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(blob))
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(blob))
    raise HTTPException(400, f"Unsupported file extension: {suffix}")


def _column_metadata(df: pd.DataFrame) -> list[dict]:
    out = []
    for col in df.columns:
        s = df[col]
        try:
            n_unique = int(s.nunique(dropna=True))
        except Exception:
            n_unique = -1
        samples: list = []
        for v in s.head(3).tolist():
            if pd.isna(v):
                samples.append(None)
                continue
            try:
                json.dumps(v, default=str)
                samples.append(v)
            except TypeError:
                samples.append(str(v))
        out.append({
            "name": str(col), "dtype": str(s.dtype),
            "n_unique": n_unique, "samples": samples,
        })
    return out


async def upload_dataset(
    file: UploadFile, *,
    name: str | None = None, description: str | None = None,
    target_column: str | None = None,
    input_columns: list[str] | None = None,
) -> dict:
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix!r}")
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "file is empty")
    sha256 = hashlib.sha256(blob).hexdigest()

    try:
        df = _read_dataset(blob, suffix)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"failed to read dataset: {e}")

    columns = [str(c) for c in df.columns]
    if not columns:
        raise HTTPException(422, "dataset has no columns")

    if target_column:
        if target_column not in columns:
            raise HTTPException(
                422, f"target_column={target_column!r} not in {columns}"
            )
        gt_col = target_column
    else:
        gt_col = _detect_column(columns, _GT_CANDIDATES)

    issues = []
    if gt_col is None:
        issues.append(
            f"no target column; pass target_column or include one of "
            f"{list(_GT_CANDIDATES)}; have {columns}"
        )

    split_col = _detect_column(columns, _SPLIT_CANDIDATES)
    if input_columns:
        missing = [c for c in input_columns if c not in columns]
        if missing:
            raise HTTPException(422, f"input_columns {missing} not in dataset")
        resolved_inputs = list(input_columns)
    else:
        resolved_inputs = [c for c in columns if c != gt_col and c != split_col]

    n_rows = int(len(df))
    if n_rows < _MIN_ROWS:
        issues.append(f"dataset has {n_rows} rows (need at least {_MIN_ROWS})")

    if gt_col is not None:
        gt_series = df[gt_col].dropna()
        if len(gt_series) == 0:
            issues.append(f"target {gt_col!r} is empty after dropna")
        else:
            gt_unique = int(gt_series.nunique())
            if gt_unique <= 1:
                issues.append(f"target {gt_col!r} has only {gt_unique} unique values")
            if gt_unique > _MAX_UNIQUE_CLASSES:
                issues.append(
                    f"target {gt_col!r} has {gt_unique} unique values "
                    f"(max {_MAX_UNIQUE_CLASSES})"
                )
    if issues:
        raise HTTPException(422, {
            "message": "dataset validation failed",
            "issues": issues, "columns": columns, "n_rows": n_rows,
        })

    schema_sig = hashlib.sha256(
        (sha256 + "\n" + (gt_col or "") + "\n" + ",".join(sorted(resolved_inputs))).encode()
    ).hexdigest()
    dataset_id = f"ds_{schema_sig[:12]}"

    detected_classes = sorted({str(v) for v in df[gt_col].dropna().unique().tolist()})
    detected_splits = (
        sorted({str(v) for v in df[split_col].dropna().unique().tolist()})
        if split_col is not None else []
    )
    columns_meta = _column_metadata(df)

    storage = dataset_path(dataset_id)
    if not storage.exists():
        try:
            df.to_parquet(storage, index=False)
        except Exception as e:
            raise HTTPException(500, f"failed to persist parquet: {e}")
    file_size = int(storage.stat().st_size) if storage.exists() else 0

    meta = {
        "dataset_id": dataset_id, "name": name or filename,
        "description": description or "", "n_rows": n_rows,
        "target_column": gt_col, "input_columns": resolved_inputs,
        "detected_split_column": split_col,
        "detected_classes": detected_classes, "detected_splits": detected_splits,
        "columns": columns_meta, "sha256": sha256,
        "file_size_bytes": file_size, "storage_path": str(storage),
        "format_original": suffix.lstrip("."),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        dataset_meta_path(dataset_id).write_text(json.dumps(meta, default=str))
    except OSError:
        logger.warning("sidecar write failed for %s", dataset_id, exc_info=True)
    logger.info(
        "dataset upload id=%s name=%s n_rows=%d target=%s inputs=%s classes=%s splits=%s",
        dataset_id, meta["name"], n_rows, gt_col, resolved_inputs,
        detected_classes, detected_splits or "(none)",
    )
    return meta


# ===========================================================================
# Scorer (just classification_exact for this standalone)
# ===========================================================================

async def classification_exact_score(prediction: str, expected: Any, **kwargs) -> float:
    return 1.0 if str(prediction).strip().lower() == str(expected).strip().lower() else 0.0


SCORERS = {"classification_exact": classification_exact_score}


# ===========================================================================
# Evaluator
# ===========================================================================

_CONCURRENCY = 10
_FAILURE_SAMPLE_CAP = 50
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 1.0
_RAW_TEXT_CAP_CHARS = 500  # truncate per-row raw model output before persisting


def _apply_sample(df: "pd.DataFrame", gt_col: str, spec: SampleSpec) -> "pd.DataFrame":
    """Deterministically reduce df to spec.n rows.

    - 'random': uniform sample without replacement.
    - 'stratified_by_gt': proportional allocation across gt classes. Every
      class with rows in the dataset gets at least 1 row when feasible, so
      small-n smoke tests still hit every label.
    """
    n = spec.n
    if n >= len(df):
        return df.reset_index(drop=True)
    if spec.strategy == "random" or gt_col not in df.columns:
        return df.sample(n=n, random_state=spec.seed).reset_index(drop=True)
    if spec.strategy != "stratified_by_gt":
        raise HTTPException(400, f"unknown sample.strategy: {spec.strategy!r}")

    classes = sorted(df[gt_col].dropna().astype(str).unique().tolist())
    if not classes:
        return df.sample(n=n, random_state=spec.seed).reset_index(drop=True)

    # Proportional quota, floor at 1 per present class. Trim/grow to exactly n.
    sizes = {c: int((df[gt_col].astype(str) == c).sum()) for c in classes}
    total = sum(sizes.values()) or 1
    quota = {c: max(1, int(round(n * sizes[c] / total))) for c in classes}
    while sum(quota.values()) > n:
        biggest = max(quota, key=lambda k: (quota[k], -sizes[k]))
        if quota[biggest] <= 1:
            break
        quota[biggest] -= 1
    while sum(quota.values()) < n:
        smallest = min(quota, key=lambda k: (quota[k], -sizes[k]))
        if quota[smallest] >= sizes[smallest]:
            quota.pop(smallest); continue
        quota[smallest] += 1

    parts = []
    for c in classes:
        sub = df[df[gt_col].astype(str) == c]
        take = min(quota.get(c, 0), len(sub))
        if take > 0:
            parts.append(sub.sample(n=take, random_state=spec.seed))
    out = pd.concat(parts).sort_index().reset_index(drop=True)
    return out


def _wilson_ci_95(correct: int, n: int) -> tuple[float, float]:
    """Wilson 95% confidence interval on a proportion. Lets an agent tell
    real accuracy deltas from noise on small datasets (e.g. on n=10, 7/10
    and 8/10 overlap heavily and shouldn't drive a prompt change)."""
    if n <= 0:
        return (0.0, 0.0)
    z = 1.96
    p = correct / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / denom
    return (max(0.0, center - margin), min(1.0, center + margin))

_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku": (0.25, 1.25),
    "claude-sonnet": (3.0, 15.0),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.0-flash": (0.075, 0.30),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.0),
}
_DEFAULT_COST = (3.0, 15.0)
_CHARS_PER_TOKEN = 4
_FIRST_JSON_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _model_rates(model: str) -> tuple[float, float]:
    for prefix, rates in _MODEL_COSTS.items():
        if model.startswith(prefix):
            return rates
    return _DEFAULT_COST


def _row_cost_usd(model: str, t_in: int, t_out: int) -> float:
    in_r, out_r = _model_rates(model)
    return (t_in / 1_000_000) * in_r + (t_out / 1_000_000) * out_r


def _prompt_hash(sys_p: str, user_t: str) -> str:
    h = hashlib.sha1()
    h.update(sys_p.encode()); h.update(b"\n"); h.update(user_t.encode())
    return h.hexdigest()


def _row_id(d: dict) -> str:
    try:
        s = json.dumps(d, sort_keys=True, default=str)
    except (TypeError, ValueError):
        s = str(d)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _safe_cell(v: Any) -> Any:
    if v is None:
        return ""
    try:
        if v != v:  # NaN
            return ""
    except (TypeError, ValueError):
        pass
    return v


def _render_user(template: str, row_inputs: dict) -> str:
    class _D(dict):
        def __missing__(self, k):
            return ""
    return template.format_map(_D({k: ("" if v is None else v) for k, v in row_inputs.items()}))


def _clean_fences(t: str) -> str:
    return _FENCE_RE.sub("", t).strip()


def _extract_label(text: str, classes: list[str]) -> str:
    if not isinstance(text, str):
        return ""
    lc = {c.lower(): c for c in classes}
    cleaned = _clean_fences(text)

    def _scan(obj):
        if isinstance(obj, str):
            return lc.get(obj.strip().lower())
        if isinstance(obj, dict):
            for k in ("classified_intent", "intent", "label", "class"):
                if k in obj:
                    f = _scan(obj[k])
                    if f:
                        return f
            for v in obj.values():
                f = _scan(v)
                if f:
                    return f
        if isinstance(obj, list):
            for v in obj:
                f = _scan(v)
                if f:
                    return f
        return None

    def _try(c):
        try:
            return _scan(json.loads(c))
        except (ValueError, TypeError):
            return None

    lbl = _try(cleaned)
    if lbl:
        return lbl
    m = _FIRST_JSON_RE.search(cleaned)
    if m:
        lbl = _try(m.group(0))
        if lbl:
            return lbl
    tl = cleaned.lower()
    for low, orig in lc.items():
        if low in tl:
            return orig
    # No known class matched. Return "" rather than raw text so unparseable
    # outputs (truncated JSON, prose, garbage) don't pollute per_class metrics
    # with junk keys like "h" or "{\"classified_intent".
    logger.debug("_extract_label: no match for %r (classes=%s)", cleaned[:80], classes)
    return ""


def _is_retryable(e: Exception) -> bool:
    msg = str(e).lower()
    name = e.__class__.__name__.lower()
    return any(t in name or t in msg for t in
               ("ratelimit", "rate_limit", "429", "timeout", "503", "502", "504",
                "overloaded", "unavailable"))


async def _call_once(*, model, system, user, max_tokens, top_p, seed,
                     thinking_budget, anthropic, gemini) -> tuple[str, int, int]:
    if model.startswith("gemini-"):
        if gemini is None:
            raise RuntimeError("GEMINI_API_KEY not set")
        cfg: dict = {
            "temperature": 0, "max_output_tokens": max_tokens,
            "system_instruction": system, "response_mime_type": "application/json",
        }
        if top_p is not None:
            cfg["top_p"] = top_p
        if seed is not None:
            cfg["seed"] = seed
        # Gemini-2.5 spends max_output_tokens on internal thinking first. If
        # we don't cap thinking, the visible response gets truncated to 1-2
        # tokens. Set thinking_budget=0 by default for deterministic
        # classification; caller can override via EvalConfig.thinking_budget.
        if model.startswith("gemini-2.5") and thinking_budget is not None:
            cfg["thinking_config"] = {"thinking_budget": int(thinking_budget)}
        resp = await gemini.aio.models.generate_content(
            model=model, contents=user, config=cfg
        )
        text = resp.text or ""
        usage = getattr(resp, "usage_metadata", None)
        return (text,
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0))

    if anthropic is None:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    kw = {
        "model": model, "max_tokens": max_tokens, "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if top_p is not None:
        kw["top_p"] = top_p
    resp = await anthropic.messages.create(**kw)
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return ("".join(parts),
            int(getattr(resp.usage, "input_tokens", 0) or 0),
            int(getattr(resp.usage, "output_tokens", 0) or 0))


async def _call_model(**kw) -> tuple[str, int, int]:
    delay = _RETRY_BASE_DELAY
    last: Exception | None = None
    for i in range(_MAX_RETRIES):
        try:
            return await _call_once(**kw)
        except Exception as e:
            last = e
            if i >= _MAX_RETRIES - 1 or not _is_retryable(e):
                if not _is_retryable(e):
                    logger.warning("non-retryable LLM error model=%s: %s: %s",
                                   kw.get("model"), type(e).__name__, e)
                raise
            logger.warning("retry %d/%d model=%s: %s: %s",
                           i + 1, _MAX_RETRIES, kw.get("model"), type(e).__name__, e)
            await asyncio.sleep(delay); delay *= 2
    assert last is not None
    raise last


async def _eval_row(row, *, model, system_prompt, user_template, intent_classes,
                    top_p, seed, max_tokens, thinking_budget, scorer_fn,
                    anthropic, gemini, sem):
    async with sem:
        rendered = _render_user(user_template, row["input"])
        rid = row.get("row_id") or _row_id(row["input"])
        t0 = time.perf_counter()
        try:
            text, t_in, t_out = await _call_model(
                model=model, system=system_prompt, user=rendered,
                max_tokens=max_tokens, top_p=top_p, seed=seed,
                thinking_budget=thinking_budget,
                anthropic=anthropic, gemini=gemini,
            )
        except Exception as e:
            logger.warning("row %s LLM call failed: %s: %s", rid, type(e).__name__, e)
            return {
                "row_id": rid,
                "input": row["input"], "gt": row["gt"], "split": row["split"],
                "predicted": "", "score": 0.0, "tokens_in": 0, "tokens_out": 0,
                "latency_ms": (time.perf_counter() - t0) * 1000.0,
                "raw_text": "", "error": f"{type(e).__name__}: {e}"[:_RAW_TEXT_CAP_CHARS],
            }
        lat = (time.perf_counter() - t0) * 1000.0
        pred = _extract_label(text, intent_classes)
        score = await scorer_fn(pred, row["gt"])
        logger.debug("row %s gt=%s pred=%s score=%.2f tokens=%d/%d latency=%.0fms",
                     rid, row["gt"], pred, score, t_in, t_out, lat)
        return {
            "row_id": rid,
            "input": row["input"], "gt": row["gt"], "split": row["split"],
            "predicted": pred, "score": float(score),
            "tokens_in": int(t_in), "tokens_out": int(t_out), "latency_ms": lat,
            "raw_text": (text or "")[:_RAW_TEXT_CAP_CHARS],
        }


def _split_metrics(rows: list[dict], classes: list[str]) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "accuracy": 0.0, "accuracy_ci_95": [0.0, 0.0],
            "macro_f1": 0.0, "macro_precision": 0.0, "macro_recall": 0.0,
            "weighted_f1": 0.0, "weighted_precision": 0.0, "weighted_recall": 0.0,
            "n_rows": 0, "n_failures": 0,
            "per_class": {},
            "confusion_matrix": {c: {c2: 0 for c2 in classes} for c in classes},
        }
    gts = [r["gt"] for r in rows]
    preds = [r["predicted"] for r in rows]
    correct = sum(1 for g, p in zip(gts, preds) if g == p)
    acc = correct / n

    labels = list(classes)
    for lbl in sorted({g for g in gts} | {p for p in preds}):
        if lbl not in labels:
            labels.append(lbl)

    pr, rc, f1, sup = precision_recall_fscore_support(
        gts, preds, labels=labels, zero_division=0
    )
    per_class = {labels[i]: {
        "precision": float(pr[i]), "recall": float(rc[i]),
        "f1": float(f1[i]), "support": int(sup[i]),
    } for i in range(len(labels))}

    # Macro: unweighted mean over configured classes only (matches prior behavior).
    cfg_classes = [c for c in classes if c in per_class]
    macro_f1 = sum(per_class[c]["f1"] for c in cfg_classes) / len(cfg_classes) if cfg_classes else 0.0
    macro_p = sum(per_class[c]["precision"] for c in cfg_classes) / len(cfg_classes) if cfg_classes else 0.0
    macro_r = sum(per_class[c]["recall"] for c in cfg_classes) / len(cfg_classes) if cfg_classes else 0.0

    # Weighted: support-weighted mean over configured classes. For imbalanced
    # data this is what the agent should optimize for — a single bad rare
    # class won't dominate macro_f1 anymore.
    total_sup = sum(per_class[c]["support"] for c in cfg_classes)
    if total_sup > 0:
        weighted_f1 = sum(per_class[c]["f1"] * per_class[c]["support"] for c in cfg_classes) / total_sup
        weighted_p = sum(per_class[c]["precision"] * per_class[c]["support"] for c in cfg_classes) / total_sup
        weighted_r = sum(per_class[c]["recall"] * per_class[c]["support"] for c in cfg_classes) / total_sup
    else:
        weighted_f1 = weighted_p = weighted_r = 0.0

    conf: dict = {c: {c2: 0 for c2 in classes} for c in classes}
    for g, p in zip(gts, preds):
        conf.setdefault(g, {c2: 0 for c2 in classes})
        conf[g].setdefault(p, 0)
        conf[g][p] += 1

    lo, hi = _wilson_ci_95(correct, n)
    return {
        "accuracy": acc, "accuracy_ci_95": [lo, hi],
        "macro_f1": macro_f1, "macro_precision": macro_p, "macro_recall": macro_r,
        "weighted_f1": weighted_f1, "weighted_precision": weighted_p, "weighted_recall": weighted_r,
        "n_rows": n, "n_failures": n - correct,
        "per_class": per_class,
        "confusion_matrix": conf,
    }


# ===========================================================================
# Schemas
# ===========================================================================

class EvalConfig(BaseModel):
    """LLM call configuration. Validated at parse time — unknown keys are rejected."""
    model: str = "gemini-2.5-flash"
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int | None = None
    # Gemini-2.5 only. None = SDK default (often a large internal budget that
    # silently truncates the visible response). 0 = disable thinking, full
    # max_tokens budget goes to output. We default to 0 below in run_evaluation.
    thinking_budget: int | None = Field(default=None, ge=0)

    model_config = {"extra": "forbid"}


class SampleSpec(BaseModel):
    """Server-side sampling for cheap iteration.

    n           rows to keep (must be >= 1; if >= dataset size, full dataset used)
    strategy    'random' or 'stratified_by_gt' (latter ensures every class is represented)
    seed        deterministic — same (n, strategy, seed, dataset) gives same sample
    """
    n: int = Field(ge=1)
    strategy: str = "random"
    seed: int = 42

    model_config = {"extra": "forbid"}


# Canonical stage → sample mapping. Centralizes the convention so every
# caller (workflow agents, manual curl, future analytics-service port) uses
# the SAME numbers — apples-to-apples cross-session comparisons depend on
# this. Override by passing `sample` explicitly in the request.
_STAGE_DEFAULTS: dict[str, SampleSpec | None] = {
    "smoke": SampleSpec(n=10,  strategy="random",           seed=42),
    "quick": SampleSpec(n=100, strategy="stratified_by_gt", seed=42),
    "full":  None,  # no sampling — whole dataset
}


class EvaluateRequest(BaseModel):
    dataset_id: str
    prompt_system: str
    prompt_user_template: str
    intent_classes: list[str]
    config: EvalConfig = Field(default_factory=EvalConfig)
    session_id: str | None = None
    target_column: str | None = None
    input_columns: list[str] | None = None
    evaluation_splits: list[str] = Field(default_factory=list)
    sample: SampleSpec | None = None
    # Free-form label persisted with the run. The workflow agent uses these to
    # filter trend queries: "smoke"/"quick" runs are diagnostic; "full" runs
    # are the authoritative comparison set.
    stage: str | None = None
    # Strategy + parent_run_id are used by the optimizer workflow's decision
    # agent for anti-repetition and lineage tracking. Persisted as-is.
    strategy: str | None = None
    parent_run_id: str | None = None
    scorer: str = "classification_exact"
    task_type: str = "classification"
    use_cache: bool = True
    max_cost_usd: float = 1.0


class DatasetColumnInfo(BaseModel):
    name: str
    dtype: str
    n_unique: int
    samples: list[Any] = Field(default_factory=list)


class DatasetEntry(BaseModel):
    dataset_id: str
    name: str
    description: str = ""
    n_rows: int
    target_column: str
    input_columns: list[str] = Field(default_factory=list)
    detected_split_column: str | None = None
    detected_classes: list[str] = Field(default_factory=list)
    detected_splits: list[str] = Field(default_factory=list)
    columns: list[DatasetColumnInfo] = Field(default_factory=list)
    sha256: str
    file_size_bytes: int
    storage_path: str
    format_original: str
    created_at: str


# ===========================================================================
# Evaluator entrypoint
# ===========================================================================

async def run_evaluation(request: EvaluateRequest) -> dict:
    if request.task_type != "classification":
        raise HTTPException(400, f"task_type={request.task_type!r} not supported")
    if not request.intent_classes:
        raise HTTPException(400, "intent_classes must be non-empty")
    if not request.dataset_id:
        raise HTTPException(422, "dataset_id is required")

    dataset_id = _validate_dataset_id(request.dataset_id)
    intent_classes = list(request.intent_classes)
    meta = load_dataset_meta(dataset_id) or {}
    target_column = request.target_column or meta.get("target_column")
    input_columns = request.input_columns or meta.get("input_columns")

    # Resolve sampling: explicit `sample` wins; otherwise derive from `stage`
    # via the canonical mapping. Single source of truth for smoke=10/quick=100.
    resolved_sample = request.sample
    if resolved_sample is None and request.stage in _STAGE_DEFAULTS:
        resolved_sample = _STAGE_DEFAULTS[request.stage]

    cfg = request.config
    model = cfg.model
    top_p = cfg.top_p
    seed = cfg.seed
    max_tokens = cfg.max_tokens
    # For gemini-2.5, disable thinking by default so max_tokens is fully
    # available for the visible response. Caller can opt back in by setting
    # config.thinking_budget to a positive integer.
    thinking_budget = cfg.thinking_budget
    if model.startswith("gemini-2.5") and thinking_budget is None:
        thinking_budget = 0

    logger.info(
        "eval start dataset=%s model=%s scorer=%s session=%s stage=%s "
        "strategy=%s classes=%d splits=%s sample=%s max_tokens=%d "
        "thinking_budget=%s use_cache=%s",
        dataset_id, model, request.scorer, request.session_id, request.stage,
        request.strategy, len(intent_classes), request.evaluation_splits or "(all)",
        (f"{resolved_sample.strategy}:{resolved_sample.n}" if resolved_sample else "full"),
        max_tokens, thinking_budget, request.use_cache,
    )

    if request.session_id:
        await upsert_session_minimal(request.session_id, max_cost_usd=request.max_cost_usd)

    scorer_fn = SCORERS.get(request.scorer)
    if scorer_fn is None:
        raise HTTPException(400, f"unknown scorer: {request.scorer}")

    prompt_hash = _prompt_hash(request.prompt_system, request.prompt_user_template)
    cache_key = make_cache_key(
        prompt_hash=prompt_hash, dataset_id=dataset_id, model=model,
        intent_classes=intent_classes,
        evaluation_splits=request.evaluation_splits,
        target_column=target_column or "", input_columns=input_columns,
        sample=resolved_sample, stage=request.stage,
    )

    if request.use_cache:
        cached = await get_cached_eval(cache_key)
        if cached is not None:
            logger.info("cache HIT dataset=%s prompt_hash=%s cache_key=%s",
                        dataset_id, prompt_hash[:10], cache_key[:10])
            spent, cap = (await get_budget(request.session_id)
                          if request.session_id else (0.0, request.max_cost_usd))
            cr = dict(cached)
            cr.update({
                "dataset_id": dataset_id, "session_id": request.session_id,
                "cached": True, "cost_usd": 0.0,
                "budget_spent_usd": spent, "budget_max_usd": cap,
            })
            if request.session_id:
                rd = await insert_run(
                    session_id=request.session_id, prompt_hash=prompt_hash,
                    prompt_system=request.prompt_system,
                    prompt_user_template=request.prompt_user_template,
                    model=model, response=cr,
                    strategy=request.strategy,
                    parent_run_id=request.parent_run_id,
                    stage=request.stage,
                )
                cr["run_id"] = rd["run_id"]
                # Cache hit on a full-stage candidate must still update best —
                # update_session_best is idempotent ($lt guard), so it's safe to
                # call repeatedly. Without this, sessions whose first run lands
                # on a primed cache never get best_run_id set.
                if request.stage in (None, "full"):
                    cached_metrics = (cr.get("metrics") or {})
                    cached_overall = (cached_metrics.get("overall") or {})
                    cached_splits = (cached_metrics.get("by_split") or {})
                    best = float(cached_overall.get("macro_f1") or 0.0)
                    for sn in ("Holdout", "holdout", "test", "Test"):
                        sm = cached_splits.get(sn)
                        if sm and (sm.get("n_rows") or 0) > 0:
                            best = float(sm.get("macro_f1") or 0.0); break
                    await update_session_best(
                        request.session_id, run_id=rd["run_id"], macro_f1=best
                    )
            return cr
        logger.info("cache MISS dataset=%s prompt_hash=%s cache_key=%s",
                    dataset_id, prompt_hash[:10], cache_key[:10])

    # Load dataset
    p = dataset_path(dataset_id)
    if not p.exists():
        logger.warning("dataset not found on disk: %s (path=%s)", dataset_id, p)
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    df = pq.read_table(p).to_pandas()
    cols = list(df.columns)
    logger.debug("dataset loaded id=%s rows=%d cols=%s", dataset_id, len(df), cols)

    # Server-side sampling for smoke/quick stages. We need gt_col resolved
    # before sampling so stratified mode works — duplicate the inference here.
    if resolved_sample is not None:
        _gt_col = target_column or _detect_column(cols, _GT_CANDIDATES) or ""
        before = len(df)
        df = _apply_sample(df, _gt_col, resolved_sample)
        logger.info("sample applied stage=%s strategy=%s n_requested=%d before=%d after=%d",
                    request.stage, resolved_sample.strategy, resolved_sample.n, before, len(df))

    gt_col = target_column or _detect_column(cols, _GT_CANDIDATES)
    if gt_col is None or gt_col not in cols:
        raise HTTPException(422, f"target column not resolved: {target_column!r}")
    split_col = _detect_column(cols, _SPLIT_CANDIDATES)
    default_split = request.evaluation_splits[0] if request.evaluation_splits else "all"
    if input_columns:
        missing = [c for c in input_columns if c not in cols]
        if missing:
            raise HTTPException(422, f"input_columns {missing} not in dataset")
        resolved_inputs = list(input_columns)
    else:
        resolved_inputs = [c for c in cols if c != gt_col and c != split_col]

    rows: list[dict] = []
    available: set = set()
    for _, r in df.iterrows():
        inputs = {c: _safe_cell(r[c]) for c in resolved_inputs}
        split = str(r[split_col]) if split_col is not None else default_split
        rows.append({"input": inputs, "gt": str(r[gt_col]), "split": split})
        available.add(split)

    if not rows:
        raise HTTPException(422, f"Dataset {dataset_id!r} is empty")
    if request.evaluation_splits:
        req = set(request.evaluation_splits)
        rows = [r for r in rows if r["split"] in req]
        if not rows:
            raise HTTPException(
                422, f"None of evaluation_splits={request.evaluation_splits} "
                f"present (available: {sorted(available)})"
            )

    n_rows = len(rows)
    in_r, out_r = _model_rates(model)
    est_in = max(1, (len(request.prompt_system) + len(request.prompt_user_template)) // _CHARS_PER_TOKEN)
    est_out = est_in
    projected = n_rows * ((est_in / 1_000_000) * in_r + (est_out / 1_000_000) * out_r)

    if request.session_id:
        allowed, spent, cap = await check_budget(request.session_id, projected)
        logger.info("budget check session=%s spent=$%.6f projected=$%.6f cap=$%.4f allowed=%s",
                    request.session_id, spent, projected, cap, allowed)
        if not allowed:
            raise HTTPException(
                429, f"Session {request.session_id} would exceed budget: "
                f"current=${spent:.4f}, projected=${projected:.4f}, max=${cap:.4f}",
            )
    else:
        cap = request.max_cost_usd
        logger.info("budget check (no session) projected=$%.6f cap=$%.4f", projected, cap)
        if projected > cap:
            raise HTTPException(
                429, f"Projected ${projected:.4f} exceeds max ${cap:.4f}"
            )

    anthropic_c: AsyncAnthropic | None = None
    gemini_c: Any | None = None
    if model.startswith("gemini-"):
        if not GEMINI_API_KEY:
            raise HTTPException(500, "GEMINI_API_KEY not configured")
        from google import genai
        gemini_c = genai.Client(api_key=GEMINI_API_KEY)
    else:
        if not ANTHROPIC_API_KEY:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        anthropic_c = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    logger.info("dispatching %d evaluations concurrency=%d", n_rows, _CONCURRENCY)
    t_wall = time.perf_counter()
    sem = asyncio.Semaphore(_CONCURRENCY)
    tasks = [_eval_row(
        row, model=model, system_prompt=request.prompt_system,
        user_template=request.prompt_user_template,
        intent_classes=intent_classes, top_p=top_p, seed=seed,
        max_tokens=max_tokens, thinking_budget=thinking_budget,
        scorer_fn=scorer_fn,
        anthropic=anthropic_c, gemini=gemini_c, sem=sem,
    ) for row in rows]
    results = await asyncio.gather(*tasks)
    logger.info("evaluations complete n=%d wall=%.1fs", n_rows, time.perf_counter() - t_wall)

    t_in_total = sum(r["tokens_in"] for r in results)
    t_out_total = sum(r["tokens_out"] for r in results)
    lats = sorted(r["latency_ms"] for r in results)
    p50 = float(statistics.median(lats)) if lats else 0.0
    p95 = float(lats[int(0.95 * (len(lats) - 1))]) if lats else 0.0
    p99 = float(lats[int(0.99 * (len(lats) - 1))]) if lats else 0.0
    cost = _row_cost_usd(model, t_in_total, t_out_total)
    new_total = (await add_spend(request.session_id, cost)
                 if request.session_id else cost)

    by_split: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_split[r["split"]].append(r)
    split_metrics = {sp: _split_metrics(rs, intent_classes) for sp, rs in by_split.items()}
    overall = _split_metrics(results, intent_classes)

    failures = [r for r in results if r["gt"] != r["predicted"]]
    pair_c: Counter[str] = Counter()
    split_c: Counter[str] = Counter()
    for f in failures:
        pair_c[f"{f['gt']}->{f['predicted']}"] += 1
        split_c[f["split"]] += 1
    top = [p for p, _ in pair_c.most_common(5)]

    if len(failures) > _FAILURE_SAMPLE_CAP:
        stride = max(1, len(failures) // _FAILURE_SAMPLE_CAP)
        sampled = failures[::stride][:_FAILURE_SAMPLE_CAP]
    else:
        sampled = failures

    # Diagnose empty predictions. There are two distinct failure modes and
    # they need different fixes — don't conflate them:
    #
    #  (a) TRUNCATION: model returned no text or a tiny fragment because
    #      max_tokens was too small (often gemini-2.5 burning budget on
    #      thinking). raw_text is empty or < 5 chars.
    #  (b) PARSE FAILURE: model returned complete output but in the wrong
    #      schema (e.g. {"label":"X"} instead of {"classified_intent":"X"}),
    #      or hallucinated a class not in intent_classes. raw_text is
    #      non-trivial; the response just doesn't match the contract.
    warnings_out: list[str] = []
    n_errors = sum(1 for r in results if r.get("error"))
    successful = [r for r in results if not r.get("error")]
    empty_with_no_text = sum(
        1 for r in successful
        if r["predicted"] == "" and len((r.get("raw_text") or "").strip()) < 5
    )
    empty_with_text = sum(
        1 for r in successful
        if r["predicted"] == "" and len((r.get("raw_text") or "").strip()) >= 5
    )
    if empty_with_no_text >= 3 or (successful and empty_with_no_text / len(successful) >= 0.3):
        msg = (
            f"{empty_with_no_text}/{len(successful)} rows had truncated or "
            f"empty model output — raise config.max_tokens (currently "
            f"{max_tokens}) or, for gemini-2.5, ensure thinking_budget=0."
        )
        warnings_out.append(msg)
        logger.warning("truncation suspected: %s", msg)
    if empty_with_text >= 3 or (successful and empty_with_text / len(successful) >= 0.3):
        msg = (
            f"{empty_with_text}/{len(successful)} rows returned non-empty "
            f"output that did not parse to a configured class — model is "
            f"likely using a different output schema or hallucinating "
            f"classes outside intent_classes={intent_classes}. Inspect "
            f"per_row[].raw_text for examples and tighten the prompt."
        )
        warnings_out.append(msg)
        logger.warning("parse-failure suspected: %s", msg)
    if n_errors:
        warnings_out.append(f"{n_errors}/{len(results)} rows failed with LLM errors")
        logger.warning("LLM error rate: %d/%d rows", n_errors, len(results))

    # per_row: every row's full record. This is the agent's primary signal
    # for prompt refinement — it can diff between runs, find consistently
    # failing inputs, spot class-specific regressions. Stored in Mongo via
    # the response dict so the agent can query historical runs.
    per_row = [{
        "row_id": r["row_id"],
        "split": r["split"],
        "inputs": r["input"] if isinstance(r["input"], dict) else {"value": r["input"]},
        "gt": r["gt"],
        "predicted": r["predicted"],
        "correct": r["gt"] == r["predicted"],
        "tokens_in": r["tokens_in"],
        "tokens_out": r["tokens_out"],
        "latency_ms": r["latency_ms"],
        "raw_text": r.get("raw_text", ""),
        "error": r.get("error"),
    } for r in results]

    response = {
        "dataset_id": dataset_id,
        "session_id": request.session_id,
        "run_id": None,
        "prompt_hash": prompt_hash,
        # Echo back the resolved config (after defaults) so the agent can
        # verify what was actually executed — important on cache hits where
        # the agent has no other proof of the parameters used.
        "config": {
            "model": model,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "seed": seed,
            "thinking_budget": thinking_budget,
        },
        "metrics": {"overall": overall, "by_split": split_metrics},
        "per_row": per_row,
        "failures_sample": [{
            "row_id": f.get("row_id") or _row_id(f["input"]),
            "split": f["split"],
            "inputs": f["input"] if isinstance(f["input"], dict) else {"value": f["input"]},
            "gt": f["gt"], "predicted": f["predicted"],
            "raw_text": f.get("raw_text", ""),
            "error": f.get("error"),
            "confusion_pair": f"{f['gt']}->{f['predicted']}",
        } for f in sampled],
        "failure_summary": {
            "total": len(failures),
            "by_confusion_pair": dict(pair_c),
            "by_split": dict(split_c),
            "top_confusion_pairs": top,
        },
        "tokens_in": t_in_total,
        "tokens_out": t_out_total,
        "cost_usd": cost,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
        "cached": False,
        "budget_spent_usd": new_total,
        "budget_max_usd": cap,
        "warnings": warnings_out,
    }

    logger.info(
        "eval done dataset=%s model=%s accuracy=%.3f ci95=[%.3f,%.3f] "
        "macro_f1=%.3f weighted_f1=%.3f n_rows=%d failures=%d "
        "tokens=%d/%d cost=$%.6f p50=%.0fms p95=%.0fms",
        dataset_id, model,
        overall["accuracy"], overall["accuracy_ci_95"][0], overall["accuracy_ci_95"][1],
        overall["macro_f1"], overall["weighted_f1"],
        overall["n_rows"], overall["n_failures"],
        t_in_total, t_out_total, cost, p50, p95,
    )

    run_id: str | None = None
    if request.session_id:
        rd = await insert_run(
            session_id=request.session_id, prompt_hash=prompt_hash,
            prompt_system=request.prompt_system,
            prompt_user_template=request.prompt_user_template,
            model=model, response=response,
            strategy=request.strategy,
            parent_run_id=request.parent_run_id,
            stage=request.stage,
        )
        run_id = rd["run_id"]
        response["run_id"] = run_id
        # Update best — prefer Holdout/test split if present, else overall.
        # Only consider full-dataset runs as candidates for "best" — a 10-row
        # smoke run scoring 1.0 must not crown itself as the session's best.
        if request.stage in (None, "full"):
            best = overall["macro_f1"]
            for sn in ("Holdout", "holdout", "test", "Test"):
                sm = split_metrics.get(sn)
                if sm and sm["n_rows"] > 0:
                    best = sm["macro_f1"]; break
            await update_session_best(request.session_id, run_id=run_id, macro_f1=best)
        else:
            logger.debug("skipping best update for stage=%s run=%s", request.stage, run_id)

    cached_payload = dict(response)
    if run_id:
        cached_payload["run_id"] = run_id
    await put_cached_eval(cache_key, cached_payload)
    logger.debug("cache put cache_key=%s", cache_key[:10])

    return response


# ===========================================================================
# FastAPI app + routes
# ===========================================================================

@asynccontextmanager
async def lifespan(application: FastAPI):
    datasets_dir()
    await init_mongo()
    try:
        await ensure_indexes()
    except Exception:
        logger.exception("index setup failed; continuing")
    yield
    await dispose_mongo()


app = FastAPI(
    title="PromptLab (standalone)",
    description="Two-endpoint prompt evaluator. Self-contained.",
    version="0.1.0",
    lifespan=lifespan,
)

router = APIRouter(prefix="/prompt-lab")


def _parse_json_form(value: str | None, field: str) -> list[str] | None:
    if value is None or value == "":
        return None
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"{field} not valid JSON: {e}")
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise HTTPException(400, f"{field} must be a JSON list of strings")
        return parsed
    return [s.strip() for s in value.split(",") if s.strip()]


@router.post("/datasets", response_model=DatasetEntry)
async def upload_dataset_endpoint(
    file: UploadFile,
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    target_column: str | None = Form(default=None),
    input_columns: str | None = Form(default=None),
) -> DatasetEntry:
    inputs_parsed = _parse_json_form(input_columns, "input_columns")
    entry = await upload_dataset(
        file, name=name, description=description,
        target_column=target_column, input_columns=inputs_parsed,
    )
    return DatasetEntry(
        dataset_id=entry["dataset_id"], name=entry["name"],
        description=entry["description"], n_rows=entry["n_rows"],
        target_column=entry["target_column"], input_columns=entry["input_columns"],
        detected_split_column=entry["detected_split_column"],
        detected_classes=entry["detected_classes"],
        detected_splits=entry["detected_splits"],
        columns=[DatasetColumnInfo(**c) for c in entry["columns"]],
        sha256=entry["sha256"], file_size_bytes=entry["file_size_bytes"],
        storage_path=entry["storage_path"],
        format_original=entry["format_original"],
        created_at=entry["created_at"],
    )


@router.post("/evaluate")
async def evaluate_endpoint(request: EvaluateRequest) -> dict:
    return await run_evaluation(request)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "prompt-lab-standalone"}


app.include_router(router)


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    import uvicorn
    # Logging is already configured at module import via PROMPTLAB_LOG_LEVEL.
    port = int(os.environ.get("PROMPTLAB_PORT", "8001"))
    logger.info("starting PromptLab standalone port=%d log_level=%s storage=%s mongo_db=%s",
                port, _log_level, STORAGE_DIR, MONGO_DB)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level=_log_level.lower())
