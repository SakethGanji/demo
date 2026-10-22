# Codebase map: where the code is

Measured 2026-10-22. Counts are lines of source code, not counting tests. They include the uncommitted working tree, which has the visual editor in it.

**The GitHub branch is smaller.** `revert-to-5684ba5` at `87989ea` measures:

| App | Lines |
|---|---|
| Studio | 100.4k |
| Engine | 102.1k |
| **Total** | **~202k** |

That count leaves out the visual editor (10.2k) and the newer local kit and builder changes. `CUT-GUIDE.md` uses the GitHub numbers.

## Top level

| App | Lines | Notes |
|---|---|---|
| `apps/workflow-studio`: React UI | **111.8k** | 10.2k of it is the uncommitted visual editor |
| `apps/workflow-engine`: Python API | **104.9k** | |
| `apps/analytics-service` | 8.4k | separate service |
| `apps/test-builder` | 4.7k | only jobs-service uses it |
| `apps/jobs-service` | 2.1k | the engine calls it (broker) |
| **Total, the main two apps** | **~217k** | |
| Tests, on top of that | 85k | engine 67.8k, studio 17.2k |

## The 10 biggest areas, across both apps

| # | Area | Lines | Where |
|---|---|---|---|
| 1 | **App builder** (engine + UI, without the visual editor) | ~32k | engine services 25k + studio 9k (see below) |
| 2 | **Workflow editor** (canvas UI) | 23.9k | `studio/features/workflow-editor` |
| 3 | **Workflow nodes** | 17.4k | `engine/nodes`. The AI nodes alone are 8.8k, and `ai_agent.py` by itself is 2.7k. |
| 4 | **AI workflow builder** (chat that writes workflows) | 14.7k | engine `ai_*` and friends 6.9k + `studio/features/ai-builder` 7.8k |
| 5 | **Shared UI** (components, API client, CSS) | 13.4k | `studio/shared` 11.8k + `index.css` 1.6k |
| 6 | **Engine plumbing** (routes, DB, schemas, repositories, queue, SDK) | 19.7k | `engine/routes`, `db`, `schemas`, `repositories`, `broker`, `sdk`, `core`, `utils`, `sandbox` |
| 7 | **Project pages** (overview, home, nav, plan) | 15.2k | `studio/features/project` 10.7k + `plan` 4.5k |
| 8 | **Backend builder** | 13.6k | engine module generator and templates 7.2k + `studio/features/backend` 6.4k |
| 9 | **Visual editor** (uncommitted, being dropped) | 10.2k | `studio/features/app-builder/visual` |
| 10 | **Knowledge** (being cut) | 9.8k | engine `kg_*` 3.5k + `studio/features/knowledge` 6.3k |

Two more:
- The workflow runner and management code is about 17.7k: engine `engine/` 9.3k plus the workflow services 8.5k.
- Everything else is small pages, each under 3.5k.

## Engine (104.9k) in detail

| Folder | Lines | What's in it |
|---|---|---|
| `services/` | 57.2k | all the business logic, split in the next table |
| `nodes/` | 17.4k | ai 8.8k · data 3.2k · flow 2.4k · integrations 2.0k · triggers 0.8k |
| `engine/` | 9.3k | the workflow runner (1.5k), the LLM provider (1.0k), test recipes (0.9k), … |
| `routes/` | 7.0k | HTTP endpoints |
| `repositories/` | 3.6k | database access |
| `db/` | 3.1k | models (1.1k) and demo seed data (1.3k) |
| `schemas/` | 2.1k | request and response types |
| `broker/`, `sdk/`, `core/`, `utils/`, `sandbox/` | 4.8k | queue, SDK, config, sandbox |

### `services/` (57.2k), by feature

| Feature | Lines | Main files |
|---|---|---|
| **App builder core** | 10.0k | `app_builder_ai_service` 3.3k, `oneshot_builder` 2.3k, `build_coordinator` 1.5k (legacy), `builder_gates` 1.1k, `app_scaffold`, `app_service`, `app_autotest`, selftest, editability lint |
| App kit + bundler | 5.0k | `app_kit/*.tsx`: the components generated apps import |
| Backend builder | 7.2k | `module_generator` 1.8k, `backend_templates/` 3.0k, module and route services |
| Workflow management | 8.5k | `workflow_service` 1.0k, `template_installer` 1.0k, `template_bundle` 0.9k, `n8n_import` 1.2k, subworkflows, executions |
| AI workflow builder | 6.9k | `ai_builder` 2.1k, `ai_spec`, `ai_repairs`, `ai_grounding`, `workflow_toolkit`, `proposal_sandbox`, … |
| Knowledge (`kg_*`) | 3.5k | extract, learn, understand, slots, ontology, retrieve |
| Plan step | 2.6k | `project_plan` 1.1k, `plan_clarify`, `plan_checklist`, `plan_compact` |
| Releases and interfaces | 1.8k | `release_service`, `interface_*` |
| Data tables and schemas | 1.8k | `project_dataset_service`, `schema_*`, `data_table_service` |
| Hidden app composer | 1.4k | `app_composer`, `app_plan`, `app_bundle` |
| Workflow QA extras | 1.2k | `fuzz`, `drift`, `test_runner` |
| Everything else | ~6.8k | auth, secrets, API keys, webhooks, files, connections, chat, approvals, mock data, … |

The app builder adds up to about **25k** of engine code: core 10.0k, kit and bundler 5.0k, plan 2.6k, hidden composer 1.4k, prompts 0.5k, plus parts of the checks.

## Studio (111.8k) in detail

| Folder | Lines | What's in it |
|---|---|---|
| `features/workflow-editor` | 23.9k | canvas, node forms (`DynamicNodeForm` 1.1k), store (1.1k), runs, the Tests tab, interfaces/AskBar pieces |
| `features/app-builder` | 19.3k | **visual editor 10.2k** + chat, preview and sandbox 9.1k (`ApiTesterPanel` alone is 1.9k) |
| `shared/` | 11.8k | components 8.8k (incl. the copied-in `ai-elements` kit; `prompt-input.tsx` alone is 1.5k), API client 2.7k |
| `features/project` | 10.7k | project overview, home, nav, setup panels, the old dead `story/` and overview tiles (~3.5k dead) |
| `features/ai-builder` | 7.8k | the AI workflow builder chat panel, plus the hidden app composer (`apps/`, 1.8k) |
| `features/backend` | 6.4k | the backend builder page: `BackendView` 1.1k, `TestPanel` 0.9k, code map |
| `features/knowledge` | 6.3k | 3D graph, list, coverage, memory |
| `features/plan` | 4.5k | the plan page and plan flow |
| `features/data` + `datasets` | 4.1k | data tables |
| `features/interfaces` + `releases` | 3.3k | being cut |
| `runs`, `settings`, `secrets` | 4.8k | |
| `index.css` + `app/` (router, layouts) | 2.8k | |
| Small pages | ~6.5k | build-studio 1.0k, code 1.0k, templates 1.0k, landing 0.9k, approvals 0.7k, connections 0.6k, projects 0.5k, api-catalog 0.4k |

## The 15 biggest files (kept areas)

| Lines | File |
|---|---|
| 3,265 | engine `services/app_builder_ai_service.py` |
| 2,749 | engine `nodes/ai/ai_agent.py` |
| 2,262 | engine `services/oneshot_builder.py` |
| 2,141 | engine `services/ai_builder.py` |
| 1,920 | studio `app-builder/components/ApiTesterPanel.tsx` |
| 1,775 | engine `services/module_generator.py` |
| 1,537 | engine `services/build_coordinator.py` (legacy) |
| 1,453 | studio `shared/components/ai-elements/prompt-input.tsx` |
| 1,450 | engine `engine/workflow_runner.py` |
| 1,380 | engine `services/workflow_checker.py` |
| 1,319 | engine `db/seed.py` (demo data) |
| 1,155 | engine `services/n8n_import.py` |
| 1,140 | studio `workflow-editor/stores/workflowStore.ts` |
| 1,127 | engine `services/builder_gates.py` |
| 1,124 | engine `services/project_plan.py` |

## By product

| Product | Lines | Share |
|---|---|---|
| **Workflows**: nodes, runner, management, editor, runs | ~67k | 31% |
| **App builder** (incl. plan, kit, hidden composer) | ~40k | 18% |
| **Platform**: plumbing, shared UI, project pages, data, settings, secrets | ~52k | 24% |
| **AI workflow builder** | ~15k | 7% |
| **Backend builder** | ~14k | 6% |
| **Being cut**: visual editor, knowledge, releases, interfaces, API tester | ~27k | 12% |
| Small pages and misc | ~4k | 2% |

These figures are rounded. Folder totals are exact; the feature buckets inside `services/` are grouped by file name, so they're within a few hundred lines.

See `SIMPLIFICATION-PLAN.md` for what to cut and simplify.
