"""Route aggregation for workflow engine API."""

from fastapi import APIRouter

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

# Main API router
api_router = APIRouter(prefix="/api")
api_router.include_router(workflows_router, tags=["Workflows"])
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

# Non-prefixed routers (for webhooks, streaming, and public deployed apps)
webhook_router = webhooks_router
stream_router = streaming_router
public_app_router = public_apps_router

__all__ = [
    "api_router",
    "webhook_router",
    "stream_router",
    "public_app_router",
]
