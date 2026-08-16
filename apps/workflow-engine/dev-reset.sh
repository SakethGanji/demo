#!/usr/bin/env bash
# Quick dev reset: drop all tables, re-apply migrations, seed demo data.
# Usage:
#   bash dev-reset.sh          # reset + seed + start server
#   bash dev-reset.sh --no-run # reset + seed only (don't start server)
set -euo pipefail

cd "$(dirname "$0")"

echo ""
echo "  Resetting database..."
python3 -m src.db.migrate reset

echo ""
echo "  Seeding demo workflows..."
python3 -m src.db.seed

if [[ "${1:-}" == "--no-run" ]]; then
  echo ""
  echo "  Done. Start server with: SEED=1 python3 -m uvicorn src.main:app --port 8000"
else
  echo ""
  echo "  Starting server..."
  python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000
fi
