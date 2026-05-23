"""
Resolve a doctor's display department from staff assignments (source of truth),
then doctor profile / user metadata fallbacks.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import DoctorProfile
from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.user import User

_GENERIC_DEPARTMENT_NAMES = frozenset(
    {
        "general",
        "general opd",
        "general medicine",
        "general department",
        "general physician",
    }
)


def _is_generic_department_name(name: Optional[str]) -> bool:
    return (name or "").strip().lower() in _GENERIC_DEPARTMENT_NAMES


def _metadata_department_name(user: Optional[User]) -> str:
    if not user:
        return ""
    md = getattr(user, "user_metadata", None) or {}
    if not isinstance(md, dict):
        return ""
    for key in ("department_name", "department", "departmentName"):
        val = (md.get(key) or "").strip()
        if val:
            return val
    return ""


async def _assignment_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_user_id: uuid.UUID,
) -> Optional[Department]:
    result = await db.execute(
        select(Department)
        .join(
            StaffDepartmentAssignment,
            StaffDepartmentAssignment.department_id == Department.id,
        )
        .where(
            and_(
                StaffDepartmentAssignment.staff_id == doctor_user_id,
                StaffDepartmentAssignment.hospital_id == hospital_id,
                StaffDepartmentAssignment.is_active == True,
                Department.is_active == True,
            )
        )
        .order_by(
            desc(StaffDepartmentAssignment.is_primary),
            desc(StaffDepartmentAssignment.effective_from),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _profile_department(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_user_id: uuid.UUID,
) -> Optional[Department]:
    result = await db.execute(
        select(Department)
        .join(DoctorProfile, DoctorProfile.department_id == Department.id)
        .where(
            and_(
                DoctorProfile.user_id == doctor_user_id,
                DoctorProfile.hospital_id == hospital_id,
                Department.is_active == True,
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_doctor_primary_department(
    sessions: Sequence[AsyncSession],
    hospital_id: uuid.UUID,
    doctor_user_id: uuid.UUID,
) -> Optional[Department]:
    """Primary department across one or more DB sessions (tenant first recommended)."""
    seen_sessions: set[int] = set()
    for sess in sessions:
        key = id(sess)
        if key in seen_sessions:
            continue
        seen_sessions.add(key)
        dept = await _assignment_department(sess, hospital_id, doctor_user_id)
        if dept:
            return dept

    for sess in sessions:
        key = id(sess)
        if key in seen_sessions:
            continue
        dept = await _profile_department(sess, hospital_id, doctor_user_id)
        if dept and not _is_generic_department_name(dept.name):
            return dept

    for sess in sessions:
        dept = await _profile_department(sess, hospital_id, doctor_user_id)
        if dept:
            return dept

    return None


async def resolve_doctor_departments_batch(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_user_ids: List[uuid.UUID],
) -> Dict[uuid.UUID, Department]:
    """Batch resolve primary department per doctor on a single session."""
    if not doctor_user_ids:
        return {}

    out: Dict[uuid.UUID, Department] = {}

    assign_result = await db.execute(
        select(StaffDepartmentAssignment.staff_id, Department)
        .join(Department, StaffDepartmentAssignment.department_id == Department.id)
        .where(
            and_(
                StaffDepartmentAssignment.staff_id.in_(doctor_user_ids),
                StaffDepartmentAssignment.hospital_id == hospital_id,
                StaffDepartmentAssignment.is_active == True,
                Department.is_active == True,
            )
        )
        .order_by(
            StaffDepartmentAssignment.staff_id,
            desc(StaffDepartmentAssignment.is_primary),
            desc(StaffDepartmentAssignment.effective_from),
        )
    )
    for staff_id, dept in assign_result.all():
        if staff_id not in out:
            out[staff_id] = dept

    missing = [uid for uid in doctor_user_ids if uid not in out]
    if not missing:
        return out

    profile_result = await db.execute(
        select(DoctorProfile.user_id, Department)
        .join(Department, DoctorProfile.department_id == Department.id)
        .where(
            and_(
                DoctorProfile.user_id.in_(missing),
                DoctorProfile.hospital_id == hospital_id,
                Department.is_active == True,
            )
        )
    )
    for user_id, dept in profile_result.all():
        if user_id not in out and not _is_generic_department_name(dept.name):
            out[user_id] = dept

    still_missing = [uid for uid in doctor_user_ids if uid not in out]
    if still_missing:
        profile_result2 = await db.execute(
            select(DoctorProfile.user_id, Department)
            .join(Department, DoctorProfile.department_id == Department.id)
            .where(
                and_(
                    DoctorProfile.user_id.in_(still_missing),
                    DoctorProfile.hospital_id == hospital_id,
                )
            )
        )
        for user_id, dept in profile_result2.all():
            if user_id not in out:
                out[user_id] = dept

    return out


async def resolve_doctor_departments_multi_session(
    sessions: Sequence[AsyncSession],
    hospital_id: uuid.UUID,
    doctor_user_ids: List[uuid.UUID],
) -> Dict[uuid.UUID, Department]:
    """Merge department maps from tenant + platform; prefer non-generic names."""
    merged: Dict[uuid.UUID, Department] = {}
    seen_sessions: set[int] = set()
    for sess in sessions:
        key = id(sess)
        if key in seen_sessions:
            continue
        seen_sessions.add(key)
        batch = await resolve_doctor_departments_batch(sess, hospital_id, doctor_user_ids)
        for uid, dept in batch.items():
            existing = merged.get(uid)
            if existing is None:
                merged[uid] = dept
            elif _is_generic_department_name(existing.name) and not _is_generic_department_name(
                dept.name
            ):
                merged[uid] = dept
    return merged


def doctor_department_display(
    department: Optional[Department],
    user: Optional[User],
    *,
    specialization: Optional[str] = None,
) -> Tuple[str, Optional[uuid.UUID]]:
    """Human-readable department label + id for API payloads."""
    dept_id: Optional[uuid.UUID] = None
    name = ""

    if department:
        dept_id = department.id
        name = (department.name or "").strip()

    if _is_generic_department_name(name):
        meta_name = _metadata_department_name(user)
        if meta_name and not _is_generic_department_name(meta_name):
            name = meta_name

    if not name and specialization and not _is_generic_department_name(specialization):
        name = specialization.strip()

    if not name and user:
        name = _metadata_department_name(user)

    return name, dept_id
