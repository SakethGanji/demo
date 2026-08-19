-- The "sdk" tool source: the workflow toolkit (build_workflow / list_workflows
-- / run_workflow) resolved by agent_tool_resolver. Mirrors the ToolSource
-- Literal in src/schemas/agent.py — both lists must change together.
ALTER TABLE agent_tool_bindings
    DROP CONSTRAINT IF EXISTS agent_tool_bindings_source_check;
ALTER TABLE agent_tool_bindings
    ADD CONSTRAINT agent_tool_bindings_source_check
    CHECK (source IN ('builtin','mcp','openapi','node','promoted','sdk'));
