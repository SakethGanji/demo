"""Structured logging with execution context correlation."""

import contextvars
import logging

# Context variable for execution ID — automatically included in all log messages
execution_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "execution_id", default="-"
)

# Per-execution team variables ($vars), set by WorkflowRunner.run().
# Read by ExpressionEngine.create_context so nodes that build their own
# expression context (HTTP, Set, Filter, Switch, etc.) inherit $vars
# without each node having to thread it explicitly.
execution_variables_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "execution_variables", default={}
)

# Per-execution decrypted set of `type=secret` VALUES, set by WorkflowRunner.run().
# Nodes that emit user-visible execution metadata (e.g. HttpRequest.requestUrl
# after expression resolution) read this and scrub these values out before
# the metadata is persisted to node_outputs or streamed via SSE — otherwise
# `{{ $vars.MY_TOKEN }}` interpolated into a URL query param leaks to the UI.
execution_secrets_var: contextvars.ContextVar[set[str]] = contextvars.ContextVar(
    "execution_secrets", default=set()
)


class ExecutionContextFilter(logging.Filter):
    """Injects execution_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.execution_id = execution_id_var.get("-")  # type: ignore[attr-defined]
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging with execution_id correlation."""
    fmt = "%(asctime)s %(levelname)-5s [exec:%(execution_id)s] %(name)s — %(message)s"

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    handler.addFilter(ExecutionContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
