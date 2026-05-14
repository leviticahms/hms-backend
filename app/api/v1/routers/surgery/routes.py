"""
Surgery Module API - Phase 1 POA.
Doctor: create case, assign team, upload documentation, update status.
Head Nurse (OT): upload surgery video.
Patient: view own surgery docs and videos (stream with token + audit).
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.database.session import get_db_session
from app.api.deps import require_roles
from app.core.enums import UserRole
from app.models.user import User
from app.schemas.surgery import (
    SurgeryCaseCreate,
    SurgeryTeamAssignRequest,
    SurgeryDocumentationCreate,
    SurgeryCaseResponse,
    SurgeryDocumentationResponse,
    SurgeryVideoResponse,
    SurgeryVideoStreamTokenResponse,
)
from app.services.surgery_service import SurgeryService

from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/surgery", tags=["Surgery - Phase 1 POA"])


def _hospital_id_from_user(user: User) -> uuid.UUID:
    if not user.hospital_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hospital context required")
    return user.hospital_id


# =============================================================================
# STEP 1 — Create Surgery Case (Doctor only)
# =============================================================================

@router.post("/doctor/cases", response_model=dict)
async def create_surgery_case(
    body: SurgeryCaseCreate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Register a surgery for a patient.
    
    Access Control:
    - **Who can access:** Doctors only (becomes Lead Surgeon; patient must have active IPD admission)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    return await service.create_surgery_case(
        doctor_user_id=current_user.id,
        patient_ref=body.patient_ref,
        admission_ref=body.admission_ref,
        surgery_name=body.surgery_name,
        surgery_type=body.surgery_type,
        scheduled_date=body.scheduled_date,
    )


# =============================================================================
# STEP 2 — Assign Surgical Team (Lead Surgeon only)
# =============================================================================

@router.post("/doctor/cases/{surgery_id}/team", response_model=dict)
async def assign_surgical_team(
    surgery_id: uuid.UUID,
    body: SurgeryTeamAssignRequest,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Assign assistant doctors, anesthesiologist, supporting staff.
    
    Access Control:
    - **Who can access:** Doctors only (Lead Surgeon of this surgery)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    members = [{"staff_name": m.staff_name, "role": m.role} for m in body.members]
    return await service.assign_surgical_team(surgery_id, members, current_user.id)


# =============================================================================
# Update surgery status (Lead Surgeon) — e.g. COMPLETED before documentation
# =============================================================================

@router.patch("/doctor/cases/{surgery_id}/status", response_model=dict)
async def update_surgery_status(
    surgery_id: uuid.UUID,
    status: str = Query(..., description="IN_PROGRESS or COMPLETED"),
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Set surgery status to IN_PROGRESS or COMPLETED.
    
    Access Control:
    - **Who can access:** Doctors only (Lead Surgeon)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    return await service.update_surgery_status(surgery_id, status, current_user.id)


# =============================================================================
# STEP 3 — Upload Surgery Documentation (Lead Surgeon only)
# =============================================================================

@router.post("/doctor/documentation", response_model=dict)
async def upload_surgery_documentation(
    body: SurgeryDocumentationCreate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Submit operative report after surgery.
    
    Access Control:
    - **Who can access:** Doctors only (Lead Surgeon; surgery must be COMPLETED)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    data = {
        "procedure_performed": body.procedure_performed,
        "findings": body.findings,
        "complications": body.complications,
        "notes": body.notes,
        "post_op_instructions": body.post_op_instructions,
    }
    return await service.submit_documentation(
        surgery_id=body.surgery_id,
        patient_ref=body.patient_ref,
        data=data,
        lead_surgeon_user_id=current_user.id,
    )


# =============================================================================
# STEP 5 & 6 — Patient: view documentation, list videos, get stream token, stream (with audit)
# =============================================================================

@router.get("/patient/cases", response_model=List[dict])
async def patient_list_my_surgeries(
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List surgeries for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own surgeries from JWT token)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    return await service.get_patient_surgeries(current_user.id)


@router.get("/patient/cases/{surgery_id}/documentation", response_model=dict)
async def patient_get_surgery_documentation(
    surgery_id: uuid.UUID,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get operative documentation for a surgery.
    
    Access Control:
    - **Who can access:** Patients only (own surgery from JWT token)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    result = await service.get_surgery_documentation_for_patient(surgery_id, current_user.id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documentation not found")
    return result


@router.get("/patient/cases/{surgery_id}/videos", response_model=List[dict])
async def patient_list_surgery_videos(
    surgery_id: uuid.UUID,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List video metadata for a surgery.
    
    Access Control:
    - **Who can access:** Patients only (own surgery from JWT token)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    return await service.get_patient_surgery_videos(surgery_id, current_user.id)


@router.get("/patient/videos/{video_id}/stream-token", response_model=SurgeryVideoStreamTokenResponse)
async def patient_get_video_stream_token(
    video_id: uuid.UUID,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get a short-lived token to stream the surgery video.
    
    Access Control:
    - **Who can access:** Patients only (own video from JWT token)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    return await service.get_video_stream_token(video_id, current_user.id)


@router.get("/patient/videos/{video_id}/stream")
async def patient_stream_surgery_video(
    video_id: uuid.UUID,
    token: str = Query(..., description="Short-lived stream token"),
    request: Request = None,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Stream surgery video (requires token from stream-token endpoint).
    
    Access Control:
    - **Who can access:** Patients only (own video from JWT token + valid stream token)
    """
    hospital_id = _hospital_id_from_user(current_user)
    service = SurgeryService(db, hospital_id)
    info = await service.stream_video_and_log_audit(
        video_id,
        token,
        patient_user_id=current_user.id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    if not info:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired token")
    return FileResponse(
        path=info["file_path"],
        filename=info["file_name"],
        media_type=info["mime_type"],
    )


