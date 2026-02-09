"""Neo4j node - executes Cypher queries against a Neo4j graph database."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from neo4j import AsyncGraphDatabase

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...utils.serialization import serialize_value as _base_serialize, parse_json_params

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "testpassword"
DEFAULT_DATABASE = "neo4j"


def _serialize_value(val: Any) -> Any:
    """Serialize Neo4j-specific types, falling back to common serialization."""
    # Neo4j Node / Relationship / Path objects
    if hasattr(val, '__class__') and val.__class__.__name__ == 'Node':
        return {
            "_neo4jType": "node",
            "id": val.element_id,
            "labels": list(val.labels),
            "properties": {k: _serialize_value(v) for k, v in dict(val).items()},
        }
    if hasattr(val, '__class__') and val.__class__.__name__ == 'Relationship':
        return {
            "_neo4jType": "relationship",
            "id": val.element_id,
            "type": val.type,
            "startNodeId": val.start_node.element_id if val.start_node else None,
            "endNodeId": val.end_node.element_id if val.end_node else None,
            "properties": {k: _serialize_value(v) for k, v in dict(val).items()},
        }
    if hasattr(val, '__class__') and val.__class__.__name__ == 'Path':
        return {
            "_neo4jType": "path",
            "nodes": [_serialize_value(n) for n in val.nodes],
            "relationships": [_serialize_value(r) for r in val.relationships],
        }
    # Lists/dicts need to recurse through this function (not the base) for Neo4j types
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return _base_serialize(val)


def _process_records(records_raw: list, keys: list) -> list:
    """Convert raw neo4j record values into serialized dicts/values."""
    records = []
    for record_values in records_raw:
        if len(keys) == 1:
            records.append(_serialize_value(record_values[0]))
        else:
            records.append({
                keys[i]: _serialize_value(v) for i, v in enumerate(record_values)
            })
    return records


class Neo4jNode(BaseNode):
    """Neo4j node - executes Cypher queries against a Neo4j graph database."""

    node_description = NodeTypeDescription(
        name="Neo4j",
        display_name="Neo4j",
        description="Execute Cypher queries against a Neo4j graph database",
        icon="fa:project-diagram",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "records": {"type": "array", "description": "Query result records"},
                        "recordCount": {"type": "number", "description": "Number of records returned"},
                        "summary": {"type": "object", "description": "Query execution summary"},
                    },
                },
            )
        ],
        properties=[
            # --- Connection ---
            NodeProperty(
                display_name="URI",
                name="uri",
                type="string",
                default=DEFAULT_URI,
                required=True,
                description="Neo4j connection URI. Supports expressions.",
                placeholder="bolt://localhost:7687",
            ),
            NodeProperty(
                display_name="Username",
                name="username",
                type="string",
                default=DEFAULT_USER,
                description="Neo4j username. Supports expressions.",
            ),
            NodeProperty(
                display_name="Password",
                name="password",
                type="string",
                default=DEFAULT_PASSWORD,
                description="Neo4j password. Supports expressions.",
            ),
            NodeProperty(
                display_name="Database",
                name="database",
                type="string",
                default=DEFAULT_DATABASE,
                description="Neo4j database name. Supports expressions.",
            ),
            # --- Operation ---
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="query",
                options=[
                    NodePropertyOption(
                        name="Query",
                        value="query",
                        description="Run any Cypher query (read or write). Use $param for parameters.",
                    ),
                    NodePropertyOption(
                        name="Transaction",
                        value="transaction",
                        description="Run multiple Cypher statements in one atomic transaction",
                    ),
                ],
            ),
            # --- Query fields ---
            NodeProperty(
                display_name="Cypher Query",
                name="query",
                type="string",
                default="",
                description="Any Cypher query. Use $param syntax for parameters. Supports expressions.",
                type_options={"rows": 5},
                display_options={"show": {"operation": ["query"]}},
            ),
            NodeProperty(
                display_name="Parameters",
                name="queryParameters",
                type="json",
                default="{}",
                description='JSON object of named parameters for $param placeholders. e.g. {"name": "Alice"}. Supports expressions.',
                type_options={"language": "json", "rows": 4},
                display_options={"show": {"operation": ["query"]}},
            ),
            # --- Transaction fields ---
            NodeProperty(
                display_name="Statements",
                name="statements",
                type="json",
                default="[]",
                description='JSON array of {"query": "...", "params": {...}} objects. All run atomically. Supports expressions.',
                type_options={"language": "json", "rows": 10},
                display_options={"show": {"operation": ["transaction"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Neo4j"

    @property
    def description(self) -> str:
        return "Execute Cypher queries against a Neo4j graph database"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        operation = self.get_parameter(node_definition, "operation", "query")
        items = input_data if input_data else [ND(json={})]

        first_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        uri = str(expression_engine.resolve(self.get_parameter(node_definition, "uri", DEFAULT_URI), first_ctx))
        user = str(expression_engine.resolve(self.get_parameter(node_definition, "username", DEFAULT_USER), first_ctx))
        pwd = str(expression_engine.resolve(self.get_parameter(node_definition, "password", DEFAULT_PASSWORD), first_ctx))
        db = str(expression_engine.resolve(self.get_parameter(node_definition, "database", DEFAULT_DATABASE), first_ctx))

        driver = AsyncGraphDatabase.driver(uri, auth=(user, pwd))
        try:
            if operation == "transaction":
                return await self._exec_transaction(driver, db, node_definition, items, input_data, context, expression_engine, ExpressionEngine)

            # Single query
            query_template = self.get_parameter(node_definition, "query", "")
            params_template = self.get_parameter(node_definition, "queryParameters", "{}")
            results: list[ND] = []

            async with driver.session(database=db) as session:
                for idx, item in enumerate(items):
                    expr_ctx = ExpressionEngine.create_context(
                        input_data, context.node_states, context.execution_id, idx,
                    )
                    query = str(expression_engine.resolve(query_template, expr_ctx))
                    params = expression_engine.resolve_json_template(params_template, expr_ctx)
                    if not isinstance(params, dict):
                        params = parse_json_params(params, default={})

                    result = await session.run(query, params)
                    records_raw = await result.values()
                    keys = result.keys()
                    summary = await result.consume()

                    results.append(ND(json={
                        "records": _process_records(records_raw, keys),
                        "recordCount": len(records_raw),
                        "summary": {
                            "counters": {
                                "nodesCreated": summary.counters.nodes_created,
                                "nodesDeleted": summary.counters.nodes_deleted,
                                "relationshipsCreated": summary.counters.relationships_created,
                                "relationshipsDeleted": summary.counters.relationships_deleted,
                                "propertiesSet": summary.counters.properties_set,
                                "labelsAdded": summary.counters.labels_added,
                                "labelsRemoved": summary.counters.labels_removed,
                            },
                            "queryType": summary.query_type,
                        },
                    }))

            return self.output(results)
        finally:
            await driver.close()

    async def _exec_transaction(self, driver, db, node_definition, items, input_data, context, expression_engine, ExpressionEngine):
        from ...engine.types import NodeData as ND

        stmts_template = self.get_parameter(node_definition, "statements", "[]")
        expr_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        stmts = expression_engine.resolve_json_template(stmts_template, expr_ctx)
        if not isinstance(stmts, list) or len(stmts) == 0:
            return self.output([ND(json={"success": False, "error": "No statements provided"})])

        statement_results = []

        async with driver.session(database=db) as session:
            tx = await session.begin_transaction()
            try:
                for i, stmt in enumerate(stmts):
                    query = str(expression_engine.resolve(stmt.get("query", ""), expr_ctx))
                    params = parse_json_params(stmt.get("params", {}), default={})

                    result = await tx.run(query, params)
                    records_raw = await result.values()
                    keys = result.keys()
                    summary = await result.consume()

                    statement_results.append({
                        "statementIndex": i,
                        "records": _process_records(records_raw, keys),
                        "recordCount": len(records_raw),
                        "counters": {
                            "nodesCreated": summary.counters.nodes_created,
                            "relationshipsCreated": summary.counters.relationships_created,
                            "propertiesSet": summary.counters.properties_set,
                        },
                    })

                await tx.commit()
            except Exception as e:
                await tx.rollback()
                return self.output([ND(json={
                    "success": False,
                    "error": str(e),
                    "rolledBack": True,
                    "failedStatementIndex": i,
                    "completedStatements": statement_results,
                })])

        return self.output([ND(json={
            "success": True,
            "statementCount": len(stmts),
            "results": statement_results,
        })])
