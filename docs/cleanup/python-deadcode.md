# Python dead-code report: apps/workflow-engine

Tools: vulture and deptry in /tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/cleanup/pyvenv. No repo files were touched. Raw outputs are in the same directory: v60.txt, v100.txt, cand.txt, spans.txt, routes.json, zero_importers.txt.

## Summary

Engine src is 364 .py files. Dead code is small. Almost all of the ~500 vulture hits are false positives (FastAPI handlers, SQLModel/pydantic fields, getattr-dispatched n8n handlers, HTMLParser overrides, pydantic validators). Counts below are after hand verification.

| Check | Verified items | Removable lines |
|---|---|---|
| Whole dead modules (no importer anywhere, not dynamically loaded) | 3 files | 313 |
| Dead functions/methods in live modules (0 references in src, tests, scripts) | 16 | about 100 |
| Functions referenced only by tests | 11 | about 90 (review; some are test hooks) |
| Dead ORM classes (table still used by raw SQL) | 12 | 240, do NOT remove blindly |
| Unused HTTP routes (no UI caller, no internal caller) | 21 route handlers, plus about 30 more that are served-app or test-hook routes | 251 handler lines; about 560 with their schemas and repo |
| Unused deps in requirements.txt | 2 clearly unused (aiosqlite, openpyxl), 1 optional (pandas) | 2-3 lines |
| Tests that only test dead code | 0 found | 0 |

Safe-delete total: about 313 (modules) + about 100 (functions) + 251 (route handlers) = about 660 lines. About 950 if their now-orphaned schemas, `folder_repository.py` and `credential.py`/`folder.py` schemas go too.

Larger finding, which contradicts the "not needed" marking: the engine is wired to apps/jobs-service (see section 6). Deleting jobs-service without also removing that wiring breaks the "jobs" capability. The wiring is about 430 lines, plus tests and template/selftest/check references.

## 1. vulture

Run at --min-confidence 60 (493 lines) and at 100 (7 lines).

### 1a. 100% hits (7)
| Location | Item | Verdict |
|---|---|---|
| src/engine/task_resumer.py:352 | arg `startup` | Signature kept for callers; harmless. Low-value. |
| src/repositories/task_repository.py:265 | args `boot_id`, `startup` | Same: unused params kept for API compatibility. |
| src/engine/test_recipes.py:501 | arg `ctx_nodes` | Unused param. Trivial. |
| src/services/ai_chat_service.py:158 | arg `has_project` in `_possible` | Unused param. Trivial. |
| src/services/app_builder_ai_service.py:33 | `if False:` | Intentional typing-only import (`OpSig`). Keep. |
| src/services/tsx_parser.py:272 | arg `declarator` | Unused param. Trivial. |

None are removable lines; they are signature clean-ups.

### 1b. 60% hits: false positive classes (do not report)
- About 140 `routes/*` functions are FastAPI handlers (decorator-registered).
- n8n_import.py `h_*` (27 methods, lines 411-919) are dispatched with `getattr(self, handler_name)` at n8n_import.py:970.
- `Config` classes (schemas/node.py:31,51, schemas/workflow.py:152,191) are pydantic config.
- Schema validators (`_id_shape`, `_target_shape`, `_locked_means_approved`, `_tests_unique` in schemas/workflow.py; `_column_types` in template.py; `_answers_size`, `_one_mode` in plan_clarify.py; `_spec_size`, `_answers_size`, `_answers_cover_questions` in module_generator.py) are pydantic decorators.
- report_html.py:222,252,276 `handle_starttag`, `handle_endtag`, `handle_data` are HTMLParser overrides.
- Methods with a same-named definition elsewhere (`file_storage.get_file`, `workflow_checker.secret_exists`, `workflow_checker.table_column_defs`, `api_route_service.current_spec`, `template_installer.installed_in`, `execution_repository.cleanup`) have references in src. Not dead, or too ambiguous to claim.
- `sdk/static/jobs.py:128,144` (`artifacts`, `control`) are generated into the user SDK, so public API. Keep.
- `db/models.py` imports: `ForeignKey` unused at line 9 (90%). One-line removal, trivial.

### 1c. Verified dead: zero references in src, tests, scripts, studio
Evidence: `grep -rw NAME src tests scripts` returns only the definition. Confidence is high unless noted.

| Location | Item | Lines | Confidence |
|---|---|---|---|
| src/core/dependencies.py:113-117 | `get_node_output_repository` | 5 | high |
| src/engine/ai_policy.py:343-358 | `scrub_value_pii` | 16 | high |
| src/nodes/integrations/send_email.py:47-53 | `render_markdown_to_html` | 7 | high |
| src/nodes/integrations/send_email.py:56-58 | `wrap_html_in_template` | 3 | high |
| src/repositories/execution_repository.py:544-559 | `find_latest_successful_id` | 16 | high |
| src/repositories/kg_repository.py:390-400 | `remove_edges` | 11 | high |
| src/repositories/node_output_repository.py:89-101 | `get_failed_node` | 13 | high |
| src/repositories/trigger_repository.py:154-166 | `record_error` | 13 | high |
| src/repositories/trigger_repository.py:168-174 | `update_state` | 7 | high |
| src/repositories/workflow_ref_repository.py:43-46, 48-52, 68-71 | `draft_edges`, `published_edges`, `blocking_published_parents` | 13 | high |
| src/schemas/interface.py:266-268 | `find_trigger` | 3 | high |
| src/services/ai_chat_service.py:64-71 | `_tool_get_node_schema` | 8 | medium (could be looked up by name via getattr; grep found no `_tool_` dispatch for it) |
| src/services/api_route_service.py:374-376 | `stub_count` | 3 | high |
| src/services/credential_service.py:67-72 | `get_decrypted` | 6 | high |
| src/services/module_generator.py:385-386 | `_fmt_columns` | 2 | high |
| src/services/project_plan.py:922-923 | `latest_draft` | 2 | high |
| src/services/template_bundle.py:935-936 | `clear_catalog_cache` | 2 | high |

Together about 130 lines, plus the dead-module contents below. `ChatMessage` in db/models.py also shows 10 hits in the studio, but those are studio-side types.

### 1d. Referenced only by tests (src has 0 callers)
Delete only together with the test, or keep if they are deliberate test hooks.

| Location | Item | Lines | Test refs | Note |
|---|---|---|---|---|
| src/engine/fake_llm.py:335-341 | `uninstall` | 7 | 18 | test hook; keep |
| src/engine/wf_ai.py:272-274 | `clear_cache` | 3 | 8 | test hook; keep |
| src/engine/llm_provider.py:100-105 | `require_complete` | 6 | 2 | test-only |
| src/repositories/kg_repository.py:50-51 | `role_key` | 2 | 8 | test-only |
| src/repositories/node_output_repository.py:67-75 | `get_outputs` | 9 | 4 | test-only |
| src/services/ai_grounding.py:75-76 | `catalog_hash` | 2 | 3 | test-only |
| src/services/ai_grounding.py:294-299 | `expression_help_drift` | 6 | 2 | test-only |
| src/services/ai_spec.py:260-262 | `tests_by_ref` | 3 | 1 | test-only |
| src/services/execution_service.py:380-419 | `run_detached` | 40 | 4 | test-only; biggest item |
| src/services/schema_migrations.py:50-51 | `is_additive` | 2 | 2 | test-only |
| src/nodes/data/object_store.py `set_value` | in a dead module (see section 2) | n/a | n/a | n/a |

### 1e. Dead ORM classes (src/db/models.py, knowledge_models.py)
These classes are never imported: HumanTask :774, InterfaceSubmission :807, ChatSession :824, ChatMessage :841, AIProposal :877 (2 test refs), AIProposalEvent :926 (2 test refs), WorkflowRefModel :946, KgNodeModel :986, KgEdgeModel :1016, KgLearnEventModel :1032, AppBuildModel :1048, and ProjectKnowledgeModel (knowledge_models.py:18). That is 240 lines in all.

Confidence that they are removable: low. Their tables (`human_tasks`, `chat_sessions`, `kg_nodes`, and the others) are used by raw SQL in repositories (7 to 43 references each). The classes serve as schema documentation or `create_all` inputs. Check `metadata.create_all` use before touching them.

## 2. Unused modules

Method: AST import resolution (absolute and relative, including `from pkg import module`), then transitive reachability from roots (`src.main`, `src.db.migrate`, `src.sandbox.runtime.runner`, and the `src.sdk.static.*` modules loaded through `importlib.import_module`).

Modules with no importer outside themselves (tests excluded) and not reachable from any root:

| File | Lines | Evidence | Confidence |
|---|---|---|---|
| src/services/schema_analyzer.py | 234 | 0 importers in src, tests, scripts, studio; the `schema_analyzer` string only appears in unrelated hits | high |
| src/nodes/data/object_store.py | 64 | in-memory KV store; 0 importers; its functions have 0 callers (`set_value` hit in tests is a different symbol) | high |
| src/nodes/ai/memory/utils.py | 15 | "Backward-compat re-exports" of utils/memory.py; nothing imports `memory.utils` | high |

Total: 313 lines.

Imported by tests only: none. Every module with zero src importers was either one of the three above or a legitimate entry point: `src/main.py`; `src/db/migrate.py` (run as `python -m src.db.migrate` from reset-db.sh and dev-reset.sh); `src/sandbox/runtime/runner.py` (copied into the bwrap sandbox by sdk/generate.py:367); and `sdk/static/{connections,resources,secret}.py` (loaded with `importlib.import_module` at sdk/generate.py:112-157 and read as text).

Empty `__init__.py` files (nodes/ai/memory, nodes/ai/tools, sdk, utils) are packages, not dead code.

## 3. Dependencies (requirements.txt, no pyproject)

deptry results, checked by hand against src, tests and scripts:

| Package | Finding | Confidence |
|---|---|---|
| aiosqlite | Not imported anywhere. Memory node "sqlite" uses stdlib `sqlite3` (utils/memory.py:13). Removable. | high |
| openpyxl | Not imported in src; no `read_excel` calls. Removable unless pandas Excel reads are exposed via user code in the sandbox. | medium-high |
| pandas | Only an optional import in nodes/data/code.py:239 and the sandbox runner (string check `"pd." in code`). Not strictly required; keep if user code scripts rely on it. | judgement call |
| python-multipart | deptry says unused, but FastAPI `Form()`/`UploadFile` (routes/datasets.py:57, webhooks.py) need it at runtime. Keep. | |
| argon2-cffi, google-genai, google-api-core, psycopg2-binary, PyYAML | deptry DEP002 is a naming mismatch (import names differ: argon2, google.genai, google.api_core, psycopg2, yaml). All are used. Keep. | |

Missing declarations (the reverse problem, worth fixing if the aim is a reproducible install):
- `psycopg` (v3) is imported at the top level in nodes/integrations/postgres.py:7, services/external_db.py:13, services/connection_service.py, broker/policy.py and routes/test_mocks.py. Only `psycopg2-binary` is declared.
- `sqlalchemy` (about 195 imports; comes in transitively via sqlmodel), `starlette` (via fastapi), `markdown` (utils/report_html.py:20), `json_repair`, `docstring_parser` (engine/tool_schema.py), `bson` and `motor` (mongo), `pyarrow` and `sklearn` (optional). None are declared.

Dead-code relevance: after removing the dead modules above, no additional dependency becomes unused.

## 4. HTTP routes with no UI caller

Method: AST extraction of 296 route handlers from src/routes/*.py (plus prefixes), matched against normalised paths in apps/workflow-studio/src (template literals folded to `{}`), then against engine-internal, SDK and test references. Hand-verified every hit where a templated URL could have hidden the call, e.g. `${P(p)}/revise` in features/plan/planApi.ts. That check removed `plan/versions` and `plan/revise` from the list: the studio does use them.

### 4a. Unused by the studio and with no internal caller (21 handlers, 251 lines)

| File:line | Route | Handler lines | Test refs | Note |
|---|---|---|---|---|
| routes/credentials.py:24,38,44,54,72 | POST/GET /credentials, GET/PUT/DELETE /credentials/{id} | 48 | POST/GET only | NOT_REQUIRED in tests/integration/test_zz_endpoint_coverage.py says "credentials are not used by the studio". Reached only by `credential_service`. Route file is 79 lines, schemas/credential.py 32. `credential_service`/`credential_repository` are still used by workflow_runner, so keep those. |
| routes/folders.py:25,44,58,72,92 | /folders CRUD (5 routes) | 65 | POST/GET only | Whole group unused ("folders are not used by the studio"). Routes 97 lines, schemas/folder.py 29, repositories/folder_repository.py 75. `FolderModel` and `folder_id` columns remain in use. |
| routes/files.py:117 | GET /files/validate | 36 | 1 journey test | Listed as NOT_REQUIRED. |
| routes/executions.py:45,58,73,88 | DELETE /executions/{id}, POST .../cancel, POST .../retry, DELETE /executions | 42 | 0 | Listed as NOT_REQUIRED ("not used by the studio"). These handlers call `execution_service` methods. Check whether cancel/retry are wanted before deleting; a user-visible feature gap could exist. |
| routes/broker.py:111 | GET /projects/{p}/broker-calls | 10 | 5 tests | Only `BrokerCallRepository.list` reaches it from here; the repository is also used by broker/service and api_routes. |
| routes/module_schedules.py:53 | PATCH /projects/{p}/schedules/{id} | 10 | unit test | Studio only lists schedules. |
| routes/project_knowledge.py:69 | POST /projects/{p}/knowledge/reindex | 4 | 0 | |
| routes/templates.py:138,144 | GET /projects/{p}/template-installs, .../{id} | 11 | integration | |
| routes/templates.py:153 | GET /workflows/{id}/export-template | 10 | integration | |
| routes/workflows.py:398 | POST /workflows/{id}/apply-ops | 15 | integration, referenced in workflow_toolkit docstring | Medium confidence: may be intended for AI/external callers. |

Medium or low confidence overall: tests and the "kept for external callers" comment in the coverage test suggest the owner kept these on purpose. They are unused by the studio and by anything internal.

### 4b. Not called by the studio, but called by something else (keep)
- `/a/{slug}/_assets/*`, `_token`, `_unlock`, `api-tester/replay/*`, and `/a/{slug}/api/workflow/{id}/run` in routes/public_apps.py are the published-app runtime (the iframe/app_kit and password page call them).
- `/api/_test/*` (routes/test_hooks.py:70,85,92,100,108,116,135,158) are test hooks gated by `_guard`. Used by the integration tests; not by the studio. Keep or leave out of the prune.
- `GET /execution-stream/{workflow_id}` (routes/streaming.py:308) is legacy; the studio uses POST (per NOT_REQUIRED).
- `/nodes/catalog` (test_authoring.py:55) is used by services/node_catalog.
- `POST /broker/call` (broker.py:43) is called by sdk/static/_transport.py.

## 5. Tests that test only removed or unused code

None. Every candidate in sections 1c and 2 has 0 test references (dead modules are not imported by any test either). Tests exist for the unused routes in 4a (for example test_char_apps.py:201 for /folders, test_j22 for /files/validate, test_stage8_b5_templates_http.py for template-installs/export-template, test_project_plan_routes.py). These would need to be deleted along with the routes if those are removed. tests/integration/test_zz_endpoint_coverage.py lists the unused routes in its NOT_REQUIRED set and needs the same edit (6 entries for credentials/folders/files/executions).

## 6. apps/jobs-service and apps/test-builder

Size: jobs-service about 2.2k lines of TS (excluding node_modules); test-builder about 5.5k lines of TS (excluding node_modules, runs and state). jobs-service imports test-builder (`../../test-builder/src/secrets.ts`, `planners/llm.ts`, actions), so they are coupled. Neither is imported by workflow-studio/src (0 hits).

workflow-engine references jobs-service. It is NOT independent:
- src/core/config.py:72-80 has `jobs_service_url`, `jobs_service_token`, `jobs_dev` settings.
- src/broker/jobs.py (283 lines) is a broker node type `jobs`; src/broker/service.py:19,42,180-192 and src/broker/policy.py dispatch to it.
- src/sdk/static/jobs.py (149 lines) is the generated `studio.jobs` SDK function; src/sdk/generate.py registers it.
- src/services/backend_templates/{checks.py:245, selftest.py:57, gaps.py, index.py, prompts.py, quality.py}, plus builder_gates.py, build_coordinator.py, module_generator.py, app_builder_ai_service.py, platform_index.py, project_plan.py, plan_clarify.py and proposal_sandbox.py mention jobs. About 43 hits in backend_templates alone.
- Tests: tests/unit/test_broker_jobs.py (369 lines), tests/unit/test_sdk_jobs_generation.py (147 lines).
- src/sdk/static/jobs.py:29 also references apps/test-builder/src/actions.ts (comment only).

test-builder: no import or HTTP call from the engine or studio. Only comments (sdk/static/jobs.py:29) and jobs-service's own imports.

Recommendation: if jobs-service is dropped, jobs support in the engine (about 430 lines in broker/jobs.py and sdk/static/jobs.py, the `jobs` branch in broker/service.py and policy.py, the config settings, the template checks and selftest, and the two unit tests) must be removed in the same change, or the "jobs" capability will be a dead call. test-builder can only be removed together with jobs-service.

## Method notes
- vulture cannot see FastAPI, pydantic, getattr or importlib references. Every item kept in sections 1c/1d/2 was re-checked with `grep -rw` across src, tests, scripts and the studio.
- The route matcher can falsely mark a route "used" when a different path shares a literal tail with a studio string. Section 4a was spot-checked by hand for that, not exhaustively. About 82 routes matched only on a weak tail; the ones sampled were real callers.
- The studio is checked statically; there could be callers I cannot see (external clients, MCP, curl scripts).
