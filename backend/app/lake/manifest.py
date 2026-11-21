"""P02: canonical JSON, every hash of plan §3.3, and the startup self-check (§7 risk 3).

Rule (§3.3): nothing stored or compared across time is computed with an engine's private hash function. Only sha256
over bytes defined here is used, plus splitmix64 (numpy, defined here) for sampling.

Canonical JSON (§3.2): `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8; every string
(keys included) NFC-normalised; no floats anywhere (a writer that meets one raises); integers within int64;
timestamps `YYYY-MM-DDTHH:MM:SSZ` (`ts()`).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Sequence

import numpy as np

FORMAT = "ensemble.manifest/1"
CHUNK = 8 * 1024 * 1024
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
TABLE_ROLES = ("base", "append", "upsert", "delete")
#: the first audit row of a team chains onto this (C §1.7; the chain itself is written from P19)
AUDIT_GENESIS = "0" * 64


class CanonicalError(ValueError):
    """A value that hashed JSON may not hold (a float, NaN, a big int, a non-str key, an unknown type)."""


# == canonical JSON =================================================================================================

def _norm(v: Any, path: str) -> Any:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        if not INT64_MIN <= v <= INT64_MAX:
            raise CanonicalError(f"{path}: integer {v} does not fit in int64")
        return int(v)
    if isinstance(v, float):
        raise CanonicalError(f"{path}: float {v!r} in hashed JSON (write shares, thresholds and params as decimal strings)")
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, x in v.items():
            if not isinstance(k, str):
                raise CanonicalError(f"{path}: key {k!r} is not a string")
            nk = unicodedata.normalize("NFC", k)
            if nk in out:
                raise CanonicalError(f"{path}: two keys normalise to {nk!r}")
            out[nk] = _norm(x, f"{path}.{nk}")
        return out
    if isinstance(v, (list, tuple)):
        return [_norm(x, f"{path}[{i}]") for i, x in enumerate(v)]
    if isinstance(v, np.integer):
        return _norm(int(v), path)
    if isinstance(v, np.bool_):
        return bool(v)
    raise CanonicalError(f"{path}: {type(v).__name__} is not allowed in hashed JSON")


def canonical(obj: Any) -> bytes:
    """The canonical UTF-8 bytes of `obj` (raises CanonicalError on anything §3.2 forbids)."""
    return json.dumps(_norm(obj, "$"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def ts(dt: datetime | None = None) -> str:
    """A hashed timestamp: UTC, seconds, 'Z'."""
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def decimal_str(x: float | int | str, places: int = 6) -> str:
    """A share / threshold as decimal text for hashed JSON ("0.2", not 0.2)."""
    if isinstance(x, str):
        return x
    s = f"{x:.{places}f}".rstrip("0").rstrip(".")
    return s or "0"


# == hashes (§3.3) ==================================================================================================

def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_sha256(src: Path | str | BinaryIO) -> tuple[str, int]:
    """(sha256, bytes) of a file or an open binary stream, read in 8 MB chunks."""
    h, n = hashlib.sha256(), 0
    f = open(src, "rb") if isinstance(src, (str, Path)) else src
    try:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            n += len(b)
    finally:
        if isinstance(src, (str, Path)):
            f.close()
    return h.hexdigest(), n


def table_sha256(parts: Sequence[tuple[str, str]]) -> str:
    """sha256("ensemble.table/1\\n" + "\\n".join(f"{role}:{part_sha}")) over the parts in order."""
    if not parts:
        raise ValueError("a table has at least one part")
    for role, sha in parts:
        if role not in TABLE_ROLES or len(sha) != 64:
            raise ValueError(f"bad part ({role!r}, {sha!r})")
    return sha256_hex(("ensemble.table/1\n" + "\n".join(f"{r}:{s}" for r, s in parts)).encode("ascii"))


def schema_sha256(schema: list[dict[str, Any]]) -> str:
    return sha256_hex(canonical(schema))


def data_sha256(data: dict[str, Any]) -> str:
    """The dedupe identity: the canonical `manifest.data` (tables, schemas, keys, relations), independent of who / why."""
    return sha256_hex(canonical(data))


def seal(manifest: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    """Fill `data_sha256`, then (manifest, canonical bytes, manifest_sha256). The bytes are the manifest blob."""
    if manifest.get("format") != FORMAT:
        raise CanonicalError(f"format must be {FORMAT}")
    m = {**manifest, "data_sha256": data_sha256(manifest["data"])}
    b = canonical(m)
    return m, b, sha256_hex(b)


def check_manifest_bytes(b: bytes) -> tuple[dict[str, Any], list[str]]:
    """Parse a manifest blob and list what is wrong with it: not canonical, or `data_sha256` not matching `data`."""
    problems: list[str] = []
    m = json.loads(b.decode("utf-8"))
    try:
        if canonical(m) != b:
            problems.append("manifest bytes are not canonical")
    except CanonicalError as e:
        problems.append(f"manifest is not canonical: {e}")
    try:
        if m.get("data_sha256") != data_sha256(m.get("data") or {}):
            problems.append("data_sha256 does not match manifest.data")
    except CanonicalError as e:
        problems.append(f"manifest.data is not canonical: {e}")
    return m, problems


class SplitHasher:
    """sha256("ensemble.split/1\\n" + per row int64-LE(__row_id) ‖ int8(fold)), rows in strictly increasing __row_id,
    fed in batches (streamed; independent of the Parquet writer)."""

    _dt = np.dtype([("r", "<i8"), ("f", "i1")])

    def __init__(self, prefix: bytes = b"ensemble.split/1\n") -> None:
        self.h = hashlib.sha256(prefix)
        self.last: int | None = None
        self.n = 0

    def update(self, row_ids: Any, folds: Any) -> None:
        r = np.asarray(row_ids, dtype=np.int64)
        f = np.asarray(folds)
        if r.shape != f.shape or r.ndim != 1:
            raise ValueError("row ids and folds must be 1-d and the same length")
        if not len(r):
            return
        if f.min() < -128 or f.max() > 127:
            raise ValueError("fold out of int8 range")
        if (np.diff(r) <= 0).any() or (self.last is not None and r[0] <= self.last):
            raise ValueError("split rows must be in strictly increasing __row_id order")
        a = np.empty(len(r), dtype=self._dt)
        a["r"], a["f"] = r, f.astype(np.int8)
        self.h.update(a.tobytes())
        self.last, self.n = int(r[-1]), self.n + len(r)

    def hexdigest(self) -> str:
        return self.h.hexdigest()


def split_sha256(row_ids: Any, folds: Any) -> str:
    h = SplitHasher()
    h.update(row_ids, folds)
    return h.hexdigest()


def split_sha256_parquet(path: Path | str, batch_rows: int = 1_000_000) -> tuple[str, int]:
    """(split_sha256, n_rows) recomputed from a split file `(__row_id BIGINT, fold TINYINT)`, streamed by batches."""
    import pyarrow.parquet as pq

    h = SplitHasher()
    pf = pq.ParquetFile(str(path))
    for batch in pf.iter_batches(batch_size=batch_rows, columns=["__row_id", "fold"]):
        h.update(batch.column(0).to_numpy(zero_copy_only=False), batch.column(1).to_numpy(zero_copy_only=False))
    return h.hexdigest(), h.n


def sample_sha256(row_ids: Any) -> str:
    """sha256("ensemble.sample/1\\n" + int64-LE selected __row_id, sorted)."""
    r = np.unique(np.asarray(row_ids, dtype=np.int64))  # sorted, and a row is selected once
    return sha256_hex(b"ensemble.sample/1\n" + r.astype("<i8").tobytes())


def join_frame_sha256(spec_sha: str, input_table_shas: Sequence[str]) -> str:
    """sha256("ensemble.join/1\\n" + spec_sha256 + "\\n" + "\\n".join(input table shas in spec order))."""
    return sha256_hex(("ensemble.join/1\n" + spec_sha + "\n" + "\n".join(input_table_shas)).encode("ascii"))


def audit_row_hash(prev_hash: str | None, row: dict[str, Any]) -> str:
    """sha256(prev_hash ‖ canonical(row)) for the per-team audit chain (C §1.7; written from P19)."""
    return sha256_hex((prev_hash or AUDIT_GENESIS).encode("ascii") + canonical(row))


# == splitmix64 (R13: sampling and new folds; numpy, library-independent) ==========================================

_GOLDEN = np.uint64(0x9E3779B97F4A7C15)
_M1, _M2 = np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB)


def splitmix64(x: Any) -> np.ndarray:
    """The splitmix64 output for state `x` (uint64, wrapping): z = x + φ; z = (z ^ z>>30)·M1; z = (z ^ z>>27)·M2;
    z ^ z>>31. splitmix64(0) = 0xE220A8397B1DCDAF, the reference generator's first output from seed 0."""
    z = np.asarray(x).astype(np.uint64, copy=True)
    with np.errstate(over="ignore"):
        z += _GOLDEN
        z = (z ^ (z >> np.uint64(30))) * _M1
        z = (z ^ (z >> np.uint64(27))) * _M2
        return z ^ (z >> np.uint64(31))


def _splitmix64_py(x: int) -> int:
    m = (1 << 64) - 1
    z = (x + 0x9E3779B97F4A7C15) & m
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & m
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & m
    return z ^ (z >> 31)


# == engine versions (recorded in every manifest) =================================================================

def engine_versions() -> dict[str, Any]:
    import duckdb
    import pandas
    import pyarrow

    from .. import config

    return {"duckdb": duckdb.__version__, "pyarrow": pyarrow.__version__, "pandas": pandas.__version__,
            "parquet": {"compression": config.PARQUET_COMPRESSION, "level": config.PARQUET_LEVEL}}


# == the startup self-check (§7 risk 3: a runtime guard, not a test suite) =======================================
# Fixed inputs, hashed with the code above and compared with constants. A mismatch means a library, the interpreter
# or this module changed what a stored hash means, so every hash stored before would stop verifying: refuse to start.

_VEC_MANIFEST = {
    "format": FORMAT,
    "dataset": {"root_id": "ds_00000001", "version_id": "ds_00000002", "version_no": 2, "team_id": "tm_default",
                "project_id": "prj_00000001"},
    "data": {"primary_table": "applications",
             "tables": [{"name": "applications", "table_sha256": "a" * 64, "n_rows": 10, "n_cols": 3,
                         "row_id": {"column": "__row_id", "min": 0, "max": 9, "sorted": True},
                         "schema": [{"name": "__row_id", "type": "BIGINT", "nullable": False},
                                    {"name": "zip", "type": "VARCHAR", "nullable": True, "reason": "leading zeros"},
                                    {"name": "Café", "type": "DOUBLE", "nullable": True}],  # NFD in, NFC hashed
                         "schema_sha256": "b" * 64,
                         "files": [{"sha256": "c" * 64, "key": "blobs/cc/" + "c" * 64 + ".parquet", "role": "base",
                                    "bytes": 1234, "n_rows": 10, "row_id_min": 0, "row_id_max": 9}],
                         "keys": [{"kind": "primary", "columns": ["zip"], "valid": True, "declared_by": "pr_x"}]}],
             "relations": []},
    "provenance": {"parent": None, "other_parents": [], "op": "selfcheck", "ops": [], "sources": [],
                   "checks": {"warnings": []}, "created_at": "2026-11-21T00:00:00Z", "created_by": "system",
                   "reason": "", "engine": {"holdout_share": "0.2", "big": INT64_MAX, "neg": INT64_MIN}},
    "legacy": None,
}
_VEC_SPLIT_IDS = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55]
_VEC_SPLIT_FOLDS = [-1, 0, 1, 2, 3, 4, -1, -2, -3, 0]
_VEC_SAMPLE = [55, 3, 21, 3, 0, 2**40]

#: Pinned at P02 (duckdb 2.0.1, pyarrow 24.0.0, pandas 2.3.3, Python 3.13). table / split / sample were also recomputed
#: independently (sha256sum, struct.pack) when pinned; splitmix64_0 is the reference generator's published value.
EXPECTED = {
    "sha256_abc": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    "canonical_prefix": '{"data":{"primary_table":"applications","relations":[],"tables":[{"files":[{"bytes":1234,',
    "data_sha256": "121f267c6ab9be43a212164482924746880d92b0acedba3cf549bc0f42791e9b",
    "manifest_sha256": "d41a43eaebced3db711a241ed2515daa81d47d9eaf6649aa73ce964cbe5ce563",
    "schema_sha256": "91a02eaf6100aab2cfef84f422504eef207cd0f3099eec984d12df06c82fb2da",
    "table_sha256": "cb1bf2017a0ecceb126c0eed0184f8e4a3316377977311ff8e964e3517e4d7f1",
    "split_sha256": "f9c6144af3a0df2ff8fd6460ed2ac324eb8167d9b4a433c8ed4fdcf0d147dc4d",
    "split_parquet_sha256": "f9c6144af3a0df2ff8fd6460ed2ac324eb8167d9b4a433c8ed4fdcf0d147dc4d",
    "sample_sha256": "125e4915ae6c3f2cdc692b15d5fa428a1919619944a64125e3baf31efa6f6bb9",
    "join_sha256": "0c7fc6cb3a237908e51479e9f90d3906a333eff66c435ae241493d463e6fd6da",
    "audit_chain": "e30b6475dc15e41b9b5629263826812ef4c350bc9ad6034ef1e32b2209932ea3",
    "splitmix64_0": "e220a8397b1dcdaf",
    "splitmix64_vec": "678a54624904d8d7c7d7cab871bb55261c6bee9c6abb1d34592978dc38bcd13d",
}


def _vectors() -> dict[str, Any]:
    """What the code computes for each fixed input (the 'got' side of the self-check)."""
    import tempfile

    out: dict[str, Any] = {"sha256_abc": sha256_hex(b"abc")}
    m, b, msha = seal(_VEC_MANIFEST)
    out["canonical_prefix"] = b.decode("utf-8")[:len(EXPECTED["canonical_prefix"])]
    out["canonical_nfc"] = "Café" in b.decode("utf-8") and "Café" not in b.decode("utf-8")
    out["data_sha256"], out["manifest_sha256"] = m["data_sha256"], msha
    out["schema_sha256"] = schema_sha256(_VEC_MANIFEST["data"]["tables"][0]["schema"])
    out["table_sha256"] = table_sha256([("base", "c" * 64), ("append", "d" * 64)])
    out["split_sha256"] = split_sha256(_VEC_SPLIT_IDS, _VEC_SPLIT_FOLDS)
    out["sample_sha256"] = sample_sha256(_VEC_SAMPLE)
    out["join_sha256"] = join_frame_sha256("e" * 64, ["a" * 64, "f" * 64])
    h1 = audit_row_hash(None, {"action": "dataset_version_created", "ts": "2026-11-21T00:00:00Z", "n": 1})
    out["audit_chain"] = audit_row_hash(h1, {"action": "split_created", "ts": "2026-11-21T00:00:01Z", "n": 2})
    out["splitmix64_0"] = f"{int(splitmix64(np.array([0], dtype=np.uint64))[0]):016x}"
    ids = np.array([0, 1, 42, 2**40, 2**63 - 1], dtype=np.uint64)
    vec = splitmix64(ids)
    out["splitmix64_py_equal"] = all(int(a) == _splitmix64_py(int(i)) for a, i in zip(vec, ids))
    out["splitmix64_vec"] = sha256_hex(vec.astype("<u8").tobytes())
    # the engine stack: write the 10-row split as Parquet with DuckDB, read it back with pyarrow, re-hash the rows
    from . import duck

    with tempfile.TemporaryDirectory(prefix="ens-selfcheck-") as d:
        p = Path(d) / "split.parquet"
        with duck.job(mem_mb=128, threads=1) as con:
            vals = ",".join(f"({i}::BIGINT,{f}::TINYINT)" for i, f in zip(_VEC_SPLIT_IDS, _VEC_SPLIT_FOLDS))
            con.execute(f"COPY (SELECT * FROM (VALUES {vals}) t(__row_id, fold) ORDER BY __row_id) TO '{p}' "
                        "(FORMAT parquet, COMPRESSION zstd)")
        out["split_parquet_sha256"], _ = split_sha256_parquet(p)
    try:
        canonical({"x": 0.5})
        out["float_refused"] = False
    except CanonicalError:
        out["float_refused"] = True
    try:
        split_sha256([2, 1], [0, 0])
        out["unsorted_refused"] = False
    except ValueError:
        out["unsorted_refused"] = True
    return out


def selfcheck() -> dict[str, Any]:
    """{"ok", "vectors": [{name, ok, expected, got}], "engine"}. Never raises; see assert_selfcheck()."""
    rows: list[dict[str, Any]] = []
    try:
        got = _vectors()
    except Exception as e:  # noqa: BLE001  (a broken engine is a failed self-check, reported, not a crash here)
        return {"ok": False, "vectors": [{"name": "compute", "ok": False, "error": f"{type(e).__name__}: {e}"}],
                "engine": _safe_engine()}
    for k, exp in EXPECTED.items():
        rows.append({"name": k, "ok": bool(exp) and got.get(k) == exp, "expected": exp, "got": got.get(k)})
    rows.append({"name": "split_parquet_roundtrip", "ok": got["split_parquet_sha256"] == got["split_sha256"],
                 "expected": got["split_sha256"], "got": got["split_parquet_sha256"]})
    for k in ("canonical_nfc", "splitmix64_py_equal", "float_refused", "unsorted_refused"):
        rows.append({"name": k, "ok": got[k] is True, "expected": True, "got": got[k]})
    return {"ok": all(r["ok"] for r in rows), "vectors": rows, "engine": _safe_engine()}


def _safe_engine() -> dict[str, Any]:
    try:
        return engine_versions()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


class SelfCheckFailed(RuntimeError):
    pass


def assert_selfcheck() -> dict[str, Any]:
    """Startup: raise SelfCheckFailed (the API refuses to start) unless every vector matches."""
    r = selfcheck()
    if not r["ok"]:
        bad = [v["name"] for v in r["vectors"] if not v["ok"]]
        raise SelfCheckFailed(f"lake self-check failed ({', '.join(bad)}): stored hashes would no longer verify with this "
                              f"code / library stack {r['engine']}. Refusing to start.")
    return r

