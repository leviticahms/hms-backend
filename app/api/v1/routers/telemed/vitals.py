"""
Telemedicine remote vitals. Patient (self) or doctor entry.
"""
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db_session
from app.api.deps import get_current_user, require_hospital_context
from app.models.user import User
from app.models.patient import PatientProfile
from app.schemas.telemed import TelemedVitalsCreate
from app.schemas.response import SuccessResponse
from app.services.telemed_vitals_service import TelemedVitalsService
from app.services.telemed_prescription_service import TelemedPrescriptionService

router = APIRouter(prefix="/patients", tags=["Telemedicine - Vitals"])


# Disabled in API registration: prescriptions are exposed only through Doctor Portal routes.
async def list_my_prescriptions(
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List prescriptions for current patient. Patient only. SOW: GET /telemed/patients/me/prescriptions"""
    user_roles = [r.name for r in (current_user.roles or [])]
    if "PATIENT" not in user_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Patients only")
    hospital_id = uuid.UUID(context["hospital_id"])
    patient = (
        await db.execute(
            select(PatientProfile).where(
                PatientProfile.user_id == current_user.id,
                PatientProfile.hospital_id == hospital_id,
            )
        )
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found")
    service = TelemedPrescriptionService(db)
    items = await service.list_for_patient(
        hospital_id,
        patient.id,
        current_user.id,
        is_patient_self=True,
    )
    return SuccessResponse(
        success=True,
        message="Prescriptions retrieved",
        data={
            "items": [
                {
                    "id": str(rx.id),
                    "prescription_no": rx.prescription_no,
                    "session_id": str(rx.session_id) if rx.session_id else None,
                    "tele_appointment_id": str(rx.tele_appointment_id) if rx.tele_appointment_id else None,
                    "doctor_id": str(rx.doctor_id),
                    "patient_id": str(rx.patient_id),
                    "patient_ref": patient.patient_id,
                    "diagnosis": rx.diagnosis,
                    "clinical_notes": rx.clinical_notes,
                    "follow_up_date": rx.follow_up_date,
                    "status": rx.status,
                    "signed_at": rx.signed_at.isoformat() if rx.signed_at else None,
                    "created_at": rx.created_at.isoformat() if rx.created_at else None,
                }
                for rx in items
            ],
            "total": len(items),
        },
    ).dict()


@router.get("/me/vitals", response_model=dict)
async def list_my_vitals(
    from_date: Optional[str] = Query(None, description="From date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="To date YYYY-MM-DD"),
    vitals_type: Optional[str] = Query(None, description="BP, HR, SPO2, TEMP, WEIGHT, GLUCOSE"),
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List vitals for current patient (me). Patient only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    patient = (
        await db.execute(
            select(PatientProfile).where(
                PatientProfile.user_id == current_user.id,
                PatientProfile.hospital_id == hospital_id,
            )
        )
    ).scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found")
    user_roles = [r.name for r in (current_user.roles or [])]
    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    service = TelemedVitalsService(db)
    items = await service.list_vitals(
        hospital_id,
        patient.id,
        current_user.id,
        user_roles,
        from_date=from_dt,
        to_date=to_dt,
        vitals_type=vitals_type,
    )
    return SuccessResponse(
        success=True,
        message="Vitals retrieved",
        data={
            "items": [
                {
                    "id": str(v.id),
                    "patient_id": str(v.patient_id),
                    "patient_ref": patient.patient_id,
                    "session_id": str(v.session_id) if v.session_id else None,
                    "vitals_type": v.vitals_type,
                    "value_json": v.value_json,
                    "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                    "entered_by": str(v.entered_by),
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in items
            ],
            "total": len(items),
        },
    ).dict()


@router.get("/{patient_id}/vitals", response_model=dict)
async def list_vitals(
    patient_id: str,
    from_date: Optional[str] = Query(None, description="From date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="To date YYYY-MM-DD"),
    vitals_type: Optional[str] = Query(None, description="BP, HR, SPO2, TEMP, WEIGHT, GLUCOSE"),
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List vitals for patient. Patient (own) or doctor/staff."""
    hospital_id = uuid.UUID(context["hospital_id"])
    user_roles = [r.name for r in (current_user.roles or [])]
    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    service = TelemedVitalsService(db)
    items = await service.list_vitals(
        hospital_id,
        uuid.UUID(patient_id),
        current_user.id,
        user_roles,
        from_date=from_dt,
        to_date=to_dt,
        vitals_type=vitals_type,
    )
    return SuccessResponse(
        success=True,
        message="Vitals retrieved",
        data={
            "items": [
                {
                    "id": str(v.id),
                    "patient_id": str(v.patient_id),
                    "session_id": str(v.session_id) if v.session_id else None,
                    "vitals_type": v.vitals_type,
                    "value_json": v.value_json,
                    "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                    "entered_by": str(v.entered_by),
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in items
            ],
            "total": len(items),
        },
    ).dict()


@router.post("/{patient_id}/vitals", response_model=dict)
async def create_vital(
    patient_id: str,
    body: TelemedVitalsCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Enter vitals. Patient (self) or doctor/staff."""
    hospital_id = uuid.UUID(context["hospital_id"])
    user_roles = [r.name for r in (current_user.roles or [])]
    session_uuid = uuid.UUID(body.session_id) if body.session_id else None
    recorded_at = body.recorded_at if body.recorded_at else None
    service = TelemedVitalsService(db)
    v = await service.create_vital(
        hospital_id,
        uuid.UUID(patient_id),
        body.vitals_type,
        body.value_json,
        current_user.id,
        user_roles,
        session_id=session_uuid,
        recorded_at=recorded_at,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Vital recorded",
        data={
            "id": str(v.id),
            "patient_id": str(v.patient_id),
            "session_id": str(v.session_id) if v.session_id else None,
            "vitals_type": v.vitals_type,
            "value_json": v.value_json,
            "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
            "entered_by": str(v.entered_by),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        },
    ).dict()
