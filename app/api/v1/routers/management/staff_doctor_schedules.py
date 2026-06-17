"""
Doctor availability for receptionists and nurses (same department as the doctor).

Paths use doctor **name** (URL-encoded if needed), e.g.
`GET /api/v1/staff/doctor-schedules/Dr.%20Jane%20Smith/check-slots?date=2026-04-10`
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_receptionist_or_nurse
from app.core.database import get_platform_db_session
from app.models.user import User
from app.services.doctor_service import DoctorService
from app.services.appointment_service import AppointmentService
from app.schemas.doctor import ScheduleCreate, ScheduleUpdate
from app.core.response_utils import success_response

router = APIRouter(prefix="/staff/doctor-schedules", tags=["Staff - Doctor availability"])


# Register /slots/... before /{doctor_name:path} so "slots" is not captured as a name.
@router.put("/slots/{schedule_id}")
async def staff_update_doctor_schedule_slot(
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    current_user: User = Depends(require_receptionist_or_nurse()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Update a schedule row for a doctor in your department."""
    svc = DoctorService(db)
    result = await svc.update_schedule_slot_for_staff(
        current_user, str(schedule_id), body.model_dump(exclude_unset=True)
    )
    return success_response(message="Schedule slot updated successfully", data=result)


@router.delete("/slots/{schedule_id}")
async def staff_delete_doctor_schedule_slot(
    schedule_id: uuid.UUID,
    current_user: User = Depends(require_receptionist_or_nurse()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Delete a schedule row for a doctor in your department."""
    svc = DoctorService(db)
    result = await svc.delete_schedule_slot_for_staff(current_user, str(schedule_id))
    return success_response(message="Schedule slot deleted successfully", data=result)


@router.get("/{doctor_name:path}/check-slots")
async def staff_check_doctor_available_slots(
    doctor_name: str,
    date: str = Query(..., description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current_user: User = Depends(require_receptionist_or_nurse()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Bookable time slots for a date (same rules as patient booking)."""
    ds = DoctorService(db)
    doc = await ds.get_target_doctor_in_hospital_for_staff_by_name(current_user, doctor_name)
    dept_nm = await ds.get_staff_department_name(current_user)
    appt = AppointmentService(db)
    slots = await appt.get_available_time_slots_for_doctor_user(doc.id, date)
    return success_response(
        message="Available slots computed for date",
        data={
            "doctor_name": f"Dr. {doc.first_name} {doc.last_name}",
            "doctor_user_id": str(doc.id),
            "department_name": dept_nm,
            "date": date,
            "slots": slots,
        },
    )


@router.get("/{doctor_name:path}")
async def staff_get_doctor_schedule_template(
    doctor_name: str,
    current_user: User = Depends(require_receptionist_or_nurse()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Date-specific schedule slots for a doctor in your department."""
    svc = DoctorService(db)
    result = await svc.get_schedule_slots_for_target_doctor(current_user, doctor_name)
    return success_response(message="Schedule configuration retrieved successfully", data=result)


@router.post("/{doctor_name:path}")
async def staff_create_doctor_schedule_slot(
    doctor_name: str,
    body: ScheduleCreate,
    current_user: User = Depends(require_receptionist_or_nurse()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Add one date-specific availability slot for a doctor in your department."""
    svc = DoctorService(db)
    result = await svc.create_schedule_slot_for_staff(
        current_user, doctor_name, body.model_dump()
    )
    return success_response(message="Schedule slot created successfully", data=result)
