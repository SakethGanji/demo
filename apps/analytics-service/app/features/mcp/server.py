"""The MCP tool surface, built over this service's own API.

27 tools, not one wrapper per endpoint: every tool schema is serialized into
context on every model call, so tool count is a real cost. Related operations
are grouped behind one tool with an ``action``/``target`` parameter.

These tools used to live in a separate process (``apps/analytics-mcp``) that was
an HTTP client of this service. There is exactly one copy of them, and it is
this one; the stdio adapter that Claude Desktop launches is now a transport
relay that holds no tool definitions.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .client import AnalyticsClient
from .identity import identity_middleware
from .strict_args import UnknownArgumentGuard
from .tools import artifacts, compute, context, curate, look, orient, pipeline
from .tools._common import Ctx

SERVER_NAME = "analytics"
SERVER_VERSION = "1.0.0"

INSTRUCTIONS = """\
A dataset platform. Datasets are versioned; each version has one or more sheets;
each sheet has typed columns. Most tools read; a few change state and say so in
their first word. Nothing here can delete anything.

UNDERSTAND — start here, and stop as soon as you can answer. These cost almost
nothing and return no data rows at all:
  search_datasets      turn a topic into a dataset_id. No other tool returns one.
  describe_dataset     every sheet and column with its type. Read this before
                       writing any filter or SQL, so you use real column names.
  get_data_dictionary  what columns mean: business names, units, semantic types,
                       sensitivity, allowed values, and the sheet's grain.
  get_dataset_health / check_quality   whether the data can be trusted.
  get_lineage / get_activity / list_relationships / list_saved_objects
                       where it came from, what happened to it, how sheets join,
                       and what other people already built on it.

ANALYSE — let the database compute; do not do arithmetic over rows yourself:
  query_rows           filter, sort, project and page through one sheet.
  run_sql              one read-only SELECT. Joins, aggregates, window functions.
                       The most capable tool here; reach for it when one sheet or
                       plain column names are not enough.
  aggregate / pivot    grouped summaries of a single sheet, no joins or
                       expressions.
  profile_column       distribution of one column.

ACT — these change state:
  write_documentation  record what columns mean, a sheet's grain and primary key.
  manage_quality_rules / run_quality_check   declare expectations and test them.
                       Health reports 'unknown' until a run exists; this is how
                       you fix that.
  manage_relationships confirm a join key. Only confirmed keys can drive a join.
  join_datasets        preview first — it is free and reveals row explosion.
  transform_data       preview is a dry run; run writes an artifact.
  publish_result       promote a result to a real dataset or version.
  manage_tags          name a whole version, e.g. production.

Never read a dataset in full. Push filtering, grouping and arithmetic into the
service: it is exact, fast, and bounded. Results too large for a response are
saved as artifacts — feed the returned filename to read_artifact with columns, a
filter and paging, rather than asking for everything at once.

Three behaviours worth knowing. A 'not found' can mean the resource exists but
belongs to a team you are not in — it is not proof of absence. A version with
more than one sheet will refuse to guess which one you meant, and will list the
candidates. And an aggregate's overall total spans every group the filter
matched, not just the rows shown, and is omitted for non-additive functions
(the response names which, and why).
"""


def build(app, *, api_prefix: str, log_level: str = "WARNING") -> tuple[MCPServer, AnalyticsClient]:
    """Assemble the MCP server over an in-process client of *app*."""
    client = AnalyticsClient(app, api_prefix=api_prefix)
    ctx = Ctx(client=client)

    server: MCPServer = MCPServer(
        name=SERVER_NAME,
        title="Analytics Platform",
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        log_level=log_level,  # type: ignore[arg-type]
        # Binds the acting user for the duration of each inbound message. Every
        # tool below runs as whoever sent that message, never as whoever opened
        # the connection.
        middleware=[identity_middleware],
    )

    orient.register(server, ctx)
    look.register(server, ctx)
    compute.register(server, ctx)
    artifacts.register(server, ctx)
    context.register(server, ctx)
    pipeline.register(server, ctx)
    curate.register(server, ctx)

    # Installed after registration, so the guard reads a complete tool registry
    # rather than a list it would have to be told about. Appending puts it
    # INSIDE identity_middleware in the chain (the list is outermost-first), so
    # the caller is already bound when a call is rejected and the identity
    # middleware's ordering and ContextVar lifetime are untouched.
    server.middleware.append(UnknownArgumentGuard(server))

    return server, client
