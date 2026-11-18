"""A small client for the Project ENSEMBLE API, for notebooks and scripts.

    from ensemble_client import Ensemble
    ens = Ensemble("http://127.0.0.1:8000")
    run = ens.runs.start("credit-default", eval_metric="roc_auc", time_limit=60)
    run.wait()
    run.leaderboard()
"""

from .client import ApiError, Dataset, Ensemble, Run, Version

__all__ = ["Ensemble", "Dataset", "Run", "Version", "ApiError"]
__version__ = "0.6.0"
