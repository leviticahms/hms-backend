"""
Doctor Management API
Comprehensive module for doctors to manage their appointments, schedules, and patient interactions.
Provides appointment management, personal schedule control, and patient consultation features.

BUSINESS RULES:
- Only Doctors can access their management features
- Department-based data filtering (doctor sees only their department's data)
- Hospital isolation (doctor sees only their hospital's data)
- Doctors can manage their own schedules and appointments
- Patient consultation and medical record management
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.api.deps import (
    get_db_session, get_current_user, require_doctor, 
    get_service_context, require_doctor_context
)
from app.core.database import get_platform_db_session,get_db_session
from app.models.user import User
from app.services.doctor_service import DoctorService
from app.schemas.doctor import (
    ScheduleSlotOut, ScheduleCreate, ScheduleUpdate, AppointmentDetailsOut,
    AppointmentUpdate
)
from app.core.response_utils import success_response

router = APIRouter(prefix="/doctor-management", tags=["Doctor Portal - Schedule Management"])


def _doctor_service(
    db: AsyncSession,
    platform_db: AsyncSession,
) -> DoctorService:
    return DoctorService(db, platform_db)


# ============================================================================
# TEMPORARY PYDANTIC MODELS (TO BE MIGRATED)
# ============================================================================

class CreateMedicalRecordRequest(BaseModel):
    """Request to create medical record"""
    appointment_ref: str
    chief_complaint: str
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[str] = None
    examination_findings: Optional[str] = None
    vital_signs: Optional[dict] = None
    diagnosis: Optional[str] = None
    differential_diagnosis: Optional[list] = None
    treatment_plan: Optional[str] = None
    follow_up_instructions: Optional[str] = None
    prescriptions: Optional[list] = None
    lab_orders: Optional[list] = None
    imaging_orders: Optional[list] = None


# ============================================================================
# SCHEDULE MANAGEMENT
# ============================================================================

@router.get("/schedule/weekly")
async def get_weekly_schedule(
    week_start: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_platform_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get doctor's weekly schedule with appointments.
    
    Access Control:
    - Only Doctors can access their schedule
    """
    result = await _doctor_service(db, platform_db).get_weekly_schedule(week_start, current_user)
    return success_response(message="Weekly schedule retrieved successfully", data=result)


@router.get("/schedule/slots")
async def get_schedule_slots(
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get doctor's schedule slots configuration.

    Access Control:
    - Only Doctors can access their schedule slots
    """
    result = await _doctor_service(
        db,
        platform_db
    ).get_schedule_slots(current_user)

    return success_response(
        message="Schedule slots retrieved successfully",
        data=result
    )


@router.get("/schedule/{schedule_id}")
async def get_schedule_slot_details(
    schedule_id: str,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """Get a single schedule slot by id (for edit forms)."""
    result = await _doctor_service(
        db,
        platform_db
    ).get_schedule_slot_by_id(
        schedule_id,
        current_user
    )

    return success_response(
        message="Schedule slot retrieved successfully",
        data=result
    )


@router.post("/schedule/create")
async def create_schedule_slot(
    request: ScheduleCreate,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Create a date-specific schedule slot for doctor.

    Access Control:
    - Only Doctors can create their schedule slots
    """
    result = await _doctor_service(
        db,
        platform_db
    ).create_schedule_slot(
        request.model_dump(),
        current_user
    )

    return success_response(
        message="Schedule slot created successfully",
        data=result
    )


@router.put("/schedule/{schedule_id}")
async def update_schedule_slot(
    schedule_id: str,
    request: ScheduleUpdate,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Update existing schedule slot.

    Access Control:
    - Only Doctors can update their own schedule slots
    """
    result = await _doctor_service(
        db,
        platform_db
    ).update_schedule_slot(
        schedule_id,
        request.model_dump(exclude_unset=True),
        current_user
    )

    return success_response(
        message="Schedule slot updated successfully",
        data=result
    )


@router.delete("/schedule/{schedule_id}")
async def delete_schedule_slot(
    schedule_id: str,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Delete schedule slot.

    Access Control:
    - Only Doctors can delete their own schedule slots
    """
    result = await _doctor_service(
        db,
        platform_db
    ).delete_schedule_slot(
        schedule_id,
        current_user
    )

    return success_response(
        message="Schedule slot deleted successfully",
        data=result
    )


# ============================================================================
# APPOINTMENT MANAGEMENT
# ============================================================================

# Duplicate disabled: use GET /api/v1/doctor-sidebar/appointments for the doctor portal list.
async def get_doctor_appointments(
    date_from: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    date_to: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get doctor's appointments with filtering options.
    
    Access Control:
    - Only Doctors can access their appointments
    """
    doctor_service = DoctorService(db)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "limit": limit
    }
    result = await doctor_service.get_doctor_appointments(filters, current_user)
    return success_response(message="Appointments retrieved successfully", data=result)


# Duplicate disabled: use GET /api/v1/doctor-appointment-tracking/appointments/{appointment_ref}/tracking.
async def get_appointment_details(
    appointment_ref: str,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed appointment information.
    
    Access Control:
    - Only Doctors can access their appointment details
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.get_appointment_details(appointment_ref, current_user)
    return success_response(message="Appointment details retrieved successfully", data=result)


@router.put("/appointments/{appointment_ref}")
async def update_appointment(
    appointment_ref: str,
    request: AppointmentUpdate,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update appointment details.
    
    Access Control:
    - Only Doctors can update their appointments
    """
    doctor_service = DoctorService(db)
    update_data = request.model_dump(exclude_unset=True)
    result = await doctor_service.update_appointment(appointment_ref, update_data, current_user)
    return success_response(message="Appointment updated successfully", data=result)


@router.post("/appointments/{appointment_ref}/complete")
async def complete_appointment(
    appointment_ref: str,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Mark appointment as completed.
    
    Access Control:
    - Only Doctors can complete their appointments
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.complete_appointment(appointment_ref, current_user)
    return success_response(message="Appointment completed successfully", data=result)


@router.post("/appointments/{appointment_ref}/cancel")
async def cancel_appointment(
    appointment_ref: str,
    cancellation_reason: str = Body(..., embed=True),
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Cancel appointment.
    
    Access Control:
    - Only Doctors can cancel their appointments
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.cancel_appointment(appointment_ref, cancellation_reason, current_user)
    return success_response(message="Appointment cancelled successfully", data=result)


# ============================================================================
# PATIENT CONSULTATION
# ============================================================================

@router.get("/consultation/{patient_ref}")
async def get_patient_consultation_details(
    patient_ref: str,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get comprehensive patient details for consultation.
    
    Access Control:
    - Only Doctors can access patient consultation details
    - Department-based filtering (if applicable)
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.get_patient_consultation_details(patient_ref, current_user)
    return success_response(message="Patient consultation details retrieved successfully", data=result)


# Duplicate disabled: use POST /api/v1/doctor-patient-records/medical-records.
async def create_medical_record(
    request: CreateMedicalRecordRequest,
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create medical record for patient consultation.
    
    Access Control:
    - Only Doctors can create medical records
    """
    doctor_service = DoctorService(db)
    record_data = {
        "appointment_ref": request.appointment_ref,
        "chief_complaint": request.chief_complaint,
        "history_of_present_illness": request.history_of_present_illness,
        "past_medical_history": request.past_medical_history,
        "examination_findings": request.examination_findings,
        "vital_signs": request.vital_signs,
        "diagnosis": request.diagnosis,
        "differential_diagnosis": request.differential_diagnosis,
        "treatment_plan": request.treatment_plan,
        "follow_up_instructions": request.follow_up_instructions,
        "prescriptions": request.prescriptions,
        "lab_orders": request.lab_orders,
        "imaging_orders": request.imaging_orders
    }
    result = await doctor_service.create_medical_record(record_data, current_user)
    return success_response(message="Medical record created successfully", data=result)


# Duplicate disabled: use GET /api/v1/doctor-patient-records/patients/search.
async def search_patients(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search patients by name, phone, or patient ID.
    
    Access Control:
    - Only Doctors can search patients
    - Hospital isolation applied
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.search_patients(query, limit, current_user)
    return success_response(message="Patients retrieved successfully", data=result)


@router.get("/statistics/summary")
async def get_doctor_statistics_summary(
    period: str = Query("month", pattern="^(week|month|quarter|year)$"),
    current_user: User = Depends(require_doctor()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get comprehensive statistics summary for doctor.
    
    Access Control:
    - Only Doctors can access their statistics
    """
    doctor_service = DoctorService(db)
    result = await doctor_service.get_statistics_summary(period, current_user)
    return success_response(message="Statistics retrieved successfully", data=result)