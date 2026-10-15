# UI-PROGRESS — studio rebuild per UI-DESIGN.md v2.1

Branch `revert-to-5684ba5` (local commits only). Spec: `UI-DESIGN.md` (v2.1, approved).
E2E how-to: `E2E.md`.

## Coordinator notes applied
- Seeded secret for T3/T10 has `allowed_hosts: ["127.0.0.1"]` (`e2e/fixtures/seed.ts`).
- N3 `_token`: unlock cookie SameSite=Lax/HttpOnly/path `/a/{slug}`, no CORS on `/a/*` — engine agent, with tests.
- Mockup nits (dialogs.png both delete copies; command-palette backdrop): spec text followed.
- Spec decision: draft-only apps ARE versioned into releases (UI-DESIGN §3.8 marked CHANGED).
- Backend audit (AUDIT-BACKEND-FIXES.md, 12 findings): forwarded to the engine agent; read_only egress rule
  clarified so T3's self-test against the allow-listed 127.0.0.1 upstream still passes (engine's own port always denied).

## Dev stack (UI development only)
`$SCRATCH/devstack/up.sh [fresh]` → engine 127.0.0.1:8710, DB `workflows_test_e2e_devui` (tmp cwd, `env -i`).
Studio: `VITE_WORKFLOW_API_URL=http://127.0.0.1:8710 npx vite --port 5190`.

## Slices
| Slice | Status | Commit |
|---|---|---|
| 0 foundation (data layer, tokens + contrast test, primitives, nested-button fixes, env store) | done | 123f6d9, 74bc7ad |
| 1 shell, overview, projects home, redirects, first-run | done | 123f6d9 |
| 2 backend view (editor, analysis, save, test panel, reference, client.ts, dialogs, composer client) | done | 2afac7c |
| 4 data / secrets / runs (+ route settings) | done | f2d8ba3 |
| 5 + 6a create-UI, builder, route picker, preview bridge, publish, app settings | done | 68f8c78 |
| 6b settings sheet, release sheet; 7 workflows list | in progress (sub-agent) | |
| B2 engine: fake LLM, run-draft, generate, audit fixes, gaps, e2e_stack.py | in progress (engine sub-agent) | |
| 3 composer/drafts in the browser | code done in slice 2; verified once the engine endpoints land | |
| E2E (46 scenarios) | specs written for 01–05, 07; 06 pending; first run blocked on engine fake mode + e2e_stack.py | |

## Counts
- tsc -b: baseline 53 → 44
- unit (vitest): 23

## Deviations (so far)
- The AI draft is applied to the editor on the terminal `result` (one transaction), not streamed line by line;
  the editor is read-only with the phase chips during streaming.
- Live analysis dry-runs omit `base_sha` (the save path sends it), so typing never 409s.
- Data grid: the new-row editor is the first row (newest-first sort); grid panel marked `data-scroll-x="allowed"`.
- Builder: `vN · saved` badge doubles as the manual save; API tester opens from App settings.
