from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_platform_db_session
from app.dependencies.auth import get_current_patient
from app.models.billing.bill import Bill
from app.models.patient import Appointment, PatientProfile
from app.models.prescription import TelePrescription
from app.models.telemedicine import TelemedNotification, TelemedVitals

router = APIRouter(prefix="/patient-dashboard", tags=["Patient Portal - Dashboard"])


@router.get("/overview-metrics")
async def get_overview_metrics(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    upcoming = await db.execute(
        select(func.count(Appointment.id)).where(
            and_(
                Appointment.patient_id == current_patient.id,
                Appointment.status.in_(["REQUESTED", "CONFIRMED", "CHECKED_IN", "IN_PROGRESS"]),
            )
        )
    )
    pending_bills = await db.execute(
        select(func.coalesce(func.sum(Bill.balance_due), 0)).where(
            and_(Bill.patient_id == current_patient.id, Bill.balance_due > 0)
        )
    )
    active_rx = await db.execute(
        select(func.count(TelePrescription.id)).where(
            and_(
                TelePrescription.patient_id == current_patient.id,
                TelePrescription.status.in_(["SIGNED", "ACTIVE"]),
            )
        )
    )
    return {
        "patient_ref": current_patient.patient_id,
        "upcoming_appointments_count": upcoming.scalar() or 0,
        "pending_bill_amount": float(pending_bills.scalar() or 0),
        "active_prescriptions_count": active_rx.scalar() or 0,
    }


@router.get("/recent-vitals")
async def get_recent_vitals(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelemedVitals)
        .where(TelemedVitals.patient_id == current_patient.id)
        .order_by(TelemedVitals.recorded_at.desc())
        .limit(20)
    )
    vitals = result.scalars().all()
    latest = {}
    for v in vitals:
        k = (v.vitals_type or "").upper()
        if k and k not in latest:
            latest[k] = {"value": v.value_json, "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None}
    return {
        "blood_pressure": latest.get("BP"),
        "heart_rate": latest.get("HR"),
        "bmi": latest.get("BMI"),
        "temperature": latest.get("TEMP"),
        "all": latest,
    }


@router.get("/notifications")
async def get_dashboard_notifications(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelemedNotification)
        .where(TelemedNotification.recipient_user_id == current_patient.user_id)
        .order_by(TelemedNotification.created_at.desc())
        .limit(20)
    )
    rows = result.scalars().all()
    return {
        "notifications": [
            {
                "id": str(n.id),
                "title": n.title,
                "body": n.body,
                "event_type": n.event_type,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "read": n.read_at is not None,
            }
            for n in rows
        ]
    }
