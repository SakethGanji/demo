"""F-X5b (X5 K3, J5, J2, sweep): one guard in front of every route, so a garbage input is a 400, never a 500.

`InputGuard` is a plain ASGI middleware installed outermost (it wraps the authz middleware). Before any route runs:

- **path**: a NUL or another control character → 400 `bad_id`; a path segment over `MAX_SEGMENT` characters → 400
  `bad_id` (a 10 KB id used to reach the file system as a too-long name).
- **query string**: a NUL / control character / lone surrogate in any name or value → 400 `bad_text`.
- **JSON bodies** (content type JSON, text/plain or none, i.e. whatever FastAPI or a route would parse as JSON): the
  body must be UTF-8 (400 `bad_json`), valid JSON without NaN / Infinity literals or a number that overflows a float
  (1e400) (400 `bad_number`), at most `MAX_DEPTH` levels deep (400 `too_deep`), and no string (key or value) may
  hold a NUL or a lone surrogate (400 `bad_text`). Name-like fields (`by`, `name`, `*_by`, `*_id`, …) refuse every
  control character. Form-urlencoded bodies get the same text checks. Multipart bodies (uploads) are not buffered;
  their form fields are caught later by the Postgres DataError handler and the audit writer's sanitiser.
- a route that raises `SystemExit` (argparse, a CLI helper) answers 500 instead of stopping the server (J2).

`install(app)` also registers the exception handlers that turn engine errors raised by a garbage value into 400s:
RequestValidationError (422, without echoing the input: the default echo of a NaN / bytes value was itself a 500),
OverflowError (a 2**64 int into SQLite), RecursionError, and psycopg's DataError (NUL / bad escapes on Postgres).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any
from urllib.parse import parse_qsl

log = logging.getLogger("ensemble.input_guard")

MAX_DEPTH = int(os.environ.get("ENSEMBLE_JSON_MAX_DEPTH", "64"))
MAX_SEGMENT = int(os.environ.get("ENSEMBLE_MAX_PATH_SEGMENT", "512"))
#: bodies above this are passed through unchecked (none of the JSON routes takes one this big)
MAX_CHECK_BYTES = int(os.environ.get("ENSEMBLE_JSON_CHECK_MAX_MB", "256")) * 1024 * 1024

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # control characters but tab / newline / carriage return
_CTRL_ALL = re.compile(r"[\x00-\x1f\x7f]")
_SURR = re.compile(r"[\ud800-\udfff]")
#: body keys holding a name or an id: no control character at all (a newline or a tab is not part of a name)
_NAME_KEYS = {"by", "name", "actor", "owner", "reviewer", "principal", "team", "user", "who", "table", "kind", "action",
              "model_name", "label", "target", "id"}
_NAME_SUFFIX = ("_by", "_id", "_ids")
#: the body fields naming who acts (authz/deps.BY_FIELDS plus the deciders): text or absent
BY_KEYS = ("by", "actor", "created_by", "requested_by", "decided_by")
#: top-level selectors that are always one text value (an action, a kind, a table name)
TEXT_KEYS = ("action", "kind", "table")


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def bad_text(s: str, *, strict: bool = False) -> str | None:
    """Why a text is refused, or None. `strict`: every control character (names, ids, paths, query values)."""
    if "\x00" in s:
        return "holds a NUL character"
    if (_CTRL_ALL if strict else _CTRL).search(s):
        return "holds a control character"
    if _SURR.search(s):
        return "holds an unpaired UTF-16 surrogate"
    return None


def _name_key(k: Any) -> int:
    """0: free text; 1: a name (no control character); 2: an id (also at most MAX_SEGMENT characters)."""
    if not isinstance(k, str):
        return 0
    if k == "id" or k.endswith(("_id", "_ids")):
        return 2
    return 1 if (k in _NAME_KEYS or k.endswith(_NAME_SUFFIX)) else 0


def _reject_constant(s: str) -> Any:
    raise Refused("bad_number", f"{s} is not a number JSON allows (send a finite number, or null).")


def _float(s: str) -> float:
    f = float(s)
    if math.isinf(f):
        raise Refused("bad_number", f"{s[:40]} is out of range for a number (it overflows to infinity).")
    return f


def check_json(raw: bytes, declared: bool = True) -> None:
    """Raise Refused when the JSON body breaks a rule above. `declared`: the content type says JSON, so a body that is
    not UTF-8 JSON is refused too; otherwise (no content type, text/plain) a body that is not JSON is left alone."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if not declared:
            return
        raise Refused("bad_json", "The request body is not UTF-8 text.") from None
    if not text.strip():
        return
    try:
        doc = json.loads(text, parse_constant=_reject_constant, parse_float=_float)
    except Refused:
        raise
    except RecursionError:
        raise Refused("too_deep", f"The JSON body is nested too deeply (at most {MAX_DEPTH} levels).") from None
    except ValueError as e:
        if not declared:
            return
        raise Refused("bad_json", f"The request body is not valid JSON ({str(e).split(':')[0][:80]}).") from None
    walk(doc)
    if isinstance(doc, dict):  # X5 m-a: who is acting is a name (text) on every route; X5 B7: so are these selectors
        for k in TEXT_KEYS:
            if doc.get(k) is not None and not isinstance(doc[k], str):
                raise Refused("bad_field", f"'{k}' is text.")
        for k in BY_KEYS:
            if doc.get(k) is not None and not isinstance(doc[k], str):
                raise Refused("bad_by", f"'{k}' is a name (text).")


def walk(doc: Any) -> None:
    stack: list[tuple[Any, int, int]] = [(doc, 1, 0)]
    while stack:
        x, depth, strict = stack.pop()
        if isinstance(x, str):
            if strict == 2 and len(x) > MAX_SEGMENT:  # X5 B5: a 20 KB id reached the file system as a name
                raise Refused("bad_id", f"An id in the body is longer than {MAX_SEGMENT} characters: no id is that long.")
            why = bad_text(x, strict=bool(strict))
            if why:
                raise Refused("bad_text", f"A text value in the body {why}." + (" Names and ids take printable characters only."
                                                                               if strict and "control" in why else ""))
        elif isinstance(x, (dict, list)):
            if depth > MAX_DEPTH:
                raise Refused("too_deep", f"The JSON body is nested too deeply (at most {MAX_DEPTH} levels).")
            if isinstance(x, dict):
                for k, v in x.items():
                    why = bad_text(k, strict=True)
                    if why:
                        raise Refused("bad_text", f"A key in the body {why}.")
                    stack.append((v, depth + 1, _name_key(k)))
            else:
                stack.extend((v, depth + 1, strict) for v in x)
        elif isinstance(x, float) and not math.isfinite(x):  # pragma: no cover - parse_float refuses these already
            raise Refused("bad_number", "A number in the body is not finite.")


def check_query(qs: bytes) -> None:
    if not qs:
        return
    for k, v in parse_qsl(qs.decode("latin-1"), keep_blank_values=True):
        for s in (k, v):
            why = bad_text(s, strict=True)
            if why:
                raise Refused("bad_text", f"The query parameter {k[:40]!r} {why}." if s is v else f"A query parameter name {why}.")


def check_path(path: str) -> None:
    why = bad_text(path, strict=True)
    if why:
        raise Refused("bad_id", f"The path {why}.")
    if any(len(seg) > MAX_SEGMENT for seg in path.split("/")):
        raise Refused("bad_id", f"A path segment is longer than {MAX_SEGMENT} characters: no id is that long.")


#: LOOSE_MAX: a body without a JSON content type is only looked at when it is at most this big (a raw upload is not)
LOOSE_MAX = 8 * 1024 * 1024
#: routes that stream their body themselves (the raw / multipart upload)
_STREAMED = re.compile(r"^/api/uploads/?$")


def _kind(ctype: str) -> str:
    c = ctype.split(";")[0].strip().lower()
    if c.endswith("/json") or c.endswith("+json"):
        return "json"
    if c == "application/x-www-form-urlencoded":
        return "form"
    if not c or c == "text/plain":
        return "loose"
    return ""


class InputGuard:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            check_path(scope.get("path") or "")
            check_query(scope.get("query_string") or b"")
            if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
                hdr = {k.lower(): v for k, v in scope.get("headers") or []}
                kind = _kind(hdr.get(b"content-type", b"").decode("latin-1"))
                try:
                    clen = int(hdr.get(b"content-length") or -1)
                except ValueError:
                    clen = -1
                if kind == "loose" and not 0 <= clen <= LOOSE_MAX or _STREAMED.match(scope.get("path") or ""):
                    kind = ""
                if kind:
                    chunks, more, size = [], True, 0
                    while more:
                        msg = await receive()
                        if msg["type"] == "http.disconnect":
                            return
                        b = msg.get("body", b"")
                        chunks.append(b)
                        size += len(b)
                        more = msg.get("more_body", False)
                    raw = b"".join(chunks)
                    if size <= MAX_CHECK_BYTES:
                        if kind == "form":
                            check_query(raw)
                        else:
                            check_json(raw, declared=kind == "json")
                    sent = False

                    async def replay() -> dict[str, Any]:
                        nonlocal sent
                        if not sent:
                            sent = True
                            return {"type": "http.request", "body": raw, "more_body": False}
                        return await receive()

                    receive = replay
        except Refused as e:
            return await _answer(send, 400, {"detail": {"code": e.code, "message": e.message}})
        try:
            await self.app(scope, receive, send_wrapper)
        except SystemExit as e:  # J2: never let a route stop the server
            log.error("a route raised SystemExit (%s) on %s %s", e, scope.get("method"), scope.get("path"))
            if not started:
                await _answer(send, 500, {"detail": {"code": "internal_exit", "message": "The request failed; the server "
                                                                                          "keeps running."}})


async def _answer(send: Any, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


def install(app: Any) -> None:
    """Called last in create_app: the guard becomes the outermost middleware; the 400 handlers."""
    from fastapi.exceptions import RequestValidationError

    from .lake.values import SafeJSONResponse

    async def validation(request: Any, exc: RequestValidationError) -> Any:
        """FastAPI's 422, without echoing `input` / `ctx` (the echo of a NaN or of bytes could not be serialised)."""
        errs = []
        for e in exc.errors()[:20]:
            errs.append({"loc": [x if isinstance(x, (int, str)) else str(x) for x in e.get("loc") or []][:10],
                         "msg": str(e.get("msg") or "")[:300], "type": str(e.get("type") or "")[:60]})
        return SafeJSONResponse({"detail": errs}, status_code=422)

    async def overflow(request: Any, exc: OverflowError) -> Any:
        return SafeJSONResponse({"detail": {"code": "value_out_of_range",
                                            "message": "A number in the request is out of range (integers are 64-bit)."}},
                                status_code=400)

    async def recursion(request: Any, exc: RecursionError) -> Any:
        return SafeJSONResponse({"detail": {"code": "too_deep", "message": "The request is nested too deeply."}}, status_code=400)

    from .frames import FrameError

    gone_rx = re.compile(r"(?:A file of|The legacy CSV of) (\S+) \(([0-9a-f]{6,64})")

    async def frame_error(request: Any, exc: FrameError) -> Any:
        """X4 m4: a run / version whose input data is gone (deleted, purged) or names nothing → 410 / 404, not 500."""
        msg = str(exc)
        m = gone_rx.search(msg)
        if m:
            vid, sha = m.group(1), m.group(2)
            try:
                with request.app.state.db.connect() as con:
                    b = con.execute("SELECT state FROM blobs WHERE sha256 LIKE ?", (sha + "%",)).fetchone()
                    d = con.execute("SELECT state FROM datasets WHERE id = ?", (vid,)).fetchone()
            except Exception:  # noqa: BLE001
                b = d = None
            if (b and b[0] == "purged") or (d and d[0] == "deleted"):
                what = "purged" if b and b[0] == "purged" else "deleted"
                return SafeJSONResponse({"detail": {"code": "purged" if what == "purged" else "version_deleted", "version_id": vid,
                                                    "message": f"The data of {vid} was {what}: it cannot be read any more."}},
                                        status_code=410)
            return SafeJSONResponse({"detail": {"code": "blob_missing", "version_id": vid, "message": msg}}, status_code=409)
        return SafeJSONResponse({"detail": {"code": "input_not_found", "message": msg[:300]}}, status_code=404)

    app.add_exception_handler(FrameError, frame_error)
    from .lake.pages import PageError

    async def page_error(request: Any, exc: PageError) -> Any:  # X5 B2: an unknown ?table= is the route's 4xx
        return SafeJSONResponse({"detail": exc.payload()}, status_code=exc.status)

    app.add_exception_handler(PageError, page_error)
    from .datasets import DatasetConflict, DatasetError

    async def ds_conflict(request: Any, exc: DatasetConflict) -> Any:  # X5 m-j: not a DatasetError, used to be a 500
        return SafeJSONResponse({"detail": {"code": "conflict", "message": str(exc)[:500]}}, status_code=409)

    async def ds_error(request: Any, exc: DatasetError) -> Any:  # the message is written for the user (datasets.py)
        return SafeJSONResponse({"detail": str(exc)[:1000]}, status_code=400)

    app.add_exception_handler(DatasetConflict, ds_conflict)
    app.add_exception_handler(DatasetError, ds_error)
    app.add_exception_handler(RequestValidationError, validation)
    app.add_exception_handler(OverflowError, overflow)
    app.add_exception_handler(RecursionError, recursion)
    import sqlite3

    bad_value = re.compile(r"cannot contain NUL|unsupported Unicode escape|invalid byte sequence|surrogates not allowed",
                           re.I)

    def _pg_value_error(exc: BaseException) -> bool:
        try:
            import psycopg
        except ImportError:  # pragma: no cover - SQLite-only installs
            return False
        e: BaseException | None = exc
        while e is not None:
            if isinstance(e, psycopg.DataError) or bad_value.search(str(e)):
                return True
            e = e.__cause__
        return False

    async def db_error(request: Any, exc: Exception) -> Any:
        """Postgres refusing a value (NUL in text, a bad escape) is the request's fault: 400. db_pg wraps psycopg
        errors as sqlite3 errors; anything else stays a logged 500."""
        if _pg_value_error(exc):
            log.warning("the database refused a value: %s", str(exc).splitlines()[0][:200])
            return SafeJSONResponse({"detail": {"code": "bad_text", "message": "A value in the request cannot be stored "
                                                                               "(for example a NUL character)."}},
                                    status_code=400)
        from fastapi.responses import PlainTextResponse

        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc)
        return PlainTextResponse("Internal Server Error", status_code=500)

    app.add_exception_handler(sqlite3.Error, db_error)
    try:
        import psycopg

        app.add_exception_handler(psycopg.Error, db_error)
    except ImportError:  # pragma: no cover
        pass
    app.add_middleware(InputGuard)
