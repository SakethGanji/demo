"""Request bodies of the AI app composer routes (PLAN-CAPABILITY-GRAPH section 5.1; ``routes/ai_apps.py``).

Same conventions as the Stage 6 builder: every mutating call names its actor (body ``actor`` or the ``X-Actor-Name`` header; 422
``actor_required`` without one). Responses are plain dicts (``AppBuildView``, see ``services/app_composer.view``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")

    actor: str | None = Field(None, description="Who is asking (typed name until login exists). Or the X-Actor-Name header.")


class CreateAppBody(_Body):
    project_id: str = Field(..., min_length=1)
    request: str = Field(..., min_length=1, description="What the application should do, in plain English.")
    answers: dict[str, Any] | None = Field(None, description="Answers to questions, by question id, when the caller already knows them.")
    use_llm: bool = Field(True, description="Let Understand make its one WF_AI:UNDERSTAND call (it degrades to keyword matching when the model is unavailable).")


class AppAnswersBody(_Body):
    answers: dict[str, Any] = Field(..., description="{question id: value}. Every required question needs one.")


class PutPlanBody(_Body):
    plan: dict[str, Any] = Field(..., description="The whole AppPlan (CONTRACTS-CAPGRAPH.md 5.2); re-validated with APP001-APP008, no model call.")


class AppActorBody(_Body):
    pass


class KeepAppBody(_Body):
    accept_failing: bool = Field(False, description="Keep although an approved test is red (the sub-workflows still must publish).")
