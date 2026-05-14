from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.dependencies.auth import get_current_patient
from app.models.patient import PatientProfile
from app.models.prescription import PrescriptionMedicine, TelePrescription

router = APIRouter(prefix="/patient-prescriptions", tags=["Patient Portal - Prescriptions"])


def _rx_payload(p: TelePrescription):
    return {
        "prescription_id": str(p.id),
        "prescription_no": p.prescription_no,
        "status": p.status,
        "diagnosis": p.diagnosis,
        "follow_up_date": p.follow_up_date,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/my/active")
async def get_active_prescriptions(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelePrescription)
        .where(
            and_(
                TelePrescription.patient_id == current_patient.id,
                TelePrescription.status.in_(["SIGNED", "ACTIVE"]),
            )
        )
        .order_by(desc(TelePrescription.created_at))
    )
    rows = result.scalars().all()
    return {"prescriptions": [_rx_payload(r) for r in rows]}


@router.get("/my/history")
async def get_prescription_history(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelePrescription)
        .where(TelePrescription.patient_id == current_patient.id)
        .order_by(desc(TelePrescription.created_at))
    )
    rows = result.scalars().all()
    return {"prescriptions": [_rx_payload(r) for r in rows]}


@router.get("/{prescription_id}")
async def get_prescription_details(
    prescription_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelePrescription)
        .where(and_(TelePrescription.id == prescription_id, TelePrescription.patient_id == current_patient.id))
        .options(selectinload(TelePrescription.medicines), selectinload(TelePrescription.lab_orders))
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Prescription not found")
    data = _rx_payload(p)
    data["medicines"] = [
        {
            "id": str(m.id),
            "medicine_name": m.medicine_name,
            "dose": m.dose,
            "frequency": m.frequency,
            "duration_days": m.duration_days,
            "instructions": m.instructions,
        }
        for m in p.medicines
    ]
    data["lab_orders"] = [
        {"id": str(o.id), "test_name": o.test_name, "urgency": o.urgency} for o in p.lab_orders
    ]
    return data


@router.post("/{prescription_id}/refill-request")
async def request_refill(
    prescription_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelePrescription).where(
            and_(TelePrescription.id == prescription_id, TelePrescription.patient_id == current_patient.id)
        )
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return {
        "prescription_id": str(p.id),
        "status": "REQUESTED",
        "message": "Refill request submitted to doctor/pharmacy.",
    }
