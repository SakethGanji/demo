"""PromptLab evaluator — session-scoped classification eval against parquet datasets.

Flow per call:

1. Resolve session (REQUIRED) — pulls dataset_id, intent_classes, target_column,
   input_columns, model defaults when not overridden in the request.
2. Compute cache key over (prompt, dataset, model, splits, target, inputs). On
   cache hit, return the stored response immediately — no LLM calls, no spend.
3. Load parquet, restrict to ``evaluation_splits`` if supplied.
4. Pre-flight budget check against the session's ``max_cost_usd``.
5. Run LLM concurrently with retry/backoff (rate limits).
6. Aggregate per-split + overall metrics, sample failures.
7. Persist run to ``prompt_runs``, update session best, cache the response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from typing import Any

import pyarrow.parquet as pq
from anthropic import AsyncAnthropic
from fastapi import HTTPException
from sklearn.metrics import precision_recall_fscore_support

from app.infra.config import settings

from ..scorers import get_scorer
from ..schemas import (
    EvaluateRequest,
    EvaluateResponse,
    FailureSampleRow,
    FailureSummary,
    MetricsBlock,
    PerClassMetric,
    SplitMetrics,
)
from . import budget, mongo_store
from .dataset_files import dataset_path as _dataset_path
from .dataset_files import load_dataset_meta as _load_dataset_meta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONCURRENCY = 10
_FAILURE_SAMPLE_CAP = 50
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 1.0

_GT_CANDIDATES = ("gt", "GT", "label", "expected")
_SPLIT_CANDIDATES = ("split", "Split", "SPLIT")

# USD per 1M tokens (input, output). Prefix-matched against config.model.
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

# Pre-flight estimate.
_CHARS_PER_TOKEN = 4
_ESTIMATED_OUT_TO_IN_RATIO = 1.0


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _prompt_hash(prompt_system: str, prompt_user_template: str) -> str:
    h = hashlib.sha1()
    h.update(prompt_system.encode("utf-8"))
    h.update(b"\n")
    h.update(prompt_user_template.encode("utf-8"))
    return h.hexdigest()


def _row_id(input_fields: dict[str, Any]) -> str:
    try:
        payload = json.dumps(input_fields, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(input_fields)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def _model_rates(model: str) -> tuple[float, float]:
    for prefix, rates in _MODEL_COSTS.items():
        if model.startswith(prefix):
            return rates
    return _DEFAULT_COST


def _row_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    in_rate, out_rate = _model_rates(model)
    return (tokens_in / 1_000_000) * in_rate + (tokens_out / 1_000_000) * out_rate


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def _parse_input_cell(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    return {"value": raw}


def _resolve_dataset_path(dataset_id: str):
    try:
        path = _dataset_path(dataset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not path.exists():
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return path


def _detect_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _load_dataset(
    dataset_id: str,
    *,
    target_column: str | None,
    input_columns: list[str] | None,
    evaluation_splits: list[str],
) -> tuple[list[dict[str, Any]], list[str], str, list[str]]:
    """Return ``(rows, available_splits, resolved_target, resolved_inputs)``.

    Each row is ``{"input": dict, "gt": str, "split": str}``. ``input`` is
    restricted to ``input_columns`` only — demographic / metadata columns are
    NOT exposed to the LLM.
    """
    path = _resolve_dataset_path(dataset_id)
    table = pq.read_table(path)
    df = table.to_pandas()
    columns = list(df.columns)

    gt_col = target_column or _detect_column(columns, _GT_CANDIDATES)
    if gt_col is None or gt_col not in columns:
        raise HTTPException(
            422,
            (
                f"Dataset {dataset_id} target column not resolved: "
                f"requested={target_column!r}, columns={columns}"
            ),
        )

    split_col = _detect_column(columns, _SPLIT_CANDIDATES)
    default_split = evaluation_splits[0] if evaluation_splits else "all"

    if input_columns:
        missing = [c for c in input_columns if c not in columns]
        if missing:
            raise HTTPException(
                422,
                f"input_columns {missing} not present in dataset {dataset_id}",
            )
        resolved_inputs = list(input_columns)
    else:
        resolved_inputs = [
            c for c in columns if c != gt_col and c != split_col
        ]

    rows: list[dict[str, Any]] = []
    available: set[str] = set()
    for _, r in df.iterrows():
        inputs = {col: _safe_cell(r[col]) for col in resolved_inputs}
        split = str(r[split_col]) if split_col is not None else default_split
        rows.append({"input": inputs, "gt": str(r[gt_col]), "split": split})
        available.add(split)

    return rows, sorted(available), gt_col, resolved_inputs


def _safe_cell(v: Any) -> Any:
    """Normalise a pandas cell into JSON-friendly form (NaN → '')."""
    if v is None:
        return ""
    try:
        # Catches pandas/numpy NaN. ``v != v`` is True only for NaN.
        if v != v:  # type: ignore[comparison-overlap]
            return ""
    except (TypeError, ValueError):
        pass
    return v


# ---------------------------------------------------------------------------
# Prompt rendering + label extraction
# ---------------------------------------------------------------------------

def _render_user(template: str, row_inputs: dict[str, Any]) -> str:
    class _Defaulting(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return ""

    return template.format_map(
        _Defaulting({k: ("" if v is None else v) for k, v in row_inputs.items()})
    )


_FIRST_JSON_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _clean_json_fences(text: str) -> str:
    """Strip ```json fences and ```` markers."""
    return _FENCE_RE.sub("", text).strip()


def _extract_label(text: str, intent_classes: list[str]) -> str:
    """Pull a classification label out of the model's response."""
    if not isinstance(text, str):
        return ""

    lowered_classes = {c.lower(): c for c in intent_classes}
    cleaned = _clean_json_fences(text)

    def _scan_obj(obj: Any) -> str | None:
        if isinstance(obj, str):
            return lowered_classes.get(obj.strip().lower())
        if isinstance(obj, dict):
            # Prefer a "classified_intent" / "intent" / "label" key.
            for preferred in ("classified_intent", "intent", "label", "class"):
                if preferred in obj:
                    found = _scan_obj(obj[preferred])
                    if found:
                        return found
            for v in obj.values():
                found = _scan_obj(v)
                if found:
                    return found
        if isinstance(obj, list):
            for v in obj:
                found = _scan_obj(v)
                if found:
                    return found
        return None

    def _try_parse(candidate: str) -> str | None:
        try:
            return _scan_obj(json.loads(candidate))
        except (ValueError, TypeError):
            return None

    label = _try_parse(cleaned)
    if label:
        return label

    m = _FIRST_JSON_RE.search(cleaned)
    if m:
        label = _try_parse(m.group(0))
        if label:
            return label

    text_lower = cleaned.lower()
    for low, original in lowered_classes.items():
        if low in text_lower:
            return original

    return cleaned.strip().lower()


# ---------------------------------------------------------------------------
# LLM call (with retry)
# ---------------------------------------------------------------------------

async def _call_model_once(
    *, model: str, system: str, user: str, max_tokens: int,
    top_p: float | None, seed: int | None,
    anthropic_client: AsyncAnthropic | None, gemini_client: Any | None,
) -> tuple[str, int, int]:
    """One LLM call. Returns ``(text, tokens_in, tokens_out)``. Temperature=0."""
    if model.startswith("gemini-"):
        if gemini_client is None:
            raise RuntimeError("GEMINI_API_KEY is not configured but model is gemini-*")
        cfg: dict[str, Any] = {
            "temperature": 0,
            "max_output_tokens": max_tokens,
            "system_instruction": system,
            "response_mime_type": "application/json",
        }
        if top_p is not None:
            cfg["top_p"] = top_p
        if seed is not None:
            cfg["seed"] = seed
        resp = await gemini_client.aio.models.generate_content(
            model=model, contents=user, config=cfg,
        )
        text = (resp.text or "")
        usage = getattr(resp, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        return text, int(tokens_in), int(tokens_out)

    if anthropic_client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured but model is not gemini-*")
    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if top_p is not None:
        create_kwargs["top_p"] = top_p
    resp = await anthropic_client.messages.create(**create_kwargs)
    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    prediction = "".join(text_parts)
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    return prediction, int(tokens_in), int(tokens_out)


def _is_retryable(exc: Exception) -> bool:
    """Heuristic: retry on rate-limit / transient errors."""
    msg = str(exc).lower()
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or "rate_limit" in msg or "429" in msg:
        return True
    if "timeout" in name or "timeout" in msg:
        return True
    if "503" in msg or "502" in msg or "504" in msg:
        return True
    if "overloaded" in msg or "unavailable" in msg:
        return True
    return False


async def _call_model(
    *, model: str, system: str, user: str, max_tokens: int,
    top_p: float | None, seed: int | None,
    anthropic_client: AsyncAnthropic | None, gemini_client: Any | None,
) -> tuple[str, int, int]:
    """``_call_model_once`` with exponential backoff on retryable failures."""
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await _call_model_once(
                model=model, system=system, user=user, max_tokens=max_tokens,
                top_p=top_p, seed=seed,
                anthropic_client=anthropic_client, gemini_client=gemini_client,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= _MAX_RETRIES - 1 or not _is_retryable(exc):
                raise
            logger.warning(
                "LLM call failed (attempt %d/%d, will retry): %s",
                attempt + 1, _MAX_RETRIES, exc,
            )
            await asyncio.sleep(delay)
            delay *= 2
    # Unreachable, but for type-checker:
    assert last_exc is not None
    raise last_exc


async def _evaluate_row(
    row: dict[str, Any],
    *,
    model: str,
    system_prompt: str,
    user_template: str,
    intent_classes: list[str],
    top_p: float | None,
    seed: int | None,
    max_tokens: int,
    scorer_fn,
    anthropic_client: AsyncAnthropic | None,
    gemini_client: Any | None,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        rendered_user = _render_user(user_template, row["input"])
        t0 = time.perf_counter()
        try:
            text, tokens_in, tokens_out = await _call_model(
                model=model, system=system_prompt, user=rendered_user,
                max_tokens=max_tokens, top_p=top_p, seed=seed,
                anthropic_client=anthropic_client, gemini_client=gemini_client,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {
                "row_id": row.get("row_id") or _row_id(row["input"]),
                "input": row["input"],
                "gt": row["gt"],
                "split": row["split"],
                "predicted": "",
                "score": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": latency_ms,
                "error": str(exc),
            }
        latency_ms = (time.perf_counter() - t0) * 1000.0

        predicted = _extract_label(text, intent_classes)
        score_val = await scorer_fn(predicted, row["gt"])

        return {
            "row_id": row.get("row_id") or _row_id(row["input"]),
            "input": row["input"],
            "gt": row["gt"],
            "split": row["split"],
            "predicted": predicted,
            "score": float(score_val),
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "latency_ms": latency_ms,
        }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _split_metrics(
    rows: list[dict[str, Any]], intent_classes: list[str]
) -> SplitMetrics:
    n_rows = len(rows)
    if n_rows == 0:
        return SplitMetrics(
            accuracy=0.0,
            macro_f1=0.0,
            n_rows=0,
            n_failures=0,
            per_class={},
            confusion_matrix={
                c: {c2: 0 for c2 in intent_classes} for c in intent_classes
            },
        )

    gts = [r["gt"] for r in rows]
    preds = [r["predicted"] for r in rows]

    n_correct = sum(1 for g, p in zip(gts, preds) if g == p)
    accuracy = n_correct / n_rows

    label_set = list(intent_classes)
    for lbl in sorted({g for g in gts} | {p for p in preds}):
        if lbl not in label_set:
            label_set.append(lbl)

    precision, recall, f1, support = precision_recall_fscore_support(
        gts, preds, labels=label_set, zero_division=0,
    )
    per_class: dict[str, PerClassMetric] = {}
    for i, cls in enumerate(label_set):
        per_class[cls] = PerClassMetric(
            precision=float(precision[i]),
            recall=float(recall[i]),
            f1=float(f1[i]),
            support=int(support[i]),
        )

    configured_f1 = [per_class[c].f1 for c in intent_classes if c in per_class]
    macro_f1 = (sum(configured_f1) / len(configured_f1)) if configured_f1 else 0.0

    confusion: dict[str, dict[str, int]] = {
        c: {c2: 0 for c2 in intent_classes} for c in intent_classes
    }
    for g, p in zip(gts, preds):
        if g not in confusion:
            confusion[g] = {c2: 0 for c2 in intent_classes}
        if p not in confusion[g]:
            confusion[g][p] = 0
        confusion[g][p] += 1

    return SplitMetrics(
        accuracy=accuracy,
        macro_f1=macro_f1,
        n_rows=n_rows,
        n_failures=n_rows - n_correct,
        per_class=per_class,
        confusion_matrix=confusion,
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def run_evaluation(request: EvaluateRequest) -> EvaluateResponse:
    """Execute a classification evaluation.

    Self-contained: ``dataset_id`` + ``intent_classes`` from the request.
    ``session_id`` is optional — if provided, a session doc is upserted
    lazily so budget aggregates across calls. ``target_column`` and
    ``input_columns`` fall back to the dataset's sidecar metadata.
    """
    if request.task_type != "classification":
        raise HTTPException(
            400,
            f"task_type={request.task_type!r} is not supported (only 'classification')",
        )
    if not request.intent_classes:
        raise HTTPException(400, "intent_classes must be non-empty")
    if not request.dataset_id:
        raise HTTPException(422, "dataset_id is required")

    dataset_id = request.dataset_id
    intent_classes = list(request.intent_classes)

    # Sidecar metadata fills missing target/inputs (the upload requires them).
    meta = _load_dataset_meta(dataset_id) or {}
    target_column = request.target_column or meta.get("target_column")
    input_columns = request.input_columns or meta.get("input_columns")

    config = request.config or {}
    model = config.get("model") or "gemini-2.5-flash"
    top_p = config.get("top_p")
    seed = config.get("seed")
    max_tokens = int(config.get("max_tokens", 256))

    # Lazily upsert a session doc when caller supplies an id — gives us
    # cumulative budget and run grouping without needing a separate POST.
    if request.session_id:
        await mongo_store.upsert_session_minimal(
            request.session_id, max_cost_usd=request.max_cost_usd
        )

    try:
        scorer_fn = get_scorer(request.scorer)
    except KeyError as exc:
        raise HTTPException(400, str(exc))

    prompt_hash = _prompt_hash(request.prompt_system, request.prompt_user_template)

    # Cache lookup — idempotent on (prompt, dataset, model, splits, target, inputs).
    cache_key = mongo_store.make_cache_key(
        prompt_hash=prompt_hash,
        dataset_id=dataset_id,
        model=model,
        intent_classes=intent_classes,
        evaluation_splits=request.evaluation_splits,
        target_column=target_column or "",
        input_columns=input_columns,
    )
    if request.use_cache:
        cached = await mongo_store.get_cached_eval(cache_key)
        if cached is not None:
            if request.session_id:
                spent, max_usd = await mongo_store.get_budget(request.session_id)
            else:
                spent, max_usd = 0.0, request.max_cost_usd
            cached_resp = dict(cached)
            cached_resp.update({
                "dataset_id": dataset_id,
                "session_id": request.session_id,
                "cached": True,
                "cost_usd": 0.0,
                "budget_spent_usd": spent,
                "budget_max_usd": max_usd,
            })
            # If a session is in play, still record this iteration as a run
            # (cost=0, marked cached) so the agent's trend view doesn't have gaps.
            if request.session_id:
                run_doc = await mongo_store.insert_run(
                    session_id=request.session_id,
                    prompt_hash=prompt_hash,
                    prompt_system=request.prompt_system,
                    prompt_user_template=request.prompt_user_template,
                    model=model,
                    response=cached_resp,
                    parent_run_id=request.parent_run_id,
                    strategy=request.strategy,
                )
                cached_resp["run_id"] = run_doc["run_id"]
            return EvaluateResponse(**cached_resp)

    # Load + split-filter.
    all_rows, available_splits, resolved_target, resolved_inputs = _load_dataset(
        dataset_id,
        target_column=target_column,
        input_columns=input_columns,
        evaluation_splits=request.evaluation_splits,
    )
    if not all_rows:
        raise HTTPException(422, f"Dataset {dataset_id!r} is empty")

    if request.evaluation_splits:
        requested = set(request.evaluation_splits)
        rows = [r for r in all_rows if r["split"] in requested]
        if not rows:
            raise HTTPException(
                422,
                (
                    f"None of evaluation_splits={request.evaluation_splits} "
                    f"present in {dataset_id!r} (available: {available_splits})"
                ),
            )
    else:
        rows = all_rows

    n_rows = len(rows)

    # Pre-flight budget.
    in_rate, out_rate = _model_rates(model)
    estimated_chars = len(request.prompt_system) + len(request.prompt_user_template)
    est_tokens_in_per_row = max(1, estimated_chars // _CHARS_PER_TOKEN)
    est_tokens_out_per_row = max(1, int(est_tokens_in_per_row * _ESTIMATED_OUT_TO_IN_RATIO))
    projected_cost = n_rows * (
        (est_tokens_in_per_row / 1_000_000) * in_rate
        + (est_tokens_out_per_row / 1_000_000) * out_rate
    )

    if request.session_id:
        allowed, current_spend, max_usd = await mongo_store.check_budget(
            request.session_id, projected_cost
        )
        if not allowed:
            raise HTTPException(
                429,
                (
                    f"Session {request.session_id} would exceed budget: "
                    f"current=${current_spend:.4f}, projected=${projected_cost:.4f}, "
                    f"max=${max_usd:.4f}"
                ),
            )
    else:
        # No session — apply the per-request cap directly.
        max_usd = request.max_cost_usd
        if projected_cost > max_usd:
            raise HTTPException(
                429,
                (
                    f"Projected cost ${projected_cost:.4f} exceeds max_cost_usd "
                    f"${max_usd:.4f} (no session to aggregate against)"
                ),
            )

    anthropic_client: AsyncAnthropic | None = None
    gemini_client: Any | None = None
    if model.startswith("gemini-"):
        if not settings.gemini_api_key:
            raise HTTPException(500, "GEMINI_API_KEY is not configured")
        from google import genai
        gemini_client = genai.Client(api_key=settings.gemini_api_key)
    else:
        if not settings.anthropic_api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY is not configured")
        anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _evaluate_row(
            row=row,
            model=model,
            system_prompt=request.prompt_system,
            user_template=request.prompt_user_template,
            intent_classes=intent_classes,
            top_p=top_p,
            seed=seed,
            max_tokens=max_tokens,
            scorer_fn=scorer_fn,
            anthropic_client=anthropic_client,
            gemini_client=gemini_client,
            semaphore=semaphore,
        )
        for row in rows
    ]
    results = await asyncio.gather(*tasks)

    tokens_in_total = sum(r["tokens_in"] for r in results)
    tokens_out_total = sum(r["tokens_out"] for r in results)
    latencies = [r["latency_ms"] for r in results]
    latency_p50 = float(statistics.median(latencies)) if latencies else 0.0
    actual_cost = _row_cost_usd(model, tokens_in_total, tokens_out_total)
    if request.session_id:
        new_total = await budget.add_spend(request.session_id, actual_cost)
    else:
        new_total = actual_cost

    by_split_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_split_rows[r["split"]].append(r)
    by_split = {
        sp: _split_metrics(srs, intent_classes)
        for sp, srs in by_split_rows.items()
    }
    overall = _split_metrics(results, intent_classes)
    metrics_block = MetricsBlock(overall=overall, by_split=by_split)

    failures = [r for r in results if r["gt"] != r["predicted"]]
    pair_counter: Counter[str] = Counter()
    split_counter: Counter[str] = Counter()
    for f in failures:
        pair = f"{f['gt']}->{f['predicted']}"
        pair_counter[pair] += 1
        split_counter[f["split"]] += 1

    top_pairs = [p for p, _ in pair_counter.most_common(5)]

    if len(failures) > _FAILURE_SAMPLE_CAP:
        stride = max(1, len(failures) // _FAILURE_SAMPLE_CAP)
        sampled = failures[::stride][:_FAILURE_SAMPLE_CAP]
    else:
        sampled = failures

    failures_sample = [
        FailureSampleRow(
            row_id=f.get("row_id") or _row_id(f["input"]),
            split=f["split"],
            inputs=f["input"] if isinstance(f["input"], dict) else {"value": f["input"]},
            gt=f["gt"],
            predicted=f["predicted"],
            confusion_pair=f"{f['gt']}->{f['predicted']}",
        )
        for f in sampled
    ]

    failure_summary = FailureSummary(
        total=len(failures),
        by_confusion_pair=dict(pair_counter),
        by_split=dict(split_counter),
        top_confusion_pairs=top_pairs,
    )

    response_no_run = EvaluateResponse(
        dataset_id=dataset_id,
        session_id=request.session_id,
        run_id=None,
        prompt_hash=prompt_hash,
        metrics=metrics_block,
        failures_sample=failures_sample,
        failure_summary=failure_summary,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        cost_usd=actual_cost,
        latency_p50_ms=latency_p50,
        cached=False,
        budget_spent_usd=new_total,
        budget_max_usd=max_usd,
    )

    response_payload = response_no_run.model_dump(mode="json")
    run_id: str | None = None

    # Persist run + update session best only when we have a session_id.
    if request.session_id:
        run_doc = await mongo_store.insert_run(
            session_id=request.session_id,
            prompt_hash=prompt_hash,
            prompt_system=request.prompt_system,
            prompt_user_template=request.prompt_user_template,
            model=model,
            response=response_payload,
            parent_run_id=request.parent_run_id,
            strategy=request.strategy,
        )
        run_id = run_doc["run_id"]
        # Prefer holdout macro_f1 when present; otherwise overall.
        best_metric = overall.macro_f1
        for split_name in ("Holdout", "holdout", "test", "Test"):
            sm = by_split.get(split_name)
            if sm is not None and sm.n_rows > 0:
                best_metric = sm.macro_f1
                break
        await mongo_store.update_session_best(
            request.session_id, run_id=run_id, macro_f1=best_metric
        )

    # Cache regardless of session — same (prompt, dataset, model, ...) is free.
    cached_payload = dict(response_payload)
    if run_id:
        cached_payload["run_id"] = run_id
    await mongo_store.put_cached_eval(cache_key, cached_payload)

    return EvaluateResponse(**{**response_payload, "run_id": run_id})
