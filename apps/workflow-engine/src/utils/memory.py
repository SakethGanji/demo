"""Shared utilities for memory providers.

General-purpose helpers for chat history formatting, token counting,
embeddings, similarity, LLM summarization/extraction, and SQLite
connection management used across all memory provider implementations.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Shared DB Infrastructure
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).resolve().parents[2] / "agent_memory.db"

_local = threading.local()


def get_db_connection(attr_name: str, init_sql: list[str] | None = None) -> sqlite3.Connection:
    """
    Get a thread-local SQLite connection with WAL mode.

    Args:
        attr_name: Unique attribute name for thread-local storage (e.g. "buffer_conn").
        init_sql: SQL statements to run on first connection (CREATE TABLE, CREATE INDEX, etc).
    """
    conn = getattr(_local, attr_name, None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        if init_sql:
            for sql in init_sql:
                conn.execute(sql)
            conn.commit()
        setattr(_local, attr_name, conn)
    return conn


# ---------------------------------------------------------------------------
# Sync/Async Bridge
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """
    Run an async coroutine from a synchronous context.

    Handles the case where an event loop is already running (e.g. inside FastAPI)
    by dispatching to a new thread with its own event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Cypher Sanitization
# ---------------------------------------------------------------------------


def sanitize_cypher_label(value: str) -> str:
    """Sanitize a string for use as a Neo4j relationship type or label.

    Only allows uppercase letters, digits, and underscores.
    """
    sanitized = re.sub(r"[^A-Z0-9_]", "", value.upper().replace(" ", "_").replace("-", "_"))
    return sanitized if sanitized else "RELATED_TO"

# ---------------------------------------------------------------------------
# Token Counting
# ---------------------------------------------------------------------------


def count_tokens(text: str, method: str = "chars", model: str = "gpt-4") -> int:
    """
    Count tokens in text using specified method.

    Args:
        text: The text to count tokens for.
        method: "tiktoken" for actual token counting, "chars" for character-based estimate.
        model: Model name for tiktoken encoding (default "gpt-4").

    Returns:
        Estimated number of tokens.
    """
    if method == "tiktoken":
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model(model)
            return len(encoding.encode(text))
        except Exception:
            # Fallback to char-based if tiktoken fails
            return len(text) // 4
    else:
        # Character-based estimate: ~4 chars per token
        return len(text) // 4


def count_message_tokens(message: dict[str, str], method: str = "chars", model: str = "gpt-4") -> int:
    """Count tokens for a single message including role overhead."""
    # Add overhead for message structure (role, formatting)
    overhead = 4  # Roughly accounts for role and formatting tokens
    content = message.get("content", "")
    return count_tokens(content, method, model) + overhead


# ---------------------------------------------------------------------------
# History Formatting
# ---------------------------------------------------------------------------


def format_history_text(history: list[dict[str, str]]) -> str:
    """Format chat history as readable text for prompt injection."""
    if not history:
        return ""

    lines = []
    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['content']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Embeddings (delegated to llm_provider)
# ---------------------------------------------------------------------------

from ..engine.llm_provider import get_embedding  # noqa: F401


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Calculate cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity score between -1 and 1.
    """
    try:
        import numpy as np
        a_arr = np.array(a)
        b_arr = np.array(b)
        dot = np.dot(a_arr, b_arr)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
    except ImportError:
        # Fallback without numpy
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# LLM Calls for Summarization/Extraction
# ---------------------------------------------------------------------------


async def call_llm_for_summary(
    messages: list[dict[str, str]],
    model: str = "gemini-2.0-flash",
    max_tokens: int = 500,
    previous_summary: Optional[str] = None,
) -> str:
    """
    Call LLM to summarize conversation messages.

    Args:
        messages: Messages to summarize.
        model: Model to use for summarization.
        max_tokens: Maximum tokens for summary.
        previous_summary: Previous summary to update (for progressive summarization).

    Returns:
        Summary text.
    """
    from ..engine.llm_provider import call_llm

    history_text = format_history_text(messages)

    if previous_summary:
        prompt = f"""Update this conversation summary with the new messages.

Previous Summary:
{previous_summary}

New Messages:
{history_text}

Provide an updated, concise summary that incorporates the new information while maintaining important context from the previous summary. Keep the summary focused and under {max_tokens} tokens."""
    else:
        prompt = f"""Summarize the following conversation concisely, capturing the key topics, decisions, and any important context that should be remembered.

Conversation:
{history_text}

Provide a concise summary under {max_tokens} tokens."""

    response = await call_llm(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=max_tokens,
    )

    return response.text or ""


async def extract_entities(
    text: str,
    model: str = "gemini-2.0-flash",
    entity_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Extract entities from text using LLM.

    Args:
        text: Text to extract entities from.
        model: Model to use for extraction.
        entity_types: Types of entities to extract (default: person, place, organization, concept, fact).

    Returns:
        List of entities with name, type, and description.
    """
    from ..engine.llm_provider import call_llm

    if entity_types is None:
        entity_types = ["person", "place", "organization", "concept", "fact"]

    types_str = ", ".join(entity_types)

    prompt = f"""Extract entities from the following text. Focus on: {types_str}.

Text:
{text}

Return a JSON object with an "entities" key containing an array of entities. Each entity should have:
- "name": The entity name
- "type": One of [{types_str}]
- "description": Brief description based on context

Example output:
{{"entities": [
  {{"name": "John Smith", "type": "person", "description": "User's colleague in engineering"}},
  {{"name": "Project Alpha", "type": "concept", "description": "ML pipeline project due next Friday"}}
]}}

Return only the JSON object, no additional text."""

    response = await call_llm(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.text or "[]")
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "entities" in result:
            return result["entities"]
        return []
    except json.JSONDecodeError:
        return []


async def extract_relationships(
    text: str,
    model: str = "gemini-2.0-flash",
) -> list[tuple[str, str, str]]:
    """
    Extract entity relationships from text using LLM.

    Args:
        text: Text to extract relationships from.
        model: Model to use for extraction.

    Returns:
        List of (subject, predicate, object) tuples.
    """
    from ..engine.llm_provider import call_llm

    prompt = f"""Extract entities and relationships from the following text.

Text:
{text}

Return a JSON object with a "relationships" key containing an array. Each relationship should be an array of 3 elements: [subject, predicate, object].

Examples:
- "John works at Acme Corp" -> ["John", "works_at", "Acme Corp"]
- "The project uses TensorFlow" -> ["Project", "uses", "TensorFlow"]
- "Alice manages the marketing team" -> ["Alice", "manages", "marketing team"]

Example output:
{{"relationships": [["John", "works_at", "Acme Corp"], ["Project", "uses", "TensorFlow"]]}}

Use snake_case for predicates. If no clear relationships can be extracted, return {{"relationships": []}}."""

    response = await call_llm(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.text or "[]")
        if isinstance(result, list):
            return [tuple(r) for r in result if isinstance(r, list) and len(r) == 3]
        elif isinstance(result, dict) and "relationships" in result:
            return [tuple(r) for r in result["relationships"] if isinstance(r, list) and len(r) == 3]
        return []
    except json.JSONDecodeError:
        return []
