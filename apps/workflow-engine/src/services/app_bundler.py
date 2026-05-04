"""Server-side bundler for published apps.

Mirrors the in-browser esbuild-wasm pipeline used during preview:
  * IIFE output assigning to globalName `__AppModule`
  * JSX transform (React.createElement / React.Fragment)
  * react / react-dom / react-dom/client mapped to window globals (loaded
    from CDN by the standalone HTML)
  * `process.env.NODE_ENV = "production"` define
  * Minified

We invoke the `esbuild` binary via subprocess. The binary must be on PATH;
this is documented as a deployment dep. It is intentionally not pip-pinned
so the operator can choose how to install (apk add esbuild / npm i -g /
download release) — the bundler verifies availability at call time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime

from .bundle_storage import BundleArtifact


class BundlerUnavailableError(RuntimeError):
    """Raised when the esbuild binary is not on PATH. Operator must install it."""


class BundleBuildError(RuntimeError):
    """Raised when esbuild fails to produce a bundle (syntax errors, etc.).
    Carries the raw stderr so the client can show actionable messages."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class AppFileInput:
    path: str
    content: str


# Entry-point resolution mirrors apps/workflow-studio esbuild-bundler.ts
_ENTRY_DIRS = ("", "src/")
_ENTRY_NAMES = ("App", "app", "index")
_ENTRY_EXTS = (".tsx", ".ts", ".jsx", ".js")


def _find_entry_point(files: list[AppFileInput]) -> str:
    paths = {f.path for f in files}
    for d in _ENTRY_DIRS:
        for n in _ENTRY_NAMES:
            for e in _ENTRY_EXTS:
                cand = f"{d}{n}{e}"
                if cand in paths:
                    return cand
    # Fallback: first .ts/.tsx/.js/.jsx file
    for f in files:
        if f.path.endswith(_ENTRY_EXTS):
            return f.path
    raise BundleBuildError("no entry point found among files")


def _esbuild_path() -> str:
    path = shutil.which("esbuild")
    if not path:
        raise BundlerUnavailableError(
            "esbuild binary not found on PATH. Install with `npm i -g esbuild` "
            "or your distro package manager."
        )
    return path


# Inject globals for react / react-dom so the bundle assumes they're already on
# `window` (loaded by the standalone HTML's CDN scripts). This matches the
# studio's virtual-fs plugin behaviour.
_REACT_SHIM_BANNER = (
    "var require=function(m){"
    "if(m==='react')return window.React;"
    "if(m==='react-dom'||m==='react-dom/client')return window.ReactDOM;"
    "throw new Error('Cannot resolve module: '+m);"
    "};"
)


async def bundle_app(files: list[AppFileInput]) -> BundleArtifact:
    """Bundle an app into a production artifact.

    Writes the virtual filesystem to a temp dir, runs esbuild, reads stdout
    JS + CSS files. Raises BundlerUnavailableError if esbuild isn't installed
    and BundleBuildError on compilation failure.
    """
    if not files:
        raise BundleBuildError("no files to bundle")

    binary = _esbuild_path()
    entry = _find_entry_point(files)

    # Materialize the virtual fs in a temp dir. Each app bundle is independent;
    # we don't reuse temp dirs across publishes — bundle output ends up in
    # Postgres / object storage, not on disk.
    with tempfile.TemporaryDirectory(prefix="app-bundle-") as tmp:
        for f in files:
            full = os.path.join(tmp, f.path)
            os.makedirs(os.path.dirname(full) or tmp, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(f.content)

        outdir = os.path.join(tmp, "__out__")
        os.makedirs(outdir, exist_ok=True)

        cmd = [
            binary,
            entry,
            "--bundle",
            "--format=iife",
            "--global-name=__AppModule",
            "--jsx=transform",
            "--jsx-factory=React.createElement",
            "--jsx-fragment=React.Fragment",
            "--minify",
            "--target=es2020",
            f"--banner:js={_REACT_SHIM_BANNER}",
            "--external:react",
            "--external:react-dom",
            "--external:react-dom/client",
            f"--define:process.env.NODE_ENV={json.dumps('production')}",
            f"--outdir={outdir}",
            "--log-level=warning",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=tmp,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        if proc.returncode != 0:
            raise BundleBuildError(
                f"esbuild exited {proc.returncode}",
                stderr=stderr_b.decode("utf-8", errors="replace"),
            )

        # Output filename mirrors the entry path with extension swapped.
        out_js: str = ""
        out_css: str = ""
        for name in os.listdir(outdir):
            full = os.path.join(outdir, name)
            with open(full, encoding="utf-8") as fh:
                contents = fh.read()
            if name.endswith(".js"):
                out_js = contents
            elif name.endswith(".css"):
                out_css = contents

    if not out_js:
        raise BundleBuildError(
            "esbuild produced no JS output",
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )

    digest = hashlib.sha256()
    digest.update(out_js.encode("utf-8"))
    digest.update(b"\0")
    digest.update(out_css.encode("utf-8"))
    bundle_hash = digest.hexdigest()[:16]  # short hash is enough for cache busting

    return BundleArtifact(
        js=out_js,
        css=out_css,
        hash=bundle_hash,
        bundled_at=datetime.now(),
    )
