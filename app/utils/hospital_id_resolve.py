"""
Resolve the hospital UUID a staff user operates under.

Some accounts have NULL users.hospital_id but a valid ReceptionistProfile /
StaffDepartmentAssignment → Department chain. Registration and patient search
must use the same effective hospital_id.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.receptionist import ReceptionistProfile
from app.models.user import User


async def resolve_effective_hospital_id(db: AsyncSession, user: User) -> Optional[uuid.UUID]:
    if getattr(user, "hospital_id", None):
        hid = user.hospital_id
        return hid if isinstance(hid, uuid.UUID) else uuid.UUID(str(hid))

    rp_res = await db.execute(
        select(ReceptionistProfile.hospital_id).where(ReceptionistProfile.user_id == user.id).limit(1)
    )
    row = rp_res.first()
    if row and row[0]:
        hid = row[0]
        return hid if isinstance(hid, uuid.UUID) else uuid.UUID(str(hid))

    sa_res = await db.execute(
        select(Department.hospital_id)
        .join(StaffDepartmentAssignment, StaffDepartmentAssignment.department_id == Department.id)
        .where(
            and_(
                StaffDepartmentAssignment.staff_id == user.id,
                StaffDepartmentAssignment.is_active == True,
            )
        )
        .limit(1)
    )
    row2 = sa_res.first()
    if row2 and row2[0]:
        hid = row2[0]
        return hid if isinstance(hid, uuid.UUID) else uuid.UUID(str(hid))

    return None
