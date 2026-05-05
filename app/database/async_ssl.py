"""
asyncpg TLS options for SQLAlchemy async engines.

asyncpg does not accept libpq query params (e.g. sslmode=); SSL is passed via connect_args.
Sync/psycopg2 helpers live in app.database.ssl_connect.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)
_render_asyncpg_ssl_logged = False


def asyncpg_unverified_ssl_context():
    """TLS to Postgres without verifying server cert (Render / self-signed chains)."""
    import ssl as ssl_module

    ctx = ssl_module.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl_module.CERT_NONE
    return ctx


def asyncpg_connect_args_for_url(url: str) -> dict:
    """
    Extra SQLAlchemy create_async_engine(connect_args=...) for asyncpg.

    - DATABASE_SSL_INSECURE=true: always unverified TLS.
    - On Render (non-local): default encrypted session without remote cert verify unless
      DATABASE_SSL_VERIFY=true (then ssl=True for strict verification).
    """
    import ssl as ssl_module

    raw = (url or "").strip()
    if settings.DATABASE_SSL_INSECURE and raw:
        ctx = ssl_module.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl_module.CERT_NONE
        logger.warning(
            "DATABASE_SSL_INSECURE=true: asyncpg TLS certificate verification is disabled"
        )
        return {"ssl": ctx}
    if os.getenv("RENDER", "").lower() not in {"true", "1"}:
        return {}
    if not raw:
        return {}
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
    except Exception:
        return {}
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return {}
    if settings.DATABASE_SSL_VERIFY:
        return {"ssl": True}
    global _render_asyncpg_ssl_logged
    if not _render_asyncpg_ssl_logged:
        _render_asyncpg_ssl_logged = True
        logger.info(
            "Render: asyncpg uses TLS without server cert verification by default "
            "(set DATABASE_SSL_VERIFY=true for strict verification)"
        )
    return {"ssl": asyncpg_unverified_ssl_context()}
