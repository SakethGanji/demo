"""Route aggregation for workflow engine API."""

from fastapi import APIRouter

from .n8n_import import router as n8n_import_router
from .api_specs import router as api_specs_router
from .test_mocks import router as test_mocks_router
from .test_authoring import router as test_authoring_router
from .test_ai import router as test_ai_router
from .test_fuzz import router as test_fuzz_router
from .drift_smoke import router as drift_smoke_router
from .interfaces import router as interfaces_router
from .public_interfaces import router as public_interfaces_router
from .tasks import router as tasks_router
from .ai_builder import router as ai_builder_router
from .ai_apps import router as ai_apps_router
from .workflows import router as workflows_router
from .executions import router as executions_router
from .nodes import router as nodes_router
from .webhooks import router as webhooks_router
from .streaming import router as streaming_router
from .files import router as files_router
from .ai_chat import router as ai_chat_router
from .credentials import router as credentials_router
from .folders import router as folders_router
from .variables import router as variables_router
from .apps import router as apps_router
from .app_builder_ai import router as app_builder_ai_router
from .public_apps import router as public_apps_router
from .api_tester import router as api_tester_router
from .projects import router as projects_router
from .project_secrets import router as project_secrets_router
from .project_connections import router as project_connections_router
from .broker import router as broker_router
from .api_routes import router as api_routes_router, project_router as project_api_router
from .tables import router as tables_router
from .datasets import router as datasets_router
from .modules import router as modules_router
from .releases import router as releases_router
from .templates import router as templates_router
from .subworkflows import router as subworkflows_router
from .knowledge import router as knowledge_router
from .project_knowledge import router as project_knowledge_router
from .module_schedules import router as module_schedules_router
from .builder_gates import router as builder_gates_router
from .project_plan import router as project_plan_router
from .builds import router as builds_router
from .project_files import router as project_files_router
from .notebook_keep import router as notebook_keep_router
from .notebooks import router as notebooks_router

# Main API router
api_router = APIRouter(prefix="/api")
# Must precede workflows_router: "/workflows/import/n8n" would otherwise match /workflows/{workflow_id}/...
api_router.include_router(n8n_import_router, tags=["Workflows"])
api_router.include_router(test_mocks_router, tags=["Workflows"])
# Must precede workflows_router and nodes_router: "/nodes/catalog" would otherwise match /nodes/{node_type}
api_router.include_router(test_authoring_router, tags=["Workflows"])
api_router.include_router(test_ai_router, tags=["Workflows"])
api_router.include_router(test_fuzz_router, tags=["Workflows"])
api_router.include_router(drift_smoke_router, tags=["Workflows"])
# Stage 5: /workflows/{id}/interface* (before workflows_router) and the anonymous /api/i/{project}/{slug}/*
api_router.include_router(interfaces_router, tags=["Interfaces"])
api_router.include_router(public_interfaces_router, tags=["Public Interfaces"])
api_router.include_router(tasks_router, tags=["Tasks"])
# Stage 6: /ai/builder, /ai/proposals and /workflows/{id}/nodes/{name}/ai (before workflows_router and nodes_router)
api_router.include_router(ai_builder_router, tags=["AI Builder"])
# Capability graph: /ai/apps (the app composer; its parts are ordinary /ai/proposals)
api_router.include_router(ai_apps_router, tags=["AI Apps"])
# Stage 8 templates: /templates, /projects/{p}/templates/*, /workflows/{id}/export-template (before workflows_router)
api_router.include_router(templates_router, tags=["Templates"])
# Stage 8 reuse: /projects/{p}/subworkflows and /workflows/{id}/io, /used-by, /extract-subworkflow (before workflows_router)
api_router.include_router(subworkflows_router, tags=["Sub-workflows"])
# Capability graph: /knowledge/* (before workflows_router for good measure; no path overlaps)
api_router.include_router(knowledge_router, tags=["Knowledge"])
# Builders' per-project knowledge store: /projects/{p}/knowledge (retrieve, upsert, reindex)
api_router.include_router(project_knowledge_router, tags=["Project Knowledge"])
api_router.include_router(module_schedules_router, tags=["Module Schedules"])
# The project plan: /projects/{p}/plan, /plan/versions, /plan/{v:int}, /plan/draft, /plan/revise (versions are int-typed, so
# builder_gates' POST /plan/check never matches /plan/{v})
api_router.include_router(project_plan_router, tags=["Project Plan"])
# Builds of the approved plan: /projects/{p}/builds, /builds/{id}, /events, /cancel, /continue
api_router.include_router(builds_router, tags=["Builds"])
# One project file tree + its change log: /projects/{p}/files, /files/content, /files/history, /changes
api_router.include_router(project_files_router, tags=["Project Files"])
# Builder gates run on saved artifacts: /projects/{p}/modules/{m}/check, /apps/{id}/check (before apps_router: no path overlap)
api_router.include_router(builder_gates_router, tags=["Builder Gates"])
# Notebooks: notebook_keep first so the literal /notebooks/capabilities never matches /notebooks/{notebook_id}
api_router.include_router(notebook_keep_router, tags=["Notebooks"])
api_router.include_router(notebooks_router, tags=["Notebooks"])
api_router.include_router(workflows_router, tags=["Workflows"])
api_router.include_router(api_specs_router, tags=["API Catalog"])
api_router.include_router(apps_router, tags=["Apps"])
api_router.include_router(executions_router, tags=["Executions"])
api_router.include_router(nodes_router, tags=["Nodes"])
api_router.include_router(files_router, tags=["Files"])
api_router.include_router(ai_chat_router, tags=["AI Chat"])
api_router.include_router(app_builder_ai_router, tags=["App Builder AI"])
api_router.include_router(credentials_router, tags=["Credentials"])
api_router.include_router(folders_router, tags=["Folders"])
api_router.include_router(variables_router, tags=["Variables"])
api_router.include_router(api_tester_router, tags=["API Tester"])
api_router.include_router(project_secrets_router, tags=["Project Secrets"])
api_router.include_router(project_connections_router, tags=["Project Connections"])
api_router.include_router(broker_router, tags=["Broker & SDK"])
api_router.include_router(api_routes_router, tags=["API Routes"])
api_router.include_router(tables_router, tags=["Data Tables"])
api_router.include_router(datasets_router, tags=["Project Datasets"])
api_router.include_router(modules_router, tags=["Python Modules"])
api_router.include_router(releases_router, tags=["Releases"])
api_router.include_router(projects_router, tags=["Projects"])

# Non-prefixed routers (for webhooks, streaming, and public deployed apps)
webhook_router = webhooks_router
stream_router = streaming_router
public_app_router = public_apps_router
project_router = project_api_router

__all__ = [
    "api_router",
    "webhook_router",
    "stream_router",
    "public_app_router",
    "project_router",
]
