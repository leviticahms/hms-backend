"""
Optional relaxed TLS verification for PostgreSQL (psycopg2) and flags for SMTP.

Use only behind a firewall or for development. Never enable DATABASE_SSL_INSECURE
or SMTP_TLS_INSECURE on public production unless you accept MITM risk.
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)
_psycopg2_render_ssl_logged = False


def _insecure_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def psycopg2_engine_connect_args() -> dict[str, Any]:
    """
    Sync psycopg2 TLS. DATABASE_SSL_INSECURE forces unverified TLS.

    On Render, default matches asyncpg: TLS without verifying the server cert unless
    DATABASE_SSL_VERIFY=true.
    """
    if settings.DATABASE_SSL_INSECURE:
        logger.warning(
            "DATABASE_SSL_INSECURE=true: PostgreSQL certificate verification is disabled"
        )
        return {"sslcontext": _insecure_ssl_context()}

    render = os.getenv("RENDER", "").lower() in {"true", "1"}
    if not render:
        return {}

    if settings.DATABASE_SSL_VERIFY:
        return {}
    global _psycopg2_render_ssl_logged
    if not _psycopg2_render_ssl_logged:
        _psycopg2_render_ssl_logged = True
        logger.info(
            "Render: psycopg2 uses TLS without server cert verification by default "
            "(set DATABASE_SSL_VERIFY=true for strict verification)"
        )
    return {"sslcontext": _insecure_ssl_context()}
