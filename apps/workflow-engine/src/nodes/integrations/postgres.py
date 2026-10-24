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


from ...engine import shapes as sh

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
                required=False,
                description="PostgreSQL connection string. Supports expressions. Not needed when Connection is set.",
                placeholder="postgresql://user:pass@host:5432/dbname",
            ),
            NodeProperty(
                display_name="Connection",
                name="connection",
                type="string",
                default="",
                required=False,
                description="Key of a project database connection. Replaces the connection string; queries are read-only "
                            "(SELECT/WITH, 10 s timeout, row limit) unless the connection is marked writable.",
                placeholder="shopdemo",
            ),
            NodeProperty(
                display_name="Row Limit",
                name="limit",
                type="number",
                default=1000,
                description="Most rows a read-only connection query returns (at most 5000).",
                display_options={"show": {"operation": ["query"]}},
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

    # Postgres: observed -> default contract (UNKNOWN unless the instance has an approved contract).

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
        connection_key = self.get_parameter(node_definition, "connection", "")

        items = input_data if input_data else [ND(json={})]

        first_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        guard = context.extras.get("broker_egress_guard")
        if connection_key:
            return await self._exec_connection(
                str(expression_engine.resolve(connection_key, first_ctx)).strip(), operation, node_definition, items,
                input_data, context, expression_engine, ExpressionEngine, guard,
            )
        conn_string = str(expression_engine.resolve(conn_template, first_ctx))
        if guard is not None:
            guard.check_postgres_connection(conn_string)

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

    async def _exec_connection(
        self, key, operation, node_definition, items, input_data, context, expression_engine, ExpressionEngine, guard,
    ):
        """Run against a project connection: the URL comes from the project's secret, never from the parameters.
        A read-only connection (the default) only runs one SELECT/WITH per item, in a READ ONLY transaction."""
        from ...engine.types import NodeData as ND
        from ...db.session import async_session_factory
        from ...services import external_db as xdb
        from ...services.connection_service import lookup

        if not context.project_id:
            raise ValueError("a project connection needs a project run")
        async with async_session_factory() as session:
            row = await lookup(session, context.project_id, key)
        if row is None:
            raise ValueError(f"connection '{key}' does not exist in this project")
        url = (context.variables or {}).get(row.secret_key)
        if not url:
            raise ValueError(f"the secret {row.secret_key} of connection '{key}' is not defined in this environment")
        if guard is not None:
            guard.check_postgres_connection(url)
        if not row.readonly:
            if operation == "transaction":
                return await self._exec_transaction(url, node_definition, items, input_data, context, expression_engine, ExpressionEngine)
            return await self._run_queries(url, node_definition, items, input_data, context, expression_engine, ExpressionEngine)
        if operation == "transaction":
            raise ValueError(f"connection '{key}' is read-only; transactions are not allowed")
        literal = bool(context.extras.get("literal_params"))
        query_template = self.get_parameter(node_definition, "query", "")
        params_template = self.get_parameter(node_definition, "queryParameters", "[]")
        limit = self.get_parameter(node_definition, "limit", 1000)
        results = []
        for idx, _item in enumerate(items):
            if literal:
                query, params = query_template, params_template
            else:
                expr_ctx = ExpressionEngine.create_context(input_data, context.node_states, context.execution_id, idx)
                query = str(expression_engine.resolve(query_template, expr_ctx))
                params = expression_engine.resolve_json_template(params_template, expr_ctx)
            if not isinstance(params, (list, dict)):
                params = parse_json_params(params)
            try:
                res = await xdb.run_readonly(url, query, params, limit, max_limit=xdb.SDK_MAX_LIMIT)
            except xdb.ExternalDbError as e:
                raise ValueError(f"{e.code}: {e.message}") from None
            results.append(ND(json={"rows": res["rows"], "rowCount": len(res["rows"]), "truncated": res["truncated"]}))
        return self.output(results)

    async def _run_queries(self, conn_string, node_definition, items, input_data, context, expression_engine, ExpressionEngine):
        from ...engine.types import NodeData as ND

        query_template = self.get_parameter(node_definition, "query", "")
        params_template = self.get_parameter(node_definition, "queryParameters", "[]")
        results = []
        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            for idx, item in enumerate(items):
                if context.extras.get("literal_params"):   # code via the broker: data, never expressions
                    query, params = query_template, params_template
                else:
                    expr_ctx = ExpressionEngine.create_context(input_data, context.node_states, context.execution_id, idx)
                    query = str(expression_engine.resolve(query_template, expr_ctx))
                    params = expression_engine.resolve_json_template(params_template, expr_ctx)
                if not isinstance(params, (list, dict)):
                    params = parse_json_params(params)
                async with conn.cursor() as cur:
                    await cur.execute(query, params)
                    if cur.description is not None:
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
        results: list[ND] = []

        # one transaction per item, like `query` runs once per item: each item's statements commit or roll back together
        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            for idx in range(max(1, len(items))):
                expr_ctx = ExpressionEngine.create_context(
                    input_data, context.node_states, context.execution_id, idx,
                )
                stmts = expression_engine.resolve_json_template(stmts_template, expr_ctx)
                if not isinstance(stmts, list) or len(stmts) == 0:
                    results.append(ND(json={"success": False, "error": "No statements provided"}))
                    continue

                statement_results = []
                total_affected = 0
                i = 0
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
                    results.append(ND(json={
                        "success": False,
                        "error": str(e),
                        "rolledBack": True,
                        "failedStatementIndex": i,
                        "completedStatements": statement_results,
                    }))
                    continue

                results.append(ND(json={
                    "success": True,
                    "totalAffectedRows": total_affected,
                    "statementCount": len(stmts),
                    "results": statement_results,
                }))

        return self.output(results)
