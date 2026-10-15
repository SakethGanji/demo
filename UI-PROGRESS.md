# UI-PROGRESS — studio rebuild per UI-DESIGN.md v2.1

Branch `revert-to-5684ba5` (owner approved pushing this branch; `clean-main` is untouched). Spec: `UI-DESIGN.md` (v2.1, approved).
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
| 6b settings sheet, release sheet; 7 workflows list | implemented and browser-verified: rename/republish, keys, deletion blockers, snapshots, promote, rollback, canvas run, responsive layouts | this update |
| B2 engine: fake LLM, run-draft, generate, audit fixes, gaps, e2e_stack.py | implemented; release/settings dry-run compatibility fixed for lazy included FastAPI routers | this update |
| 3 composer/drafts in the browser | verified with the gated fake LLM and real sandbox/backend | this update |
| E2E (46 scenarios) | all scenario IDs implemented in specs 01–08; 47 cases including both responsive sizes | see E2E.md |

## Counts
- tsc -b: baseline 53 → 44 → **0**; production Vite build passes (large-chunk warning remains)
- unit (vitest): 23
- engine: **276 passed**, no skips (5 existing Pydantic warnings)
- browser: **46 scenarios / 47 cases passed** across the full run and affected generation-spec rerun
- final captures: **28 PNGs**, light + dark, under `screenshots/ui-built/`; strict capture run passed

## Completion fixes (2026-10-15)

- Route-picker assertions target the checkbox inside the label; builder stream errors use the existing SSE reader.
- Saving cancels queued/in-flight live analysis and pauses it during the write, preventing a false post-save name conflict.
- Publish dialog exposes Unpublish for an already-published app without requiring another publish.
- Published CSS is compiled with pinned Tailwind v3 at publish time; no development CDN script or ignored CSP meta tag.
  The full CSP remains an HTTP header, and app-supplied Tailwind config is never executed.
- Password app token refresh returns the specified `unlock_required` code.
- Release-step controls wait for release history and pending writes, avoiding an initialization race.
- Existing TypeScript errors were resolved with actual event/prop types, string/schema guards and boolean JSX conditions;
  compiler checks were not disabled and no assertions were cast to `any`.
- `e2e/capture.config.ts` captures the main screens and sheets into `screenshots/ui-built/` in light/dark themes.
- AI calls remain simulated for these browser checks. The owner deferred real Gemini Generate/Refine/Create UI validation
  until after UI completion; no successful live flow is claimed in this update. No audit agent was spawned.

## Deviations (so far)
- The AI draft is applied to the editor on the terminal `result` (one transaction), not streamed line by line;
  the editor is read-only with the phase chips during streaming.
- Live analysis dry-runs omit `base_sha` (the save path sends it), so typing never 409s.
- Data grid: the new-row editor is the first row (newest-first sort); grid panel marked `data-scroll-x="allowed"`.
- Builder: `vN · saved` badge doubles as the manual save; API tester opens from App settings.
