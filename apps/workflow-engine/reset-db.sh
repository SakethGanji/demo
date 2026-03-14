#!/bin/bash
set -e

NAME="workflow-postgres"

echo "Stopping and removing existing container..."
docker rm -f "$NAME" 2>/dev/null || true

echo "Starting fresh PostgreSQL..."
docker run -d --name "$NAME" \
  -e POSTGRES_USER=workflow \
  -e POSTGRES_PASSWORD=workflow \
  -e POSTGRES_DB=workflows \
  -p 5433:5432 \
  postgres:16

echo "Waiting for PostgreSQL to be ready..."
until docker exec "$NAME" pg_isready -U workflow -d workflows -q 2>/dev/null; do
  sleep 0.5
done

echo "Running migrations..."
cd "$(dirname "$0")"
python3 -m src.db.migrate reset

echo "Done!"
