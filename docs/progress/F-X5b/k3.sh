#!/usr/bin/env bash
# K3 repro: A=base
A=$1/api; H="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
c() { local lab=$1; shift; printf '%s | %s | ' "$(curl -s -o /tmp/k3o -w '%{http_code}' --max-time 60 "$@")" "$lab"; head -c 200 /tmp/k3o; echo; }
c "history credit-default" $H $A/datasets/credit-default/history
c "POST /projects {name:p, by:NUL}" $H $J -X POST $A/projects -d '{"name":"p","by":"\u0000"}'
c "POST /projects {name:NUL, by:Ada}" $H $J -X POST $A/projects -d '{"name":"p\u0000q","by":"Ada"}'
c "GET /runs?status=%00" $H "$A/runs?status=%00"
c "GET /datasets?project_id=%00" $H "$A/datasets?project_id=%00"
c "history credit-default" $H $A/datasets/credit-default/history
c "history loan-losses" $H $A/datasets/loan-losses/history
c "history account-attrition" $H $A/datasets/account-attrition/history
