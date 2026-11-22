import os, sys, time
sys.path.insert(0, os.getcwd())
import numpy as np
def hwm():
    return int([l for l in open("/proc/self/status") if l.startswith("VmHWM")][0].split()[1]) / 1e6
from app import frames
from app.autopilot import audit as A
print("imports", hwm())
import cleanlab.filter, cleanlab.outlier, sklearn.neighbors
print("cleanlab imports", hwm())
path = str(frames.version_file('credit-default'))
df = frames.read_csv_path(path)
label="defaulted"; feats=[c for c in df.columns if c!=label]
num=[c for c in feats if str(df[c].dtype).startswith(("int","float"))]; cat=[c for c in feats if c not in num]
E = A._embed(df, [], num, cat); print("embed", E.shape, hwm())
import sklearn
with sklearn.config_context(working_memory=128):
    nn = sklearn.neighbors.NearestNeighbors(n_neighbors=6, metric="cosine").fit(E); d,i = nn.kneighbors(E); print("knn", hwm())
    ood = cleanlab.outlier.OutOfDistribution().fit_score(features=E); print("ood in ctx", hwm())
ood = cleanlab.outlier.OutOfDistribution().fit_score(features=E); print("ood", hwm())
