"""
Telemedicine session API - video sessions and join tokens.
"""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db_session
from app.api.deps import get_current_user, require_hospital_context, require_roles
from app.core.enums import UserRole
from app.models.user import User
from app.models.patient import PatientProfile
from app.schemas.telemed import (
    TelemedSessionCreate,
    TelemedSessionStart,
    TelemedSessionEnd,
    JoinTokenRequest,
    TelemedMessageCreate,
    TelemedFileCreate,
    TelemedNoteCreate,
    TelemedPrescriptionCreate,
)
from app.schemas.response import SuccessResponse
from app.services.telemed_session_service import TelemedSessionService
from app.services.telemed_chat_service import TelemedChatService, TelemedFileService
from app.services.telemed_notes_service import TelemedNotesService
from app.services.telemed_prescription_service import TelemedPrescriptionService

router = APIRouter(prefix="/sessions", tags=["Telemedicine - Sessions"])


def _session_to_response(session) -> dict:
    return {
        "id": str(session.id),
        "hospital_id": str(session.hospital_id),
        "tele_appointment_id": str(session.tele_appointment_id),
        "provider": session.provider,
        "room_name": session.room_name,
        "status": session.status,
        "scheduled_start": session.scheduled_start.isoformat() if session.scheduled_start else None,
        "scheduled_end": session.scheduled_end.isoformat() if session.scheduled_end else None,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "recording_enabled": session.recording_enabled,
        "recording_status": session.recording_status,
        "duration_seconds": session.duration_seconds,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.post("", response_model=dict)
async def create_session(
    body: TelemedSessionCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(require_roles(UserRole.RECEPTIONIST, UserRole.HOSPITAL_ADMIN, UserRole.DOCTOR)),
    db: AsyncSession = Depends(get_db_session),
):
    """Create video session for tele-appointment. RECEPTIONIST, HOSPITAL_ADMIN, DOCTOR only. Lazy-create; idempotent if exists."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedSessionService(db)
    session = await service.create(
        hospital_id=hospital_id,
        tele_appointment_id=uuid.UUID(body.tele_appointment_id),
        provider=body.provider,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Session created",
        data=_session_to_response(session),
    ).dict()


@router.get("", response_model=dict)
async def list_sessions(
    doctor_id: Optional[str] = Query(None),
    patient_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List sessions. Role-filtered: doctor=assigned, patient=own, receptionist=all."""
    hospital_id = uuid.UUID(context["hospital_id"])
    user_roles = [r.name for r in (current_user.roles or [])]
    service = TelemedSessionService(db)

    doc_id = uuid.UUID(doctor_id) if doctor_id else None
    pat_id = uuid.UUID(patient_id) if patient_id else None

    if "PATIENT" in user_roles and not pat_id:
        patient_result = await db.execute(
            select(PatientProfile).where(PatientProfile.user_id == current_user.id)
        )
        patient = patient_result.scalar_one_or_none()
        if patient:
            pat_id = patient.id
    if "DOCTOR" in user_roles and not doc_id:
        doc_id = current_user.id

    items = await service.list_for_hospital(hospital_id, doctor_id=doc_id, patient_id=pat_id, status_filter=status)
    return SuccessResponse(
        success=True,
        message="Sessions retrieved",
        data={"items": [_session_to_response(i) for i in items], "total": len(items)},
    ).dict()


@router.get("/{session_id}", response_model=dict)
async def get_session(
    session_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get session by ID. Participant access only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    user_roles = [r.name for r in (current_user.roles or [])]
    service = TelemedSessionService(db)
    session = await service.get_by_id(uuid.UUID(session_id), hospital_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    is_doctor = session.tele_appointment.doctor_id == current_user.id
    is_patient = False
    if "PATIENT" in user_roles:
        patient_result = await db.execute(
            select(PatientProfile).where(PatientProfile.user_id == current_user.id)
        )
        patient = patient_result.scalar_one_or_none()
        is_patient = patient and session.tele_appointment.patient_id == patient.id
    is_staff = "RECEPTIONIST" in user_roles or "HOSPITAL_ADMIN" in user_roles
    if not is_doctor and not is_patient and not is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return SuccessResponse(
        success=True,
        message="Session retrieved",
        data=_session_to_response(session),
    ).dict()


@router.post("/{session_id}/start", response_model=dict)
async def start_session(
    session_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Start session. Doctor only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedSessionService(db)
    session = await service.start(uuid.UUID(session_id), hospital_id, current_user.id)
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Session started",
        data=_session_to_response(session),
    ).dict()


@router.post("/{session_id}/end", response_model=dict)
async def end_session(
    session_id: str,
    body: TelemedSessionEnd = None,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """End session. Doctor only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedSessionService(db)
    end_reason = body.end_reason if body else "COMPLETED"
    session = await service.end(uuid.UUID(session_id), hospital_id, current_user.id, end_reason)
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Session ended",
        data=_session_to_response(session),
    ).dict()


@router.post("/{session_id}/join-token", response_model=dict)
async def get_join_token(
    session_id: str,
    body: JoinTokenRequest = None,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get join token for video call. Doctor or patient only, within join window."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedSessionService(db)
    device_type = body.device_type if body else "WEB"
    result = await service.generate_join_token(
        uuid.UUID(session_id),
        hospital_id,
        current_user.id,
        device_type,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Join token generated",
        data={
            "provider": result["provider"],
            "room_name": result["room_name"],
            "token": result["token"],
            "expires_at": result["expires_at"].isoformat(),
            "session_id": result["session_id"],
        },
    ).dict()


@router.post("/{session_id}/refresh-token", response_model=dict)
async def refresh_join_token(
    session_id: str,
    body: JoinTokenRequest = None,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Refresh join token. Same as join-token (generates new token)."""
    return await get_join_token(session_id, body, context, current_user, db)


# --- Chat / Messages (participants only) ---

@router.get("/{session_id}/messages", response_model=dict)
async def list_messages(
    session_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List chat messages. Doctor or patient (participants) only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedChatService(db)
    items = await service.list_messages(hospital_id, uuid.UUID(session_id), current_user.id)
    return SuccessResponse(
        success=True,
        message="Messages retrieved",
        data={
            "items": [
                {
                    "id": str(m.id),
                    "session_id": str(m.session_id),
                    "sender_id": str(m.sender_id),
                    "sender_role": m.sender_role,
                    "message_type": m.message_type,
                    "content": m.content,
                    "file_ref": m.file_ref,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in items
            ],
            "total": len(items),
        },
    ).dict()


@router.post("/{session_id}/messages", response_model=dict)
async def send_message(
    session_id: str,
    body: TelemedMessageCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Send chat message. Participants only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedChatService(db)
    msg = await service.send_message(
        hospital_id,
        uuid.UUID(session_id),
        current_user.id,
        message_type=body.message_type,
        content=body.content,
        file_ref=body.file_ref,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Message sent",
        data={
            "id": str(msg.id),
            "session_id": str(msg.session_id),
            "sender_id": str(msg.sender_id),
            "sender_role": msg.sender_role,
            "message_type": msg.message_type,
            "content": msg.content,
            "file_ref": msg.file_ref,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        },
    ).dict()


# --- Files (participants only) ---

@router.get("/{session_id}/files", response_model=dict)
async def list_files(
    session_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List shared files. Participants only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedFileService(db)
    items = await service.list_files(hospital_id, uuid.UUID(session_id), current_user.id)
    return SuccessResponse(
        success=True,
        message="Files retrieved",
        data={
            "items": [
                {
                    "id": str(f.id),
                    "session_id": str(f.session_id),
                    "uploaded_by": str(f.uploaded_by),
                    "file_name": f.file_name,
                    "mime_type": f.mime_type,
                    "size_bytes": f.size_bytes,
                    "storage_url": f.storage_url,
                    "checksum": f.checksum,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in items
            ],
            "total": len(items),
        },
    ).dict()


@router.post("/{session_id}/files", response_model=dict)
async def register_file(
    session_id: str,
    body: TelemedFileCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Register shared file metadata. Participants only. Upload to storage separately."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedFileService(db)
    f = await service.register_file(
        hospital_id,
        uuid.UUID(session_id),
        current_user.id,
        file_name=body.file_name,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        storage_url=body.storage_url,
        checksum=body.checksum,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="File registered",
        data={
            "id": str(f.id),
            "session_id": str(f.session_id),
            "uploaded_by": str(f.uploaded_by),
            "file_name": f.file_name,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
            "storage_url": f.storage_url,
            "checksum": f.checksum,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        },
    ).dict()


# --- Consultation notes (doctor only) ---

@router.get("/{session_id}/notes", response_model=dict)
async def list_notes(
    session_id: str,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List SOAP notes. Doctor only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedNotesService(db)
    items = await service.list_notes(hospital_id, uuid.UUID(session_id), current_user.id)
    return SuccessResponse(
        success=True,
        message="Notes retrieved",
        data={
            "items": [
                {
                    "id": str(n.id),
                    "session_id": str(n.session_id),
                    "doctor_id": str(n.doctor_id),
                    "soap_json": n.soap_json,
                    "soap_text": n.soap_text,
                    "version": n.version,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                    "updated_at": n.updated_at.isoformat() if n.updated_at else None,
                }
                for n in items
            ],
            "total": len(items),
        },
    ).dict()


@router.post("/{session_id}/notes", response_model=dict)
async def create_note(
    session_id: str,
    body: TelemedNoteCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create SOAP note. Doctor only."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedNotesService(db)
    note = await service.create_note(
        hospital_id,
        uuid.UUID(session_id),
        current_user.id,
        soap_json=body.soap_json,
        soap_text=body.soap_text,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Note created",
        data={
            "id": str(note.id),
            "session_id": str(note.session_id),
            "doctor_id": str(note.doctor_id),
            "soap_json": note.soap_json,
            "soap_text": note.soap_text,
            "version": note.version,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        },
    ).dict()


# --- Prescription (doctor only) ---

# Disabled in API registration: prescriptions are exposed only through Doctor Portal routes.
async def create_prescription(
    session_id: str,
    body: TelemedPrescriptionCreate,
    context: dict = Depends(require_hospital_context),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create prescription for session (telemed only; no pharmacy DB). Doctor only. Optionally include medicine lines."""
    hospital_id = uuid.UUID(context["hospital_id"])
    service = TelemedPrescriptionService(db)
    medicines = [m.dict() for m in body.medicines] if body.medicines else None
    rx = await service.create_for_session(
        hospital_id,
        uuid.UUID(session_id),
        current_user.id,
        diagnosis=body.diagnosis,
        clinical_notes=body.clinical_notes,
        follow_up_date=body.follow_up_date,
        medicines=medicines,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        message="Prescription created",
        data={
            "id": str(rx.id),
            "prescription_no": rx.prescription_no,
            "session_id": str(rx.session_id) if rx.session_id else None,
            "tele_appointment_id": str(rx.tele_appointment_id) if rx.tele_appointment_id else None,
            "doctor_id": str(rx.doctor_id),
            "patient_id": str(rx.patient_id),
            "diagnosis": rx.diagnosis,
            "clinical_notes": rx.clinical_notes,
            "follow_up_date": rx.follow_up_date,
            "status": rx.status,
            "created_at": rx.created_at.isoformat() if rx.created_at else None,
            "updated_at": rx.updated_at.isoformat() if rx.updated_at else None,
        },
    ).dict()
