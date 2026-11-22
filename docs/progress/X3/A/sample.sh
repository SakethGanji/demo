# sample.sh <api> <work> <tag>: runs under a tiny frame budget (forced sample), seed fixed; print sample spec + sha
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; W=$2; T=$3; cd $W; . $HERE/lib.sh
V=$(cat vkeys.vid); JM=$(cat jm.id)
ids=""
for b in "\"table\":\"apps\"" "\"join_id\":\"$JM\""; do for s in 42 42 7; do for mb in 0.05 0.2; do
  call r.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",$b,\"label\":\"y\",\"time_limit\":10,\"frame_budget_mb\":$mb,\"sample_seed\":$s}"; R=$(val r.json "d.get('id') or d"); echo "$b seed $s mb $mb → $R"; ids="$ids $R"; done; done; done
for R in $ids; do wait_run $R; done
for R in $ids; do curl -s $H $A/runs/$R > $R.json; val $R.json "'$T', d['id'], d['config'].get('sample_seed'), d['config'].get('frame_budget_mb'), (d['config'].get('join') or {}).get('join_id') or 'table', '| sampled', d['data']['frame'].get('sampled'), '| rows', d['data'].get('rows_used'), '/', d['data'].get('rows_total'), '| sample_sha256', (d['data']['frame'].get('sample_sha256') or d['data'].get('sample_sha256') or '')[:16], '| rates', (d['data']['frame'].get('sample_spec') or {}).get('rates'), '| data_sha', (d['data'].get('data_sha256') or '')[:12]"; done
