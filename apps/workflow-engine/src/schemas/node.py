"""Node-related Pydantic schemas."""

from typing import Any

from pydantic import BaseModel, Field


class NodePropertyOptionSchema(BaseModel):
    """Schema for node property option."""

    name: str
    value: str
    description: str | None = None


class NodePropertySchema(BaseModel):
    """Schema for node property definition."""

    display_name: str = Field(..., alias="displayName")
    name: str
    type: str
    default: Any = None
    required: bool = False
    description: str | None = None
    placeholder: str | None = None
    options: list[NodePropertyOptionSchema] | None = None
    properties: list["NodePropertySchema"] | None = None
    display_options: dict[str, Any] | None = Field(None, alias="displayOptions")
    type_options: dict[str, Any] | None = Field(None, alias="typeOptions")

    class Config:
        populate_by_name = True


class NodeTypeInfo(BaseModel):
    """Full information about a node type."""

    type: str
    display_name: str = Field(..., alias="displayName")
    description: str
    icon: str | None = None
    group: list[str]
    input_count: int | str = Field(..., alias="inputCount")
    output_count: int | str = Field(..., alias="outputCount")
    properties: list[dict[str, Any]]
    inputs: list[dict[str, Any]] | str
    outputs: list[dict[str, Any]] | str
    input_strategy: dict[str, Any] | None = Field(None, alias="inputStrategy")
    output_strategy: dict[str, Any] | None = Field(None, alias="outputStrategy")

    class Config:
        populate_by_name = True
