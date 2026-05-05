"""
Central application logging: formats, root handler, and per-module levels.

Use ``get_logger(__name__)`` in each module so log lines include the logger
name (package path), which makes filtering and tracing straightforward.

Environment-driven details live on ``Settings`` (``LOG_LEVEL``, ``LOG_FORMAT``,
``LOG_MODULE_LEVELS``, third-party logger levels).
"""
from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.config import Settings


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger (same as logging.getLogger, named for clarity)."""
    return logging.getLogger(name)


def _parse_level(name: str, default: int = logging.INFO) -> int:
    level = logging.getLevelName(name.upper())
    if isinstance(level, int):
        return level
    return default


def _parse_module_levels(raw: str) -> dict[str, int]:
    """Parse ``LOG_MODULE_LEVELS`` like ``app.middleware=DEBUG,app.services=INFO``."""
    out: dict[str, int] = {}
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        prefix, level_name = item.split("=", 1)
        prefix = prefix.strip()
        level_name = level_name.strip()
        if not prefix:
            continue
        out[prefix] = _parse_level(level_name)
    return out


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line (works well with log aggregators)."""

    def __init__(self, datefmt: str | None = None):
        super().__init__(datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Optional structured fields from ``logger.info(..., extra={...})``
        for key in (
            "url",
            "method",
            "status_code",
            "errors",
            "request_id",
            "correlation_id",
            "client_ip",
            "duration",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings, *, force: bool = True) -> None:
    """
    Attach a single stream handler on the root logger and apply levels.

    ``force=True`` replaces existing root handlers so this wins over prior
    basicConfig (e.g. from library defaults).
    """
    root = logging.getLogger()
    if force:
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()

    level = _parse_level(settings.LOG_LEVEL)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    if settings.LOG_FORMAT == "json":
        fmt = JsonLogFormatter(datefmt=settings.LOG_DATE_FORMAT)
    else:
        fmt = logging.Formatter(
            fmt=settings.LOG_TEXT_FORMAT,
            datefmt=settings.LOG_DATE_FORMAT,
        )
    handler.setFormatter(fmt)
    root.addHandler(handler)

    for prefix, mod_level in _parse_module_levels(settings.LOG_MODULE_LEVELS).items():
        logging.getLogger(prefix).setLevel(mod_level)

    logging.getLogger("uvicorn").setLevel(_parse_level(settings.LOG_UVICORN_LEVEL))
    logging.getLogger("uvicorn.error").setLevel(_parse_level(settings.LOG_UVICORN_LEVEL))
    if settings.LOG_UVICORN_ACCESS:
        logging.getLogger("uvicorn.access").setLevel(
            _parse_level(settings.LOG_UVICORN_ACCESS_LEVEL)
        )
    else:
        logging.getLogger("uvicorn.access").setLevel(logging.CRITICAL)

    logging.getLogger("sqlalchemy.engine").setLevel(
        _parse_level(settings.LOG_SQLALCHEMY_LEVEL)
    )
    logging.getLogger("sqlalchemy.pool").setLevel(
        _parse_level(settings.LOG_SQLALCHEMY_POOL_LEVEL)
    )
    logging.getLogger("asyncpg").setLevel(_parse_level(settings.LOG_ASYNCPG_LEVEL))
