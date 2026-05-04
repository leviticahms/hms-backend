"""
Optional TLS hints for PostgreSQL (psycopg2) via libpq connection parameters.

psycopg2 passes connect_args through make_dsn() — only libpq keywords are allowed
(not sslcontext). See: https://www.postgresql.org/docs/current/libpq-connect.html
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)
_psycopg2_render_ssl_logged = False


def psycopg2_engine_connect_args() -> dict[str, Any]:
    """
    Extra SQLAlchemy create_engine(connect_args=...) for sync psycopg2.

    Uses sslmode=require (libpq): TLS is required. Without sslrootcert, many libpq
    builds do not perform full certificate chain verification (works with Render).

    DATABASE_SSL_INSECURE: same sslmode=require (legacy name; libpq has no sslcontext here).

    On Render, when DATABASE_SSL_VERIFY is false (default), require TLS this way.
    When DATABASE_SSL_VERIFY is true, rely on DATABASE_URL_SYNC only (often no extra args).
    """
    if settings.DATABASE_SSL_INSECURE:
        logger.warning(
            "DATABASE_SSL_INSECURE=true: using sslmode=require for sync PostgreSQL "
            "(set sslrootcert in DATABASE_URL_SYNC if you need custom CA trust)"
        )
        return {"sslmode": "require"}

    render = os.getenv("RENDER", "").lower() in {"true", "1"}
    if not render:
        return {}

    if settings.DATABASE_SSL_VERIFY:
        return {}

    global _psycopg2_render_ssl_logged
    if not _psycopg2_render_ssl_logged:
        _psycopg2_render_ssl_logged = True
        logger.info(
            "Render: psycopg2 connect_args use sslmode=require "
            "(set DATABASE_SSL_VERIFY=true and optional sslrootcert in URL for strict TLS verify)"
        )
    return {"sslmode": "require"}
