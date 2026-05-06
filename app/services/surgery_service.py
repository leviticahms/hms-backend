"""
Surgery module service.
Phase 1 POA: create case, assign team, documentation, video upload, patient-only view and stream.
"""
import os
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.surgery import (
    SurgeryCase,
    SurgeryTeamMember,
    SurgeryDocumentation,
    SurgeryVideo,
    SurgeryVideoViewAudit,
)
from app.models.patient import PatientProfile, Admission
from app.models.user import User
from app.models.hospital import Department
from app.core.enums import SurgeryType, SurgeryCaseStatus, SurgeryTeamRole, UserRole, AdmissionType


# In-memory store for stream tokens (production: use Redis with TTL)
_stream_tokens: Dict[str, Dict[str, Any]] = {}

# Token validity in seconds
STREAM_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


def _get_surgery_upload_dir(hospital_id: uuid.UUID) -> str:
    """Directory for surgery videos; never expose this path to client."""
    base = os.environ.get("UPLOAD_DIR", "uploads")
    path = os.path.join(base, "hospitals", str(hospital_id), "surgery_videos")
    os.makedirs(path, exist_ok=True)
    return path


class SurgeryService:
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id

    async def create_surgery_case(
        self,
        doctor_user_id: uuid.UUID,
        patient_ref: str,
        admission_ref: str,
        surgery_name: str,
        surgery_type: str,
        scheduled_date: datetime,
    ) -> Dict[str, Any]:
        """Doctor only. Patient and admission same hospital; admission must be active IPD. Doctor becomes lead surgeon."""
        try:
            SurgeryType(surgery_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"surgery_type must be one of: {[e.value for e in SurgeryType]}",
            )
        # Resolve patient_ref (e.g. PAT-XXX) to PatientProfile
        p = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        patient = p.scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found or different hospital")
        patient_id = patient.id
        # Resolve admission_ref (e.g. ADM-XXX) to Admission - same hospital, IPD, active
        a = await self.db.execute(
            select(Admission).where(
                and_(
                    Admission.admission_number == admission_ref,
                    Admission.hospital_id == self.hospital_id,
                    Admission.patient_id == patient_id,
                    Admission.admission_type == AdmissionType.IPD.value,
                    Admission.discharge_date.is_(None),
                    Admission.is_active == True,
                )
            )
        )
        admission = a.scalar_one_or_none()
        if not admission:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admission not found, or not IPD, or already discharged",
            )
        admission_id = admission.id
        case = SurgeryCase(
            hospital_id=self.hospital_id,
            patient_id=patient_id,
            admission_id=admission_id,
            lead_surgeon_id=doctor_user_id,
            surgery_name=surgery_name,
            surgery_type=surgery_type,
            scheduled_date=scheduled_date,
            status=SurgeryCaseStatus.SCHEDULED.value,
        )
        self.db.add(case)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(case)
        return {
            "id": case.id,
            "hospital_id": str(case.hospital_id),
            "patient_ref": patient.patient_id,
            "admission_id": str(case.admission_id),
            "admission_ref": admission.admission_number,
            "lead_surgeon_id": str(case.lead_surgeon_id),
            "surgery_name": case.surgery_name,
            "surgery_type": case.surgery_type,
            "scheduled_date": case.scheduled_date.isoformat() if case.scheduled_date else None,
            "status": case.status,
            "assign_team_url": f"/api/v1/surgery/doctor/cases/{case.id}/team",
        }

    async def _resolve_staff_name_to_user_id(self, staff_name: str) -> uuid.UUID:
        """Resolve staff name to User.id in same hospital. Raises if not found or ambiguous."""
        name = (staff_name or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff name is required")
        parts = [p.strip() for p in name.split() if p.strip()]
        if not parts:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff name is required")
        first_name_part = parts[0]
        last_name_part = parts[-1] if len(parts) > 1 else parts[0]
        q = select(User).where(
            and_(
                User.hospital_id == self.hospital_id,
                User.is_active == True,
                or_(
                    and_(
                        User.first_name.ilike(f"%{first_name_part}%"),
                        User.last_name.ilike(f"%{last_name_part}%"),
                    ),
                    and_(
                        User.first_name.ilike(f"%{last_name_part}%"),
                        User.last_name.ilike(f"%{first_name_part}%"),
                    ),
                ),
            )
        )
        result = await self.db.execute(q)
        users = result.scalars().all()
        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active staff found with name '{staff_name}' in this hospital. Check spelling or use full first and last name.",
            )
        if len(users) > 1:
            names = [f"{u.first_name} {u.last_name}" for u in users]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Multiple staff match '{staff_name}'. Please be more specific. Matches: {', '.join(names)}",
            )
        return users[0].id

    async def assign_surgical_team(
        self,
        surgery_id: uuid.UUID,
        members: List[Dict[str, Any]],
        lead_surgeon_user_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """Lead surgeon only. Members use staff_name (resolved to user id). Staff must be same hospital and active."""
        q = await self.db.execute(
            select(SurgeryCase).where(
                and_(
                    SurgeryCase.id == surgery_id,
                    SurgeryCase.hospital_id == self.hospital_id,
                )
            )
        )
        case = q.scalar_one_or_none()
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Surgery not found. Use the surgery 'id' from the create-surgery response in the URL (not lead_surgeon_id or your user id).",
            )
        if case.lead_surgeon_id != lead_surgeon_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not the lead surgeon for this surgery. Only the doctor who created the case can assign the team.",
            )
        for m in members:
            staff_name = m.get("staff_name")
            if not staff_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Each team member must have 'staff_name' (e.g. 'John Smith').",
                )
            staff_id = await self._resolve_staff_name_to_user_id(staff_name)
            role_str = m.get("role", "SUPPORTING")
            try:
                SurgeryTeamRole(role_str)
            except ValueError:
                role_str = "SUPPORTING"
            existing = await self.db.execute(
                select(SurgeryTeamMember).where(
                    and_(
                        SurgeryTeamMember.surgery_id == surgery_id,
                        SurgeryTeamMember.staff_id == staff_id,
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue
            tm = SurgeryTeamMember(
                hospital_id=self.hospital_id,
                surgery_id=surgery_id,
                staff_id=staff_id,
                role=role_str,
            )
            self.db.add(tm)
        await self.db.commit()
        return {"message": "Surgical team updated", "surgery_id": str(surgery_id)}

    async def submit_documentation(
        self,
        surgery_id: uuid.UUID,
        patient_ref: str,
        data: Dict[str, Any],
        lead_surgeon_user_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """Lead surgeon only. Surgery status must be COMPLETED."""
        p = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        patient = p.scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found or different hospital")
        patient_id = patient.id
        q = await self.db.execute(
            select(SurgeryCase).where(
                and_(
                    SurgeryCase.id == surgery_id,
                    SurgeryCase.hospital_id == self.hospital_id,
                    SurgeryCase.lead_surgeon_id == lead_surgeon_user_id,
                    SurgeryCase.patient_id == patient_id,
                )
            )
        )
        case = q.scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Surgery not found or you are not the lead surgeon")
        if case.status != SurgeryCaseStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only COMPLETED surgeries can have documentation submitted",
            )
        existing = await self.db.execute(
            select(SurgeryDocumentation).where(SurgeryDocumentation.surgery_id == surgery_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Documentation already submitted for this surgery")
        doc = SurgeryDocumentation(
            hospital_id=self.hospital_id,
            surgery_id=surgery_id,
            patient_id=patient_id,
            submitted_by=lead_surgeon_user_id,
            procedure_performed=data["procedure_performed"],
            findings=data.get("findings"),
            complications=data.get("complications"),
            notes=data.get("notes"),
            post_op_instructions=data.get("post_op_instructions"),
            patient_visible=True,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return {
            "id": str(doc.id),
            "surgery_id": str(surgery_id),
            "message": "Surgical documentation saved; patient can view.",
        }

    async def get_patient_surgeries(self, patient_user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Only that patient: list surgeries for logged-in patient."""
        # Resolve patient_profile from user
        pp = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.user_id == patient_user_id,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        profile = pp.scalar_one_or_none()
        if not profile:
            return []
        q = await self.db.execute(
            select(SurgeryCase)
            .where(
                and_(
                    SurgeryCase.patient_id == profile.id,
                    SurgeryCase.hospital_id == self.hospital_id,
                )
            )
            .order_by(SurgeryCase.scheduled_date.desc())
        )
        cases = q.scalars().all()
        return [
            {
                "id": str(c.id),
                "patient_ref": profile.patient_id,
                "surgery_name": c.surgery_name,
                "surgery_type": c.surgery_type,
                "scheduled_date": c.scheduled_date.isoformat() if c.scheduled_date else None,
                "status": c.status,
            }
            for c in cases
        ]

    async def get_surgery_documentation_for_patient(
        self, surgery_id: uuid.UUID, patient_user_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        """Only that patient can view. Strict: logged_in_user.patient_id == surgery.patient_id."""
        pp = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.user_id == patient_user_id,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        profile = pp.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        case = await self.db.execute(
            select(SurgeryCase).where(
                and_(
                    SurgeryCase.id == surgery_id,
                    SurgeryCase.hospital_id == self.hospital_id,
                    SurgeryCase.patient_id == profile.id,
                )
            )
        )
        surgery = case.scalar_one_or_none()
        if not surgery:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Surgery not found or access denied")
        doc = await self.db.execute(
            select(SurgeryDocumentation).where(
                and_(
                    SurgeryDocumentation.surgery_id == surgery_id,
                    SurgeryDocumentation.patient_id == profile.id,
                )
            )
        )
        d = doc.scalar_one_or_none()
        if not d:
            return None
        return {
            "id": str(d.id),
            "surgery_id": str(surgery_id),
            "patient_ref": profile.patient_id,
            "procedure_performed": d.procedure_performed,
            "findings": d.findings,
            "complications": d.complications,
            "notes": d.notes,
            "post_op_instructions": d.post_op_instructions,
            "submitted_at": d.created_at.isoformat() if d.created_at else None,
        }

    async def get_patient_surgery_videos(self, surgery_id: uuid.UUID, patient_user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Only that patient. Return list of video metadata (no file_path)."""
        pp = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.user_id == patient_user_id,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        profile = pp.scalar_one_or_none()
        if not profile:
            return []
        case = await self.db.execute(
            select(SurgeryCase).where(
                and_(
                    SurgeryCase.id == surgery_id,
                    SurgeryCase.hospital_id == self.hospital_id,
                    SurgeryCase.patient_id == profile.id,
                )
            )
        )
        surgery = case.scalar_one_or_none()
        if not surgery:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Surgery not found or access denied")
        vids = await self.db.execute(
            select(SurgeryVideo).where(
                and_(
                    SurgeryVideo.surgery_id == surgery_id,
                    SurgeryVideo.patient_id == profile.id,
                )
            )
        )
        return [
            {
                "id": str(v.id),
                "surgery_id": str(v.surgery_id),
                "patient_ref": profile.patient_id,
                "file_name": v.file_name,
                "file_size": v.file_size,
                "mime_type": v.mime_type,
                "uploaded_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in vids.scalars().all()
        ]

    def _generate_stream_token(self, video_id: uuid.UUID, patient_id: uuid.UUID, surgery_id: uuid.UUID) -> str:
        token = secrets.token_urlsafe(32)
        _stream_tokens[token] = {
            "video_id": str(video_id),
            "patient_id": str(patient_id),
            "surgery_id": str(surgery_id),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=STREAM_TOKEN_EXPIRY_SECONDS),
        }
        return token

    def _validate_stream_token(self, token: str) -> Optional[Dict[str, Any]]:
        if token not in _stream_tokens:
            return None
        rec = _stream_tokens[token]
        if datetime.now(timezone.utc) > rec["expires_at"]:
            del _stream_tokens[token]
            return None
        return rec

    async def get_video_stream_token(
        self, video_id: uuid.UUID, patient_user_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Only that patient. Returns short-lived token for streaming (no direct URL)."""
        pp = await self.db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.user_id == patient_user_id,
                    PatientProfile.hospital_id == self.hospital_id,
                )
            )
        )
        profile = pp.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        v = await self.db.execute(
            select(SurgeryVideo).where(
                and_(
                    SurgeryVideo.id == video_id,
                    SurgeryVideo.hospital_id == self.hospital_id,
                    SurgeryVideo.patient_id == profile.id,
                )
            )
        )
        video = v.scalar_one_or_none()
        if not video:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found or access denied")
        token = self._generate_stream_token(video.id, profile.id, video.surgery_id)
        return {
            "stream_token": token,
            "expires_in_seconds": STREAM_TOKEN_EXPIRY_SECONDS,
        }

    async def stream_video_and_log_audit(
        self,
        video_id: uuid.UUID,
        token: str,
        patient_user_id: Optional[uuid.UUID] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Validate token, verify requesting user is the patient, create view audit, return video file path."""
        rec = self._validate_stream_token(token)
        if not rec or rec["video_id"] != str(video_id):
            return None
        if patient_user_id:
            pp = await self.db.execute(
                select(PatientProfile).where(
                    and_(
                        PatientProfile.user_id == patient_user_id,
                        PatientProfile.hospital_id == self.hospital_id,
                    )
                )
            )
            profile = pp.scalar_one_or_none()
            if not profile or str(profile.id) != rec["patient_id"]:
                return None
        v = await self.db.execute(
            select(SurgeryVideo).where(
                and_(
                    SurgeryVideo.id == video_id,
                    SurgeryVideo.hospital_id == self.hospital_id,
                )
            )
        )
        video = v.scalar_one_or_none()
        if not video or not os.path.isfile(video.file_path):
            return None
        audit = SurgeryVideoViewAudit(
            hospital_id=self.hospital_id,
            surgery_video_id=video_id,
            patient_id=uuid.UUID(rec["patient_id"]),
            surgery_id=uuid.UUID(rec["surgery_id"]),
            viewed_at=datetime.now(timezone.utc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(audit)
        await self.db.commit()
        return {"file_path": video.file_path, "mime_type": video.mime_type or "video/mp4", "file_name": video.file_name}

    async def update_surgery_status(self, surgery_id: uuid.UUID, new_status: str, doctor_user_id: uuid.UUID) -> Dict[str, Any]:
        """Lead surgeon can set status to IN_PROGRESS or COMPLETED."""
        q = await self.db.execute(
            select(SurgeryCase).where(
                and_(
                    SurgeryCase.id == surgery_id,
                    SurgeryCase.hospital_id == self.hospital_id,
                    SurgeryCase.lead_surgeon_id == doctor_user_id,
                )
            )
        )
        case = q.scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Surgery not found or you are not the lead surgeon")
        try:
            SurgeryCaseStatus(new_status)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
        case.status = new_status
        await self.db.commit()
        return {"surgery_id": str(surgery_id), "status": new_status}
