"""Request bodies of the Stage 6 AI builder routes (DESIGN-STAGE6-AI-BUILDER section 4).

Every mutating call names its actor: the body field ``actor`` (1-80 characters, trimmed) or the ``X-Actor-Name`` header; the
routes answer 422 ``actor_required`` without one. Responses are plain dicts (``ProposalView`` and friends, see ``services/ai_builder``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")

    actor: str | None = Field(None, description="Who is asking (typed name until login exists). Or the X-Actor-Name header.")


class SpecRequestBody(_Body):
    project_id: str = Field(..., min_length=1)
    workflow_id: str | None = Field(None, description="Set to change an existing workflow (kind=modify).")
    request: str = Field(..., min_length=1, description="What the workflow should do, in plain English.")
    model: str | None = Field(None, description="Defaults to WF_AI_MODEL; must pass the allowlist.")
    use_graph: bool = Field(False, description="Ground the spec in the knowledge graph (Understand). Off: behaviour is exactly as before.")
    clarify: bool = Field(False, description="With use_graph: ask the one WF_AI:UNDERSTAND call and stop with status `clarify` while a required slot is empty.")
    answers: dict[str, Any] | None = Field(None, description="Answers to questions, by question id, when the caller already knows them.")


class AnswersBody(_Body):
    answers: dict[str, Any] = Field(..., description="{question id: value}. Every required question needs one.")


class PutSpecBody(_Body):
    spec: dict[str, Any]


class ActorBody(_Body):
    pass


class KeepBody(_Body):
    accept_failing: bool = False
    name: str | None = Field(None, max_length=255, description="Name of the new workflow (new workflows only).")


class ReviseBody(_Body):
    instruction: str = Field(..., min_length=1)


class NodeAIBody(_Body):
    task: Literal["fix_error", "write_code", "write_expression", "explain"]
    field: str | None = Field(None, description="write_expression: the parameter path; write_code: the code parameter (default `code`).")
    instruction: str | None = None
    execution_id: str | None = Field(None, description="fix_error: the failed run (default: the node's latest failed run).")
