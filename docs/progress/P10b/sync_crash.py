"""P10b: run `python -m app.lake.sync_s3` but crash it after N puts (the process exits mid-copy, as a killed job would),
to show the re-run resumes. usage: sync_crash.py <N>. Same environment as the tool (ENSEMBLE_DATA_DIR, ENSEMBLE_S3_*)."""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app.lake import s3store, sync_s3  # noqa: E402

N = int(sys.argv[1])
orig = s3store.S3BlobStore.put_file
done = [0]


def put_then_crash(self, local, sha, ext):
    if done[0] >= N:
        print(f"-- simulated crash after {N} puts (os._exit, no cleanup)", flush=True)
        os._exit(137)
    done[0] += 1
    return orig(self, local, sha, ext)


s3store.S3BlobStore.put_file = put_then_crash
sys.exit(sync_s3.main(sys.argv[2:]))
