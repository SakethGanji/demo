#!/usr/bin/env bash
# LB-PG: run the app on a local Postgres (make dev-pg / make dev-pg-stop).
#
#   scripts/dev_pg.sh start    initdb once, start Postgres, create the db and roles, print the env (no app)
#   scripts/dev_pg.sh env      print the export lines (eval "$(scripts/dev_pg.sh env)")
#   scripts/dev_pg.sh stop     stop Postgres
#   scripts/dev_pg.sh dev      start, then run the app as `make dev` does (API + UI) against it
#
# Cluster: $ENSEMBLE_PG_DIR (default ~/.ensemble/pg; backend/data/pg when run as root, so the postgres user can reach it).
# Port: $ENSEMBLE_PG_PORT (default 55432), listening on 127.0.0.1 only. Two login roles: ens_owner (owns the db, runs the
# migrations) and ens_app (the app; §2.2 grants). Their password is generated once into $ENSEMBLE_PG_DIR/password (0600,
# outside git); nothing is committed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" = 0 ]; then DEF_DIR="$ROOT/backend/data/pg"; else DEF_DIR="$HOME/.ensemble/pg"; fi
PGROOT="${ENSEMBLE_PG_DIR:-$DEF_DIR}"
PORT="${ENSEMBLE_PG_PORT:-55432}"
DB="${ENSEMBLE_PG_DB:-ensemble}"
DATA="$PGROOT/data"
SOCK="$PGROOT/run"
PWFILE="$PGROOT/password"

find_bin() {
  if command -v pg_ctl >/dev/null 2>&1; then dirname "$(command -v pg_ctl)"; return; fi
  for d in /usr/lib/postgresql/*/bin /usr/local/opt/postgresql@*/bin /opt/homebrew/opt/postgresql@*/bin \
           /opt/homebrew/bin /usr/local/pgsql/bin; do
    [ -x "$d/pg_ctl" ] && { echo "$d"; return; }
  done
  echo "dev_pg: no Postgres found (pg_ctl). Install Postgres 16 (apt install postgresql-16 / brew install postgresql@16)." >&2
  exit 1
}
BIN="$(find_bin)"

# as root, the server must run as the postgres user
as_pg() { if [ "$(id -u)" = 0 ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

psql_su() { as_pg "$BIN/psql" -h "$SOCK" -p "$PORT" -U postgres -d "${2:-postgres}" -v ON_ERROR_STOP=1 -qAt -c "$1"; }

init_once() {
  mkdir -p "$PGROOT"
  chmod 700 "$PGROOT"
  if [ ! -s "$PWFILE" ]; then
    (umask 077; head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' >"$PWFILE")
  fi
  if [ ! -f "$DATA/PG_VERSION" ]; then
    mkdir -p "$DATA" "$SOCK"
    [ "$(id -u)" = 0 ] && chown -R postgres "$PGROOT"
    echo "dev_pg: initdb $DATA"
    # local socket: trust for the superuser bootstrap (the socket dir is 0700); TCP: password (scram) only
    as_pg "$BIN/initdb" -D "$DATA" -U postgres --auth-local=trust --auth-host=scram-sha-256 -E UTF8 --locale=C >/dev/null
  fi
  mkdir -p "$SOCK"
  [ "$(id -u)" = 0 ] && chown -R postgres "$PGROOT"
  chmod 700 "$SOCK"
}

start_pg() {
  init_once
  if as_pg "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
    echo "dev_pg: Postgres already running (port $PORT)" >&2
  else
    as_pg "$BIN/pg_ctl" -D "$DATA" -l "$PGROOT/postgres.log" -w \
      -o "-p $PORT -k $SOCK -c listen_addresses=127.0.0.1" start >/dev/null
    echo "dev_pg: Postgres started on 127.0.0.1:$PORT (log $PGROOT/postgres.log)" >&2
  fi
  local pw; pw="$(cat "$PWFILE")"
  for r in ens_owner ens_app; do
    if [ -z "$(psql_su "SELECT 1 FROM pg_roles WHERE rolname = '$r'")" ]; then
      psql_su "CREATE ROLE $r LOGIN PASSWORD '$pw'"
    else
      psql_su "ALTER ROLE $r LOGIN PASSWORD '$pw'"  # the password file is the source of truth
    fi
  done
  if [ -z "$(psql_su "SELECT 1 FROM pg_database WHERE datname = '$DB'")" ]; then
    psql_su "CREATE DATABASE $DB OWNER ens_owner"
    psql_su "ALTER SCHEMA public OWNER TO ens_owner; REVOKE CREATE ON SCHEMA public FROM PUBLIC" "$DB"
  fi
}

env_lines() {
  local pw; pw="$(cat "$PWFILE")"
  echo "export ENSEMBLE_DB_URL='postgresql://ens_app:$pw@127.0.0.1:$PORT/$DB'"
  echo "export ENSEMBLE_DB_OWNER_URL='postgresql://ens_owner:$pw@127.0.0.1:$PORT/$DB'"
  echo "export ENSEMBLE_DB_APP_ROLE=ens_app"
}

case "${1:-dev}" in
  start)
    start_pg
    echo "ENSEMBLE_DB_URL=postgresql://ens_app:***@127.0.0.1:$PORT/$DB  (password in $PWFILE)"
    echo "use it in a shell:  eval \"\$(scripts/dev_pg.sh env)\""
    ;;
  env) init_once >/dev/null; env_lines ;;
  stop)
    if [ -f "$DATA/PG_VERSION" ] && as_pg "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      as_pg "$BIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null && echo "dev_pg: Postgres stopped"
    else
      echo "dev_pg: Postgres is not running"
    fi
    ;;
  dev)
    start_pg
    eval "$(env_lines)"
    echo "ENSEMBLE_DB_URL=postgresql://ens_app:***@127.0.0.1:$PORT/$DB  (password in $PWFILE)"
    echo "Postgres keeps running after Ctrl+C; make dev-pg-stop stops it."
    cd "$ROOT"
    exec make dev
    ;;
  *) echo "usage: $0 [dev|start|env|stop]" >&2; exit 2 ;;
esac
