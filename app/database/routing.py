"""
HTTP request → database binding (platform registry vs per-hospital tenant DB).

Layers:
- Platform DB: hospitals registry, users, auth-resolution tables.
- Tenant DB: optional dedicated PostgreSQL database per hospital (`tenant_database_name`).

See app.database.session for session lifecycle and app.database.engines for connection pools.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from starlette.requests import Request

from app.core.config import settings
from app.database.tenant_context import resolve_tenant_database_name_for_hospital

logger = logging.getLogger(__name__)

# Paths that always use the platform database (no tenant routing).
PLATFORM_ONLY_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/super-admin",
    "/api/v1/super-admin",
    "/api/v1/analytics",
    # OPD receptionist workflow (platform DB). Nested `/receptionist/departments/*` uses tenant DB.
    "/api/v1/receptionist/profile",
    "/api/v1/receptionist/dashboard",
    "/api/v1/receptionist/quick-actions",
    # Patient profiles + documents live on the hospital tenant DB (see receptionist_management).
    # "/api/v1/receptionist/appointments",
    # Subscription + plan flags live on the platform DB (`hospital_subscriptions`, `subscription_plans`).
    "/api/v1/hospital-admin/platform-settings",
    # Appointment slot math and patient booking always use platform DB.
    "/api/v1/appointments/available-slots",
    "/api/v1/patient-appointment-booking",
    # BUG FIX: Doctor appointment tracking must use the SAME platform DB as patient-appointment-booking
    # (appointments are saved to platform DB via get_platform_db_session; tracking must read from there).
    # On Render the hospital has a tenant_database_name so get_db_session would route to tenant DB
    # and return empty results because no appointments exist there.
    "/api/v1/doctor-appointment-tracking",
    # doctor-management schedule endpoints use get_platform_db_session explicitly in the router.
    # doctor portal (dashboard, sidebar, IPD) reads tenant DB when provisioned — not platform-only.
    "/api/v1/patient-discharge-summary",
)

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

    return DatabaseRoute(use_platform=False, tenant_database_name=db_name)
