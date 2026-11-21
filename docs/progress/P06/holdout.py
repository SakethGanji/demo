import sys, json
from pathlib import Path
from app import frames
from app import frames as _f
v1, v2, fixed = sys.argv[1:4]
d = _f._settings().datasets_dir
def hold(v):
    sp = json.load(open(next((d / v / 'splits').glob('*.json'))))
    df = frames.read_version(v, ['acct'])
    return set(df.index[sp['holdout']].tolist()), df
h1, f1 = hold(v1); h2, f2 = hold(v2)
fx = set(Path(fixed).read_text().split(','))
i1 = set(f1.index[f1['acct'].isin(fx)].tolist()); i2 = set(f2.index[f2['acct'].isin(fx)].tolist())
print('holdout __row_id sets equal:', h1 == h2, '| size', len(h1), '| holdout acct equal:', set(f1.loc[sorted(h1), 'acct']) == set(f2.loc[sorted(h2), 'acct']))
print('15 corrected rows keep their v1 __row_id:', i1 == i2, '| of them in the holdout:', len(i1 & h1))
