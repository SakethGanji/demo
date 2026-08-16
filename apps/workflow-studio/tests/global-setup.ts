import { API_BASE, ADMIN } from './fixtures'
import { sweepTestDatasets } from './sweep'

/**
 * Fail fast, with the command to fix it.
 *
 * A suite that starts against a dead API produces twenty identical timeouts and
 * buries the one fact that matters. Check once, say what is wrong, stop.
 */
export default async function globalSetup() {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/datasets?limit=1`, { headers: { 'X-User-Id': ADMIN } })
  } catch {
    throw new Error(
      `The analytics service is not reachable at ${API_BASE}.\n\n` +
        `Start it with:\n` +
        `  cd apps/analytics-service && \\\n` +
        `  ACCELERATOR_DB_NAME=accelerator ACCELERATOR_DB_PASSWORD=accelerator \\\n` +
        `  ACCELERATOR_STORAGE_BACKEND=local ACCELERATOR_STORAGE_DIR=/tmp/accelerator \\\n` +
        `  ACCELERATOR_AUTH_ENABLED=true ACCELERATOR_PORT=8001 \\\n` +
        `  venv/bin/python -m uvicorn app.main:app --port 8001\n\n` +
        `It needs Postgres and (optionally) MinIO:  docker start analytics-pg analytics-minio`,
    )
  }

  if (!res.ok) {
    throw new Error(
      `The analytics service answered ${res.status} for the seeded System superuser ` +
        `(${ADMIN}). Has the database been migrated?\n` +
        `  cd apps/analytics-service && venv/bin/python -m app.infra.db.postgres.migrate apply`,
    )
  }

  // Clear anything a previous run left behind (e.g. one that was interrupted),
  // so this run starts from a known state without needing a database reset.
  const swept = await sweepTestDatasets()
  if (swept > 0) console.log(`[setup] removed ${swept} leftover test dataset(s)`)
}
