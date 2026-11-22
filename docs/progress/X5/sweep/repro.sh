#!/usr/bin/env bash
# Minimal curl repros for each distinct 5xx the sweep found. Usage: repro.sh <base> <state.json>
# Prints: HTTP status | label. Run against an API with the fixture (fixture.py) loaded.
A=$1/api; S=$2
st() { python3 -c "import json;print(json.load(open('$S'))['$1'])"; }
V1=$(st v1); RUN=$(st run); MV=$(st mv); UP=$(st up); AN=$(st anomaly); CO=$(st co)
H="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
T=$(mktemp -d)
c() { local lab=$1; shift; printf '%s | %s\n' "$(curl -s -o $T/o -w '%{http_code}' --max-time 90 "$@")" "$lab"; head -c 160 $T/o | tr '\n' ' '; echo; }
printf '%s' "$(python3 -c 'print("{\"a\":"*1000+"1"+"}"*1000)')" > $T/deep.json
printf '%s' "$(python3 -c 'print("["*1000+"]"*1000)')" > $T/deeparr.json
printf '%s' "$(python3 -c 'print("["*100000)')" > $T/unclosed.json
head -c 3000 /dev/urandom > $T/bin.csv; printf 'a,b\n\xe9,\xfc\n' > $T/latin1.csv
printf 'income,age\n"unterminated\n1,2,3,4\n' > $T/ragged.csv; printf 'PAR1\0\0\0PAR1' > $T/bad.parquet

echo "## R1 422 echo of a NaN/Infinity/1e400 literal (any pydantic-typed body)"
c "POST /runs {dataset_id: NaN}" $H $J -X POST $A/runs -d '{"dataset_id": NaN}'
c "POST /runs {time_limit: 1e400}" $H $J -X POST $A/runs -d '{"time_limit": 1e400}'
c "POST /registry/versions/{mv}/predict {rows: Infinity}" $H $J -X POST $A/registry/versions/$MV/predict -d '{"rows": Infinity}'
c "control: dict-typed route PATCH /datasets/{v1} {x: NaN}" $H $J -X PATCH $A/datasets/$V1 -d '{"x": NaN}'
echo "## R2 422 echo of deeply nested JSON (RecursionError)"
c "POST /runs 1000-deep object" $H $J -X POST $A/runs --data-binary @$T/deep.json
c "POST /runs 1000-deep array" $H $J -X POST $A/runs --data-binary @$T/deeparr.json
c "POST /datasets/{v1}/splits 1000-deep array (dict route)" $H $J -X POST $A/datasets/$V1/splits --data-binary @$T/deeparr.json
c "POST /datasets/{v1}/splits 100000 unclosed [" $H $J -X POST $A/datasets/$V1/splits --data-binary @$T/unclosed.json
c "POST /shares 1000-deep object" $H $J -X POST $A/shares --data-binary @$T/deep.json
echo "## R3 422 echo of non-UTF-8 bytes (multipart to a JSON route)"
c "PATCH /uploads/{up} multipart binary" $H -X PATCH $A/uploads/$UP -F file=@$T/bin.csv
c "POST /runs non-utf8 json body" $H $J -X POST $A/runs --data-binary @$T/bin.csv
c "POST /runs/{run}/scorecard/score multipart latin1" $H -X POST $A/runs/$RUN/scorecard/score -F file=@$T/latin1.csv
echo "## R4 GET /datasets/{id}/export"
c "export nonexistent id" $H "$A/datasets/nonexistent_zzz/export?by=Ada"
c "export run id" $H "$A/datasets/$RUN/export?by=Ada"
c "export 0" $H "$A/datasets/0/export?by=Ada"
c "export 10 KB id" $H "$A/datasets/$(python3 -c 'print("A"*10000)')/export?by=Ada"
c "export unicode id" $H "$A/datasets/%C3%A9%F0%9F%98%80/export?by=Ada"
c "control: GET /datasets/nonexistent_zzz" $H "$A/datasets/nonexistent_zzz"
c "rename v1 (card name) to a non-latin-1 name" $H $J -X PATCH $A/datasets/$V1/card -d '{"name":"prêts 😀","by":"Ada"}'
c "export v1 (non-latin-1 name)" $H "$A/datasets/$V1/export?by=Ada"
c "export v1 format=parquet" $H "$A/datasets/$V1/export?by=Ada&format=parquet"
c "export v1 format=csv" $H "$A/datasets/$V1/export?by=Ada&format=csv"
c "rename back" $H $J -X PATCH $A/datasets/$V1/card -d '{"name":"loans","by":"Ada"}'
echo "## R5 PII table selector"
c "GET /datasets/{v1}/pii?table=abc" $H "$A/datasets/$V1/pii?table=abc"
c "GET /datasets/{v1}/pii?table=18446744073709551616" $H "$A/datasets/$V1/pii?table=18446744073709551616"
c "PUT /datasets/{v1}/pii {table: 1}" $H $J -X PUT $A/datasets/$V1/pii -d '{"confirm":"suggested","by":"Ada","table":1}'
c "PUT /datasets/{v1}/pii {table: 2**64}" $H $J -X PUT $A/datasets/$V1/pii -d '{"confirm":"suggested","by":"Ada","table":18446744073709551616}'
c "PUT /datasets/{v1}/pii {table: [\"a\"]}" $H $J -X PUT $A/datasets/$V1/pii -d '{"confirm":"suggested","by":"Ada","table":["a"]}'
c "PUT /datasets/{v1}/pii {table: \"nope\"}" $H $J -X PUT $A/datasets/$V1/pii -d '{"confirm":"suggested","by":"Ada","table":"nope"}'
echo "## R6 splits target non-string"
c "POST /datasets/{v1}/splits {target: 1}" $H $J -X POST $A/datasets/$V1/splits -d '{"target":1,"by":"Ada"}'
c "POST /datasets/{v1}/splits {target: [\"a\"]}" $H $J -X POST $A/datasets/$V1/splits -d '{"target":["a"],"by":"Ada"}'
echo "## R7 rows/preview table non-string"
c "POST /datasets/{v1}/rows/preview {table: [\"a\"]}" $H $J -X POST $A/datasets/$V1/rows/preview -d '{"table":["a"],"by":"Ada"}'
c "POST /datasets/{v1}/rows/preview {table: 2**64}" $H $J -X POST $A/datasets/$V1/rows/preview -d '{"table":18446744073709551616,"by":"Ada"}'
c "POST /datasets/{v1}/rows/preview {table: 1}" $H $J -X POST $A/datasets/$V1/rows/preview -d '{"table":1,"by":"Ada"}'
echo "## R8 SQLite INTEGER overflow"
c "GET /runs/{run}/events?from=2**64" $H "$A/runs/$RUN/events?from=18446744073709551616"
c "PATCH /annotations/dataset/{v1}/notes/2**64" $H $J -X PATCH "$A/annotations/dataset/$V1/notes/18446744073709551616" -d '{"note":"x","by":"Ada"}'
c "DELETE /annotations/dataset/{v1}/notes/2**64" $H -X DELETE "$A/annotations/dataset/$V1/notes/18446744073709551616?by=Ada"
c "POST /runs/{run}/predict {rows:[{income: 2**64}]}" $H $J -X POST $A/runs/$RUN/predict -d '{"rows":[{"income":18446744073709551616,"age":30,"segment":"sme"}]}'
echo "## R9 multipart parse errors"
c "POST /uploads multipart w/o boundary" $H -H 'content-type: multipart/form-data; boundary=zz' -X POST "$A/uploads?filename=a.csv" --data-binary '{"by":"Ada"}'
c "PATCH /uploads/{up} multipart garbage" $H -H 'content-type: multipart/form-data; boundary=zz' -X PATCH $A/uploads/$UP --data-binary '{"by":"Ada"}'
echo "## R10 scoring uploads"
c "POST /anomaly/runs/{an}/score binary" $H -X POST $A/anomaly/runs/$AN/score -F file=@$T/bin.csv
c "POST /anomaly/runs/{an}/score bad parquet" $H -X POST $A/anomaly/runs/$AN/score -F "file=@$T/bad.parquet;filename=x.parquet"
c "POST /anomaly/runs/{an}/score ragged csv" $H -X POST $A/anomaly/runs/$AN/score -F file=@$T/ragged.csv
c "POST /runs/{run}/scorecard/score binary" $H -X POST $A/runs/$RUN/scorecard/score -F file=@$T/bin.csv
c "POST /registry/versions/{mv}/score binary" $H -X POST $A/registry/versions/$MV/score -F file=@$T/bin.csv
c "POST /uploads/{up}/commit multipart binary" $H -X POST $A/uploads/$UP/commit -F file=@$T/bin.csv
echo "## K known: non-text by"
c "POST /shares {by: 1}" $H $J -X POST $A/shares -d "{\"object_id\":\"$V1\",\"grantee_team\":\"$CO\",\"scope\":\"all\",\"reason\":\"x5 sweep\",\"by\":1}"
c "POST /datasets/{v1}/certifications {by: 1}" $H $J -X POST $A/datasets/$V1/certifications -d '{"level":"certified","reason":"x","by":1}'
c "PUT /datasets/{v1}/contract {by: 1}" $H $J -X PUT $A/datasets/$V1/contract -d '{"by":1}'
c "POST /datasets/{v1}/contract/from-version/{v1} {by: 1}" $H $J -X POST $A/datasets/$V1/contract/from-version/$V1 -d '{"by":1}'
rm -rf $T
