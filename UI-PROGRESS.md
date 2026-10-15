# UI-PROGRESS — studio rebuild per UI-DESIGN.md v2.1

Branch `revert-to-5684ba5` (local commits only). Spec: `UI-DESIGN.md` (v2.1, approved).

## Coordinator notes to apply
- Seeded secret for T3/T10 must have `allowed_hosts: ["127.0.0.1"]` (else self-test 2/2 fails).
- N3 `_token`: unlock cookie SameSite=Lax, HttpOnly, path `/a/{slug}`; endpoint same-origin only (no CORS on `/a/*`); a test for each.
- Mockup nits: dialogs.png shows both delete-module copies; command-palette.png backdrop — follow spec text.
- Do not edit `apps/workflow-engine` until the coordinator confirms the backend agent is done.

## Dev stack (UI development only)
`$SCRATCH/devstack/up.sh [fresh]` starts an engine on 127.0.0.1:8710 against `workflows_test_e2e_devui`
(tmp cwd, `env -i`, no .env). Studio: `VITE_WORKFLOW_API_URL=http://127.0.0.1:8710 npx vite --port 5190`.

## Slices
| Slice | Status | Notes |
|---|---|---|
| 0 foundation | in progress | http.ts/ApiError, types, endpoints, queryKeys+invalidate, hoisted queryClient, config override, tokens + contrast test (vitest, 20 tests), primitives, nested-button fixes, per-project env store |
| 1 shell/overview/home/redirects | todo | |
| 2 backend view (manual) | todo | |
| B2 fake LLM / generate / run-draft | blocked (engine) | |
| 3 composer/drafts | todo | |
| 4 data/secrets/runs | todo | |
| 5 create-ui/builder | todo | |
| 6 publish/release/settings | todo | |
| 7 workflows/responsive/dark | todo | |
| E2E (45) | todo | |

## Counts
- tsc -b: baseline 53 → current 50
- unit (vitest): 20
