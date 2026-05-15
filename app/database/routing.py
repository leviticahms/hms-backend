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
    # OPD appointment workflow lives in the platform DB because receptionist and patient
    # booking create patients/appointments there for patient auth and shared doctor visibility.
    "/api/v1/receptionist",
    # Doctors / departments directory reads from tenant DB when `tenant_database_name` is set
    # (staff + DoctorProfile live there). Keep appointment slot math on platform below.
    "/api/v1/appointments/available-slots",
    "/api/v1/patient-appointment-booking",
    "/api/v1/doctor-management",
    "/api/v1/staff/doctor-schedules",
    "/api/v1/doctor-dashboard",
    "/api/v1/doctor-appointment-tracking",
    "/api/v1/doctor-patient-records",
    "/api/v1/doctor-treatment-plans",
    "/api/v1/doctor-sidebar",
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
