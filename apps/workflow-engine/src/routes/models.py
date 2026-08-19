"""Model catalog for the agent create/edit UI.

The studio needs a source of selectable models. This exposes the models the
engine can actually route to, flagged by whether their provider credential is
configured — so the create-agent form can populate a selector and default to a
model that will actually run instead of hard-coding one string.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from ..engine.llm_provider import _get_env

router = APIRouter(prefix="/models")


class ModelInfo(BaseModel):
    id: str
    label: str
    provider: str  # anthropic | gemini | openai
    available: bool  # provider credential configured
    default: bool = False


# Ordered best-first within each provider. `default` marks the model the UI
# should preselect; it is chosen at request time as the first *available* one.
_CATALOG: list[tuple[str, str, str]] = [
    ("gemini-3.6-flash", "Gemini 3.6 Flash", "gemini"),
    ("claude-opus-5", "Claude Opus 5", "anthropic"),
    ("claude-sonnet-5", "Claude Sonnet 5", "anthropic"),
    ("gpt-4o", "GPT-4o", "openai"),
]

_PROVIDER_KEY = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@router.get("", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    """Selectable models, flagged by provider-credential availability."""
    out: list[ModelInfo] = []
    default_taken = False
    for model_id, label, provider in _CATALOG:
        available = bool(_get_env(_PROVIDER_KEY[provider]))
        is_default = available and not default_taken
        if is_default:
            default_taken = True
        out.append(
            ModelInfo(
                id=model_id,
                label=label,
                provider=provider,
                available=available,
                default=is_default,
            )
        )
    return out
