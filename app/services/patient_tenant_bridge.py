"""
Bridge OPD/receptionist patients between platform (auth/login) and hospital tenant DB.

Receptionist APIs persist ``PatientProfile`` + portal ``User`` on the **tenant** database.
Only ``users`` + ``user_roles`` are mirrored to platform for ``POST /auth/patient/login``.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import UserRole
from app.models.patient import PatientProfile
from app.models.user import Role, User, user_roles


async def upsert_tenant_user_from_platform_user(
    tenant_db: AsyncSession,
    user: User,
    fallback_role: Optional[str] = None,
) -> None:
    data = {column.name: getattr(user, column.name) for column in User.__table__.columns}
    existing_user = await tenant_db.get(User, user.id)
    if existing_user:
        for key, value in data.items():
            if key != "id":
                setattr(existing_user, key, value)
    else:
        tenant_db.add(User(**data))
    await tenant_db.flush()

    loaded_roles = user.__dict__.get("roles") or []
    role_names = [getattr(role, "name", None) for role in loaded_roles if getattr(role, "name", None)]
    if fallback_role and fallback_role not in role_names:
        role_names.append(fallback_role)

    for role_name in role_names:
        if not role_name:
            continue
        role_result = await tenant_db.execute(
            select(Role)
            .where(Role.name == role_name)
            .order_by(Role.created_at.desc())
            .limit(1)
        )
        tenant_role = role_result.scalars().first()
        if not tenant_role:
            tenant_role = Role(
                id=uuid.uuid4(),
                name=role_name,
                display_name=role_name.replace("_", " ").title(),
                description="Mirrored for tenant access",
                is_system_role=True,
                level=1 if role_name == UserRole.PATIENT.value else 50,
            )
            tenant_db.add(tenant_role)
            await tenant_db.flush()
        await tenant_db.execute(
            pg_insert(user_roles)
            .values(user_id=user.id, role_id=tenant_role.id)
            .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
        )
    await tenant_db.flush()


async def resolve_patient_profile_id_for_tenant(
    patient_ref: str,
    hospital_id: uuid.UUID,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
) -> uuid.UUID:
    """
    Return ``PatientProfile.id`` on the tenant DB, copying from platform when needed.
    Preserves primary key UUID so FKs stay aligned with platform.
    """
    ref = str(patient_ref).strip()
    result = await tenant_db.execute(
        select(PatientProfile.id)
        .where(
            PatientProfile.patient_id == ref,
            PatientProfile.hospital_id == hospital_id,
        )
        .limit(1)
    )
    profile_id = result.scalar_one_or_none()
    if profile_id:
        return profile_id

    platform_result = await platform_db.execute(
        select(PatientProfile)
        .where(
            PatientProfile.patient_id == ref,
            PatientProfile.hospital_id == hospital_id,
        )
        .options(selectinload(PatientProfile.user))
        .limit(1)
    )
    platform_patient = platform_result.scalar_one_or_none()
    if not platform_patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient not found with patient_ref: {patient_ref}",
        )

    platform_user = platform_patient.user or await platform_db.get(User, platform_patient.user_id)
    if platform_user:
        await upsert_tenant_user_from_platform_user(
            tenant_db, platform_user, UserRole.PATIENT.value
        )

    patient_data = {
        column.name: getattr(platform_patient, column.name) for column in PatientProfile.__table__.columns
    }
    existing_patient = await tenant_db.get(PatientProfile, platform_patient.id)
    if existing_patient:
        for key, value in patient_data.items():
            if key != "id":
                setattr(existing_patient, key, value)
    else:
        tenant_db.add(PatientProfile(**patient_data, id=platform_patient.id,))
    await tenant_db.flush()
    return platform_patient.id


async def mirror_patient_auth_to_platform(
    platform_db: AsyncSession,
    tenant_user: User,
) -> None:
    """
    Copy portal login credentials to platform DB only (users + PATIENT role).

    ``PatientProfile`` stays on the tenant DB — not duplicated on platform.
    """
    await upsert_tenant_user_from_platform_user(
        platform_db, tenant_user, UserRole.PATIENT.value
    )


async def mirror_opd_patient_to_platform(
    platform_db: AsyncSession,
    tenant_patient: PatientProfile,
    tenant_user: User,
) -> None:
    """Legacy full mirror; prefer ``mirror_patient_auth_to_platform`` for new OPD writes."""
    await mirror_patient_auth_to_platform(platform_db, tenant_user)
