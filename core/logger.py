"""Structured logging utility for the multi-agent system.

Provides:
- Python `logging` module with custom formatter
- Dual output: console (stderr) + rotating log file (logs/agent.log)
- Timestamps, log levels, and component names
- Request-level tracing via contextvars (request_id flows through all log lines)
- Timing helper for measuring step durations
- Real-time file flushing (no buffering delay)

Log file: logs/agent.log
- Rotates at 10 MB, keeps last 5 backup files (agent.log.1 ... agent.log.5)
- Tail in real time:  tail -f logs/agent.log
- Configurable via LOG_DIR and LOG_LEVEL env vars in settings.py

Usage:
    from core.logger import get_logger, log_time, set_request_id

    logger = get_logger("Orchestrator")
    logger.info("Task classified", task=task, query=query[:80])

    with log_time(logger, "ES search"):
        results = es.search(...)
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import Any

# --- Request Tracing ---

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(rid: str | None = None) -> str:
    """Set (or generate) a request ID for the current async context."""
    rid = rid or uuid.uuid4().hex[:8]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


# --- Custom Formatter ---

class AgentFormatter(logging.Formatter):
    """Format: TIMESTAMP | LEVEL | [Component] req=XXXX | message | key=value ..."""

    LEVEL_SHORT = {
        "DEBUG": "DBG",
        "INFO": "INF",
        "WARNING": "WRN",
        "ERROR": "ERR",
        "CRITICAL": "CRT",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        ms = int(record.msecs)
        level = self.LEVEL_SHORT.get(record.levelname, record.levelname[:3])
        rid = _request_id.get()

        # Component name from logger name (e.g. "v2.Orchestrator" -> "Orchestrator")
        component = record.name.split(".")[-1]

        # Redact LLM brand/model tokens from the message body + every
        # structured extra. Centralized here so call sites don't need to
        # remember to scrub — covers prints from upstream SDK error
        # strings, model identifiers in log_time messages, and any
        # accidental f-string interpolation of model names.
        # Lazy import: core.logger is imported very early; core.redact
        # is a leaf module with no back-edges, so this is safe.
        from core.redact import redact_brands, redact_kv

        # Base message
        base = (
            f"{ts}.{ms:03d} | {level} | [{component}] req={rid} | "
            f"{redact_brands(record.getMessage())}"
        )

        # Append structured key=value pairs if present
        extras = getattr(record, "_extra_kv", None)
        if extras:
            kv_str = " | ".join(
                f"{k}={redact_kv(v)}" for k, v in extras.items()
            )
            base = f"{base} | {kv_str}"

        # Append exception info if present
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            base = f"{base}\n{redact_brands(record.exc_text)}"

        return base


# --- Flush-on-write File Handler ---

class FlushFileHandler(RotatingFileHandler):
    """RotatingFileHandler that flushes after every emit for real-time log tailing."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


# --- Structured Logger Wrapper ---

class StructuredLogger:
    """Thin wrapper around stdlib logger that supports key=value structured fields."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, kwargs: dict[str, Any], exc_info=None):
        if not self._logger.isEnabledFor(level):
            return
        # Honor stdlib logging conventions for `exc_info` before handing
        # the record to makeRecord, which expects either None or a
        # (type, value, tb) 3-tuple. Without this coercion, call sites
        # that pass `exc_info=True` (the idiomatic form used in ~38 places
        # across the codebase) end up with record.exc_info = True, and
        # the AgentFormatter later crashes in stdlib's formatException
        # with `TypeError: 'bool' object is not subscriptable`. That
        # shadows the ORIGINAL exception the caller was trying to log
        # (e.g. a Gemini 429), which is significantly worse than the
        # original error itself. See stdlib Logger._log for the canonical
        # coercion — we mirror it here.
        if exc_info:
            if isinstance(exc_info, BaseException):
                exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
            elif not isinstance(exc_info, tuple):
                exc_info = sys.exc_info()
        record = self._logger.makeRecord(
            self._logger.name,
            level,
            "(logger)",
            0,
            msg,
            (),
            exc_info,
        )
        record._extra_kv = kwargs  # type: ignore[attr-defined]
        self._logger.handle(record)

    def debug(self, msg: str, **kwargs: Any):
        self._log(logging.DEBUG, msg, kwargs)

    def info(self, msg: str, **kwargs: Any):
        self._log(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs: Any):
        self._log(logging.WARNING, msg, kwargs)

    def error(self, msg: str, exc_info=None, **kwargs: Any):
        self._log(logging.ERROR, msg, kwargs, exc_info=exc_info)

    def critical(self, msg: str, exc_info=None, **kwargs: Any):
        self._log(logging.CRITICAL, msg, kwargs, exc_info=exc_info)


# --- Handler Singletons ---

_console_handler: logging.Handler | None = None
_file_handler: logging.Handler | None = None
_log_level: int = logging.DEBUG


def _init_handlers() -> tuple[logging.Handler, logging.Handler]:
    """Initialize console + file handlers (called once on first get_logger)."""
    global _console_handler, _file_handler, _log_level

    if _console_handler is not None and _file_handler is not None:
        return _console_handler, _file_handler

    # Import settings lazily to avoid circular imports
    from core.settings import LOG_DIR, LOG_LEVEL

    _log_level = getattr(logging, LOG_LEVEL, logging.DEBUG)
    formatter = AgentFormatter()

    # Console handler (stderr)
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(formatter)
    _console_handler.setLevel(_log_level)

    # File handler — rotating, real-time flush
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "agent.log")

    _file_handler = FlushFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,              # keep agent.log.1 … agent.log.5
        encoding="utf-8",
    )
    _file_handler.setFormatter(formatter)
    _file_handler.setLevel(_log_level)

    return _console_handler, _file_handler


def get_logger(component: str, level: int | None = None) -> StructuredLogger:
    """Get a structured logger for a component.

    Args:
        component: Component name shown in brackets, e.g. "Orchestrator", "Gateway"
        level: Override minimum log level. Default uses LOG_LEVEL from settings.

    Returns:
        StructuredLogger instance with .info(), .debug(), .warning(), .error() methods
    """
    name = f"v2.{component}"
    logger = logging.getLogger(name)

    if not logger.handlers:
        console_h, file_h = _init_handlers()
        logger.addHandler(console_h)
        logger.addHandler(file_h)
        logger.setLevel(level if level is not None else _log_level)
        logger.propagate = False

    return StructuredLogger(logger)


# --- Timing Helper ---

@contextmanager
def log_time(logger: StructuredLogger, operation: str, **extra_kv):
    """Context manager that logs operation duration.

    Usage:
        with log_time(logger, "ES search", index="legislation"):
            results = es.search(...)
        # Logs: "ES search completed | duration_ms=142 | index=legislation"
    """
    start = time.perf_counter()
    logger.debug(f"{operation} started", **extra_kv)
    try:
        yield
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error(f"{operation} failed", duration_ms=f"{elapsed_ms:.0f}", error=str(e), **extra_kv)
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"{operation} completed", duration_ms=f"{elapsed_ms:.0f}", **extra_kv)
