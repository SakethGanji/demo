"""Postgres node - executes parameterized queries against a PostgreSQL database."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import psycopg

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...utils.serialization import serialize_value, parse_json_params

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


DEFAULT_CONNECTION_STRING = "postgresql://postgres:postgres@localhost:5433/testdb"


async def _fetch_rows(cur: psycopg.AsyncCursor) -> list[dict[str, Any]]:
    """Fetch all rows from cursor and serialize to dicts."""
    raw_rows = await cur.fetchall()
    columns = [desc[0] for desc in cur.description] if cur.description else []
    return [
        {col: serialize_value(row[i]) for i, col in enumerate(columns)}
        for row in raw_rows
    ]


class PostgresNode(BaseNode):
    """Postgres node - executes parameterized queries against a PostgreSQL database."""

    node_description = NodeTypeDescription(
        name="Postgres",
        display_name="Postgres",
        description="Execute queries against a PostgreSQL database",
        icon="fa:database",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array", "description": "Query result rows (for SELECT)"},
                        "rowCount": {"type": "number", "description": "Number of rows returned or affected"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Connection String",
                name="connectionString",
                type="string",
                default=DEFAULT_CONNECTION_STRING,
                required=True,
                description="PostgreSQL connection string. Supports expressions.",
                placeholder="postgresql://user:pass@host:5432/dbname",
            ),
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="query",
                options=[
                    NodePropertyOption(
                        name="Query",
                        value="query",
                        description="Run any SQL query. SELECT returns rows, INSERT/UPDATE/DELETE returns affected count.",
                    ),
                    NodePropertyOption(
                        name="Transaction",
                        value="transaction",
                        description="Run multiple statements in a single atomic transaction",
                    ),
                ],
            ),
            # --- Query fields ---
            NodeProperty(
                display_name="Query",
                name="query",
                type="string",
                default="",
                description="Any SQL query. Use %s for positional params or %(name)s for named params. Supports expressions.",
                type_options={"rows": 5},
                display_options={"show": {"operation": ["query"]}},
            ),
            NodeProperty(
                display_name="Query Parameters",
                name="queryParameters",
                type="json",
                default="[]",
                description="JSON array for positional params or JSON object for named params. Supports expressions.",
                type_options={"language": "json", "rows": 4},
                display_options={"show": {"operation": ["query"]}},
            ),
            # --- Transaction fields ---
            NodeProperty(
                display_name="Statements",
                name="statements",
                type="json",
                default="[]",
                description='JSON array of {"query": "...", "params": [...]} objects. All run atomically. Supports expressions.',
                type_options={"language": "json", "rows": 10},
                display_options={"show": {"operation": ["transaction"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Postgres"

    @property
    def description(self) -> str:
        return "Execute queries against a PostgreSQL database"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        conn_template = self.get_parameter(node_definition, "connectionString", DEFAULT_CONNECTION_STRING)
        operation = self.get_parameter(node_definition, "operation", "query")

        items = input_data if input_data else [ND(json={})]

        first_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        conn_string = str(expression_engine.resolve(conn_template, first_ctx))

        if operation == "transaction":
            return await self._exec_transaction(
                conn_string, node_definition, items, input_data, context, expression_engine, ExpressionEngine,
            )

        # Single query - auto-detect SELECT vs write
        query_template = self.get_parameter(node_definition, "query", "")
        params_template = self.get_parameter(node_definition, "queryParameters", "[]")

        results: list[ND] = []

        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            for idx, item in enumerate(items):
                expr_ctx = ExpressionEngine.create_context(
                    input_data, context.node_states, context.execution_id, idx,
                )
                query = str(expression_engine.resolve(query_template, expr_ctx))
                params = expression_engine.resolve_json_template(params_template, expr_ctx)
                if not isinstance(params, (list, dict)):
                    params = parse_json_params(params)

                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    is_select = cur.description is not None

                    if is_select:
                        rows = await _fetch_rows(cur)
                        results.append(ND(json={"rows": rows, "rowCount": len(rows)}))
                    else:
                        results.append(ND(json={"success": True, "rowCount": cur.rowcount}))

        return self.output(results)

    async def _exec_transaction(
        self, conn_string, node_definition, items, input_data, context, expression_engine, ExpressionEngine,
    ):
        from ...engine.types import NodeData as ND

        stmts_template = self.get_parameter(node_definition, "statements", "[]")

        expr_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        stmts = expression_engine.resolve_json_template(stmts_template, expr_ctx)
        if not isinstance(stmts, list) or len(stmts) == 0:
            return self.output([ND(json={"success": False, "error": "No statements provided"})])

        statement_results = []
        total_affected = 0

        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            try:
                for i, stmt in enumerate(stmts):
                    query = str(expression_engine.resolve(stmt.get("query", ""), expr_ctx))
                    params = parse_json_params(stmt.get("params", []))

                    async with conn.cursor() as cur:
                        await cur.execute(query, params)
                        is_select = cur.description is not None

                        if is_select:
                            rows = await _fetch_rows(cur)
                            statement_results.append({
                                "statementIndex": i,
                                "rows": rows,
                                "rowCount": len(rows),
                            })
                        else:
                            affected = cur.rowcount
                            total_affected += affected
                            statement_results.append({
                                "statementIndex": i,
                                "rowCount": affected,
                            })

                await conn.commit()
            except Exception as e:
                await conn.rollback()
                return self.output([ND(json={
                    "success": False,
                    "error": str(e),
                    "rolledBack": True,
                    "failedStatementIndex": i,
                    "completedStatements": statement_results,
                })])

        return self.output([ND(json={
            "success": True,
            "totalAffectedRows": total_affected,
            "statementCount": len(stmts),
            "results": statement_results,
        })])
