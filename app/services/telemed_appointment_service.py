"""
Telemedicine appointment service.
Standalone tele-appointments; no link to regular appointments.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.telemedicine import TeleAppointment
from app.models.patient import Appointment, PatientProfile
from app.repositories.telemed_repository import TeleAppointmentRepository
from app.models.doctor import DoctorProfile
from app.models.schedule import DoctorSchedule
from app.models.user import User
from app.core.enums import AppointmentStatus, UserRole


class TeleAppointmentService:
    """Service for tele-appointments with hospital isolation and double-booking prevention."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _validate_doctor_in_hospital(self, doctor_id: uuid.UUID, hospital_id: uuid.UUID) -> User:
        """Ensure doctor belongs to hospital (via DoctorProfile or User role)."""
        result = await self.db.execute(
            select(DoctorProfile)
            .where(
                DoctorProfile.user_id == doctor_id,
                DoctorProfile.hospital_id == hospital_id
            )
            .options(selectinload(DoctorProfile.user))
        )
        doctor_profile = result.scalar_one_or_none()
        if not doctor_profile:
            # Fallback: check User has doctor role and hospital_id
            user_result = await self.db.execute(
                select(User)
                .where(
                    User.id == doctor_id,
                    User.hospital_id == hospital_id,
                    User.is_active == True
                )
                .options(selectinload(User.roles))
            )
            user = user_result.scalar_one_or_none()
            if not user or not any(r.name == UserRole.DOCTOR for r in (user.roles or [])):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Doctor not found in this hospital"
                )
            return user
        return doctor_profile.user

    async def _validate_patient_in_hospital(self, patient_id: uuid.UUID, hospital_id: uuid.UUID) -> PatientProfile:
        """Ensure patient belongs to hospital."""
        result = await self.db.execute(
            select(PatientProfile)
            .where(
                PatientProfile.id == patient_id,
                PatientProfile.hospital_id == hospital_id
            )
        )
        patient = result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found in this hospital"
            )
        return patient

    async def _check_overlap(
        self,
        hospital_id: uuid.UUID,
        doctor_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        exclude_id: Optional[uuid.UUID] = None
    ) -> bool:
        """Check for overlapping tele_appointments or regular appointments."""
        # Tele-appointments overlap
        tele_query = (
            select(TeleAppointment)
            .where(
                and_(
                    TeleAppointment.hospital_id == hospital_id,
                    TeleAppointment.doctor_id == doctor_id,
                    TeleAppointment.status.in_(["SCHEDULED", "CONFIRMED", "IN_PROGRESS"]),
                    TeleAppointment.scheduled_start < scheduled_end,
                    TeleAppointment.scheduled_end > scheduled_start
                )
            )
        )
        if exclude_id:
            tele_query = tele_query.where(TeleAppointment.id != exclude_id)
        tele_result = await self.db.execute(tele_query)
        if tele_result.scalar_one_or_none():
            return True

        # Regular appointments overlap (doctor_id in Appointment is users.id)
        apt_date = scheduled_start.strftime("%Y-%m-%d")
        apt_time = scheduled_start.strftime("%H:%M:%S")
        apt_end = scheduled_end
        # Build overlap: appointment (date, time) + duration vs (scheduled_start, scheduled_end)
        from datetime import timedelta
        apt_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.hospital_id == hospital_id,
                    Appointment.doctor_id == doctor_id,
                    Appointment.appointment_date == apt_date,
                    Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
                )
            )
        )
        for apt in apt_result.scalars().all():
            apt_start = datetime.fromisoformat(f"{apt.appointment_date}T{apt.appointment_time}")
            apt_duration = apt.duration_minutes or 30
            apt_end_dt = apt_start + timedelta(minutes=apt_duration)
            if apt_start < scheduled_end and apt_end_dt > scheduled_start:
                return True

        return False

    async def _check_doctor_schedule(
        self,
        hospital_id: uuid.UUID,
        doctor_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
    ) -> None:
        """
        Validate tele-appointment falls within doctor's working schedule.
        Raises 400 if doctor has no schedule for that day or slot is outside working hours.
        """
        day_of_week = scheduled_start.strftime("%A").upper()
        date_iso = scheduled_start.date().isoformat()
        result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                DoctorSchedule.hospital_id == hospital_id,
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.day_of_week == day_of_week,
                DoctorSchedule.is_active == True,
                or_(DoctorSchedule.effective_from.is_(None), DoctorSchedule.effective_from <= date_iso),
                or_(DoctorSchedule.effective_to.is_(None), DoctorSchedule.effective_to >= date_iso),
            )
            .order_by((DoctorSchedule.effective_from == date_iso).desc(), DoctorSchedule.created_at.desc())
            .limit(1)
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            return  # No schedule configured: allow (SOW: "IF available")

        start_dt = scheduled_start
        end_dt = scheduled_end
        from datetime import time as dt_time
        sched_start = schedule.start_time
        sched_end = schedule.end_time
        slot_start = start_dt.time() if hasattr(start_dt, "time") else dt_time(start_dt.hour, start_dt.minute, start_dt.second)
        slot_end = end_dt.time() if hasattr(end_dt, "time") else dt_time(end_dt.hour, end_dt.minute, end_dt.second)

        if slot_start < sched_start or slot_end > sched_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "OUTSIDE_WORKING_HOURS",
                    "message": f"Slot must be within doctor's schedule ({sched_start}–{sched_end})",
                },
            )

        if schedule.break_start_time and schedule.break_end_time:
            if slot_start < schedule.break_end_time and slot_end > schedule.break_start_time:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "OVERLAPS_BREAK",
                        "message": "Slot overlaps with doctor's break time",
                    },
                )

    async def create(
        self,
        hospital_id: uuid.UUID,
        patient_id: uuid.UUID,
        doctor_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        created_by: uuid.UUID,
        reason: Optional[str] = None,
        notes: Optional[str] = None
    ) -> TeleAppointment:
        """Create tele-appointment with overlap check and transaction lock."""
        await self._validate_doctor_in_hospital(doctor_id, hospital_id)
        await self._validate_patient_in_hospital(patient_id, hospital_id)

        if scheduled_start >= scheduled_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scheduled_end must be after scheduled_start"
            )
        if scheduled_start <= datetime.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot book appointments in the past"
            )

        await self._check_doctor_schedule(hospital_id, doctor_id, scheduled_start, scheduled_end)

        if await self._check_overlap(hospital_id, doctor_id, scheduled_start, scheduled_end):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Doctor has another appointment in this time slot"
            )

        tele_app = TeleAppointment(
            hospital_id=hospital_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            reason=reason,
            notes=notes,
            status="SCHEDULED",
            created_by=created_by
        )
        self.db.add(tele_app)
        await self.db.flush()
        await self.db.refresh(tele_app)
        return tele_app

    async def get_by_id(self, tele_appointment_id: uuid.UUID, hospital_id: uuid.UUID) -> Optional[TeleAppointment]:
        repo = TeleAppointmentRepository(self.db, hospital_id)
        return await repo.get_by_id(tele_appointment_id)

    async def list_for_patient(
        self, hospital_id: uuid.UUID, patient_id: uuid.UUID, status_filter: Optional[str] = None
    ) -> List[TeleAppointment]:
        repo = TeleAppointmentRepository(self.db, hospital_id)
        return await repo.list(patient_id=patient_id, status_filter=status_filter)

    async def list_for_doctor(
        self, hospital_id: uuid.UUID, doctor_id: uuid.UUID, status_filter: Optional[str] = None
    ) -> List[TeleAppointment]:
        repo = TeleAppointmentRepository(self.db, hospital_id)
        return await repo.list(doctor_id=doctor_id, status_filter=status_filter)

    async def list_for_receptionist(
        self, hospital_id: uuid.UUID, status_filter: Optional[str] = None
    ) -> List[TeleAppointment]:
        repo = TeleAppointmentRepository(self.db, hospital_id)
        return await repo.list(status_filter=status_filter)

    async def reschedule(
        self,
        tele_appointment_id: uuid.UUID,
        hospital_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        reason: Optional[str] = None
    ) -> TeleAppointment:
        tele_app = await self.get_by_id(tele_appointment_id, hospital_id)
        if not tele_app:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tele-appointment not found")
        if tele_app.status not in ("SCHEDULED", "CONFIRMED"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reschedule appointment in current status"
            )
        if scheduled_start >= scheduled_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scheduled_end must be after scheduled_start"
            )
        await self._check_doctor_schedule(hospital_id, tele_app.doctor_id, scheduled_start, scheduled_end)
        if await self._check_overlap(
            hospital_id, tele_app.doctor_id, scheduled_start, scheduled_end, exclude_id=tele_appointment_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Doctor has another appointment in this time slot"
            )
        tele_app.scheduled_start = scheduled_start
        tele_app.scheduled_end = scheduled_end
        if reason:
            tele_app.notes = (tele_app.notes or "") + f"\nReschedule reason: {reason}"
        await self.db.flush()
        await self.db.refresh(tele_app)
        return tele_app

    async def cancel(
        self,
        tele_appointment_id: uuid.UUID,
        hospital_id: uuid.UUID,
        cancelled_by: uuid.UUID,
        cancellation_reason: Optional[str] = None
    ) -> TeleAppointment:
        tele_app = await self.get_by_id(tele_appointment_id, hospital_id)
        if not tele_app:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tele-appointment not found")
        if tele_app.status in ("CANCELLED", "COMPLETED"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel appointment in current status"
            )
        tele_app.status = "CANCELLED"
        tele_app.cancelled_at = datetime.utcnow()
        tele_app.cancelled_by = cancelled_by
        tele_app.cancellation_reason = cancellation_reason
        await self.db.flush()
        await self.db.refresh(tele_app)
        return tele_app

    async def confirm(
        self,
        tele_appointment_id: uuid.UUID,
        hospital_id: uuid.UUID
    ) -> TeleAppointment:
        tele_app = await self.get_by_id(tele_appointment_id, hospital_id)
        if not tele_app:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tele-appointment not found")
        if tele_app.status != "SCHEDULED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only SCHEDULED appointments can be confirmed"
            )
        tele_app.status = "CONFIRMED"
        await self.db.flush()
        await self.db.refresh(tele_app)
        return tele_app
