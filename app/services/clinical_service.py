"""
Clinical Operations Service
Handles OPD, IPD, and nursing management business logic.
"""
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.user import User, Role, user_roles
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission
from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.receptionist import ReceptionistProfile
from app.models.tenant import Hospital
from app.core.enums import UserRole, AppointmentStatus, UserStatus
from app.core.utils import generate_patient_ref, generate_appointment_ref, parse_time_string
from app.core.security import SecurityManager
from app.utils.receptionist_serializers import (
    build_receptionist_patient_full_payload,
    serialize_opd_appointment_full,
)
from app.services.patient_tenant_bridge import resolve_patient_profile_id_for_tenant

logger = logging.getLogger(__name__)


def _normalize_opd_gender(g: Optional[str]) -> Optional[str]:
    if not g:
        return None
    x = g.strip().upper()
    if x in ("MALE", "M", "MAN"):
        return "MALE"
    if x in ("FEMALE", "F", "WOMAN"):
        return "FEMALE"
    if x in ("OTHER", "O"):
        return "OTHER"
    if x in ("MALE", "FEMALE", "OTHER"):
        return x
    return "OTHER"


def _appointment_time_to_db_hms(raw: Any) -> str:
    """Store appointment time as HH:MM:SS (8 chars) — DB column may be VARCHAR(8)."""
    if raw is None:
        raise ValueError("appointment_time is required")
    t = parse_time_string(str(raw))
    return t.strftime("%H:%M:%S")


def _normalize_opd_appointment_type(raw: Any) -> str:
    """Map UI labels (e.g. Regular) to stored appointment_type values."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "CONSULTATION"
    s = str(raw).strip().upper()
    if s in ("REGULAR", "ROUTINE", "CONSULT", "CONSULTATION", "OPD"):
        return "CONSULTATION"
    if s in ("FOLLOW_UP", "FOLLOWUP", "FOLLOW-UP", "REVIEW"):
        return "FOLLOW_UP"
    if s in ("EMERGENCY", "ER", "URGENT"):
        return "EMERGENCY"
    return s[:50]


def _normalize_opd_appointment_status(raw: Any) -> Optional[str]:
    """Map receptionist UI labels to appointment status values stored in the DB."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    s = str(raw).strip().upper()
    if s in ("SCHEDULED", "BOOKED", "CONFIRM", "CONFIRMED"):
        return AppointmentStatus.CONFIRMED.value
    if s in ("PENDING", "REQUEST", "REQUESTED"):
        return AppointmentStatus.REQUESTED.value
    if s in ("CANCEL", "CANCELED", "CANCELLED"):
        return AppointmentStatus.CANCELLED.value
    if s in ("DONE", "COMPLETE", "COMPLETED"):
        return AppointmentStatus.COMPLETED.value
    return s


def _normalize_opd_blood_group(bg: Optional[str]) -> Optional[str]:
    if not bg:
        return None
    s = bg.strip().upper()
    allowed = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "OTHER"}
    return s if s in allowed else s


async def send_opd_portal_credentials_email_task(
    email_norm: str,
    first_name: str,
    password_plain: str,
    hospital_name: Optional[str],
) -> None:
    """
    Background task: send portal login email after receptionist registration.
    Keeps HTTP responses fast (SMTP can take several seconds).
    """
    try:
        from app.services.email_service import EmailService

        es = EmailService()
        if not es.is_smtp_configured():
            logger.warning(
                "Portal credentials not emailed (background): SMTP not configured for %s",
                email_norm,
            )
            return
        sent = await es.send_patient_portal_credentials_email(
            to_email=email_norm,
            first_name=first_name,
            login_email=email_norm,
            password_plain=password_plain,
            hospital_name=hospital_name,
        )
        if not sent:
            err = getattr(es, "last_error", None) or "unknown SMTP error"
            logger.warning(
                "Portal credentials email failed after retries (background) for %s — %s",
                email_norm,
                err,
            )
    except Exception:
        logger.exception("Unexpected error sending portal credentials (background) to %s", email_norm)


class ClinicalService:
    """Service for clinical operations (OPD, IPD, Nursing)"""

    def __init__(self, db: AsyncSession, platform_db: Optional[AsyncSession] = None):
        self.db = db
        self.platform_db = platform_db
        self.security = SecurityManager()
    
    # ============================================================================
    # USER CONTEXT AND VALIDATION
    # ============================================================================
    
    def get_user_context(self, current_user: User) -> dict:
        """Extract user context from JWT token"""
        user_roles = [role.name for role in current_user.roles]
        
        return {
            "user_id": current_user.id,  # Keep as UUID for database operations
            "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
            "role": user_roles[0] if user_roles else None,
            "all_roles": user_roles,
            # Keep authenticated principal as fallback when tenant session cannot resolve User row.
            "current_user": current_user,
        }
    
    async def validate_receptionist_access(self, user_context: dict) -> None:
        """Ensure user is a receptionist"""
        if user_context["role"] != UserRole.RECEPTIONIST:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Receptionist role required"
            )
    
    async def validate_nurse_access(self, user_context: dict) -> None:
        """Ensure user is a nurse"""
        if user_context["role"] != UserRole.NURSE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Nurse role required"
            )
    
    async def validate_doctor_access(self, user_context: dict) -> None:
        """Ensure user is a doctor"""
        if user_context["role"] != UserRole.DOCTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Doctor role required"
            )
    
    async def validate_ipd_access(self, user_context: dict) -> None:
        """Ensure user has IPD access (Nurse or Doctor)"""
        if user_context["role"] not in [UserRole.NURSE, UserRole.DOCTOR]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - IPD operations require Nurse or Doctor role"
            )
    
    # ============================================================================
    # PROFILE MANAGEMENT
    # ============================================================================
    
    async def get_receptionist_profile(self, user_context: dict):
        """Get receptionist profile with department information"""
        await self.validate_receptionist_access(user_context)
        
        # Get receptionist user and their department assignment
        receptionist_result = await self.db.execute(
            select(User)
            .where(User.id == user_context["user_id"])
        )
        receptionist_user = receptionist_result.scalar_one_or_none()
        if not receptionist_user:
            fallback_user = user_context.get("current_user")
            if fallback_user and str(getattr(fallback_user, "id", "")) == str(user_context.get("user_id")):
                receptionist_user = fallback_user
        
        if not receptionist_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Receptionist user not found. Please contact administrator."
            )
            
        # Get department assignment
        assignment_result = await self.db.execute(
            select(StaffDepartmentAssignment)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == user_context["user_id"],
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = assignment_result.scalar_one_or_none()

        # Legacy/fallback compatibility:
        # some setups have ReceptionistProfile metadata but no StaffDepartmentAssignment row
        # in the current routed DB.
        if not assignment:
            rp_result = await self.db.execute(
                select(ReceptionistProfile)
                .where(
                    and_(
                        ReceptionistProfile.user_id == user_context["user_id"],
                        ReceptionistProfile.hospital_id == receptionist_user.hospital_id,
                    )
                )
                .options(selectinload(ReceptionistProfile.department))
            )
            rp = rp_result.scalar_one_or_none()
            if rp and rp.department:
                class _AssignmentLike:
                    def __init__(self, department):
                        self.department = department
                assignment = _AssignmentLike(rp.department)

        if not assignment:
            md = getattr(receptionist_user, "user_metadata", {}) or {}
            md_name = (md.get("department_name") or "").strip()
            md_id_raw = md.get("department_id")
            if md_name:
                dept_obj = None
                if md_id_raw:
                    try:
                        md_id = uuid.UUID(str(md_id_raw))
                        dres = await self.db.execute(
                            select(Department).where(Department.id == md_id)
                        )
                        dept_obj = dres.scalar_one_or_none()
                    except Exception:
                        dept_obj = None
                if not dept_obj:
                    class _DepartmentLike:
                        def __init__(self, did, name):
                            self.id = did
                            self.name = name
                    dept_obj = _DepartmentLike(str(md_id_raw) if md_id_raw else None, md_name)

                class _AssignmentLike:
                    def __init__(self, department):
                        self.department = department
                assignment = _AssignmentLike(dept_obj)

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Receptionist department assignment not found. Please contact administrator."
            )
            
        # Create a mock object that has the same interface as the old ReceptionistProfile
        class MockReceptionistProfile:
            def __init__(self, user, department):
                self.user = user
                self.department = department
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                # Add commonly used attributes with default values
                self.work_area = "OPD"
                self.designation = "Receptionist"
                self.can_schedule_appointments = True
                self.can_modify_appointments = True
                self.can_register_patients = True
                self.can_collect_payments = False
        
        return MockReceptionistProfile(receptionist_user, assignment.department)
    
    # ============================================================================
    # OPD PATIENT REGISTRATION
    # ============================================================================
    
    async def register_opd_patient(self, patient_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Register new patient for OPD services"""
        user_context = self.get_user_context(current_user)
        receptionist = await self.get_receptionist_profile(user_context)
        
        hospital_id_str = user_context.get("hospital_id")
        if not hospital_id_str:
            from app.utils.hospital_id_resolve import resolve_effective_hospital_id

            resolved = await resolve_effective_hospital_id(self.db, current_user)
            if resolved:
                hospital_id_str = str(resolved)
        if not hospital_id_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hospital ID is required. Receptionist must be associated with a hospital.",
            )
        try:
            hospital_id_uuid = uuid.UUID(hospital_id_str) if isinstance(hospital_id_str, str) else hospital_id_str
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid hospital_id in user context.",
            )

        phone_norm = (patient_data.get("phone") or "").strip()
        if not phone_norm:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="phone is required",
            )
        
        # Check if phone already exists
        existing_phone = await self.db.execute(
            select(User).where(and_(User.phone == phone_norm, User.hospital_id == hospital_id_uuid))
        )
        if existing_phone.first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Patient with this phone number already exists"
            )
        
        # Check if email already exists (if provided)
        email_norm = (patient_data.get("email") or "").strip().lower() if patient_data.get("email") else None
        if email_norm:
            existing_email = await self.db.execute(
                select(User).where(and_(User.email == email_norm, User.hospital_id == hospital_id_uuid))
            )
            if existing_email.first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Patient with this email already exists"
                )
        
        # Generate patient reference
        patient_ref = generate_patient_ref()
        
        portal_password = (patient_data.get("password") or "").strip() or None
        temp_password: Optional[str] = None
        if portal_password and not email_norm:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required when setting a password for patient portal login.",
            )
        if portal_password:
            from app.services.auth_service import PasswordValidator

            pwd_check = PasswordValidator.validate_password(
                portal_password,
                email_norm or "",
                patient_data.get("phone", "") or "",
            )
            if not pwd_check["valid"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "PWD_001",
                        "message": "Password does not meet security requirements",
                        "errors": pwd_check["errors"],
                    },
                )
            password_hash = self.security.hash_password(portal_password)
            email_verified = True
        else:
            temp_password = self.security.generate_temp_password()
            password_hash = self.security.hash_password(temp_password)
            email_verified = False
        
        user = User(
            id=uuid.uuid4(),
            hospital_id=hospital_id_uuid,
            email=email_norm,
            phone=phone_norm,
            password_hash=password_hash,
            first_name=patient_data["first_name"],
            last_name=patient_data["last_name"],
            status=UserStatus.ACTIVE,
            email_verified=email_verified,
            phone_verified=False
        )
        
        # Add user to database first
        self.db.add(user)
        await self.db.flush()  # Flush to get the user ID
        
        # Assign PATIENT role (must succeed or patient portal login fails with AUTH_002).
        role_result = await self.db.execute(
            select(Role).where(Role.name == UserRole.PATIENT.value)
        )
        role = role_result.scalar_one_or_none()
        if not role:
            role = Role(
                id=uuid.uuid4(),
                name=UserRole.PATIENT.value,
                display_name="Patient",
                description="Patient Role",
                level=10,
            )
            self.db.add(role)
            await self.db.flush()

        await self.db.execute(
            user_roles.insert().values(
                user_id=user.id,
                role_id=role.id,
            )
        )
        
        bg_raw = _normalize_opd_blood_group(patient_data.get("blood_group"))
        bg_val = (patient_data.get("blood_group_value") or "").strip() or None
        if bg_raw == "OTHER" and not bg_val:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="blood_group_value is required when blood_group is OTHER",
            )

        # Create PatientProfile
        patient_profile = PatientProfile(
            id=uuid.uuid4(),
            hospital_id=hospital_id_uuid,
            user_id=user.id,
            patient_id=patient_ref,
            date_of_birth=patient_data.get("date_of_birth"),
            gender=_normalize_opd_gender(patient_data.get("gender")),
            blood_group=bg_raw,
            blood_group_value=bg_val if bg_raw == "OTHER" else None,
            id_type=(patient_data.get("id_type") or "").strip() or None,
            id_number=(patient_data.get("id_number") or "").strip() or None,
            id_name=(patient_data.get("id_name") or "").strip() or None,
            address=patient_data.get("address"),
            city=patient_data.get("city"),
            district=(patient_data.get("district") or "").strip() or None,
            state=(patient_data.get("state") or "").strip() or None,
            country=(patient_data.get("country") or "").strip() or None,
            pincode=(patient_data.get("pincode") or "").strip() or None,
            medical_history=(patient_data.get("medical_history") or "").strip() or None,
            emergency_contact_name=patient_data.get("emergency_contact_name"),
            emergency_contact_phone=patient_data.get("emergency_contact_phone"),
            emergency_contact_relation=patient_data.get("emergency_contact_relation"),
        )
        
        self.db.add(patient_profile)
        await self.db.flush()
        
        hospital_name = None
        try:
            hospital_result = await self.db.execute(
                select(Hospital).where(Hospital.id == hospital_id_uuid)
            )
            hospital = hospital_result.scalar_one_or_none()
            if hospital:
                hospital_name = hospital.name
        except Exception:
            hospital_name = None

        await self.db.commit()

        result = {
            "patient_ref": patient_ref,
            "patient_name": f"{patient_data['first_name']} {patient_data['last_name']}",
            "phone": patient_data["phone"],
            "email": email_norm,
            "registered_by": f"{current_user.first_name} {current_user.last_name} (Receptionist)",
            "registration_date": datetime.utcnow().isoformat(),
            "message": "Patient registered successfully for OPD services",
        }
        if portal_password:
            result["portal_login_enabled"] = True
            result[
                "message"
            ] = "Patient registered. They can sign in with POST /api/v1/auth/patient/login using this email and password."
        else:
            result["temp_password"] = temp_password
            result["portal_login_enabled"] = False
        if hospital_id_str:
            result["hospital_id"] = hospital_id_str
        if hospital_name:
            result["hospital_name"] = hospital_name

        send_credentials = patient_data.get("send_credentials_email", True)
        if portal_password and email_norm:
            result["credentials_email_sent"] = False
            result["credentials_email_queued"] = False
            result["send_credentials_email_requested"] = bool(send_credentials)
            if not send_credentials:
                result["credentials_email_hint"] = (
                    "Email send skipped (send_credentials_email=false). Share login email and password with the patient manually."
                )
            else:
                result["credentials_email_hint"] = (
                    "Credentials email is queued to send in the background after this response. "
                    "If SMTP is not configured, check server logs for warnings."
                )

        return result

    async def patch_opd_patient(
        self,
        patient_ref: str,
        updates: Dict[str, Any],
        *,
        new_password_plain: Optional[str],
        send_credentials_email: bool,
        current_user: User,
    ) -> Dict[str, Any]:
        """Apply partial updates to an OPD patient in the receptionist's hospital."""
        user_context = self.get_user_context(current_user)
        await self.validate_receptionist_access(user_context)

        hospital_id_str = user_context.get("hospital_id")
        if not hospital_id_str:
            from app.utils.hospital_id_resolve import resolve_effective_hospital_id

            resolved = await resolve_effective_hospital_id(self.db, current_user)
            if resolved:
                hospital_id_str = str(resolved)
        if not hospital_id_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hospital ID is required. Receptionist must be associated with a hospital.",
            )

        try:
            hospital_id_uuid = uuid.UUID(hospital_id_str) if isinstance(hospital_id_str, str) else hospital_id_str
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid hospital_id in user context.",
            )

        pr = (patient_ref or "").strip()
        if not pr:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_ref is required")

        payload_keys = {k for k in updates.keys()}
        if not payload_keys and not (new_password_plain and str(new_password_plain).strip()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update. Send at least one profile field or password.",
            )

        result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == pr,
                    PatientProfile.hospital_id == hospital_id_uuid,
                )
            )
            .options(selectinload(PatientProfile.user).selectinload(User.roles))
        )
        profile = result.scalar_one_or_none()
        if not profile or not profile.user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient '{pr}' not found for this hospital.",
            )

        pu = profile.user
        role_names = [getattr(r, "name", "") for r in (pu.roles or [])]
        if UserRole.PATIENT.value not in role_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is not a patient record.",
            )

        email_norm: Optional[str] = None

        phone_norm = updates.get("phone", None)
        if phone_norm is not None:
            phone_norm = str(phone_norm).strip()
            if not phone_norm:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="phone cannot be empty")
            dup_phone = await self.db.execute(
                select(User.id).where(
                    and_(
                        User.phone == phone_norm,
                        User.hospital_id == hospital_id_uuid,
                        User.id != pu.id,
                    )
                )
            )
            if dup_phone.first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another patient already uses this phone number at this hospital.",
                )
            pu.phone = phone_norm

        if "email" in updates:
            raw_em = updates.get("email")
            if raw_em is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="email cannot be removed; omit the field to leave unchanged.",
                )
            email_norm = str(raw_em).strip().lower()
            dup_em = await self.db.execute(
                select(User.id).where(
                    and_(
                        User.email == email_norm,
                        User.hospital_id == hospital_id_uuid,
                        User.id != pu.id,
                    )
                )
            )
            if dup_em.first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another user already uses this email at this hospital.",
                )
            pu.email = email_norm

        if "first_name" in updates:
            fn = str(updates["first_name"] or "").strip()
            if not fn:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="first_name cannot be empty")
            pu.first_name = fn

        if "last_name" in updates:
            ln = str(updates["last_name"] or "").strip()
            if not ln:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="last_name cannot be empty")
            pu.last_name = ln

        if new_password_plain and str(new_password_plain).strip():
            pw = str(new_password_plain).strip()
            eff_email = (
                email_norm
                if email_norm is not None
                else (pu.email or "").strip().lower()
            )
            if not eff_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="email is required on the patient account before setting a portal password",
                )
            from app.services.auth_service import PasswordValidator

            pwd_check = PasswordValidator.validate_password(pw, eff_email, pu.phone or "")
            if not pwd_check["valid"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "PWD_001",
                        "message": "Password does not meet security requirements",
                        "errors": pwd_check["errors"],
                    },
                )
            pu.password_hash = self.security.hash_password(pw)
            pu.email_verified = True

        profile_field_map = {
            "date_of_birth": "date_of_birth",
            "gender": "gender",
            "address": "address",
            "pincode": "pincode",
            "city": "city",
            "district": "district",
            "state": "state",
            "country": "country",
            "id_type": "id_type",
            "id_number": "id_number",
            "id_name": "id_name",
            "emergency_contact_name": "emergency_contact_name",
            "emergency_contact_phone": "emergency_contact_phone",
            "emergency_contact_relation": "emergency_contact_relation",
            "medical_history": "medical_history",
            "blood_group": "blood_group",
            "blood_group_value": "blood_group_value",
        }
        for key, col in profile_field_map.items():
            if key not in updates:
                continue
            val = updates[key]
            if key == "gender":
                setattr(profile, col, _normalize_opd_gender(val))
            elif key == "blood_group":
                setattr(profile, col, _normalize_opd_blood_group(val))
            else:
                setattr(profile, col, val)

        eff_bg = profile.blood_group
        eff_bg_val = profile.blood_group_value
        if eff_bg == "OTHER" and not (eff_bg_val or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="blood_group_value is required when blood_group is OTHER",
            )

        eff_id_type = (profile.id_type or "").strip().upper()
        if eff_id_type == "OTHER" and not (profile.id_name or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="id_name is required when id_type is Other",
            )

        hospital_name = None
        try:
            hops = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id_uuid))
            hrow = hops.scalar_one_or_none()
            if hrow:
                hospital_name = hrow.name
        except Exception:
            hospital_name = None

        await self.db.commit()
        await self.db.refresh(profile)
        await self.db.refresh(pu)

        out = self._receptionist_patient_detail_dict(profile)
        out["patient_ref"] = profile.patient_id
        out["hospital_id"] = hospital_id_str
        out["hospital_name"] = hospital_name
        out["portal_password_updated"] = bool(new_password_plain and str(new_password_plain).strip())
        login_email_norm = (pu.email or "").strip().lower()
        out["send_credentials_email_requested"] = bool(
            send_credentials_email and out["portal_password_updated"] and bool(login_email_norm)
        )
        if out["portal_password_updated"] and login_email_norm:
            out["credentials_email_hint"] = (
                "If SMTP is configured, credentials email was queued from the API layer."
                if send_credentials_email
                else "Credential email skipped (send_credentials_email=false)."
            )
        return out

    def _receptionist_patient_detail_dict(self, patient: PatientProfile) -> Dict[str, Any]:
        """Serialize patient + user for receptionist GET (full DB fields; password_hash never returned)."""
        return build_receptionist_patient_full_payload(patient)

    async def get_receptionist_patient_by_ref(self, patient_ref: str, current_user: User) -> Dict[str, Any]:
        """Return full OPD profile for autofill (receptionist)."""
        user_context = self.get_user_context(current_user)
        await self.get_receptionist_profile(user_context)
        hospital_id_str = user_context.get("hospital_id")
        if not hospital_id_str:
            from app.utils.hospital_id_resolve import resolve_effective_hospital_id

            resolved = await resolve_effective_hospital_id(self.db, current_user)
            if resolved:
                hospital_id_str = str(resolved)
        if not hospital_id_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hospital ID is required. Receptionist must be associated with a hospital.",
            )
        hospital_id_uuid = uuid.UUID(hospital_id_str)
        pr = (patient_ref or "").strip()
        result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == pr,
                    PatientProfile.hospital_id == hospital_id_uuid,
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        patient = result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient '{pr}' not found for this hospital.",
            )
        return self._receptionist_patient_detail_dict(patient)

    async def _resolve_patient_for_scheduling(
        self,
        patient_ref: Optional[str],
        patient_name: Optional[str],
        hospital_id_uuid: uuid.UUID,
    ) -> PatientProfile:
        ref = (patient_ref or "").strip()
        name = (patient_name or "").strip()

        if ref:
            patient_result = await self.db.execute(
                select(PatientProfile)
                .where(
                    and_(
                        PatientProfile.patient_id == ref,
                        PatientProfile.hospital_id == hospital_id_uuid,
                    )
                )
                .options(selectinload(PatientProfile.user))
            )
            patient = patient_result.scalar_one_or_none()
            if not patient:
                patient_result = await self.db.execute(
                    select(PatientProfile)
                    .where(PatientProfile.patient_id == ref)
                    .options(selectinload(PatientProfile.user))
                )
                patient = patient_result.scalar_one_or_none()
                if patient and patient.hospital_id is None:
                    patient.hospital_id = hospital_id_uuid
                    if patient.user and patient.user.hospital_id is None:
                        patient.user.hospital_id = hospital_id_uuid
            if not patient:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Patient '{ref}' not found. Register via POST /receptionist/patients/register first.",
                )
            return patient

        norm = " ".join(name.split()).lower()
        full = func.lower(
            func.trim(
                func.concat(
                    func.coalesce(User.first_name, ""),
                    " ",
                    func.coalesce(User.last_name, ""),
                )
            )
        )
        patient_result = await self.db.execute(
            select(PatientProfile)
            .join(User, PatientProfile.user_id == User.id)
            .where(
                and_(
                    PatientProfile.hospital_id == hospital_id_uuid,
                    full == norm,
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        rows = patient_result.scalars().all()
        if len(rows) == 1:
            return rows[0]
        if len(rows) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No patient found with name '{name}' in this hospital. "
                    "Register first or use patient_ref from search."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Multiple patients match this name; pass patient_ref to disambiguate.",
                "matches": [
                    {"patient_ref": p.patient_id, "patient_name": f"{p.user.first_name} {p.user.last_name}"}
                    for p in rows
                ],
            },
        )
    
    # ============================================================================
    # OPD APPOINTMENT SCHEDULING
    # ============================================================================
    
    async def schedule_opd_appointment(self, appointment_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Schedule appointment for OPD patient (same slot rules as patient self-booking)."""
        from app.models.doctor import DoctorProfile
        from app.services.appointment_service import AppointmentService

        user_context = self.get_user_context(current_user)
        await self.get_receptionist_profile(user_context)

        hospital_id_uuid = None
        if user_context.get("hospital_id"):
            hospital_id_uuid = (
                uuid.UUID(user_context["hospital_id"])
                if isinstance(user_context["hospital_id"], str)
                else user_context["hospital_id"]
            )

        if not hospital_id_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hospital ID is required. Receptionist must be associated with a hospital.",
            )

        patient = await self._resolve_patient_for_scheduling(
            appointment_data.get("patient_ref"),
            appointment_data.get("patient_name"),
            hospital_id_uuid,
        )

        hid = user_context.get("hospital_id")
        department = await self.get_department_by_id_or_name(
            appointment_data.get("department_id"),
            appointment_data.get("department_name"),
            hid,
        )
        if appointment_data.get("doctor_id"):
            try:
                doctor_user_id = uuid.UUID(str(appointment_data["doctor_id"]).strip())
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="doctor_id must be a valid UUID",
                )
            doctor_result = await self.db.execute(
                select(User)
                .join(user_roles, User.id == user_roles.c.user_id)
                .join(Role, user_roles.c.role_id == Role.id)
                .where(
                    and_(
                        User.id == doctor_user_id,
                        User.hospital_id == hospital_id_uuid,
                        Role.name == UserRole.DOCTOR.value,
                    )
                )
            )
            doctor = doctor_result.scalar_one_or_none()
            if not doctor:
                profile_result = await self.db.execute(
                    select(DoctorProfile)
                    .where(
                        and_(
                            DoctorProfile.id == doctor_user_id,
                            DoctorProfile.hospital_id == hospital_id_uuid,
                        )
                    )
                    .options(selectinload(DoctorProfile.user))
                )
                doctor_profile = profile_result.scalar_one_or_none()
                doctor = doctor_profile.user if doctor_profile else None
            if not doctor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Doctor not found for provided doctor_id",
                )
        else:
            doctor = await self.get_doctor_by_name(
                appointment_data.get("doctor_name"),
                hid,
                department_id=department.id if department else None,
            )
        if department is None:
            department = await self.get_primary_department_for_doctor(doctor.id, hospital_id_uuid)
        else:
            in_dept = await self.db.execute(
                select(StaffDepartmentAssignment.id).where(
                    and_(
                        StaffDepartmentAssignment.staff_id == doctor.id,
                        StaffDepartmentAssignment.department_id == department.id,
                        StaffDepartmentAssignment.is_active == True,
                    )
                )
            )
            if not in_dept.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Selected doctor is not assigned to this department",
                )

        try:
            time_hhmmss = _appointment_time_to_db_hms(appointment_data.get("appointment_time"))
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

        parts = time_hhmmss.split(":")
        time_hhmm = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        appt_date = str(appointment_data["appointment_date"]).strip()[:10]

        try:
            appointment_datetime = datetime.strptime(
                f"{appt_date} {time_hhmmss}", "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid appointment_date; use YYYY-MM-DD",
            )

        if appointment_datetime <= datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Appointment must be scheduled for a future date and time",
            )

        svc = AppointmentService(self.db)
        day_slots = await svc.get_available_time_slots_for_doctor_user(doctor.id, appt_date)
        if not day_slots:
            requested_weekday = appointment_datetime.strftime("%A").upper()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"This doctor has no published availability on {appt_date} ({requested_weekday}). "
                    "Create a doctor schedule for that date with start_time and end_time first."
                ),
            )
        match = next((s for s in day_slots if s["time"] == time_hhmm), None)
        if not match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected time is outside this doctor's schedule. Pick a slot from available-slots.",
            )
        if not match["is_available"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Time slot is not available",
            )

        conflict_check = await self.db.execute(
            select(Appointment).where(
                and_(
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date == appt_date,
                    Appointment.appointment_time == time_hhmmss,
                    Appointment.status.in_(
                        [AppointmentStatus.CONFIRMED, AppointmentStatus.REQUESTED]
                    ),
                )
            )
        )
        if conflict_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Doctor is not available at this time. Please choose a different time slot.",
            )

        dp_result = await self.db.execute(
            select(DoctorProfile).where(
                and_(
                    DoctorProfile.user_id == doctor.id,
                    DoctorProfile.hospital_id == hospital_id_uuid,
                )
            )
        )
        dp = dp_result.scalar_one_or_none()
        consultation_fee = (
            float(dp.consultation_fee) if dp and dp.consultation_fee is not None else 500.0
        )

        appointment_ref = generate_appointment_ref()
        while True:
            existing_ref = await self.db.execute(
                select(Appointment).where(Appointment.appointment_ref == appointment_ref)
            )
            if not existing_ref.scalar_one_or_none():
                break
            appointment_ref = generate_appointment_ref()

        appt_type = _normalize_opd_appointment_type(appointment_data.get("appointment_type"))

        appointment = Appointment(
            id=uuid.uuid4(),
            hospital_id=hospital_id_uuid,
            appointment_ref=appointment_ref,
            patient_id=patient.id,
            doctor_id=doctor.id,
            department_id=department.id,
            appointment_date=appt_date,
            appointment_time=time_hhmmss,
            duration_minutes=int(match["duration_minutes"]),
            appointment_type=appt_type,
            chief_complaint=appointment_data.get("chief_complaint"),
            notes=appointment_data.get("notes"),
            consultation_fee=consultation_fee,
            status=AppointmentStatus.CONFIRMED,
            created_by_role=UserRole.RECEPTIONIST,
            created_by_user=user_context["user_id"],
        )

        self.db.add(appointment)
        await self.db.commit()

        return {
            "appointment_ref": appointment_ref,
            "patient_ref": patient.patient_id,
            "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
            "doctor_name": f"Dr. {doctor.first_name} {doctor.last_name}",
            "department_name": department.name,
            "appointment_date": appt_date,
            "appointment_time": time_hhmm,
            "appointment_type": appt_type,
            "status": AppointmentStatus.CONFIRMED,
            "consultation_fee": consultation_fee,
            "scheduled_by": f"{current_user.first_name} {current_user.last_name} (Receptionist)",
            "message": "Appointment scheduled successfully",
        }
    
    async def get_todays_opd_appointments(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get today's OPD appointments with filtering"""
        user_context = self.get_user_context(current_user)
        receptionist = await self.get_receptionist_profile(user_context)
        
        # Build query for today's appointments
        today = date.today().isoformat()
        page = filters.get("page", 1)
        limit = filters.get("limit", 50)
        offset = (page - 1) * limit
        status_filter = _normalize_opd_appointment_status(filters.get("status"))
        
        query = select(Appointment).where(
            and_(
                Appointment.hospital_id == user_context["hospital_id"],
                Appointment.appointment_date == today
            )
        ).options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.doctor),
            selectinload(Appointment.department)
        ).order_by(asc(Appointment.appointment_time))
        
        # Apply filters
        if filters.get("department_name"):
            query = query.join(Department).where(Department.name == filters["department_name"])
        
        if filters.get("doctor_name"):
            query = query.join(User, Appointment.doctor_id == User.id).where(
                or_(
                    func.concat(User.first_name, ' ', User.last_name) == filters["doctor_name"],
                    func.concat('Dr. ', User.first_name, ' ', User.last_name) == filters["doctor_name"]
                )
            )
        
        if status_filter:
            query = query.where(Appointment.status == status_filter)
        
        # Get total count
        count_query = select(func.count(Appointment.id)).where(
            and_(
                Appointment.hospital_id == user_context["hospital_id"],
                Appointment.appointment_date == today
            )
        )
        
        if filters.get("department_name"):
            count_query = count_query.join(Department).where(Department.name == filters["department_name"])
        if filters.get("doctor_name"):
            count_query = count_query.join(User, Appointment.doctor_id == User.id).where(
                or_(
                    func.concat(User.first_name, ' ', User.last_name) == filters["doctor_name"],
                    func.concat('Dr. ', User.first_name, ' ', User.last_name) == filters["doctor_name"]
                )
            )
        if status_filter:
            count_query = count_query.where(Appointment.status == status_filter)
        
        total_result = await self.db.execute(count_query)
        total_appointments = total_result.scalar() or 0
        
        # Get paginated appointments
        appointments_result = await self.db.execute(query.offset(offset).limit(limit))
        appointments = appointments_result.scalars().all()
        
        appointment_list = [serialize_opd_appointment_full(a) for a in appointments]
        
        return {
            "date": today,
            "department": filters.get("department_name"),
            "doctor": filters.get("doctor_name"),
            "status_filter": status_filter,
            "appointments": appointment_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_appointments,
                "pages": (total_appointments + limit - 1) // limit
            }
        }
    
    async def get_opd_appointment_by_ref(
        self, appointment_ref: str, current_user: User
    ) -> Dict[str, Any]:
        """Return one appointment by ref for the receptionist's hospital."""
        user_context = self.get_user_context(current_user)
        await self.validate_receptionist_access(user_context)
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.hospital_id == user_context["hospital_id"],
                )
            )
            .options(
                selectinload(Appointment.patient).selectinload(PatientProfile.user),
                selectinload(Appointment.doctor),
                selectinload(Appointment.department),
            )
        )
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Appointment {appointment_ref} not found",
            )
        return serialize_opd_appointment_full(appointment)

    async def modify_opd_appointment(self, appointment_ref: str, modification_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Modify existing OPD appointment"""
        user_context = self.get_user_context(current_user)
        await self.validate_receptionist_access(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.hospital_id == user_context["hospital_id"]
                )
            )
            .options(
                selectinload(Appointment.patient).selectinload(PatientProfile.user),
                selectinload(Appointment.doctor),
                selectinload(Appointment.department)
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Appointment {appointment_ref} not found"
            )
        
        # Check if appointment can be modified
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify completed appointment"
            )
        
        # Update patient by patient_ref (frontend alias: patientId)
        if modification_data.get("patient_ref"):
            p_ref = str(modification_data["patient_ref"]).strip()
            patient_result = await self.db.execute(
                select(PatientProfile).where(
                    and_(
                        PatientProfile.hospital_id == user_context["hospital_id"],
                        PatientProfile.patient_id == p_ref,
                    )
                )
            )
            patient = patient_result.scalar_one_or_none()
            if not patient:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Patient '{p_ref}' not found in your hospital",
                )
            appointment.patient_id = patient.id

        # Apply department before doctor so name-based doctor resolution stays in the right department
        if modification_data.get("department_id"):
            try:
                dep_id = uuid.UUID(str(modification_data["department_id"]).strip())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="department_id must be a valid UUID",
                )
            dep_result = await self.db.execute(
                select(Department).where(
                    and_(
                        Department.id == dep_id,
                        Department.hospital_id == user_context["hospital_id"],
                        Department.is_active == True,
                    )
                )
            )
            dep = dep_result.scalar_one_or_none()
            if not dep:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Department not found for provided department_id",
                )
            appointment.department_id = dep.id
        elif modification_data.get("department_name"):
            department = await self.get_department_by_name(
                modification_data["department_name"], user_context["hospital_id"]
            )
            appointment.department_id = department.id

        if modification_data.get("doctor_id"):
            try:
                doctor_user_id = uuid.UUID(str(modification_data["doctor_id"]).strip())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="doctorId must be a valid UUID",
                )
            doctor_result = await self.db.execute(
                select(User)
                .join(user_roles, User.id == user_roles.c.user_id)
                .join(Role, user_roles.c.role_id == Role.id)
                .where(
                    and_(
                        User.id == doctor_user_id,
                        User.hospital_id == user_context["hospital_id"],
                        Role.name == UserRole.DOCTOR.value,
                    )
                )
            )
            doctor = doctor_result.scalar_one_or_none()
            if not doctor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Doctor not found for provided doctorId",
                )
            appointment.doctor_id = doctor.id
        elif modification_data.get("doctor_name"):
            doctor = await self.get_doctor_by_name(
                modification_data["doctor_name"],
                user_context["hospital_id"],
                department_id=appointment.department_id,
            )
            appointment.doctor_id = doctor.id

        if "appointment_date" in modification_data and modification_data.get("appointment_date") is not None:
            s = str(modification_data["appointment_date"]).strip()
            appointment.appointment_date = s[:10] if len(s) >= 10 else s
        if "appointment_time" in modification_data and modification_data.get("appointment_time") is not None:
            try:
                appointment.appointment_time = _appointment_time_to_db_hms(modification_data["appointment_time"])
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_APPOINTMENT_TIME",
                        "message": str(e),
                    },
                )
        if "appointment_type" in modification_data and modification_data.get("appointment_type") is not None:
            appointment.appointment_type = _normalize_opd_appointment_type(
                modification_data.get("appointment_type")
            )
        if "chief_complaint" in modification_data:
            appointment.chief_complaint = modification_data.get("chief_complaint")
        if "notes" in modification_data:
            appointment.notes = modification_data.get("notes")
        if "status" in modification_data and modification_data.get("status") is not None:
            status_value = _normalize_opd_appointment_status(modification_data["status"])
            if status_value:
                appointment.status = status_value

        need_slot_check = any(
            modification_data.get(k) is not None
            for k in (
                "appointment_date",
                "appointment_time",
                "doctor_name",
                "doctor_id",
                "department_name",
                "department_id",
            )
        )
        st_upper = (appointment.status or "").strip().upper()
        if st_upper not in ("CANCELLED", "COMPLETED"):
            in_dept = await self.db.execute(
                select(StaffDepartmentAssignment.id).where(
                    and_(
                        StaffDepartmentAssignment.staff_id == appointment.doctor_id,
                        StaffDepartmentAssignment.department_id == appointment.department_id,
                        StaffDepartmentAssignment.is_active == True,
                    )
                )
            )
            if not in_dept.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Selected doctor is not assigned to this department",
                )
            if need_slot_check:
                from app.services.appointment_service import AppointmentService

                try:
                    appt_dt = datetime.strptime(
                        f"{appointment.appointment_date} {appointment.appointment_time}",
                        "%Y-%m-%d %H:%M:%S",
                    )
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid stored appointment date/time",
                    )
                if appt_dt <= datetime.utcnow():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Appointment must be rescheduled to a future date and time",
                    )
                svc = AppointmentService(self.db)
                day_slots = await svc.get_available_time_slots_for_doctor_user(
                    appointment.doctor_id,
                    appointment.appointment_date,
                    exclude_appointment_id=appointment.id,
                )
                if not day_slots:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="This doctor has no published availability on that day",
                    )
                pt_parts = appointment.appointment_time.split(":")
                th = f"{int(pt_parts[0]):02d}:{int(pt_parts[1]):02d}"
                match = next((s for s in day_slots if s["time"] == th), None)
                if not match:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Selected time is outside this doctor's schedule",
                    )
                if not match["is_available"]:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Time slot is not available",
                    )
                if "duration_minutes" in match:
                    appointment.duration_minutes = int(match["duration_minutes"])

        await self.db.commit()
        
        return {
            "appointment_ref": appointment_ref,
            "message": "Appointment modified successfully",
            "modified_by": f"{current_user.first_name} {current_user.last_name} (Receptionist)",
            "modified_at": datetime.utcnow().isoformat()
        }
    
    async def check_in_patient(self, appointment_ref: str, checkin_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Check-in patient for their appointment"""
        user_context = self.get_user_context(current_user)
        await self.validate_receptionist_access(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.hospital_id == user_context["hospital_id"]
                )
            )
            .options(
                selectinload(Appointment.patient).selectinload(PatientProfile.user),
                selectinload(Appointment.doctor)
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Appointment {appointment_ref} not found"
            )
        
        # Check if appointment is for today
        today = date.today().isoformat()
        if appointment.appointment_date != today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only check-in patients for today's appointments"
            )
        
        # Check if already checked in
        if appointment.checked_in_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Patient is already checked in"
            )
        
        # Check-in patient
        appointment.checked_in_at = datetime.utcnow()
        appointment.status = AppointmentStatus.CONFIRMED
        
        await self.db.commit()
        
        return {
            "appointment_ref": appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "doctor_name": f"Dr. {appointment.doctor.first_name} {appointment.doctor.last_name}",
            "checked_in_at": appointment.checked_in_at.isoformat(),
            "checked_in_by": f"{current_user.first_name} {current_user.last_name} (Receptionist)",
            "message": "Patient checked in successfully"
        }
    
    async def get_opd_dashboard(self, current_user: User) -> Dict[str, Any]:
        """Get OPD dashboard with key metrics and information"""
        user_context = self.get_user_context(current_user)
        receptionist = await self.get_receptionist_profile(user_context)
        
        today = date.today().isoformat()
        
        # Get today's appointments count
        todays_appointments_result = await self.db.execute(
            select(func.count(Appointment.id))
            .where(
                and_(
                    Appointment.hospital_id == user_context["hospital_id"],
                    Appointment.appointment_date == today
                )
            )
        )
        todays_appointments = todays_appointments_result.scalar() or 0
        
        # Get checked-in patients count
        checked_in_result = await self.db.execute(
            select(func.count(Appointment.id))
            .where(
                and_(
                    Appointment.hospital_id == user_context["hospital_id"],
                    Appointment.appointment_date == today,
                    Appointment.checked_in_at.isnot(None)
                )
            )
        )
        checked_in_patients = checked_in_result.scalar() or 0
        
        # Get pending appointments (not checked in)
        pending_result = await self.db.execute(
            select(func.count(Appointment.id))
            .where(
                and_(
                    Appointment.hospital_id == user_context["hospital_id"],
                    Appointment.appointment_date == today,
                    Appointment.checked_in_at.is_(None),
                    Appointment.status.in_([AppointmentStatus.CONFIRMED, AppointmentStatus.REQUESTED])
                )
            )
        )
        pending_checkins = pending_result.scalar() or 0
        
        # Get total patients registered today
        today_date = date.today()  # Use actual date object instead of string
        patients_today_result = await self.db.execute(
            select(func.count(PatientProfile.id))
            .where(
                and_(
                    PatientProfile.hospital_id == user_context["hospital_id"],
                    func.date(PatientProfile.created_at) == today_date
                )
            )
        )
        patients_registered_today = patients_today_result.scalar() or 0
        
        return {
            "receptionist_name": f"{current_user.first_name} {current_user.last_name}",
            "hospital_id": user_context["hospital_id"],
            "department": receptionist.department.name,
            "work_area": receptionist.work_area,
            "dashboard_date": today,
            "statistics": {
                "todays_appointments": todays_appointments,
                "checked_in_patients": checked_in_patients,
                "pending_checkins": pending_checkins,
                "patients_registered_today": patients_registered_today
            },
            "quick_actions": [
                "Register new patient",
                "Schedule appointment",
                "Check-in patient",
                "View today's appointments",
                "Modify appointment"
            ]
        }

    # ============================================================================
    # IPD PATIENT ADMISSIONS
    # ============================================================================
    
    async def admit_patient_to_ipd(self, admission_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Admit patient to IPD"""
        user_context = self.get_user_context(current_user)
        
        # Only doctors can admit patients
        if user_context["role"] != UserRole.DOCTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only doctors can admit patients to IPD"
            )
        
        # Get doctor profile
        doctor = await self.get_doctor_profile(user_context)

        if self.platform_db and user_context.get("hospital_id"):
            hid = uuid.UUID(str(user_context["hospital_id"]))
            await resolve_patient_profile_id_for_tenant(
                str(admission_data["patient_ref"]).strip(),
                hid,
                self.db,
                self.platform_db,
            )

        # Get patient - First check if patient exists in the hospital
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == admission_data["patient_ref"],
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        
        patient = patient_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient {admission_data['patient_ref']} not found in your hospital"
            )
        
        # Check if patient is already admitted
        existing_admission = await self.db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.patient_id == patient.id,
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.is_active == True
                )
            )
        )
        
        active_admission = existing_admission.scalar_one_or_none()
        if active_admission:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Patient is already admitted with admission number {active_admission.admission_number}. Please discharge before new admission."
            )
        
        # Generate admission number
        admission_number = f"ADM-{datetime.now().year}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create admission record
        admission = Admission(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            patient_id=patient.id,
            doctor_id=doctor.id,
            department_id=doctor.department_id,
            admission_number=admission_number,
            admission_type=admission_data["admission_type"],
            admission_date=datetime.now(timezone.utc),
            chief_complaint=admission_data["chief_complaint"],
            provisional_diagnosis=admission_data["provisional_diagnosis"],
            admission_notes=admission_data["admission_notes"],
            ward=admission_data["ward"],
            room_number=admission_data["room_number"],
            bed_number=admission_data["bed_number"],
            is_active=True
        )
        
        self.db.add(admission)
        await self.db.commit()
        
        return {
            "admission_number": admission_number,
            "patient_ref": patient.patient_id,
            "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
            "admission_date": admission.admission_date.isoformat(),
            "admission_type": admission_data["admission_type"],
            "department": doctor.department.name,
            "attending_doctor": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "ward": admission_data["ward"],
            "room_number": admission_data["room_number"],
            "bed_number": admission_data["bed_number"],
            "admitted_by": f"Dr. {current_user.first_name} {current_user.last_name}",
            "message": "Patient admitted to IPD successfully"
        }
    
    async def get_available_patients_for_admission(self, current_user: User) -> Dict[str, Any]:
        """Get list of patients that the doctor can see for admission"""
        user_context = self.get_user_context(current_user)
        
        # Only doctors can access this
        if user_context["role"] != UserRole.DOCTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only doctors can access available patients"
            )
        
        # Get doctor profile
        doctor = await self.get_doctor_profile(user_context)

        # OPD patients live on platform; IPD session is tenant when provisioned.
        patient_db = self.platform_db if self.platform_db is not None else self.db
        appt_db = patient_db

        # Get all patients in the hospital
        patients_result = await patient_db.execute(
            select(PatientProfile)
            .where(PatientProfile.hospital_id == user_context["hospital_id"])
            .options(selectinload(PatientProfile.user))
            .order_by(PatientProfile.created_at.desc())
            .limit(50)  # Limit to recent 50 patients
        )
        
        patients = patients_result.scalars().all()
        
        # Get currently admitted patients to mark their status
        admitted_patients_result = await self.db.execute(
            select(Admission.patient_id, Admission.admission_number, Admission.ward)
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.is_active == True
                )
            )
        )
        admitted_patients_info = {row[0]: {"admission_number": row[1], "ward": row[2]} for row in admitted_patients_result.fetchall()}
        
        # Build available patients list
        available_patients = []
        
        for patient in patients:
            # Calculate age
            age = self.calculate_age(patient.date_of_birth) if patient.date_of_birth else 0
            
            # Get latest appointment info if available
            latest_appointment = await appt_db.execute(
                select(Appointment)
                .where(Appointment.patient_id == patient.id)
                .order_by(desc(Appointment.created_at))
                .limit(1)
            )
            appointment = latest_appointment.scalar_one_or_none()
            
            last_appointment_info = None
            if appointment:
                last_appointment_info = {
                    "date": appointment.appointment_date,
                    "ref": appointment.appointment_ref,
                    "chief_complaint": appointment.chief_complaint
                }
            
            # Check admission status
            admission_status = "available"
            admission_info = None
            if patient.id in admitted_patients_info:
                admission_status = "currently_admitted"
                admission_info = admitted_patients_info[patient.id]
            
            available_patients.append({
                "patient_id": patient.patient_id,
                "name": f"{patient.user.first_name} {patient.user.last_name}",
                "age": age,
                "gender": patient.gender,
                "phone": patient.user.phone,
                "admission_status": admission_status,
                "current_admission": admission_info,
                "last_appointment": last_appointment_info,
                "medical_info": {
                    "allergies": patient.allergies or [],
                    "chronic_conditions": patient.chronic_conditions or [],
                    "blood_group": patient.blood_group
                }
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "department": doctor.department.name,
            "available_patients": available_patients,
            "total_count": len(available_patients),
            "note": "All patients in your hospital (available for admission and currently admitted)"
        }
    
    async def _resolve_ipd_patient_display(self, admission: Admission) -> tuple[str, str]:
        """Resolve PAT-... and display name from tenant row or platform (OPD patients often platform-only)."""
        p = admission.patient
        if p is not None:
            u = getattr(p, "user", None)
            if u is not None:
                name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
                return p.patient_id, name or (getattr(u, "email", None) or p.patient_id)
        if self.platform_db is not None:
            pp = await self.platform_db.get(PatientProfile, admission.patient_id)
            if pp is not None:
                u = getattr(pp, "user", None)
                if u is None and pp.user_id:
                    u = await self.platform_db.get(User, pp.user_id)
                if u is not None:
                    name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
                    return pp.patient_id, name or (getattr(u, "email", None) or pp.patient_id)
                return pp.patient_id, pp.patient_id
        return "UNKNOWN", "Unknown patient"

    # ============================================================================
    # IPD PATIENT MANAGEMENT
    # ============================================================================
    
    async def get_ipd_patients(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get list of IPD patients in user's department"""
        user_context = self.get_user_context(current_user)
        await self.validate_ipd_access(user_context)
        
        # Get user profile
        user_profile = await self.get_ipd_user_profile(user_context)
        
        hid = uuid.UUID(str(user_context["hospital_id"]))
        page = filters.get("page", 1)
        limit = filters.get("limit", 20)
        offset = (page - 1) * limit

        if filters.get("all_hospital"):
            query = (
                select(Admission)
                .where(
                    Admission.hospital_id == hid,
                    Admission.is_active == True,
                )
                .options(
                    selectinload(Admission.patient).selectinload(PatientProfile.user),
                    selectinload(Admission.doctor),
                    selectinload(Admission.department),
                )
                .order_by(desc(Admission.admission_date))
            )
            count_query = select(func.count(Admission.id)).where(
                Admission.hospital_id == hid,
                Admission.is_active == True,
            )
        else:
            # Match admissions by department id OR same department name (handles duplicate/re-seeded dept rows).
            dept_match = [Admission.department_id == user_profile.department_id]
            dnorm = (user_profile.department.name or "").strip().lower()
            if dnorm:
                dept_match.append(func.lower(func.trim(Department.name)) == dnorm)

            query = (
                select(Admission)
                .join(Department, Admission.department_id == Department.id)
                .where(
                    Admission.hospital_id == hid,
                    Department.hospital_id == hid,
                    Admission.is_active == True,
                    or_(*dept_match),
                )
                .options(
                    selectinload(Admission.patient).selectinload(PatientProfile.user),
                    selectinload(Admission.doctor),
                    selectinload(Admission.department),
                )
                .order_by(desc(Admission.admission_date))
            )

            count_query = (
                select(func.count(Admission.id))
                .join(Department, Admission.department_id == Department.id)
                .where(
                    Admission.hospital_id == hid,
                    Department.hospital_id == hid,
                    Admission.is_active == True,
                    or_(*dept_match),
                )
            )

        if filters.get("ward"):
            query = query.where(Admission.ward == filters["ward"])
            count_query = count_query.where(Admission.ward == filters["ward"])

        total_result = await self.db.execute(count_query)
        total_patients = total_result.scalar() or 0
        
        admissions_result = await self.db.execute(query.offset(offset).limit(limit))
        admissions = admissions_result.scalars().all()
        
        # Format response
        from app.schemas.clinical import IPDPatientOut
        patient_list = []
        for admission in admissions:
            length_of_stay = (datetime.now(timezone.utc) - admission.admission_date).days
            
            latest_assessment = await self.db.execute(
                select(MedicalRecord.vital_signs)
                .where(
                    and_(
                        MedicalRecord.patient_id == admission.patient_id,
                        MedicalRecord.chief_complaint.like("Nursing Assessment%")
                    )
                )
                .order_by(desc(MedicalRecord.created_at))
                .limit(1)
            )
            
            assessment_data = latest_assessment.scalar_one_or_none()
            current_condition = None
            if assessment_data:
                current_condition = assessment_data.get("general_condition", "Unknown")
            
            pref, pname = await self._resolve_ipd_patient_display(admission)
            doc = admission.doctor
            attending = (
                f"Dr. {doc.first_name} {doc.last_name}".strip()
                if doc is not None
                else "Dr. Unknown"
            )
            dept_nm = (
                admission.department.name
                if getattr(admission, "department", None) is not None
                else user_profile.department.name
            )
            
            patient_list.append(IPDPatientOut(
                patient_ref=pref,
                patient_name=pname,
                admission_number=admission.admission_number,
                admission_date=admission.admission_date.date().isoformat(),
                admission_type=admission.admission_type,
                department_name=dept_nm,
                attending_doctor=attending,
                assigned_nurse=None,
                ward=admission.ward,
                room_number=admission.room_number,
                bed_number=admission.bed_number,
                current_condition=current_condition,
                length_of_stay=length_of_stay,
                chief_complaint=admission.chief_complaint,
                provisional_diagnosis=admission.provisional_diagnosis,
                is_active=admission.is_active
            ))
        
        return {
            "department": (
                "All departments"
                if filters.get("all_hospital")
                else user_profile.department.name
            ),
            "all_hospital": bool(filters.get("all_hospital")),
            "ward_filter": filters.get("ward"),
            "patients": patient_list,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_patients,
                "pages": (total_patients + limit - 1) // limit
            }
        }
    
    async def get_ipd_admission_details(self, admission_number: str, current_user: User) -> Dict[str, Any]:
        """Get detailed IPD admission information"""
        user_context = self.get_user_context(current_user)
        await self.validate_ipd_access(user_context)
        
        # Get user profile
        user_profile = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(admission_number, user_profile)
        
        # Calculate patient age
        patient_age = self.calculate_age(admission.patient.date_of_birth)
        
        # Get latest vital signs
        latest_vitals = await self.db.execute(
            select(MedicalRecord.vital_signs, MedicalRecord.created_at)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.vital_signs.isnot(None)
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(1)
        )
        
        vitals_data = latest_vitals.first()
        vital_signs_summary = {}
        if vitals_data:
            vital_signs_summary = {
                "last_recorded": vitals_data.created_at.isoformat(),
                "vitals": vitals_data.vital_signs
            }
        
        # Get current medications (from recent medical records)
        medications_result = await self.db.execute(
            select(MedicalRecord.prescriptions)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.prescriptions.isnot(None),
                    MedicalRecord.created_at >= admission.admission_date
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(5)
        )
        
        current_medications = []
        for prescription_record in medications_result.scalars():
            if prescription_record:
                current_medications.extend(prescription_record)
        
        # Get recent assessments
        assessments_result = await self.db.execute(
            select(MedicalRecord)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.created_at >= admission.admission_date,
                    or_(
                        MedicalRecord.chief_complaint.like("Nursing Assessment%"),
                        MedicalRecord.chief_complaint.like("Doctor Rounds%")
                    )
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(5)
        )
        
        recent_assessments = []
        for assessment in assessments_result.scalars():
            recent_assessments.append({
                "date": assessment.created_at.isoformat(),
                "type": assessment.chief_complaint,
                "findings": assessment.examination_findings,
                "assessment_data": assessment.vital_signs
            })
        
        # Calculate length of stay
        length_of_stay = (datetime.now(timezone.utc) - admission.admission_date).days
        
        from app.schemas.clinical import IPDAdmissionDetailsOut
        return IPDAdmissionDetailsOut(
            admission_number=admission.admission_number,
            patient_ref=admission.patient.patient_id,
            patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
            patient_age=patient_age,
            patient_gender=admission.patient.gender,
            admission_date=admission.admission_date.date().isoformat(),
            admission_type=admission.admission_type,
            department_name=admission.department.name,
            attending_doctor=f"Dr. {admission.doctor.first_name} {admission.doctor.last_name}",
            chief_complaint=admission.chief_complaint,
            provisional_diagnosis=admission.provisional_diagnosis,
            admission_notes=admission.admission_notes,
            ward=admission.ward,
            room_number=admission.room_number,
            bed_number=admission.bed_number,
            length_of_stay=length_of_stay,
            current_condition=None,  # Will be filled from latest assessment
            vital_signs_summary=vital_signs_summary,
            current_medications=current_medications,
            recent_assessments=recent_assessments,
            treatment_plan=None,  # TODO: Implement treatment plans
            discharge_planning=None  # TODO: Implement discharge planning
        )
    
    async def create_nursing_assessment(self, assessment_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create comprehensive nursing assessment for IPD patient"""
        user_context = self.get_user_context(current_user)
        
        # Only nurses can create nursing assessments
        if user_context["role"] != UserRole.NURSE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only nurses can create nursing assessments"
            )
        
        # Get nurse profile
        nurse = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(assessment_data["admission_number"], nurse)
        
        # Create nursing assessment as medical record
        assessment_record = MedicalRecord(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            patient_id=admission.patient_id,
            doctor_id=None,  # Nursing assessment
            chief_complaint=f"Nursing Assessment - {assessment_data['assessment_type']}",
            examination_findings=f"General Condition: {assessment_data['general_condition']}\n"
                               f"Consciousness: {assessment_data['consciousness_level']}\n"
                               f"Mobility: {assessment_data['mobility_status']}\n"
                               f"Interventions: {', '.join(assessment_data['nursing_interventions'])}",
            vital_signs={
                "assessment_type": assessment_data["assessment_type"],
                "general_condition": assessment_data["general_condition"],
                "consciousness_level": assessment_data["consciousness_level"],
                "mobility_status": assessment_data["mobility_status"],
                "pain_assessment": assessment_data["pain_assessment"],
                "skin_condition": assessment_data["skin_condition"],
                "wound_assessment": assessment_data["wound_assessment"],
                "nutritional_status": assessment_data["nutritional_status"],
                "elimination_status": assessment_data["elimination_status"],
                "psychosocial_status": assessment_data["psychosocial_status"],
                "family_involvement": assessment_data["family_involvement"],
                "discharge_planning_needs": assessment_data["discharge_planning_needs"],
                "nursing_interventions": assessment_data["nursing_interventions"],
                "goals_for_next_shift": assessment_data["goals_for_next_shift"],
                "assessed_by": f"{current_user.first_name} {current_user.last_name} (Nurse)",
                "assessed_at": datetime.now(timezone.utc).isoformat()
            },
            is_finalized=True
        )
        
        self.db.add(assessment_record)
        await self.db.commit()
        
        return {
            "assessment_id": str(assessment_record.id),
            "admission_number": assessment_data["admission_number"],
            "assessment_type": assessment_data["assessment_type"],
            "general_condition": assessment_data["general_condition"],
            "assessed_by": f"{current_user.first_name} {current_user.last_name} (Nurse)",
            "assessed_at": assessment_record.created_at.isoformat(),
            "message": "Nursing assessment completed successfully"
        }
    
    async def create_doctor_rounds(self, rounds_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Document doctor rounds for IPD patient"""
        user_context = self.get_user_context(current_user)
        
        # Only doctors can document rounds
        if user_context["role"] != UserRole.DOCTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only doctors can document rounds"
            )
        
        # Get doctor profile
        doctor = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(rounds_data["admission_number"], doctor)
        
        # Create doctor rounds as medical record
        rounds_record = MedicalRecord(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            patient_id=admission.patient_id,
            doctor_id=doctor.id,
            chief_complaint=f"Doctor Rounds - {rounds_data['round_type']}",
            examination_findings=rounds_data["clinical_findings"],
            diagnosis=rounds_data["assessment_and_plan"],
            treatment_plan=rounds_data["assessment_and_plan"],
            follow_up_instructions=rounds_data["follow_up_instructions"],
            prescriptions=rounds_data.get("medication_changes", []),
            vital_signs={
                "round_type": rounds_data["round_type"],
                "patient_condition": rounds_data["patient_condition"],
                "clinical_findings": rounds_data["clinical_findings"],
                "assessment_and_plan": rounds_data["assessment_and_plan"],
                "medication_changes": rounds_data.get("medication_changes"),
                "new_orders": rounds_data.get("new_orders"),
                "discharge_planning": rounds_data.get("discharge_planning"),
                "family_discussion": rounds_data.get("family_discussion"),
                "rounds_by": f"Dr. {current_user.first_name} {current_user.last_name}",
                "rounds_at": datetime.now(timezone.utc).isoformat()
            },
            is_finalized=True
        )
        
        self.db.add(rounds_record)
        await self.db.commit()
        
        return {
            "rounds_id": str(rounds_record.id),
            "admission_number": rounds_data["admission_number"],
            "round_type": rounds_data["round_type"],
            "patient_condition": rounds_data["patient_condition"],
            "rounds_by": f"Dr. {current_user.first_name} {current_user.last_name}",
            "rounds_at": rounds_record.created_at.isoformat(),
            "message": "Doctor rounds documented successfully"
        }
    
    async def get_ipd_dashboard(self, current_user: User) -> Dict[str, Any]:
        """Get IPD dashboard with key metrics and patient information"""
        user_context = self.get_user_context(current_user)
        await self.validate_ipd_access(user_context)
        
        # Get user profile
        user_profile = await self.get_ipd_user_profile(user_context)
        
        # Get total admitted patients in department
        total_admitted_result = await self.db.execute(
            select(func.count(Admission.id))
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.is_active == True
                )
            )
        )
        total_admitted = total_admitted_result.scalar() or 0
        
        # Get critical patients (from recent assessments)
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy import cast
        critical_patients_result = await self.db.execute(
            select(func.count(Admission.id.distinct()))
            .join(MedicalRecord, Admission.patient_id == MedicalRecord.patient_id)
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.is_active == True,
                    MedicalRecord.vital_signs.op('@>')(cast('{"general_condition": "CRITICAL"}', JSONB)),
                    MedicalRecord.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
                )
            )
        )
        critical_patients = critical_patients_result.scalar() or 0
        
        # Get today's assessments/rounds by this user
        today = datetime.now(timezone.utc).date()
        if user_context["role"] == UserRole.NURSE:
            assessments_today_result = await self.db.execute(
                select(func.count(MedicalRecord.id))
                .where(
                    and_(
                        MedicalRecord.hospital_id == user_context["hospital_id"],
                        MedicalRecord.chief_complaint.like("Nursing Assessment%"),
                        func.date(MedicalRecord.created_at) == today,
                        MedicalRecord.vital_signs.op('@>')(cast(f'{{"assessed_by": "{current_user.first_name} {current_user.last_name} (Nurse)"}}', JSONB))
                    )
                )
            )
            assessments_today = assessments_today_result.scalar() or 0
            activity_label = "Nursing Assessments Today"
        else:  # Doctor
            assessments_today_result = await self.db.execute(
                select(func.count(MedicalRecord.id))
                .where(
                    and_(
                        MedicalRecord.hospital_id == user_context["hospital_id"],
                        MedicalRecord.doctor_id == user_profile.id,
                        MedicalRecord.chief_complaint.like("Doctor Rounds%"),
                        func.date(MedicalRecord.created_at) == today
                    )
                )
            )
            assessments_today = assessments_today_result.scalar() or 0
            activity_label = "Doctor Rounds Today"
        
        # Get recent admissions (last 7 days)
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_admissions_result = await self.db.execute(
            select(func.count(Admission.id))
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.admission_date >= week_ago
                )
            )
        )
        recent_admissions = recent_admissions_result.scalar() or 0
        
        return {
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_role": user_context["role"],
            "hospital_id": user_context["hospital_id"],
            "department": user_profile.department.name,
            "dashboard_date": datetime.now(timezone.utc).date().isoformat(),
            "statistics": {
                "total_admitted_patients": total_admitted,
                "critical_patients": critical_patients,
                "recent_admissions_7_days": recent_admissions,
                activity_label.lower().replace(" ", "_"): assessments_today
            },
            "quick_actions": [
                "View IPD patients",
                "Create nursing assessment" if user_context["role"] == UserRole.NURSE else "Document rounds",
                "Record vital signs",
                "View admission details",
                "Discharge planning"
            ]
        }
    
    # ============================================================================
    # IPD HELPER METHODS
    # ============================================================================
    
    async def get_admission_by_number_with_department_check(self, admission_number: str, user_profile) -> Admission:
        """Get admission with department access control"""
        result = await self.db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.admission_number == admission_number,
                    Admission.hospital_id == user_profile.hospital_id,
                    Admission.department_id == user_profile.department_id  # Department-based access
                )
            )
            .options(
                selectinload(Admission.patient).selectinload(PatientProfile.user),
                selectinload(Admission.doctor),
                selectinload(Admission.department)
            )
        )
        
        admission = result.scalar_one_or_none()
        if not admission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Admission {admission_number} not found in your department"
            )
        
        return admission
    
    # ============================================================================
    # HELPER METHODS FOR IPD
    # ============================================================================
    
    async def get_doctor_profile(self, user_context: dict):
        """Get doctor profile with department information"""
        # Get doctor user and their department assignment
        doctor_result = await self.db.execute(
            select(User)
            .where(User.id == user_context["user_id"])
        )
        doctor_user = doctor_result.scalar_one_or_none()
        
        if not doctor_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor user not found. Please contact administrator."
            )
            
        # Get department assignment
        assignment_result = await self.db.execute(
            select(StaffDepartmentAssignment)
            .where(StaffDepartmentAssignment.staff_id == user_context["user_id"])
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = assignment_result.scalar_one_or_none()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor department assignment not found. Please contact administrator."
            )
            
        # Create a mock object that has the same interface as the old DoctorProfile
        class MockDoctorProfile:
            def __init__(self, user, department):
                self.user = user
                self.department = department
                self.id = user.id  # Add the id attribute that points to the user's id
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                self.department_id = department.id
                self.doctor_id = user.staff_id or f"DOC-{str(user.id)[:8]}"  # Add doctor_id attribute
                # Add commonly used attributes with default values
                self.specialization = "General Medicine"
                self.designation = "Doctor"
                self.experience_years = 5
                self.consultation_fee = 500.0
                self.medical_license_number = f"LIC-{user.id}"
                self.is_available = True
        
        return MockDoctorProfile(doctor_user, assignment.department)
    
    async def get_ipd_user_profile(self, user_context: dict):
        """Get user profile (nurse or doctor) with department information for IPD"""
        if user_context["role"] == UserRole.NURSE:
            # Get nurse user and their department assignment
            nurse_result = await self.db.execute(
                select(User)
                .where(User.id == user_context["user_id"])
            )
            nurse_user = nurse_result.scalar_one_or_none()
            
            if not nurse_user:
                return None
                
            # Get department assignment
            assignment_result = await self.db.execute(
                select(StaffDepartmentAssignment)
                .where(StaffDepartmentAssignment.staff_id == user_context["user_id"])
                .options(selectinload(StaffDepartmentAssignment.department))
            )
            assignment = assignment_result.scalar_one_or_none()
            
            if not assignment:
                return None
                
            # Create a mock object that has the same interface as the old NurseProfile
            class MockNurseProfile:
                def __init__(self, user, department):
                    self.user = user
                    self.department = department
                    self.user_id = user.id
                    self.hospital_id = user.hospital_id
                    self.department_id = department.id
            
            profile = MockNurseProfile(nurse_user, assignment.department)
        elif user_context["role"] == UserRole.DOCTOR:
            profile = await self.get_doctor_profile(user_context)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Nurse or Doctor role required for IPD operations"
            )
        
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{user_context['role'].title()} profile not found. Please contact administrator."
            )
        
        return profile
    
    def calculate_age(self, date_of_birth: str) -> int:
        """Calculate age from date of birth"""
        try:
            birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
            today = date.today()
            return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        except:
            return 0

    async def get_ipd_admission_details(self, admission_number: str, current_user: User) -> Dict[str, Any]:
        """Get detailed IPD admission information"""
        user_context = self.get_user_context(current_user)
        await self.validate_ipd_access(user_context)
        
        # Get user profile
        user_profile = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(admission_number, user_profile)
        
        # Calculate patient age
        patient_age = self.calculate_age(admission.patient.date_of_birth)
        
        # Get latest vital signs
        latest_vitals = await self.db.execute(
            select(MedicalRecord.vital_signs, MedicalRecord.created_at)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.vital_signs.isnot(None)
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(1)
        )
        
        vitals_data = latest_vitals.first()
        vital_signs_summary = {}
        if vitals_data:
            vital_signs_summary = {
                "last_recorded": vitals_data.created_at.isoformat(),
                "vitals": vitals_data.vital_signs
            }
        
        # Get current medications (from recent medical records)
        medications_result = await self.db.execute(
            select(MedicalRecord.prescriptions)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.prescriptions.isnot(None),
                    MedicalRecord.created_at >= admission.admission_date
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(5)
        )
        
        current_medications = []
        for prescription_record in medications_result.scalars():
            if prescription_record:
                current_medications.extend(prescription_record)
        
        # Get recent assessments
        assessments_result = await self.db.execute(
            select(MedicalRecord)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.created_at >= admission.admission_date,
                    or_(
                        MedicalRecord.chief_complaint.like("Nursing Assessment%"),
                        MedicalRecord.chief_complaint.like("Doctor Rounds%")
                    )
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(5)
        )
        
        recent_assessments = []
        for assessment in assessments_result.scalars():
            recent_assessments.append({
                "date": assessment.created_at.isoformat(),
                "type": assessment.chief_complaint,
                "findings": assessment.examination_findings,
                "assessment_data": assessment.vital_signs
            })
        
        # Calculate length of stay
        length_of_stay = (datetime.now(timezone.utc) - admission.admission_date).days
        
        from app.schemas.clinical import IPDAdmissionDetailsOut
        return IPDAdmissionDetailsOut(
            admission_number=admission.admission_number,
            patient_ref=admission.patient.patient_id,
            patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
            patient_age=patient_age,
            patient_gender=admission.patient.gender,
            admission_date=admission.admission_date.date().isoformat(),
            admission_type=admission.admission_type,
            department_name=admission.department.name,
            attending_doctor=f"Dr. {admission.doctor.first_name} {admission.doctor.last_name}",
            chief_complaint=admission.chief_complaint,
            provisional_diagnosis=admission.provisional_diagnosis,
            admission_notes=admission.admission_notes,
            ward=admission.ward,
            room_number=admission.room_number,
            bed_number=admission.bed_number,
            length_of_stay=length_of_stay,
            current_condition=None,  # Will be filled from latest assessment
            vital_signs_summary=vital_signs_summary,
            current_medications=current_medications,
            recent_assessments=recent_assessments,
            treatment_plan=None,  # TODO: Implement treatment plans
            discharge_planning=None  # TODO: Implement discharge planning
        )
    
    async def create_nursing_assessment(self, assessment_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create comprehensive nursing assessment for IPD patient"""
        user_context = self.get_user_context(current_user)
        
        # Only nurses can create nursing assessments
        if user_context["role"] != UserRole.NURSE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only nurses can create nursing assessments"
            )
        
        # Get nurse profile
        nurse = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(assessment_data["admission_number"], nurse)
        
        # Create nursing assessment as medical record
        assessment_record = MedicalRecord(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            patient_id=admission.patient_id,
            doctor_id=None,  # Nursing assessment
            chief_complaint=f"Nursing Assessment - {assessment_data['assessment_type']}",
            examination_findings=f"General Condition: {assessment_data['general_condition']}\n"
                               f"Consciousness: {assessment_data['consciousness_level']}\n"
                               f"Mobility: {assessment_data['mobility_status']}\n"
                               f"Interventions: {', '.join(assessment_data['nursing_interventions'])}",
            vital_signs={
                "assessment_type": assessment_data["assessment_type"],
                "general_condition": assessment_data["general_condition"],
                "consciousness_level": assessment_data["consciousness_level"],
                "mobility_status": assessment_data["mobility_status"],
                "pain_assessment": assessment_data["pain_assessment"],
                "skin_condition": assessment_data["skin_condition"],
                "wound_assessment": assessment_data["wound_assessment"],
                "nutritional_status": assessment_data["nutritional_status"],
                "elimination_status": assessment_data["elimination_status"],
                "psychosocial_status": assessment_data["psychosocial_status"],
                "family_involvement": assessment_data["family_involvement"],
                "discharge_planning_needs": assessment_data["discharge_planning_needs"],
                "nursing_interventions": assessment_data["nursing_interventions"],
                "goals_for_next_shift": assessment_data["goals_for_next_shift"],
                "assessed_by": f"{current_user.first_name} {current_user.last_name} (Nurse)",
                "assessed_at": datetime.now(timezone.utc).isoformat()
            },
            is_finalized=True
        )
        
        self.db.add(assessment_record)
        await self.db.commit()
        
        return {
            "assessment_id": str(assessment_record.id),
            "admission_number": assessment_data["admission_number"],
            "assessment_type": assessment_data["assessment_type"],
            "general_condition": assessment_data["general_condition"],
            "assessed_by": f"{current_user.first_name} {current_user.last_name} (Nurse)",
            "assessed_at": assessment_record.created_at.isoformat(),
            "message": "Nursing assessment completed successfully"
        }
    
    async def create_doctor_rounds(self, rounds_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Document doctor rounds for IPD patient"""
        user_context = self.get_user_context(current_user)
        
        # Only doctors can document rounds
        if user_context["role"] != UserRole.DOCTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only doctors can document rounds"
            )
        
        # Get doctor profile
        doctor = await self.get_ipd_user_profile(user_context)
        
        # Get admission with department check
        admission = await self.get_admission_by_number_with_department_check(rounds_data["admission_number"], doctor)
        
        # Create doctor rounds as medical record
        rounds_record = MedicalRecord(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            patient_id=admission.patient_id,
            doctor_id=doctor.id,
            chief_complaint=f"Doctor Rounds - {rounds_data['round_type']}",
            examination_findings=rounds_data["clinical_findings"],
            diagnosis=rounds_data["assessment_and_plan"],
            treatment_plan=rounds_data["assessment_and_plan"],
            follow_up_instructions=rounds_data["follow_up_instructions"],
            prescriptions=rounds_data.get("medication_changes", []),
            vital_signs={
                "round_type": rounds_data["round_type"],
                "patient_condition": rounds_data["patient_condition"],
                "clinical_findings": rounds_data["clinical_findings"],
                "assessment_and_plan": rounds_data["assessment_and_plan"],
                "medication_changes": rounds_data.get("medication_changes"),
                "new_orders": rounds_data.get("new_orders"),
                "discharge_planning": rounds_data.get("discharge_planning"),
                "family_discussion": rounds_data.get("family_discussion"),
                "rounds_by": f"Dr. {current_user.first_name} {current_user.last_name}",
                "rounds_at": datetime.now(timezone.utc).isoformat()
            },
            is_finalized=True
        )
        
        self.db.add(rounds_record)
        await self.db.commit()
        
        return {
            "rounds_id": str(rounds_record.id),
            "admission_number": rounds_data["admission_number"],
            "round_type": rounds_data["round_type"],
            "patient_condition": rounds_data["patient_condition"],
            "rounds_by": f"Dr. {current_user.first_name} {current_user.last_name}",
            "rounds_at": rounds_record.created_at.isoformat(),
            "message": "Doctor rounds documented successfully"
        }
    
    async def get_ipd_dashboard(self, current_user: User) -> Dict[str, Any]:
        """Get IPD dashboard with key metrics and patient information"""
        user_context = self.get_user_context(current_user)
        await self.validate_ipd_access(user_context)
        
        # Get user profile
        user_profile = await self.get_ipd_user_profile(user_context)
        
        # Get total admitted patients in department
        total_admitted_result = await self.db.execute(
            select(func.count(Admission.id))
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.is_active == True
                )
            )
        )
        total_admitted = total_admitted_result.scalar() or 0
        
        # Get critical patients (from recent assessments)
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy import cast
        critical_patients_result = await self.db.execute(
            select(func.count(Admission.id.distinct()))
            .join(MedicalRecord, Admission.patient_id == MedicalRecord.patient_id)
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.is_active == True,
                    MedicalRecord.vital_signs.op('@>')(cast('{"general_condition": "CRITICAL"}', JSONB)),
                    MedicalRecord.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
                )
            )
        )
        critical_patients = critical_patients_result.scalar() or 0
        
        # Get today's assessments/rounds by this user
        today = datetime.now(timezone.utc).date()
        if user_context["role"] == UserRole.NURSE:
            assessments_today_result = await self.db.execute(
                select(func.count(MedicalRecord.id))
                .where(
                    and_(
                        MedicalRecord.hospital_id == user_context["hospital_id"],
                        MedicalRecord.chief_complaint.like("Nursing Assessment%"),
                        func.date(MedicalRecord.created_at) == today,
                        MedicalRecord.vital_signs.op('@>')(cast(f'{{"assessed_by": "{current_user.first_name} {current_user.last_name} (Nurse)"}}', JSONB))
                    )
                )
            )
            assessments_today = assessments_today_result.scalar() or 0
            activity_label = "Nursing Assessments Today"
        else:  # Doctor
            assessments_today_result = await self.db.execute(
                select(func.count(MedicalRecord.id))
                .where(
                    and_(
                        MedicalRecord.hospital_id == user_context["hospital_id"],
                        MedicalRecord.doctor_id == user_profile.id,
                        MedicalRecord.chief_complaint.like("Doctor Rounds%"),
                        func.date(MedicalRecord.created_at) == today
                    )
                )
            )
            assessments_today = assessments_today_result.scalar() or 0
            activity_label = "Doctor Rounds Today"
        
        # Get recent admissions (last 7 days)
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_admissions_result = await self.db.execute(
            select(func.count(Admission.id))
            .where(
                and_(
                    Admission.hospital_id == user_context["hospital_id"],
                    Admission.department_id == user_profile.department_id,
                    Admission.admission_date >= week_ago
                )
            )
        )
        recent_admissions = recent_admissions_result.scalar() or 0
        
        return {
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_role": user_context["role"],
            "hospital_id": user_context["hospital_id"],
            "department": user_profile.department.name,
            "dashboard_date": datetime.now(timezone.utc).date().isoformat(),
            "statistics": {
                "total_admitted_patients": total_admitted,
                "critical_patients": critical_patients,
                "recent_admissions_7_days": recent_admissions,
                activity_label.lower().replace(" ", "_"): assessments_today
            },
            "quick_actions": [
                "View IPD patients",
                "Create nursing assessment" if user_context["role"] == UserRole.NURSE else "Document rounds",
                "Record vital signs",
                "View admission details",
                "Discharge planning"
            ]
        }
    
    # ============================================================================
    # HELPER METHODS FOR IPD
    # ============================================================================
    
    async def get_admission_by_number_with_department_check(self, admission_number: str, user_profile) -> Admission:
        """Get admission with department access control"""
        result = await self.db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.admission_number == admission_number,
                    Admission.hospital_id == user_profile.hospital_id,
                    Admission.department_id == user_profile.department_id  # Department-based access
                )
            )
            .options(
                selectinload(Admission.patient).selectinload(PatientProfile.user),
                selectinload(Admission.doctor),
                selectinload(Admission.department)
            )
        )
        
        admission = result.scalar_one_or_none()
        if not admission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Admission {admission_number} not found in your department"
            )
        
        return admission

    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    async def get_doctor_by_name(
        self,
        doctor_name: str,
        hospital_id: str,
        department_id: Optional[uuid.UUID] = None,
    ) -> User:
        """Resolve a doctor by display name (partial, case-insensitive). Optionally scope to a department."""
        dn = (doctor_name or "").strip()
        if not dn:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="doctor_name is required",
            )
        q = (
            select(User)
            .join(user_roles, User.id == user_roles.c.user_id)
            .join(Role, user_roles.c.role_id == Role.id)
            .where(
                and_(
                    User.hospital_id == hospital_id,
                    Role.name == UserRole.DOCTOR,
                    or_(
                        func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(f"%{dn}%"),
                        func.concat(User.first_name, " ", User.last_name).ilike(f"%{dn}%"),
                    ),
                )
            )
        )
        if department_id is not None:
            q = q.join(
                StaffDepartmentAssignment,
                and_(
                    StaffDepartmentAssignment.staff_id == User.id,
                    StaffDepartmentAssignment.department_id == department_id,
                    StaffDepartmentAssignment.is_active == True,
                ),
            )
        result = await self.db.execute(q.limit(2))
        rows = result.scalars().all()
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Doctor '{doctor_name}' not found",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AMBIGUOUS_DOCTOR",
                "message": f"Multiple doctors match '{doctor_name}'. Refine the name or use doctor_id.",
            },
        )

    async def get_department_by_id_or_name(
        self,
        department_id: Optional[Any],
        department_name: Optional[str],
        hospital_id: str,
    ) -> Optional[Department]:
        """Resolve an optional department from UUID/name; return None so caller can derive from doctor."""
        raw_id = str(department_id or "").strip()
        if raw_id:
            try:
                dept_uuid = uuid.UUID(raw_id)
            except (TypeError, ValueError):
                dept_uuid = None
            if dept_uuid is not None:
                result = await self.db.execute(
                    select(Department).where(
                        and_(
                            Department.id == dept_uuid,
                            Department.hospital_id == hospital_id,
                            Department.is_active == True,
                        )
                    )
                )
                department = result.scalar_one_or_none()
                if department:
                    return department

        dname = (department_name or "").strip()
        if dname:
            try:
                return await self.get_department_by_name(dname, hospital_id)
            except HTTPException as exc:
                # A stale/free-text UI value should not block scheduling if the doctor has a department.
                if exc.status_code != status.HTTP_404_NOT_FOUND:
                    raise

        return None

    async def get_primary_department_for_doctor(
        self,
        doctor_id: uuid.UUID,
        hospital_id: uuid.UUID,
    ) -> Department:
        """Resolve the doctor's active department from assignment first, then doctor profile."""
        from app.models.doctor import DoctorProfile

        result = await self.db.execute(
            select(Department)
            .join(
                StaffDepartmentAssignment,
                StaffDepartmentAssignment.department_id == Department.id,
            )
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == doctor_id,
                    StaffDepartmentAssignment.hospital_id == hospital_id,
                    StaffDepartmentAssignment.is_active == True,
                    Department.is_active == True,
                )
            )
            .order_by(desc(StaffDepartmentAssignment.is_primary))
            .limit(1)
        )
        department = result.scalar_one_or_none()
        if department:
            return department

        result = await self.db.execute(
            select(Department)
            .join(DoctorProfile, DoctorProfile.department_id == Department.id)
            .where(
                and_(
                    DoctorProfile.user_id == doctor_id,
                    DoctorProfile.hospital_id == hospital_id,
                    Department.is_active == True,
                )
            )
            .limit(1)
        )
        department = result.scalar_one_or_none()
        if department:
            return department

        department = await self._sync_doctor_department_from_tenant(doctor_id, hospital_id)
        if department:
            return department

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Selected doctor has no active department assignment. "
                "Assign the doctor to a department in Hospital Admin first."
            ),
        )

    async def _sync_doctor_department_from_tenant(
        self,
        doctor_id: uuid.UUID,
        hospital_id: uuid.UUID,
    ) -> Optional[Department]:
        """
        Receptionist OPD routes use the platform DB because OPD patients log in there.
        Hospital Admin writes departments/assignments to tenant DBs, so lazily copy the
        selected doctor's active department into the current session when needed.
        """
        try:
            from app.database.session import get_tenant_session_factory
            from app.database.tenant_context import resolve_tenant_database_name_for_hospital
            from app.models.doctor import DoctorProfile

            tenant_db = await resolve_tenant_database_name_for_hospital(hospital_id)
            if not tenant_db:
                return None

            fac = get_tenant_session_factory(tenant_db)
            tenant_department: Optional[Department] = None
            tenant_assignment: Optional[StaffDepartmentAssignment] = None

            async with fac() as tdb:
                result = await tdb.execute(
                    select(StaffDepartmentAssignment, Department)
                    .join(Department, StaffDepartmentAssignment.department_id == Department.id)
                    .where(
                        and_(
                            StaffDepartmentAssignment.staff_id == doctor_id,
                            StaffDepartmentAssignment.hospital_id == hospital_id,
                            StaffDepartmentAssignment.is_active == True,
                            Department.is_active == True,
                        )
                    )
                    .order_by(desc(StaffDepartmentAssignment.is_primary))
                    .limit(1)
                )
                row = result.first()
                if row:
                    tenant_assignment, tenant_department = row
                else:
                    result = await tdb.execute(
                        select(Department)
                        .join(DoctorProfile, DoctorProfile.department_id == Department.id)
                        .where(
                            and_(
                                DoctorProfile.user_id == doctor_id,
                                DoctorProfile.hospital_id == hospital_id,
                                Department.is_active == True,
                            )
                        )
                        .limit(1)
                    )
                    tenant_department = result.scalar_one_or_none()

                if not tenant_department:
                    return None

                department_data = {
                    col.name: getattr(tenant_department, col.name)
                    for col in Department.__table__.columns
                }
                assignment_data = (
                    {
                        col.name: getattr(tenant_assignment, col.name)
                        for col in StaffDepartmentAssignment.__table__.columns
                    }
                    if tenant_assignment
                    else None
                )

            head_doctor_id = department_data.get("head_doctor_id")
            if head_doctor_id and not await self.db.get(User, head_doctor_id):
                # Keep the department row insertable even when only the selected doctor was mirrored.
                department_data["head_doctor_id"] = None

            existing_department = await self.db.get(Department, department_data["id"])
            if existing_department:
                for key, value in department_data.items():
                    if key != "id":
                        setattr(existing_department, key, value)
                department = existing_department
            else:
                department = Department(**department_data)
                self.db.add(department)
            await self.db.flush()

            if assignment_data:
                existing_assignment = await self.db.get(
                    StaffDepartmentAssignment,
                    assignment_data["id"],
                )
                if existing_assignment:
                    for key, value in assignment_data.items():
                        if key != "id":
                            setattr(existing_assignment, key, value)
                else:
                    self.db.add(StaffDepartmentAssignment(**assignment_data))
                await self.db.flush()

            return department
        except Exception:
            try:
                await self.db.rollback()
            except Exception:
                pass
            logger.exception(
                "Failed to sync doctor department from tenant hospital_id=%s doctor_id=%s",
                hospital_id,
                doctor_id,
            )
            return None

    async def get_department_by_name(self, department_name: str, hospital_id: str) -> Department:
        """Resolve department by name (exact case-insensitive, then partial match if unique)."""
        dname = (department_name or "").strip()
        if not dname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="department_name is required",
            )
        result = await self.db.execute(
            select(Department).where(
                and_(
                    Department.hospital_id == hospital_id,
                    Department.name.ilike(dname),
                    Department.is_active == True,
                )
            )
        )
        department = result.scalar_one_or_none()
        if department:
            return department
        result = await self.db.execute(
            select(Department)
            .where(
                and_(
                    Department.hospital_id == hospital_id,
                    Department.name.ilike(f"%{dname}%"),
                    Department.is_active == True,
                )
            )
            .limit(2)
        )
        rows = result.scalars().all()
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Department '{department_name}' not found",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "AMBIGUOUS_DEPARTMENT",
                "message": f"Multiple departments match '{department_name}'. Use the exact department name.",
            },
        )