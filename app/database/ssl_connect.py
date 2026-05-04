"""
Optional relaxed TLS verification for PostgreSQL (psycopg2) and flags for SMTP.

Use only behind a firewall or for development. Never enable DATABASE_SSL_INSECURE
or SMTP_TLS_INSECURE on public production unless you accept MITM risk.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def _insecure_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def psycopg2_engine_connect_args() -> dict[str, Any]:
    """SQLAlchemy sync engine connect_args for self-signed / intercepted Postgres TLS."""
    if not settings.DATABASE_SSL_INSECURE:
        return {}
    logger.warning(
        "DATABASE_SSL_INSECURE=true: PostgreSQL certificate verification is disabled"
    )
    return {"sslcontext": _insecure_ssl_context()}
