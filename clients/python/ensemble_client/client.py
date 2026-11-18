"""The client: `Ensemble(url)` with `.datasets`, `.runs`, `.registry` and `.gym`; `Run` and `Version` objects."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any, Iterator

import httpx

TERMINAL_EVENTS = ("run_succeeded", "run_failed", "run_cancelled")


class ApiError(RuntimeError):
    """The API answered with an error status; `.status` and `.detail` say what."""

    def __init__(self, status: int, detail: Any, url: str) -> None:
        self.status, self.detail, self.url = status, detail, url
        super().__init__(f"{status} from {url}: {detail}")


def _frame(rows: Any):
    try:
        import pandas as pd
    except ImportError:  # pandas is optional
        return rows
    return pd.DataFrame(rows)


class _Http:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.c = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.c.request(method, "/api" + path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise ApiError(r.status_code, detail, str(r.url))
        ct = r.headers.get("content-type", "")
        return r.json() if ct.startswith("application/json") else r.text


class Dataset:
    def __init__(self, http: _Http, data: dict[str, Any]) -> None:
        self._h, self.data = http, data
        self.id: str = data["id"]

    @property
    def target(self) -> str | None:
        return self.data.get("target") or (self.data.get("profile") or {}).get("target")

    @property
    def columns(self) -> list[dict[str, Any]]:
        return (self.data.get("profile") or {}).get("columns") or []

    def __repr__(self) -> str:
        return f"<Dataset {self.id} rows={self.data.get('rows')} target={self.target!r}>"


class Run:
    """A run of TabularPredictor.fit on the API. Attributes refresh with `.refresh()` (or `.wait()`)."""

    def __init__(self, http: _Http, data: dict[str, Any]) -> None:
        self._h, self.data = http, data
        self.id: str = data["id"]

    def __repr__(self) -> str:
        return f"<Run {self.id} {self.status} best={self.data.get('best_model')}>"

    @property
    def status(self) -> str:
        return self.data["status"]

    def refresh(self) -> "Run":
        self.data = self._h.req("GET", f"/runs/{self.id}")
        return self

    def wait(self, timeout: float = 3600, poll: float = 1.0) -> "Run":
        """Block until the run finishes; raises ApiError-like RuntimeError if it failed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.refresh()
            if self.status not in ("queued", "running"):
                if self.status != "succeeded":
                    raise RuntimeError(f"run {self.id} {self.status}: {self.data.get('error')}")
                return self
            time.sleep(poll)
        raise TimeoutError(f"run {self.id} still {self.status} after {timeout:.0f} s")

    def events(self, start: int = 0, follow: bool = True) -> Iterator[dict[str, Any]]:
        """The run's events in order (model_started, model_finished, ensemble, leaderboard, …). With `follow`, a
        server-sent-event stream that ends with the run; otherwise what is there now."""
        if not follow:
            yield from self._h.req("GET", f"/runs/{self.id}/events", params={"format": "json", "from": start})["events"]
            return
        with self._h.c.stream("GET", f"/api/runs/{self.id}/events", params={"from": start},
                              timeout=httpx.Timeout(None, connect=10)) as r:
            data: list[str] = []
            for line in r.iter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    ev = json.loads("\n".join(data))
                    data = []
                    yield ev
                    if ev.get("type") in TERMINAL_EVENTS:
                        return

    def leaderboard(self, as_frame: bool = True):
        rows = self.refresh().data.get("leaderboard") or []
        return _frame(rows) if as_frame else rows

    def predict(self, rows, model: str | None = None) -> dict[str, Any]:
        """Predict rows (a list of dicts or a DataFrame) with the run's predictor (or one of its models)."""
        if hasattr(rows, "to_dict"):
            rows = json.loads(rows.to_json(orient="records"))
        return self._h.req("POST", f"/runs/{self.id}/predict", json={"rows": list(rows), "model": model})

    def register(self, created_by: str, **options: Any) -> "Version":
        """Register this run as a model version (options: model_name, refit_full, infer_limit, …)."""
        return Version(self._h, self._h.req("POST", f"/runs/{self.id}/register", json={"created_by": created_by, **options}))

    def export(self, path: str | Path | None = None) -> str:
        """The standalone script that reproduces this run with plain AutoGluon; written to `path` if given."""
        text = self._h.req("GET", f"/runs/{self.id}/export.py")
        if path is not None:
            Path(path).write_text(text)
        return text

    def config(self) -> dict[str, Any]:
        """The body that starts this run again (edit it and pass it to `runs.start(**body)`)."""
        return self._h.req("GET", f"/runs/{self.id}/gym")["body"]

    def vela(self) -> dict[str, Any]:
        """Vela's findings computed from this run, each with one patch (`patch['body']` starts the patched run)."""
        return self._h.req("GET", f"/runs/{self.id}/vela")

    def cancel(self) -> dict[str, Any]:
        return self._h.req("POST", f"/runs/{self.id}/cancel")


class Version:
    """A registered model version."""

    def __init__(self, http: _Http, data: dict[str, Any]) -> None:
        self._h, self.data = http, data
        self.id: str = data["id"]

    def __repr__(self) -> str:
        return f"<Version {self.data.get('model_name')} v{self.data.get('version')} {self.data.get('status')} {self.data.get('stage')}>"

    def refresh(self) -> "Version":
        self.data = self._h.req("GET", f"/registry/versions/{self.id}")
        return self

    def wait(self, timeout: float = 1800, poll: float = 1.0) -> "Version":
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.refresh()
            if self.data.get("status") != "building":
                if self.data.get("status") != "ready":
                    raise RuntimeError(f"version {self.id} {self.data.get('status')}: {self.data.get('error')}")
                return self
            time.sleep(poll)
        raise TimeoutError(f"version {self.id} still building after {timeout:.0f} s")

    def predict(self, row: dict[str, Any], explain: bool = False) -> dict[str, Any]:
        return self._h.req("POST", f"/registry/versions/{self.id}/predict", json={"row": row, "explain": explain})

    def transition(self, stage: str, actor: str, comment: str = "") -> dict[str, Any]:
        return self._h.req("POST", f"/registry/versions/{self.id}/transition", json={"stage": stage, "actor": actor, "comment": comment})

    def review(self, decision: str, reviewer: str, comment: str = "") -> dict[str, Any]:
        return self._h.req("POST", f"/registry/versions/{self.id}/review", json={"decision": decision, "reviewer": reviewer, "comment": comment})

    def card(self, fmt: str = "md") -> Any:
        return self._h.req("GET", f"/registry/versions/{self.id}/card", params={"format": fmt})


class _Datasets:
    def __init__(self, h: _Http) -> None:
        self._h = h

    def list(self) -> list[dict[str, Any]]:
        return self._h.req("GET", "/datasets")["datasets"]

    def get(self, dataset_id: str) -> Dataset:
        return Dataset(self._h, self._h.req("GET", f"/datasets/{dataset_id}"))

    def upload(self, data, name: str = "upload.csv", target: str | None = None) -> Dataset:
        """Upload a CSV: a path, bytes, or a pandas DataFrame."""
        if hasattr(data, "to_csv"):
            content = data.to_csv(index=False).encode()
        elif isinstance(data, (bytes, bytearray)):
            content = bytes(data)
        else:
            p = Path(data)
            content, name = p.read_bytes(), p.name if name == "upload.csv" else name
        files = {"file": (name, io.BytesIO(content), "text/csv")}
        return Dataset(self._h, self._h.req("POST", "/datasets", files=files, data={"target": target} if target else None))


class _Runs:
    def __init__(self, h: _Http) -> None:
        self._h = h

    def start(self, dataset_id: str | Dataset, **body: Any) -> Run:
        """Start a fit: any field of POST /api/runs (label, problem_type, eval_metric, presets, time_limit,
        fit_kwargs, predictor_kwargs, calibrate_threshold, pseudolabel, distill, …)."""
        if isinstance(dataset_id, Dataset):
            dataset_id = dataset_id.id
        body = {**body, "dataset_id": dataset_id}
        body.setdefault("source", "client")
        return Run(self._h, self._h.req("POST", "/runs", json=body))

    def get(self, run_id: str) -> Run:
        return Run(self._h, self._h.req("GET", f"/runs/{run_id}"))

    def list(self, dataset_id: str | None = None) -> list[Run]:
        params = {"dataset_id": dataset_id} if dataset_id else None
        return [Run(self._h, r) for r in self._h.req("GET", "/runs", params=params)["runs"]]


class _Registry:
    def __init__(self, h: _Http) -> None:
        self._h = h

    def versions(self, model: str | None = None) -> list[Version]:
        out = self._h.req("GET", "/registry/versions", params={"model": model} if model else None)
        return [Version(self._h, v) for v in (out["versions"] if isinstance(out, dict) else out)]

    def version(self, version_id: str) -> Version:
        return Version(self._h, self._h.req("GET", f"/registry/versions/{version_id}"))


class _Gym:
    def __init__(self, h: _Http) -> None:
        self._h = h

    def schema(self) -> dict[str, Any]:
        return self._h.req("GET", "/gym/schema")

    def validate(self, dataset_id: str, **body: Any) -> dict[str, Any]:
        """Check a run body without starting it: errors, the resolved fit call, AutoGluon's bagging/stacking choice
        for this data and a fit-time estimate."""
        return self._h.req("POST", "/gym/validate", json={**body, "dataset_id": dataset_id})


class Ensemble:
    def __init__(self, url: str = "http://127.0.0.1:8000", timeout: float = 120) -> None:
        self._h = _Http(url, timeout)
        self.datasets = _Datasets(self._h)
        self.runs = _Runs(self._h)
        self.registry = _Registry(self._h)
        self.gym = _Gym(self._h)

    def health(self) -> dict[str, Any]:
        return self._h.req("GET", "/health")

    def models(self) -> list[dict[str, Any]]:
        """The model catalogue: each family's key, objective and symbols."""
        return self._h.req("GET", "/models")["families"]

    def close(self) -> None:
        self._h.c.close()

    def __enter__(self) -> "Ensemble":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
