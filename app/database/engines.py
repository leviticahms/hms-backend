"""
Async SQLAlchemy engines: single platform pool + one pool per tenant database name.

Routing (which engine to use per request) lives in app.database.routing.
Sessions are built from these engines in app.database.session.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.database.async_ssl import asyncpg_connect_args_for_url
from app.services.tenant_database_provisioning import async_url_for_tenant_database

_platform_engine: Optional[AsyncEngine] = None
_tenant_engines: Dict[str, AsyncEngine] = {}
_engine_lock = threading.Lock()


def _async_engine_common_kwargs() -> dict:
    return {
        "echo": settings.DEBUG,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "future": True,
    }


def get_platform_engine() -> AsyncEngine:
    """Registry database (hospitals, users, subscriptions): shared pool."""
    global _platform_engine
    if _platform_engine is None:
        url = settings.DATABASE_URL
        ca = asyncpg_connect_args_for_url(url)
        _platform_engine = create_async_engine(
            url,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            connect_args=ca if ca else {},
            **_async_engine_common_kwargs(),
        )
    return _platform_engine


def get_or_create_tenant_engine(db_name: str) -> AsyncEngine:
    """Dedicated PostgreSQL database for one hospital (same cluster as platform)."""
    key = str(db_name).strip()
    if not key:
        raise ValueError("tenant database name is required")
    with _engine_lock:
        if key not in _tenant_engines:
            url = async_url_for_tenant_database(key)
            ca = asyncpg_connect_args_for_url(url)
            _tenant_engines[key] = create_async_engine(
                url,
                pool_size=settings.TENANT_DB_POOL_SIZE,
                max_overflow=settings.TENANT_DB_MAX_OVERFLOW,
                connect_args=ca if ca else {},
                **_async_engine_common_kwargs(),
            )
        return _tenant_engines[key]


async def dispose_all_async_engines() -> None:
    """Dispose platform + all tenant pools (shutdown / tests)."""
    global _platform_engine
    if _platform_engine is not None:
        await _platform_engine.dispose()
        _platform_engine = None
    for eng in list(_tenant_engines.values()):
        await eng.dispose()
    _tenant_engines.clear()
