# sample2.sh <api> <skew dir> <tag>: budget-derived rate (above the 1,000-row floor): 20k-row table g, budget 0.5 MB, seed 42 twice
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; cd $2; . $HERE/lib.sh; T=$3
R=$(cat s.vid); ids=""
for s in 42 42; do call r.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$R\",\"table\":\"g\",\"label\":\"y\",\"time_limit\":10,\"frame_budget_mb\":0.5,\"sample_seed\":$s}"; ids="$ids $(val r.json "d.get('id') or d")"; done; echo
for x in $ids; do wait_run $x; done
for x in $ids; do curl -s $H $A/runs/$x > $x.json; val $x.json "'$T', d['id'], '| rows', d['data'].get('rows_used'), '/', d['data'].get('rows_total'), '| sample_sha256', d['data']['frame'].get('sample_sha256'), '| rates', (d['data']['frame'].get('sample_spec') or {}).get('rates'), '| est bytes', (d['data']['frame'].get('estimate') or {}).get('bytes')"; done
