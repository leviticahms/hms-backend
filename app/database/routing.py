"""
HTTP request → database binding (platform registry vs per-hospital tenant DB).

Layers:
- Platform DB: hospitals registry, users, auth-resolution tables.
- Tenant DB: optional dedicated PostgreSQL database per hospital (`tenant_database_name`).

See app.database.session for session lifecycle and app.database.engines for connection pools.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from starlette.requests import Request

from app.core.config import settings
from app.database.tenant_context import resolve_tenant_database_name_for_hospital

logger = logging.getLogger(__name__)

# Paths that always use the platform database (no tenant routing).
PLATFORM_ONLY_PREFIXES: tuple[str, ...] = (
    "/api/v1/super-admin",
    "/api/v1/analytics",
)

LAB_API_PREFIX = "/api/v1/lab/"

_tenant_lab_schema_cache: dict[str, tuple[bool, float]] = {}
_LAB_SCHEMA_CACHE_TTL_SEC = 60.0


def path_requires_platform_database(path: str) -> bool:
    return any(path.startswith(p) for p in PLATFORM_ONLY_PREFIXES)


@dataclass(frozen=True)
class DatabaseRoute:
    """Resolved target for opening an AsyncSession."""

    use_platform: bool
    tenant_database_name: Optional[str] = None


async def resolve_database_route(request: Request) -> DatabaseRoute:
    path = request.url.path or ""

    if not settings.TENANT_DB_ROUTE_QUERIES:
        return DatabaseRoute(use_platform=True)

    if path_requires_platform_database(path):
        return DatabaseRoute(use_platform=True)

    hospital_id = getattr(request.state, "hospital_id", None)
    if hospital_id is None:
        return DatabaseRoute(use_platform=True)

    db_name = await resolve_tenant_database_name_for_hospital(hospital_id)
    if not db_name:
        logger.warning(
            "Hospital %s has no tenant_database_name; using platform DB for this request",
            hospital_id,
        )
        return DatabaseRoute(use_platform=True)

    if path.startswith(LAB_API_PREFIX):
        try:
            has_lab_schema = await _tenant_has_core_lab_tables(db_name)
        except Exception as e:
            logger.warning(
                "Failed to check lab schema for tenant DB '%s': %s; using platform DB",
                db_name,
                e,
            )
            has_lab_schema = False
        if not has_lab_schema:
            logger.warning(
                "Tenant DB '%s' missing core lab tables; routing lab request to platform DB",
                db_name,
            )
            return DatabaseRoute(use_platform=True)

    return DatabaseRoute(use_platform=False, tenant_database_name=db_name)


async def _tenant_has_core_lab_tables(db_name: str) -> bool:
    """True when core lab portal tables exist in the tenant database."""
    from sqlalchemy import text

    key = str(db_name or "").strip()
    if not key:
        return False
    now = time.monotonic()
    cached = _tenant_lab_schema_cache.get(key)
    if cached is not None:
        ok, ts = cached
        if now - ts < _LAB_SCHEMA_CACHE_TTL_SEC:
            return ok

    from app.database.session import get_tenant_session_factory

    fac = get_tenant_session_factory(key)
    ok = False
    async with fac() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    to_regclass('public.lab_equipment') IS NOT NULL
                    AND to_regclass('public.equipment_maintenance_logs') IS NOT NULL
                """
            )
        )
        ok = bool(result.scalar())
    _tenant_lab_schema_cache[key] = (ok, now)
    return ok
