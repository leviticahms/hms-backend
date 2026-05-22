"""
Resolve OPD patients across platform + tenant databases.

Receptionist registration stores PatientProfile on the hospital tenant DB (mirrored to
platform for portal login). Use these helpers for cross-module lookups when needed.
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional, Union

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.patient import PatientProfile
from app.models.user import User
from app.services.patient_tenant_bridge import resolve_patient_profile_id_for_tenant
from app.utils.hospital_id_resolve import resolve_effective_hospital_id


def clinical_db_sessions(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
) -> List[AsyncSession]:
    seen: set[int] = set()
    sessions: List[AsyncSession] = []
    for sess in (tenant_db, platform_db):
        key = id(sess)
        if key in seen:
            continue
        seen.add(key)
        sessions.append(sess)
    return sessions


def parse_hospital_uuid(hospital_id: Any) -> Optional[uuid.UUID]:
    if hospital_id is None:
        return None
    if isinstance(hospital_id, uuid.UUID):
        return hospital_id
    try:
        return uuid.UUID(str(hospital_id).strip())
    except (ValueError, TypeError):
        return None


async def resolve_staff_hospital_id(
    current_user: User,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    *,
    fallback: Any = None,
) -> uuid.UUID:
    for session in clinical_db_sessions(tenant_db, platform_db):
        hid = await resolve_effective_hospital_id(session, current_user)
        if hid:
            return hid
    parsed = parse_hospital_uuid(fallback)
    if parsed:
        return parsed
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Hospital ID is required. Link your account to a hospital.",
    )


async def load_patient_by_ref(
    patient_ref: str,
    hospital_id: Union[uuid.UUID, str, None],
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    *,
    ensure_on_tenant: bool = False,
) -> PatientProfile:
    """Find patient by business ref on tenant + platform; optionally mirror to tenant."""
    ref = str(patient_ref).strip()
    if not ref:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_ref is required",
        )

    hid = parse_hospital_uuid(hospital_id)

    def _filters(strict_hospital: bool) -> List[Any]:
        conditions: List[Any] = [PatientProfile.patient_id == ref]
        if strict_hospital and hid is not None:
            conditions.append(PatientProfile.hospital_id == hid)
        return conditions

    for strict in (True, False) if hid is not None else (False,):
        for session in clinical_db_sessions(tenant_db, platform_db):
            result = await session.execute(
                select(PatientProfile)
                .where(and_(*_filters(strict)))
                .options(selectinload(PatientProfile.user))
                .limit(1)
            )
            patient = result.scalar_one_or_none()
            if patient:
                if ensure_on_tenant and hid is not None:
                    profile_id = await resolve_patient_profile_id_for_tenant(
                        ref, hid, tenant_db, platform_db
                    )
                    tenant_patient = await tenant_db.get(PatientProfile, profile_id)
                    if tenant_patient:
                        await tenant_db.refresh(tenant_patient, ["user"])
                        return tenant_patient
                return patient

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Patient {ref} not found",
    )
