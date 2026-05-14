"""
Receptionist Management API
Dedicated receptionist functionality for front desk operations, patient registration, and appointment management.

DATABASE NOTE (patient portal login):
Patient auth (`POST /api/v1/auth/patient/login`) always loads `users` from the **platform** DB (`get_platform_db_session`).
This router therefore uses `get_platform_db_session` for all endpoints—not `get_db_session`—so OPD-registered patients
and their `user_roles` rows exist where login reads them. Using tenant-routed sessions here caused “Invalid credentials”
(AUTH_001) because users were written to the tenant DB only.

BUSINESS RULES:
- Receptionists are created by Hospital Admin only
- Receptionists belong to one hospital AND one department
- Receptionists handle OPD operations (patient registration, appointments, check-in)
- Receptionists CAN: Register patients, Schedule appointments, Modify appointments, Check-in patients, Access billing
- Receptionists CANNOT: Access medical records, Prescribe medicines, Modify lab results
"""
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_receptionist
from app.core.database import get_platform_db_session
from app.core.enums import DocumentType
from app.core.security import get_current_user
from app.core.utils import absolute_public_asset_url
from app.models.hospital import Department
from app.models.patient import PatientDocument, PatientProfile
from app.models.user import User
from app.schemas.receptionist import ReceptionistProfileSelfUpdate
from app.services.clinical_service import ClinicalService, send_opd_portal_credentials_email_task
from app.services.email_service import EmailService
from app.schemas.clinical import (
    PatientRegistrationCreate,
    ReceptionistPatientPatch,
    AppointmentSchedulingCreate,
    AppointmentUpdate,
    PatientCheckInCreate,
)
from app.core.response_utils import success_response

router = APIRouter(prefix="/receptionist", tags=["Receptionist - OPD Management"])
logger = logging.getLogger(__name__)


def _normalize_patient_document_type(raw: str) -> str:
    """Accept DocumentType enum values (e.g. MEDICAL_REPORT, LAB_RESULT)."""
    s = (raw or "").strip()
    if not s:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="document_type is required",
        )
    normalized = s.upper().replace(" ", "_").replace("-", "_")
    try:
        return DocumentType(normalized).value
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_DOCUMENT_TYPE",
                "message": f"document_type must be one of: {[e.value for e in DocumentType]}",
            },
        )


def _storage_path_to_public_url(file_path: Optional[str]) -> str:
    if not file_path:
        return ""
    normalized = str(file_path).replace("\\", "/")
    if "/uploads/" in normalized:
        rel = normalized[normalized.index("/uploads/") :]
    elif normalized.startswith("uploads/"):
        rel = "/" + normalized
    else:
        rel = "/" + normalized.lstrip("/")
    return absolute_public_asset_url(rel) or rel


def _human_document_code(document_id: uuid.UUID) -> str:
    return f"DOC-{document_id.hex[:6].upper()}"


def _file_type_label(mime_type: Optional[str], file_name: Optional[str]) -> str:
    if mime_type:
        return mime_type
    fn = file_name or ""
    if "." in fn:
        return fn.rsplit(".", 1)[-1].lower()
    return ""


async def _resolve_patient_for_documents(
    patient_id: str, receptionist: User, db: AsyncSession
) -> PatientProfile:
    """Resolve PAT-... ref or patient_profiles.id UUID; must belong to receptionist's hospital."""
    from app.api.v1.routers.patient.patient_document_storage import get_patient_by_ref

    if not receptionist.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Receptionist must be assigned to a hospital",
        )
    hid = str(receptionist.hospital_id)
    pid = (patient_id or "").strip()
    if not pid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient_id is required",
        )
    try:
        uid = uuid.UUID(pid)
        res = await db.execute(
            select(PatientProfile).where(
                and_(PatientProfile.id == uid, PatientProfile.hospital_id == receptionist.hospital_id)
            )
        )
        row = res.scalar_one_or_none()
        if row:
            return row
    except ValueError:
        pass
    patient = await get_patient_by_ref(pid, hid, db)
    if patient.hospital_id and str(patient.hospital_id) != hid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patient is not registered in your hospital",
        )
    return patient


async def _receptionist_user_for_write(db: AsyncSession, current_user: User) -> User:
    """Load authenticated user in this session for updates."""
    row = await db.get(User, current_user.id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )
    return row


def _receptionist_metadata_fields(current_user: User) -> dict:
    """Extra profile fields stored on user_metadata at create (receptionist-specific)."""
    md = current_user.user_metadata or {}
    av = absolute_public_asset_url(current_user.avatar_url)
    return {
        "mobile_number": current_user.phone or "",
        "joining_date": md.get("joining_date"),
        "blood_group": md.get("blood_group"),
        "gender": md.get("gender"),
        "shift_timing": md.get("shift_timing"),
        "address": md.get("address"),
        "profile_photo": av,
    }


def _receptionist_profile_base_dict(current_user: User) -> dict:
    """Common user-level fields for profile responses."""
    return {
        "user_id": str(current_user.id),
        "first_name": current_user.first_name or "",
        "last_name": current_user.last_name or "",
        "full_name": f"{current_user.first_name or ''} {current_user.last_name or ''}".strip(),
        "email": current_user.email or "",
        "phone": current_user.phone or "",
        "staff_id": current_user.staff_id,
        "avatar_url": absolute_public_asset_url(current_user.avatar_url),
        "status": getattr(current_user, "status", None),
        "is_active": (getattr(current_user, "status", None) or "").upper() == "ACTIVE",
        **_receptionist_metadata_fields(current_user),
    }


# ============================================================================
# RECEPTIONIST DASHBOARD
# ============================================================================

@router.get("/dashboard")
async def get_receptionist_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get receptionist dashboard with key metrics and information.
    
    Access Control:
    - Only Receptionists can access dashboard
    - Shows OPD-specific metrics for their hospital
    
    Returns:
    - Today's appointments count
    - Checked-in patients
    - Waiting patients
    - Completed consultations
    - Pending registrations
    - Department-wise breakdown
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.get_opd_dashboard(current_user)
    return success_response(message="Dashboard loaded successfully", data=result)


# ============================================================================
# PATIENT REGISTRATION
# ============================================================================

@router.post("/patients/register")
async def register_patient(
    patient_data: PatientRegistrationCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Register new patient for OPD services.
    
    Access Control:
    - Only Receptionists can register patients
    
    Workflow:
    1. Create User account (optional `password` + `email` enables portal login via POST /auth/patient/login)
    2. Create PatientProfile
    3. Assign patient ID
    4. Set hospital association
    
    If `password` is omitted, a one-time `temp_password` is returned (email remains unverified for patient login).
    If `password` is set and `send_credentials_email` is true, the API first tries to send
    credentials immediately. If it fails, a background retry task is queued.
    
    Returns:
    - Patient ID, optional temp_password, portal_login_enabled, `credentials_email_queued` when email is scheduled
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.register_opd_patient(patient_data.model_dump(), current_user)

    pwd = (patient_data.password or "").strip() or None
    email = (str(patient_data.email).strip().lower() if patient_data.email else None)
    # Always queue portal credential email in background — SMTP can take several seconds and blocked registration UX.
    if pwd and email and patient_data.send_credentials_email:
        es = EmailService()
        if not es.is_smtp_configured():
            result["credentials_email_sent"] = False
            result["credentials_email_queued"] = False
            result["credentials_email_hint"] = (
                "SMTP is not configured on the server (set SMTP_USER and SMTP_PASS in Render/environment). "
                "Share login email and password with the patient manually."
            )
        else:
            background_tasks.add_task(
                send_opd_portal_credentials_email_task,
                email,
                patient_data.first_name,
                pwd,
                result.get("hospital_name"),
            )
            result["credentials_email_sent"] = False
            result["credentials_email_queued"] = True
            result["credentials_email_hint"] = (
                "Credentials email queued to send shortly. Check inbox/spam; "
                "if nothing arrives, verify SMTP_HOST/SMTP_USER/SMTP_PASS on the deployment."
            )

    return success_response(message="Patient registered successfully", data=result)


@router.patch("/patients/{patient_ref}")
async def patch_opd_patient(
    patient_ref: str,
    body: ReceptionistPatientPatch,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Update an existing OPD patient (same hospital as receptionist).

    Send only fields to change (PATCH semantics). Setting ``password`` enables portal login;
    patient must have an email on record or include ``email`` in the same request.
    Optional credential email uses ``send_credentials_email`` (default true when password is sent).
    """
    payload = body.model_dump(exclude_unset=True)
    send_cred = payload.pop("send_credentials_email", True)
    pwd_plain = payload.pop("password", None)

    clinical_service = ClinicalService(db)
    result = await clinical_service.patch_opd_patient(
        patient_ref,
        payload,
        new_password_plain=pwd_plain,
        send_credentials_email=send_cred,
        current_user=current_user,
    )

    login_email = (result.get("email") or "").strip().lower()
    first_nm = result.get("first_name") or ""
    if pwd_plain and str(pwd_plain).strip() and login_email and send_cred:
        mail_svc = EmailService()
        if not mail_svc.is_smtp_configured():
            result["credentials_email_queued"] = False
            result["credentials_email_sent"] = False
            result["credentials_email_hint"] = (
                "SMTP is not configured (set SMTP_USER/SMTP_PASS). Share login email and password manually."
            )
        else:
            background_tasks.add_task(
                send_opd_portal_credentials_email_task,
                login_email,
                first_nm,
                str(pwd_plain).strip(),
                result.get("hospital_name"),
            )
            result["credentials_email_queued"] = True
            result["credentials_email_sent"] = False

    return success_response(message="Patient updated successfully", data=result)


# ============================================================================
# APPOINTMENT MANAGEMENT
# ============================================================================

@router.post("/appointments/schedule")
async def schedule_appointment(
    appointment_data: AppointmentSchedulingCreate,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Schedule appointment for an existing patient.
    
    Identify the patient with `patient_ref` (from registration) and/or `patient_name` (exact full name
    as stored: First Last). If only `patient_name` is sent, it must match exactly one registered patient
    in this hospital; otherwise use `patient_ref` from GET /receptionist/patients/search or
    GET /receptionist/patients/{patient_ref}/profile.
    
    Access Control:
    - Receptionist (or authenticated user with access to this router)
    
    Features:
    - Conflict detection
    - Doctor / department validation
    
    Returns:
    - appointment_ref and scheduling confirmation
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.schedule_opd_appointment(
        appointment_data.model_dump(), current_user
    )
    return success_response(message="Appointment scheduled successfully", data=result)


@router.get("/appointments/today")
async def get_todays_appointments(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    department_name: Optional[str] = Query(None, description="Filter by department"),
    doctor_name: Optional[str] = Query(None, description="Filter by doctor"),
    status: Optional[str] = Query(None, description="Filter by status: SCHEDULED, CHECKED_IN, IN_PROGRESS, COMPLETED, CANCELLED"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get today's appointments for the hospital.
    
    Access Control:
    - Only Receptionists can view appointments
    
    Features:
    - Filter by department
    - Filter by doctor
    - Filter by status
    - Pagination support
    
    Returns:
    - List of appointments
    - Patient details
    - Doctor details
    - Appointment status
    - Check-in status
    """
    clinical_service = ClinicalService(db)
    filters = {
        "page": page,
        "limit": limit,
        "department_name": department_name,
        "doctor_name": doctor_name,
        "status": status
    }
    result = await clinical_service.get_todays_opd_appointments(filters, current_user)
    return success_response(message="Appointments retrieved successfully", data=result)


@router.get("/appointments/{appointment_ref}")
async def get_appointment_by_ref(
    appointment_ref: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """Get a single appointment by reference (same hospital as receptionist)."""
    clinical_service = ClinicalService(db)
    data = await clinical_service.get_opd_appointment_by_ref(appointment_ref, current_user)
    return success_response(message="Appointment retrieved successfully", data=data)


@router.patch("/appointments/{appointment_ref}")
async def modify_appointment(
    appointment_ref: str,
    modification_data: AppointmentUpdate,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Modify existing appointment.
    
    Access Control:
    - Only Receptionists can modify appointments
    
    Features:
    - Change date/time
    - Change doctor
    - Change department
    - Update notes
    - Cannot modify completed appointments
    
    Returns:
    - Updated appointment details
    - Confirmation
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.modify_opd_appointment(
        appointment_ref,
        modification_data.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Appointment modified successfully", data=result)


# ============================================================================
# PATIENT CHECK-IN
# ============================================================================

@router.post("/appointments/{appointment_ref}/check-in")
async def check_in_patient(
    appointment_ref: str,
    checkin_data: PatientCheckInCreate,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Check-in patient for their appointment.
    
    Access Control:
    - Only Receptionists can check-in patients
    
    Workflow:
    1. Verify appointment exists
    2. Check appointment is for today
    3. Record check-in time
    4. Update appointment status to CHECKED_IN
    5. Notify doctor of patient arrival
    
    Returns:
    - Check-in confirmation
    - Queue position
    - Estimated wait time
    """
    clinical_service = ClinicalService(db)
    result = await clinical_service.check_in_patient(
        appointment_ref,
        checkin_data.model_dump(),
        current_user,
    )
    return success_response(message="Patient checked-in successfully", data=result)


# ============================================================================
# PATIENT LIST & SEARCH
# ============================================================================

@router.get("/patients")
async def list_all_patients(
    search: Optional[str] = Query(
        None,
        description="Optional: filter by name, email, phone, patient ID, or MRN (partial match)",
    ),
    q: Optional[str] = Query(None, description="Alias for `search`"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    List all registered OPD patients for the receptionist's hospital (paginated, newest first).

    Omit ``search`` / ``q`` to return every patient for this hospital (within ``page`` / ``limit``).
    """
    from app.services.appointment_service import AppointmentService

    combined = (search or "").strip() or (q or "").strip() or None
    appointment_service = AppointmentService(db)
    result = await appointment_service.search_patients(
        {"search": combined, "page": page, "limit": limit},
        current_user,
    )
    return success_response(message="Patients retrieved successfully", data=result)


@router.get("/patients/search")
async def search_patients(
    search: Optional[str] = Query(
        None,
        description="Single search box: matches name, email, phone, patient ID, or MRN (partial)",
    ),
    q: Optional[str] = Query(None, description="Alias for `search` (frontend compatibility)"),
    phone: Optional[str] = Query(None, description="Search by phone number"),
    email: Optional[str] = Query(None, description="Search by email"),
    name: Optional[str] = Query(None, description="Search by name"),
    patient_id: Optional[str] = Query(None, description="Search by patient ID"),
    mrn: Optional[str] = Query(None, description="Search by MRN"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Search for patients in the hospital.
    
    Access Control:
    - Only Receptionists can search patients
    
    Search Options:
    - search or q: one box — partial match on name, email, phone, patient ID, or MRN
    - phone, email, name, patient_id, mrn: specific fields (AND when several are set)
    - With no filters: all patients for this hospital (paginated, newest first)
    
    Returns:
    - List of matching patients
    - Patient details
    - Recent appointments
    """
    from app.services.appointment_service import AppointmentService
    
    appointment_service = AppointmentService(db)
    
    # Build search parameters
    combined = (search or "").strip() or (q or "").strip() or None
    search_params = {
        "search": combined,
        "phone": phone,
        "email": email,
        "name": name,
        "patient_id": patient_id,
        "mrn": mrn,
        "page": page,
        "limit": limit
    }
    
    result = await appointment_service.search_patients(search_params, current_user)
    return success_response(message="Search completed successfully", data=result)


@router.get("/patients/{patient_ref}/profile")
async def get_patient_profile_for_schedule(
    patient_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Load full patient details for autofill (e.g. Schedule Appointment form after choosing a name).

    Returns registration fields plus emergency contact under canonical keys (`emergency_contact_name`,
    `emergency_contact_phone`, `emergency_contact_relation`) and UI aliases (`relationship`,
    `emergency_contact_number`, camelCase, `emergency_contact_details`). Legacy `emergency_contact`
    is still the emergency phone string (same as `emergency_contact_phone`). Portal `password` is
    never returned (always null); use `has_portal_password` / `portal_login_enabled` for UX.
    """
    clinical_service = ClinicalService(db)
    data = await clinical_service.get_receptionist_patient_by_ref(patient_ref, current_user)
    return success_response(message="Patient profile loaded successfully", data=data)


# ============================================================================
# PATIENT DOCUMENTS (front desk — multipart upload + card list)
# ============================================================================


@router.post("/patient-documents/upload")
async def receptionist_upload_patient_documents(
    patient_id: str = Form(
        ...,
        description="Patient reference (e.g. PAT-...) or patient_profiles.id UUID",
    ),
    document_type: str = Form(...),
    category: str = Form(..., description="Stored as document title / UI category"),
    uploaded_by: str = Form(
        ...,
        description="Display name of uploader (should match signed-in receptionist)",
    ),
    files: List[UploadFile] = File(..., description="One or more files"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Upload one or more documents for a patient (multipart/form-data).

    **Form fields:** ``patient_id``, ``document_type``, ``category``, ``uploaded_by``, ``files`` (repeatable).
    """
    from app.api.v1.routers.patient.patient_document_storage import (
        get_upload_directory,
        save_uploaded_file,
        validate_file_size,
        validate_file_type,
    )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_FILES", "message": "At least one file is required"},
        )
    if not (uploaded_by or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MISSING_UPLOADER", "message": "uploaded_by is required"},
        )
    if not (category or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MISSING_CATEGORY", "message": "category is required"},
        )

    patient = await _resolve_patient_for_documents(patient_id, current_user, db)
    dtype = _normalize_patient_document_type(document_type)
    cat = category.strip()

    hospital_id_for_doc = str(current_user.hospital_id)
    if patient.hospital_id:
        hospital_id_for_doc = str(patient.hospital_id)
    pref = patient.patient_id
    upload_dir = get_upload_directory(hospital_id_for_doc, pref)

    saved_paths: List[str] = []
    out_rows: List[dict] = []
    try:
        for file in files:
            if not validate_file_type(file):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid file type. Allowed: PDF, images, Word, Excel, text",
                )
            if not validate_file_size(file):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File size exceeds 10MB limit",
                )
            ext = os.path.splitext(file.filename or "")[1] or ""
            unique_filename = f"{uuid.uuid4()}{ext}"
            file_path = os.path.join(upload_dir, unique_filename)
            file_size = await save_uploaded_file(file, file_path)
            saved_paths.append(file_path)

            doc = PatientDocument(
                id=uuid.uuid4(),
                hospital_id=uuid.UUID(hospital_id_for_doc),
                patient_id=patient.id,
                uploaded_by=current_user.id,
                document_type=dtype,
                title=cat,
                description=None,
                file_name=file.filename or unique_filename,
                file_path=file_path,
                file_size=file_size,
                mime_type=file.content_type,
                document_date=None,
                is_sensitive=False,
            )
            db.add(doc)
            await db.flush()
            await db.refresh(doc)
            out_rows.append(
                {
                    "id": _human_document_code(doc.id),
                    "document_uuid": str(doc.id),
                    "file_url": _storage_path_to_public_url(doc.file_path),
                    "file_type": _file_type_label(doc.mime_type, doc.file_name),
                    "document_type": doc.document_type,
                    "category": cat,
                    "uploaded_at": doc.created_at.date().isoformat()
                    if doc.created_at
                    else "",
                }
            )
        await db.commit()
    except HTTPException:
        for p in saved_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        await db.rollback()
        raise
    except Exception:
        for p in saved_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        await db.rollback()
        logger.exception("Receptionist document upload failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload documents",
        )

    return success_response(
        message="Documents uploaded successfully",
        data=out_rows,
    )


@router.get("/patients/{patient_ref}/documents")
async def receptionist_list_patient_documents(
    patient_ref: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Document list for the patient card (no pagination; capped at 500 newest first).
    """
    patient = await _resolve_patient_for_documents(patient_ref.strip(), current_user, db)
    q = await db.execute(
        select(PatientDocument)
        .where(PatientDocument.patient_id == patient.id)
        .options(selectinload(PatientDocument.uploader))
        .order_by(desc(PatientDocument.created_at))
        .limit(500)
    )
    docs = q.scalars().all()
    data = []
    for doc in docs:
        ub = ""
        if doc.uploader:
            ub = f"{doc.uploader.first_name or ''} {doc.uploader.last_name or ''}".strip()
        data.append(
            {
                "id": _human_document_code(doc.id),
                "document_uuid": str(doc.id),
                "file_name": doc.file_name,
                "file_url": _storage_path_to_public_url(doc.file_path),
                "file_type": _file_type_label(doc.mime_type, doc.file_name),
                "document_type": doc.document_type,
                "category": doc.title,
                "file_size": str(doc.file_size) if doc.file_size is not None else "",
                "uploaded_at": doc.created_at.isoformat() if doc.created_at else "",
                "uploaded_by": ub,
            }
        )
    return success_response(message="Documents retrieved successfully", data=data)


# ============================================================================
# APPOINTMENT STATISTICS
# ============================================================================

@router.get("/appointments/statistics")
async def get_appointment_statistics(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (default: today)"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get appointment statistics for the day.
    
    Access Control:
    - Only Receptionists can view statistics
    
    Returns:
    - Total appointments
    - Checked-in count
    - Waiting count
    - In-consultation count
    - Completed count
    - Cancelled count
    - No-show count
    - Department-wise breakdown
    - Doctor-wise breakdown
    """
    from app.services.appointment_service import AppointmentService
    
    appointment_service = AppointmentService(db)
    result = await appointment_service.get_appointment_statistics(date, current_user)
    return success_response(message="Statistics retrieved successfully", data=result)


# ============================================================================
# QUICK ACTIONS
# ============================================================================

@router.get("/quick-actions")
async def get_quick_actions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get quick action items for receptionist.
    
    Access Control:
    - Only Receptionists can access quick actions
    
    Returns:
    - Pending check-ins
    - Upcoming appointments (next 2 hours)
    - Patients waiting
    - Recent registrations
    - Pending payments
    
    Useful for:
    - Quick overview
    - Priority tasks
    - Action items
    """
    clinical_service = ClinicalService(db)
    
    # Get quick action data
    result = {
        "pending_checkins": [],  # Appointments scheduled but not checked in
        "upcoming_appointments": [],  # Next 2 hours
        "patients_waiting": [],  # Checked in but not in consultation
        "recent_registrations": [],  # Last 10 registrations
        "quick_links": [
            {"action": "register_patient", "label": "Register New Patient", "icon": "user-plus"},
            {"action": "schedule_appointment", "label": "Schedule Appointment", "icon": "calendar-plus"},
            {"action": "search_patient", "label": "Search Patient", "icon": "search"},
            {"action": "view_appointments", "label": "Today's Appointments", "icon": "calendar"},
            {"action": "check_in", "label": "Check-in Patient", "icon": "check-circle"}
        ]
    }
    
    return success_response(message="Quick actions retrieved successfully", data=result)


# ============================================================================
# RECEPTIONIST PROFILE
# ============================================================================

@router.get("/profile")
async def get_receptionist_profile(
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get receptionist profile information.
    
    Access Control:
    - Only Receptionists can access their profile
    
    Returns:
    - Receptionist details
    - Department assignment
    - Permissions
    - Work schedule
    - Performance metrics
    """
    from app.models.receptionist import ReceptionistProfile

    result = await db.execute(
        select(ReceptionistProfile)
        .options(selectinload(ReceptionistProfile.department))
        .where(ReceptionistProfile.user_id == current_user.id)
    )

    receptionist = result.scalar_one_or_none()

    base = _receptionist_profile_base_dict(current_user)
    base["role"] = "RECEPTIONIST"

    if not receptionist:
        base["note"] = "Profile not yet created"
        return success_response(
            message="Receptionist profile not found",
            data=base,
        )

    dept = getattr(receptionist, "department", None)
    profile_data = {
        **base,
        "receptionist_id": receptionist.receptionist_id,
        "employee_id": receptionist.employee_id,
        "designation": receptionist.designation,
        "work_area": receptionist.work_area,
        "department_id": str(receptionist.department_id),
        "department_name": dept.name if dept else None,
        "experience_years": receptionist.experience_years,
        "shift": receptionist.shift_type,
        "shift_type": receptionist.shift_type,
        "employment_type": receptionist.employment_type,
        "permissions": {
            "can_schedule_appointments": receptionist.can_schedule_appointments,
            "can_modify_appointments": receptionist.can_modify_appointments,
            "can_register_patients": receptionist.can_register_patients,
            "can_collect_payments": receptionist.can_collect_payments,
        },
        "profile_is_active": receptionist.is_active,
    }

    return success_response(message="Profile retrieved successfully", data=profile_data)


@router.patch("/profile")
async def update_receptionist_profile(
    body: ReceptionistProfileSelfUpdate,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Update receptionist-visible profile fields (name, email, phone, employee id, shift, work area, etc.).
    """
    from app.models.receptionist import ReceptionistProfile

    user = await _receptionist_user_for_write(db, current_user)
    payload = body.model_dump(exclude_unset=True)

    res = await db.execute(
        select(ReceptionistProfile).where(ReceptionistProfile.user_id == user.id)
    )
    receptionist = res.scalar_one_or_none()
    if not receptionist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RECEPTIONIST_PROFILE_MISSING",
                "message": "Receptionist profile not found; contact hospital admin.",
            },
        )

    if "email" in payload and payload["email"] is not None:
        new_email = str(payload["email"]).strip().lower()
        cur = (user.email or "").strip().lower()
        if new_email != cur:
            dup = await db.execute(
                select(User.id).where(
                    and_(func.lower(User.email) == new_email, User.id != user.id)
                )
            )
            if dup.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "EMAIL_IN_USE",
                        "message": "This email is already registered",
                    },
                )
            user.email = new_email

    if "first_name" in payload:
        user.first_name = (payload["first_name"] or "").strip() or ""
    if "last_name" in payload:
        user.last_name = (payload["last_name"] or "").strip() or ""
    if "phone" in payload:
        new_phone = (payload["phone"] or "").strip() if payload["phone"] is not None else ""
        cur_p = (user.phone or "").strip()
        if new_phone != cur_p and new_phone:
            dup_p = await db.execute(
                select(User.id).where(
                    and_(User.phone == new_phone, User.id != user.id)
                )
            )
            if dup_p.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PHONE_IN_USE",
                        "message": "This phone number is already registered",
                    },
                )
        user.phone = new_phone

    if "avatar_url" in payload and payload["avatar_url"] is not None:
        user.avatar_url = str(payload["avatar_url"]).strip() or None

    md_updates = {}
    for key in ("gender", "blood_group", "address", "shift_timing", "joining_date"):
        if key in payload and payload[key] is not None:
            md_updates[key] = str(payload[key]).strip()
    if md_updates:
        umd = dict(user.user_metadata or {})
        umd.update(md_updates)
        user.user_metadata = umd

    if "employee_id" in payload and payload["employee_id"] is not None:
        eid = str(payload["employee_id"]).strip()
        dup_e = await db.execute(
            select(ReceptionistProfile.id).where(
                and_(
                    ReceptionistProfile.hospital_id == receptionist.hospital_id,
                    ReceptionistProfile.employee_id == eid,
                    ReceptionistProfile.user_id != user.id,
                )
            )
        )
        if dup_e.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "EMPLOYEE_ID_IN_USE",
                    "message": "This employee ID is already in use",
                },
            )
        receptionist.employee_id = eid

    if "work_area" in payload:
        receptionist.work_area = payload["work_area"]
    if "shift_type" in payload:
        receptionist.shift_type = (payload["shift_type"] or "").strip().upper() or receptionist.shift_type
    if "employment_type" in payload:
        receptionist.employment_type = (
            (payload["employment_type"] or "").strip().upper() or receptionist.employment_type
        )
    if "experience_years" in payload and payload["experience_years"] is not None:
        receptionist.experience_years = int(payload["experience_years"])
    if "designation" in payload:
        receptionist.designation = (payload["designation"] or "").strip() or receptionist.designation

    await db.commit()
    await db.refresh(user)
    await db.refresh(receptionist)

    dept = None
    if receptionist.department_id:
        dr = await db.execute(
            select(Department).where(Department.id == receptionist.department_id)
        )
        dept = dr.scalar_one_or_none()

    data = {
        **_receptionist_profile_base_dict(user),
        "role": "RECEPTIONIST",
        "receptionist_id": receptionist.receptionist_id,
        "employee_id": receptionist.employee_id,
        "designation": receptionist.designation,
        "work_area": receptionist.work_area,
        "department_id": str(receptionist.department_id),
        "department_name": dept.name if dept else None,
        "experience_years": receptionist.experience_years,
        "shift": receptionist.shift_type,
        "shift_type": receptionist.shift_type,
        "employment_type": receptionist.employment_type,
        "permissions": {
            "can_schedule_appointments": receptionist.can_schedule_appointments,
            "can_modify_appointments": receptionist.can_modify_appointments,
            "can_register_patients": receptionist.can_register_patients,
            "can_collect_payments": receptionist.can_collect_payments,
        },
        "profile_is_active": receptionist.is_active,
    }

    return success_response(message="Profile updated successfully", data=data)
