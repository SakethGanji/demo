"""Structured logging with execution context correlation."""

import contextvars
import logging

# Context variable for execution ID — automatically included in all log messages
execution_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "execution_id", default="-"
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
