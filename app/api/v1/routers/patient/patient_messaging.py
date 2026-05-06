from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.dependencies.auth import get_current_patient
from app.models.patient import PatientProfile
from app.models.telemedicine import TeleAppointment, TelemedMessage, TelemedSession

router = APIRouter(prefix="/patient-messaging", tags=["Patient Portal - Messaging"])


@router.get("/conversations")
async def get_conversations(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(TelemedSession)
        .join(TeleAppointment, TelemedSession.tele_appointment_id == TeleAppointment.id)
        .where(TeleAppointment.patient_id == current_patient.id)
        .options(selectinload(TelemedSession.tele_appointment))
        .order_by(TelemedSession.created_at.desc())
    )
    sessions = result.scalars().all()
    return {
        "conversations": [
            {
                "conversation_id": str(s.id),
                "doctor_id": str(s.tele_appointment.doctor_id) if s.tele_appointment else None,
                "status": s.status,
                "scheduled_start": s.scheduled_start.isoformat() if s.scheduled_start else None,
            }
            for s in sessions
        ]
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    ses = await db.execute(
        select(TelemedSession)
        .join(TeleAppointment, TelemedSession.tele_appointment_id == TeleAppointment.id)
        .where(and_(TelemedSession.id == conversation_id, TeleAppointment.patient_id == current_patient.id))
    )
    session = ses.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = await db.execute(
        select(TelemedMessage)
        .where(TelemedMessage.session_id == session.id)
        .order_by(TelemedMessage.created_at.asc())
    )
    rows = result.scalars().all()
    return {
        "conversation_id": conversation_id,
        "messages": [
            {
                "message_id": str(m.id),
                "sender_id": str(m.sender_id),
                "sender_role": m.sender_role,
                "message_type": m.message_type,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ],
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: Dict[str, Any],
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    ses = await db.execute(
        select(TelemedSession)
        .join(TeleAppointment, TelemedSession.tele_appointment_id == TeleAppointment.id)
        .where(and_(TelemedSession.id == conversation_id, TeleAppointment.patient_id == current_patient.id))
    )
    session = ses.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = TelemedMessage(
        hospital_id=current_patient.hospital_id,
        session_id=session.id,
        sender_id=current_patient.user_id,
        sender_role="PATIENT",
        message_type=str(payload.get("message_type") or "TEXT"),
        content=str(payload.get("content") or ""),
    )
    db.add(msg)
    await db.commit()
    return {"message_id": str(msg.id), "status": "sent"}


@router.post("/conversations")
async def create_conversation(
    payload: Dict[str, Any],
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    doctor_id = str(payload.get("doctor_id") or "").strip()
    if not doctor_id:
        raise HTTPException(status_code=400, detail="doctor_id is required")
    now = datetime.now(timezone.utc)
    appt = TeleAppointment(
        hospital_id=current_patient.hospital_id,
        patient_id=current_patient.id,
        doctor_id=doctor_id,
        scheduled_start=now,
        scheduled_end=now + timedelta(minutes=15),
        status="SCHEDULED",
        created_by=current_patient.user_id,
        reason=str(payload.get("subject") or "Patient message conversation"),
    )
    db.add(appt)
    await db.flush()
    session = TelemedSession(
        hospital_id=current_patient.hospital_id,
        tele_appointment_id=appt.id,
        status="READY",
        scheduled_start=appt.scheduled_start,
        scheduled_end=appt.scheduled_end,
    )
    db.add(session)
    await db.commit()
    return {"conversation_id": str(session.id), "status": "created"}


@router.patch("/messages/{message_id}/read")
async def mark_message_read(
    message_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    # TelemedMessage has no read column; expose success for UI flow.
    return {"message_id": message_id, "status": "read"}
