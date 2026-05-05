"""
Tenant registry lookups against the platform database.

Caches `hospitals.tenant_database_name` per hospital_id for routing decisions.
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, Optional, Tuple

from sqlalchemy import select

_hospital_tenant_cache: Dict[str, Tuple[Optional[str], float]] = {}
_CACHE_TTL_SEC = 60.0


def invalidate_hospital_tenant_cache(hospital_id: uuid.UUID) -> None:
    _hospital_tenant_cache.pop(str(hospital_id), None)


async def resolve_tenant_database_name_for_hospital(hospital_id: uuid.UUID) -> Optional[str]:
    """Read hospitals.tenant_database_name from the platform DB (short TTL cache)."""
    key = str(hospital_id)
    now = time.monotonic()
    cached = _hospital_tenant_cache.get(key)
    if cached is not None:
        name, ts = cached
        if now - ts < _CACHE_TTL_SEC:
            return name

    from app.models.tenant import Hospital

    # Deferred import avoids circular import with app.database.session.
    from app.database.session import get_session_factory

    async with get_session_factory()() as session:
        r = await session.execute(select(Hospital.tenant_database_name).where(Hospital.id == hospital_id))
        name = r.scalar_one_or_none()
    _hospital_tenant_cache[key] = (name, now)
    return name
