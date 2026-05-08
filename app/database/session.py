"""
Database sessions (async SQLAlchemy).

Architecture:
- app.database.engines     — connection pools (platform + per-tenant)
- app.database.async_ssl   — asyncpg TLS connect_args
- app.database.ssl_connect — psycopg2 TLS (migrations / DDL)
- app.database.tenant_context — registry lookups (`tenant_database_name`)
- app.database.routing      — map Request → platform vs tenant DB

Authentication resolves users from the platform DB (see get_platform_db_session).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import AsyncGenerator, Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import Request

from app.core.config import settings
from app.database import engines as db_engines
from app.database.base import Base  # noqa: F401 — re-export for convenience
from app.database.routing import resolve_database_route
from app.database.tenant_context import (
    invalidate_hospital_tenant_cache,
    resolve_tenant_database_name_for_hospital,
)
from app.database.ssl_connect import psycopg2_engine_connect_args
from app.services.tenant_database_provisioning import sync_url_for_tenant_database

logger = logging.getLogger(__name__)

_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_tenant_session_factories: Dict[str, async_sessionmaker[AsyncSession]] = {}
_tenant_session_lock = threading.Lock()

_schema_drift_applied: set[str] = set()
_schema_drift_lock = threading.Lock()
_base_schema_applied: set[str] = set()
_base_schema_lock = threading.Lock()


def _ensure_base_schema_for_sync_dsn(sync_dsn: str) -> None:
    """
    Ensure complete SQLAlchemy model schema exists in target database.

    This runs once per process per DSN and is idempotent. It guarantees every tenant DB
    has all tables used by hospital modules (admin, lab, pharmacy, etc.).
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        logger.info("Ensuring Base.metadata schema exists for %s", dsn)
        Base.metadata.create_all(bind=eng)
    finally:
        eng.dispose()


async def _ensure_schema_drift_for_sync_dsn(sync_dsn: str) -> None:
    """Run base schema self-heal + drift patches once per process per database URL."""
    dsn = (sync_dsn or "").strip()
    if not dsn:
        return
    with _base_schema_lock:
        needs_base_schema = dsn not in _base_schema_applied
    if needs_base_schema:
        await asyncio.to_thread(_ensure_base_schema_for_sync_dsn, dsn)
        with _base_schema_lock:
            _base_schema_applied.add(dsn)
    with _schema_drift_lock:
        if dsn in _schema_drift_applied:
            return
    from app.database.schema_patches import ensure_core_schema_drift_fixes_for_database

    await asyncio.to_thread(ensure_core_schema_drift_fixes_for_database, dsn)
    with _schema_drift_lock:
        _schema_drift_applied.add(dsn)


def get_async_engine() -> AsyncEngine:
    """Platform (registry) database async engine — shared pool."""
    return db_engines.get_platform_engine()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for the platform database."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


def AsyncSessionLocal() -> AsyncSession:
    return get_session_factory()()


def get_tenant_session_factory(db_name: str) -> async_sessionmaker[AsyncSession]:
    """Async session factory for a hospital-specific database."""
    key = str(db_name).strip()
    if not key:
        raise ValueError("tenant database name is required")
    with _tenant_session_lock:
        if key not in _tenant_session_factories:
            eng = db_engines.get_or_create_tenant_engine(key)
            _tenant_session_factories[key] = async_sessionmaker(
                bind=eng,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )
        return _tenant_session_factories[key]


async def get_platform_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Platform DB session (users, hospitals registry, subscriptions)."""
    await _ensure_schema_drift_for_sync_dsn(settings.DATABASE_URL_SYNC)
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Primary FastAPI dependency: platform or tenant DB based on routing rules.
    """
    await _ensure_schema_drift_for_sync_dsn(settings.DATABASE_URL_SYNC)

    route = await resolve_database_route(request)

    if route.use_platform or not route.tenant_database_name:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        return

    tn = route.tenant_database_name
    await _ensure_schema_drift_for_sync_dsn(sync_url_for_tenant_database(tn))
    fac = get_tenant_session_factory(tn)
    async with fac() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


get_db = get_db_session


async def init_database():
    """Verify database connectivity."""
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("Database connection established successfully")
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        raise


async def close_database():
    """Close platform and tenant database connection pools."""
    global _async_session_factory, _tenant_session_factories
    await db_engines.dispose_all_async_engines()
    _async_session_factory = None
    _tenant_session_factories.clear()
    logger.info("Database connections closed")
