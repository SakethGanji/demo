"""Drop the pre-``artifacts/`` derived-output blobs (dev only).

The layout change moved every derived output from a flat ``samples/`` prefix
(plus an ``exports/`` prefix nothing ever wrote to) under
``artifacts/{team}/{dataset}/{kind}/``. Old keys are unreachable: their
``artifacts`` rows were deleted by migration 20260814000000, and resolution now
goes through those rows, so nothing can ever open them again.

Relocating them was not worth it — the old key records neither team nor dataset
nor kind, so every blob would have to be re-derived from a row that no longer
exists. This deletes them instead. Safe only because the environment is dev.

    venv/bin/python -m scripts.purge_legacy_artifacts [--dry-run]
"""

from __future__ import annotations

import sys

from app.infra.db.storage import get_storage

LEGACY_PREFIXES = ("samples", "exports")


def main() -> int:
    dry = "--dry-run" in sys.argv
    storage = get_storage()
    total = 0
    freed = 0
    for prefix in LEGACY_PREFIXES:
        keys = storage.list_keys(prefix)
        for key in keys:
            try:
                freed += storage.size(key)
            except Exception:
                pass
        total += len(keys)
        print(f"  {prefix}/: {len(keys)} object(s)")
        if keys and not dry:
            storage.delete_prefix(prefix)
    verb = "would free" if dry else "freed"
    print(f"{total} object(s), {verb} {freed / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
