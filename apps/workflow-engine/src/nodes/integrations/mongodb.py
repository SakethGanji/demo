"""MongoDB node - executes operations against a MongoDB database."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

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


DEFAULT_CONNECTION_STRING = "mongodb://admin:admin@localhost:27017"
DEFAULT_DATABASE = "testdb"


def _serialize_value(val: Any) -> Any:
    """Serialize MongoDB-specific types, falling back to common serialization."""
    if isinstance(val, ObjectId):
        return str(val)
    # Lists/dicts need to recurse through this function for ObjectId handling
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return _base_serialize(val)


class MongoDBNode(BaseNode):
    """MongoDB node - executes operations against a MongoDB database."""

    node_description = NodeTypeDescription(
        name="MongoDB",
        display_name="MongoDB",
        description="Execute operations against a MongoDB database",
        icon="fa:leaf",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "documents": {"type": "array", "description": "Query result documents"},
                        "documentCount": {"type": "number", "description": "Number of documents returned or affected"},
                    },
                },
            )
        ],
        properties=[
            # --- Connection ---
            NodeProperty(
                display_name="Connection String",
                name="connectionString",
                type="string",
                default=DEFAULT_CONNECTION_STRING,
                required=True,
                description="MongoDB connection string. Supports expressions.",
                placeholder="mongodb://user:pass@host:27017",
            ),
            NodeProperty(
                display_name="Database",
                name="database",
                type="string",
                default=DEFAULT_DATABASE,
                required=True,
                description="Database name. Supports expressions.",
                placeholder="mydb",
            ),
            NodeProperty(
                display_name="Collection",
                name="collection",
                type="string",
                default="",
                required=True,
                description="Collection name. Supports expressions.",
                placeholder="users",
            ),
            # --- Operation ---
            NodeProperty(
                display_name="Operation",
                name="operation",
                type="options",
                default="find",
                options=[
                    NodePropertyOption(
                        name="Find",
                        value="find",
                        description="Query documents with filter, projection, sort, limit, skip",
                    ),
                    NodePropertyOption(
                        name="Find One",
                        value="findOne",
                        description="Return a single document matching the filter",
                    ),
                    NodePropertyOption(
                        name="Insert One",
                        value="insertOne",
                        description="Insert a single document",
                    ),
                    NodePropertyOption(
                        name="Insert Many",
                        value="insertMany",
                        description="Insert multiple documents from input items or JSON array",
                    ),
                    NodePropertyOption(
                        name="Update One",
                        value="updateOne",
                        description="Update the first document matching the filter",
                    ),
                    NodePropertyOption(
                        name="Update Many",
                        value="updateMany",
                        description="Update all documents matching the filter",
                    ),
                    NodePropertyOption(
                        name="Replace One",
                        value="replaceOne",
                        description="Replace a single document matching the filter",
                    ),
                    NodePropertyOption(
                        name="Delete One",
                        value="deleteOne",
                        description="Delete the first document matching the filter",
                    ),
                    NodePropertyOption(
                        name="Delete Many",
                        value="deleteMany",
                        description="Delete all documents matching the filter",
                    ),
                    NodePropertyOption(
                        name="Aggregate",
                        value="aggregate",
                        description="Run an aggregation pipeline",
                    ),
                    NodePropertyOption(
                        name="Count",
                        value="count",
                        description="Count documents matching a filter",
                    ),
                    NodePropertyOption(
                        name="Distinct",
                        value="distinct",
                        description="Get distinct values for a field",
                    ),
                ],
            ),
            # --- Filter (for find, update, delete, count, findOne, replaceOne) ---
            NodeProperty(
                display_name="Filter",
                name="filter",
                type="json",
                default="{}",
                description='MongoDB query filter. e.g. {"age": {"$gt": 25}}. Supports expressions.',
                type_options={"language": "json", "rows": 4},
                display_options={"show": {"operation": [
                    "find", "findOne", "updateOne", "updateMany",
                    "deleteOne", "deleteMany", "count", "replaceOne",
                ]}},
            ),
            # --- Projection (for find, findOne) ---
            NodeProperty(
                display_name="Projection",
                name="projection",
                type="json",
                default="{}",
                description='Fields to include/exclude. e.g. {"name": 1, "_id": 0}. Supports expressions.',
                type_options={"language": "json", "rows": 3},
                display_options={"show": {"operation": ["find", "findOne"]}},
            ),
            # --- Sort (for find) ---
            NodeProperty(
                display_name="Sort",
                name="sort",
                type="json",
                default="{}",
                description='Sort order. e.g. {"created_at": -1} for descending. Supports expressions.',
                type_options={"language": "json", "rows": 2},
                display_options={"show": {"operation": ["find"]}},
            ),
            # --- Pagination (for find) ---
            NodeProperty(
                display_name="Limit",
                name="limit",
                type="number",
                default=0,
                description="Max documents to return. 0 = no limit.",
                display_options={"show": {"operation": ["find"]}},
            ),
            NodeProperty(
                display_name="Skip",
                name="skip",
                type="number",
                default=0,
                description="Number of documents to skip.",
                display_options={"show": {"operation": ["find"]}},
            ),
            # --- Document (for insertOne, replaceOne) ---
            NodeProperty(
                display_name="Document",
                name="document",
                type="json",
                default="{}",
                description="JSON document to insert or replace with. Supports expressions.",
                type_options={"language": "json", "rows": 6},
                display_options={"show": {"operation": ["insertOne", "replaceOne"]}},
            ),
            # --- Update (for updateOne, updateMany) ---
            NodeProperty(
                display_name="Update",
                name="update",
                type="json",
                default="{}",
                description='MongoDB update operations. e.g. {"$set": {"status": "active"}}. Supports expressions.',
                type_options={"language": "json", "rows": 5},
                display_options={"show": {"operation": ["updateOne", "updateMany"]}},
            ),
            NodeProperty(
                display_name="Upsert",
                name="upsert",
                type="boolean",
                default=False,
                description="If true, creates a new document when no document matches the filter.",
                display_options={"show": {"operation": ["updateOne", "updateMany", "replaceOne"]}},
            ),
            # --- Insert Many fields ---
            NodeProperty(
                display_name="Data Source",
                name="dataSource",
                type="options",
                default="inputItems",
                options=[
                    NodePropertyOption(
                        name="Input Items",
                        value="inputItems",
                        description="Each input item becomes a document (uses $json fields)",
                    ),
                    NodePropertyOption(
                        name="JSON Array",
                        value="jsonArray",
                        description="Provide an explicit JSON array of documents",
                    ),
                ],
                display_options={"show": {"operation": ["insertMany"]}},
            ),
            NodeProperty(
                display_name="Documents JSON",
                name="documentsJson",
                type="json",
                default="[]",
                description="JSON array of documents to insert. Supports expressions.",
                type_options={"language": "json", "rows": 6},
                display_options={"show": {"operation": ["insertMany"], "dataSource": ["jsonArray"]}},
            ),
            # --- Aggregate pipeline ---
            NodeProperty(
                display_name="Pipeline",
                name="pipeline",
                type="json",
                default="[]",
                description='Aggregation pipeline stages. e.g. [{"$match": {...}}, {"$group": {...}}]. Supports expressions.',
                type_options={"language": "json", "rows": 8},
                display_options={"show": {"operation": ["aggregate"]}},
            ),
            # --- Distinct field ---
            NodeProperty(
                display_name="Field",
                name="distinctField",
                type="string",
                default="",
                description="Field name to get distinct values for.",
                placeholder="city",
                display_options={"show": {"operation": ["distinct"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "MongoDB"

    @property
    def description(self) -> str:
        return "Execute operations against a MongoDB database"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        operation = self.get_parameter(node_definition, "operation", "find")
        items = input_data if input_data else [ND(json={})]

        # Resolve connection params from first item
        first_ctx = ExpressionEngine.create_context(
            input_data, context.node_states, context.execution_id, 0,
        )
        conn_str = str(expression_engine.resolve(
            self.get_parameter(node_definition, "connectionString", DEFAULT_CONNECTION_STRING), first_ctx,
        ))
        db_name = str(expression_engine.resolve(
            self.get_parameter(node_definition, "database", DEFAULT_DATABASE), first_ctx,
        ))
        coll_name = str(expression_engine.resolve(
            self.get_parameter(node_definition, "collection", ""), first_ctx,
        ))

        if not coll_name:
            return self.output([ND(json={"success": False, "error": "Collection name is required"})])

        client = AsyncIOMotorClient(conn_str)
        try:
            db = client[db_name]
            coll = db[coll_name]

            handler = {
                "find": self._op_find,
                "findOne": self._op_find_one,
                "insertOne": self._op_insert_one,
                "insertMany": self._op_insert_many,
                "updateOne": self._op_update_one,
                "updateMany": self._op_update_many,
                "replaceOne": self._op_replace_one,
                "deleteOne": self._op_delete_one,
                "deleteMany": self._op_delete_many,
                "aggregate": self._op_aggregate,
                "count": self._op_count,
                "distinct": self._op_distinct,
            }.get(operation)

            if not handler:
                return self.output([ND(json={"success": False, "error": f"Unknown operation: {operation}"})])

            return await handler(
                coll, node_definition, items, input_data, context, expression_engine, ExpressionEngine,
            )
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Find
    # ------------------------------------------------------------------
    async def _op_find(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)
            proj = ee.resolve_json_template(self.get_parameter(node_def,"projection", "{}"), ctx)
            sort_raw = ee.resolve_json_template(self.get_parameter(node_def,"sort", "{}"), ctx)
            limit = int(self.get_parameter(node_def, "limit", 0) or 0)
            skip = int(self.get_parameter(node_def, "skip", 0) or 0)

            cursor = coll.find(filt, proj or None)
            if sort_raw:
                cursor = cursor.sort(list(sort_raw.items()))
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)

            docs = [_serialize_value(doc) async for doc in cursor]
            results.append(ND(json={"documents": docs, "documentCount": len(docs)}))

        return self.output(results)

    # ------------------------------------------------------------------
    # Find One
    # ------------------------------------------------------------------
    async def _op_find_one(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)
            proj = ee.resolve_json_template(self.get_parameter(node_def,"projection", "{}"), ctx)

            doc = await coll.find_one(filt, proj or None)
            if doc:
                results.append(ND(json={"document": _serialize_value(doc), "found": True}))
            else:
                results.append(ND(json={"document": None, "found": False}))

        return self.output(results)

    # ------------------------------------------------------------------
    # Insert One
    # ------------------------------------------------------------------
    async def _op_insert_one(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            doc = ee.resolve_json_template(self.get_parameter(node_def,"document", "{}"), ctx)

            result = await coll.insert_one(doc)
            results.append(ND(json={
                "success": True,
                "insertedId": str(result.inserted_id),
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Insert Many
    # ------------------------------------------------------------------
    async def _op_insert_many(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND

        data_source = self.get_parameter(node_def, "dataSource", "inputItems")

        if data_source == "jsonArray":
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, 0)
            docs = ee.resolve_json_template(self.get_parameter(node_def, "documentsJson", "[]"), ctx)
            if not isinstance(docs, list):
                docs = []
        else:
            docs = [item.json for item in items]

        if not docs:
            return self.output([ND(json={"success": True, "insertedCount": 0})])

        result = await coll.insert_many(docs)
        return self.output([ND(json={
            "success": True,
            "insertedCount": len(result.inserted_ids),
            "insertedIds": [str(id) for id in result.inserted_ids],
        })])

    # ------------------------------------------------------------------
    # Update One
    # ------------------------------------------------------------------
    async def _op_update_one(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)
            update = ee.resolve_json_template(self.get_parameter(node_def,"update", "{}"), ctx)
            upsert = bool(self.get_parameter(node_def, "upsert", False))

            result = await coll.update_one(filt, update, upsert=upsert)
            results.append(ND(json={
                "success": True,
                "matchedCount": result.matched_count,
                "modifiedCount": result.modified_count,
                "upsertedId": str(result.upserted_id) if result.upserted_id else None,
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Update Many
    # ------------------------------------------------------------------
    async def _op_update_many(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)
            update = ee.resolve_json_template(self.get_parameter(node_def,"update", "{}"), ctx)
            upsert = bool(self.get_parameter(node_def, "upsert", False))

            result = await coll.update_many(filt, update, upsert=upsert)
            results.append(ND(json={
                "success": True,
                "matchedCount": result.matched_count,
                "modifiedCount": result.modified_count,
                "upsertedId": str(result.upserted_id) if result.upserted_id else None,
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Replace One
    # ------------------------------------------------------------------
    async def _op_replace_one(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)
            doc = ee.resolve_json_template(self.get_parameter(node_def,"document", "{}"), ctx)
            upsert = bool(self.get_parameter(node_def, "upsert", False))

            result = await coll.replace_one(filt, doc, upsert=upsert)
            results.append(ND(json={
                "success": True,
                "matchedCount": result.matched_count,
                "modifiedCount": result.modified_count,
                "upsertedId": str(result.upserted_id) if result.upserted_id else None,
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Delete One
    # ------------------------------------------------------------------
    async def _op_delete_one(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)

            result = await coll.delete_one(filt)
            results.append(ND(json={
                "success": True,
                "deletedCount": result.deleted_count,
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Delete Many
    # ------------------------------------------------------------------
    async def _op_delete_many(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)

            result = await coll.delete_many(filt)
            results.append(ND(json={
                "success": True,
                "deletedCount": result.deleted_count,
            }))

        return self.output(results)

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    async def _op_aggregate(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND

        ctx = EE.create_context(input_data, context.node_states, context.execution_id, 0)
        pipeline = ee.resolve_json_template(self.get_parameter(node_def, "pipeline", "[]"), ctx)
        if not isinstance(pipeline, list):
            pipeline = []

        docs = [_serialize_value(doc) async for doc in coll.aggregate(pipeline)]
        return self.output([ND(json={"documents": docs, "documentCount": len(docs)})])

    # ------------------------------------------------------------------
    # Count
    # ------------------------------------------------------------------
    async def _op_count(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND
        results: list[ND] = []

        for idx, item in enumerate(items):
            ctx = EE.create_context(input_data, context.node_states, context.execution_id, idx)
            filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)

            count = await coll.count_documents(filt)
            results.append(ND(json={"count": count}))

        return self.output(results)

    # ------------------------------------------------------------------
    # Distinct
    # ------------------------------------------------------------------
    async def _op_distinct(self, coll, node_def, items, input_data, context, ee, EE):
        from ...engine.types import NodeData as ND

        ctx = EE.create_context(input_data, context.node_states, context.execution_id, 0)
        field = str(ee.resolve(self.get_parameter(node_def, "distinctField", ""), ctx))
        filt = ee.resolve_json_template(self.get_parameter(node_def,"filter", "{}"), ctx)

        if not field:
            return self.output([ND(json={"success": False, "error": "Field name is required"})])

        values = await coll.distinct(field, filt or None)
        return self.output([ND(json={
            "field": field,
            "values": [_serialize_value(v) for v in values],
            "count": len(values),
        })])
