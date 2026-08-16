# Analytics Studio — reference UI

A clean React + TypeScript + Vite front end for the Analytics Service, built on a
typed client generated from the API's OpenAPI spec. It exercises every UI flow
end-to-end and doubles as the reference for building your own front end.

## Run

The UI talks to the API through a dev proxy (`/api` → the service).

```bash
# 1. Start the API (from apps/analytics-service), needs Postgres + MinIO running:
ACCELERATOR_DB_NAME=accelerator ACCELERATOR_DB_PASSWORD=accelerator \
ACCELERATOR_STORAGE_BACKEND=local ACCELERATOR_STORAGE_DIR=/tmp/accelerator \
ACCELERATOR_AUTH_ENABLED=true ACCELERATOR_PORT=8001 \
venv/bin/python -m uvicorn app.main:app --port 8001

# 2. Start the UI (from apps/analytics-service/web):
npm install
npm run dev            # http://localhost:5173  (ANALYTICS_API overrides the backend origin)
```

`npm run build` type-checks and produces a production bundle in `dist/`.

## What's here

- `src/api/client.ts` — typed fetch client: identity headers (`X-User-Id` /
  `X-Team-Id`), the `Page`/`QueryPage` envelopes, problem+json `ApiError`,
  multipart upload, and header-authorized downloads.
- `src/api/schema.d.ts` — types generated from the spec
  (`npx openapi-typescript ../openapi.json -o src/api/schema.d.ts`).
- `src/components/` — the design system (`ui.tsx`) and dependency-free SVG charts
  (`charts.tsx`) following the dataviz palette + rules (validated, colorblind-safe,
  legend + direct labels + table fallback + hover).
- `src/pages/` — Catalog, Upload, Storage, and the Dataset workspace with tabs:
  Overview (health), Explore (filter/sort/paginate/column stats), Quality,
  Versions (tags + diff), Analytics (aggregate/pivot + charts), Relationships
  (discover/join), Transform (pipelines), Library (views/SQL/lineage).

Use the **"Act as"** control in the top bar to switch identity (user/team) and see
RBAC + column masking from a viewer's seat.

See `../UI-INTEGRATION-GUIDE.md` for the full API contract (auth, pagination,
error codes, masking, invariants, and per-flow endpoints).
