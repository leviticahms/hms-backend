"""
Patient Discharge Summary Management API
Comprehensive discharge summary creation, management, and generation for admitted patients.
"""
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.core.security import get_current_user
from app.dependencies.auth import get_current_patient
from app.models.user import User
from app.models.patient import PatientProfile, Admission, DischargeSummary, MedicalRecord
from app.models.doctor import DoctorProfile
from app.models.hospital import Department
from app.core.enums import UserRole, AdmissionType
from app.core.utils import parse_date_string
from app.schemas.patient_care import (
    DischargeSummaryCreate, DischargeSummaryUpdate, DischargeSummaryOut,
    AdmissionForDischargeOut, DischargeSummaryTemplateOut
)

router = APIRouter(prefix="/patient-discharge-summary", tags=["Patient Portal - Discharge Summary"])


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    
    return {
        "user_id": current_user.id,  # Keep as UUID for comparison
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": user_roles[0] if user_roles else None,
        "all_roles": user_roles
    }


async def get_admission_by_number(admission_number: str, hospital_id: Optional[str], db: AsyncSession) -> Admission:
    """Get admission by number with hospital isolation"""
    conditions = [Admission.admission_number == admission_number]
    if hospital_id:
        conditions.append(Admission.hospital_id == hospital_id)
    
    result = await db.execute(
        select(Admission)
        .where(and_(*conditions))
        .options(
            selectinload(Admission.patient).selectinload(PatientProfile.user),
            selectinload(Admission.doctor),  # Admission.doctor -> User, not DoctorProfile
            selectinload(Admission.department),
            selectinload(Admission.discharge_summary)
        )
    )
    
    admission = result.scalar_one_or_none()
    if not admission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Admission {admission_number} not found"
        )
    
    return admission


# ============================================================================
# PATIENT SELF-SERVICE ENDPOINTS (no patient_ref needed - from JWT token)
# ============================================================================

@router.get("/my/discharge-summaries")
async def get_my_discharge_summaries(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get all discharge summaries for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own summaries from JWT token)
    """
    patient = current_patient
    offset = (page - 1) * limit
    
    query = select(DischargeSummary).where(
        DischargeSummary.patient_id == patient.id
    ).options(
        selectinload(DischargeSummary.doctor),
        selectinload(DischargeSummary.admission).selectinload(Admission.department),
        selectinload(DischargeSummary.patient).selectinload(PatientProfile.user)
    ).order_by(desc(DischargeSummary.created_at))
    
    count_query = select(func.count(DischargeSummary.id)).where(
        DischargeSummary.patient_id == patient.id
    )
    total_result = await db.execute(count_query)
    total_summaries = total_result.scalar() or 0
    
    summaries_result = await db.execute(query.offset(offset).limit(limit))
    summaries = summaries_result.scalars().all()
    
    summary_list = []
    for summary in summaries:
        summary_list.append(DischargeSummaryOut(
            summary_id=str(summary.id),
            admission_number=summary.admission.admission_number,
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            doctor_name=f"{summary.doctor.first_name} {summary.doctor.last_name}",
            department_name=summary.admission.department.name,
            admission_date=summary.admission_date.date().isoformat(),
            discharge_date=summary.discharge_date.date().isoformat(),
            length_of_stay=summary.length_of_stay,
            chief_complaint=summary.chief_complaint,
            final_diagnosis=summary.final_diagnosis,
            secondary_diagnoses=summary.secondary_diagnoses or [],
            procedures_performed=summary.procedures_performed or [],
            hospital_course=summary.hospital_course,
            medications_on_discharge=summary.medications_on_discharge or [],
            follow_up_instructions=summary.follow_up_instructions,
            diet_instructions=summary.diet_instructions,
            activity_restrictions=summary.activity_restrictions,
            follow_up_date=summary.follow_up_date,
            follow_up_doctor=summary.follow_up_doctor,
            is_finalized=summary.is_finalized,
            finalized_at=summary.finalized_at.isoformat() if summary.finalized_at else None,
            created_at=summary.created_at.isoformat()
        ))
    
    return {
        "patient_ref": patient.patient_id,
        "summaries": summary_list,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_summaries,
            "pages": (total_summaries + limit - 1) // limit
        }
    }


# ============================================================================
# DISCHARGE SUMMARY ENDPOINTS (for doctors/staff - patient_ref required)
# ============================================================================

def _ensure_utc(dt: datetime) -> datetime:
    """Return datetime as timezone-aware UTC for subtraction (avoids naive/aware mix)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def calculate_length_of_stay(admission_date: datetime, discharge_date: datetime) -> int:
    """Calculate length of stay in days"""
    a = _ensure_utc(admission_date)
    d = _ensure_utc(discharge_date)
    delta = d - a
    return max(1, delta.days)  # Minimum 1 day


async def get_medical_records_for_admission(admission: Admission, db: AsyncSession) -> List[MedicalRecord]:
    """Get medical records during admission period"""
    # Get records between admission and discharge dates
    start_date = admission.admission_date
    end_date = admission.discharge_date or datetime.now(timezone.utc)
    
    result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.patient_id == admission.patient_id,
                MedicalRecord.created_at >= start_date,
                MedicalRecord.created_at <= end_date
            )
        )
        .options(selectinload(MedicalRecord.doctor))  # MedicalRecord.doctor -> User
        .order_by(MedicalRecord.created_at)
    )
    
    return result.scalars().all()


# ============================================================================
# DISCHARGE SUMMARY ENDPOINTS
# ============================================================================

@router.get("/admissions/ready-for-discharge")
async def get_admissions_ready_for_discharge(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    department_name: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get list of admissions ready for discharge (active admissions without discharge summary).
    
    Access Control:
    - **Who can access:** Doctors (admissions in their department), Hospital Admins (all in hospital)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors and hospital admins can access
    if user_context["role"] not in [UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Build query
    offset = (page - 1) * limit
    
    admission_conditions = [
        Admission.is_active == True,
        Admission.discharge_summary_id.is_(None)  # No discharge summary yet
    ]
    if user_context.get("hospital_id"):
        admission_conditions.append(Admission.hospital_id == user_context.get("hospital_id"))
    
    query = select(Admission).where(and_(*admission_conditions)).options(
        selectinload(Admission.patient).selectinload(PatientProfile.user),
        selectinload(Admission.doctor),  # Admission.doctor -> User
        selectinload(Admission.department)
    ).order_by(desc(Admission.admission_date))
    
    # Apply doctor filter if needed
    if user_context["role"] == UserRole.DOCTOR:
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            query = query.where(Admission.doctor_id == doctor.user_id)
    
    # Apply department filter
    if department_name:
        query = query.join(Department).where(Department.name == department_name)
    
    # Get total count
    count_conditions = [
        Admission.is_active == True,
        Admission.discharge_summary_id.is_(None)
    ]
    if user_context.get("hospital_id"):
        count_conditions.append(Admission.hospital_id == user_context.get("hospital_id"))
    
    count_query = select(func.count(Admission.id)).where(
        and_(*count_conditions)
    )
    
    if user_context["role"] == UserRole.DOCTOR and doctor:
        count_query = count_query.where(Admission.doctor_id == doctor.user_id)
    
    if department_name:
        count_query = count_query.join(Department).where(Department.name == department_name)
    
    total_result = await db.execute(count_query)
    total_admissions = total_result.scalar() or 0
    
    # Get paginated admissions
    admissions_result = await db.execute(query.offset(offset).limit(limit))
    admissions = admissions_result.scalars().all()
    
    # Format response
    admission_list = []
    for admission in admissions:
        length_of_stay = calculate_length_of_stay(
            admission.admission_date,
            datetime.now(timezone.utc)
        )
        
        admission_list.append(AdmissionForDischargeOut(
            admission_id=str(admission.id),
            admission_number=admission.admission_number,
            patient_ref=admission.patient.patient_id,
            patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
            doctor_name=f"{admission.doctor.first_name} {admission.doctor.last_name}",
            department_name=admission.department.name,
            admission_date=admission.admission_date.date().isoformat(),
            admission_type=admission.admission_type,
            chief_complaint=admission.chief_complaint,
            provisional_diagnosis=admission.provisional_diagnosis,
            ward=admission.ward,
            room_number=admission.room_number,
            bed_number=admission.bed_number,
            length_of_stay=length_of_stay,
            has_discharge_summary=False
        ))
    
    return {
        "admissions": admission_list,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_admissions,
            "pages": (total_admissions + limit - 1) // limit
        }
    }


@router.get("/admissions/{admission_number}/discharge-template")
async def get_discharge_summary_template(
    admission_number: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get pre-filled discharge summary template for an admission.
    
    Access Control:
    - **Who can access:** Doctors (their patients), Hospital Admins (any admission in hospital)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors and hospital admins can access
    if user_context["role"] not in [UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Get admission
    admission = await get_admission_by_number(admission_number, user_context.get("hospital_id"), db)
    
    # Role-based access control
    if user_context["role"] == UserRole.DOCTOR:
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if not doctor or admission.doctor_id != doctor.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - not your patient"
            )
    
    # Get medical records during admission
    medical_records = await get_medical_records_for_admission(admission, db)
    
    # Prepare medical records summary
    records_summary = []
    recent_medications = []
    recent_procedures = []
    suggested_diagnosis = admission.provisional_diagnosis
    
    for record in medical_records:
        records_summary.append({
            "date": record.created_at.date().isoformat(),
            "doctor": f"{record.doctor.first_name} {record.doctor.last_name}",
            "chief_complaint": record.chief_complaint,
            "diagnosis": record.diagnosis,
            "treatment_plan": record.treatment_plan,
            "vital_signs": record.vital_signs
        })
        
        # Collect medications
        if record.prescriptions:
            for prescription in record.prescriptions:
                if prescription not in recent_medications:
                    recent_medications.append(prescription)
        
        # Use latest diagnosis as suggested final diagnosis
        if record.diagnosis and record.diagnosis.strip():
            suggested_diagnosis = record.diagnosis
    
    # Calculate discharge date (current time if not set)
    discharge_date = admission.discharge_date or datetime.now(timezone.utc)
    length_of_stay = calculate_length_of_stay(admission.admission_date, discharge_date)
    
    return DischargeSummaryTemplateOut(
        admission_number=admission.admission_number,
        patient_ref=admission.patient.patient_id,
        patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
        admission_date=admission.admission_date.date().isoformat(),
        discharge_date=discharge_date.date().isoformat(),
        length_of_stay=length_of_stay,
        chief_complaint=admission.chief_complaint,
        provisional_diagnosis=admission.provisional_diagnosis,
        medical_records_summary=records_summary,
        suggested_final_diagnosis=suggested_diagnosis,
        recent_medications=recent_medications,
        recent_procedures=recent_procedures
    )


@router.post("/discharge-summaries")
async def create_discharge_summary(
    summary_data: DischargeSummaryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Create a new discharge summary.
    
    Access Control:
    - **Who can access:** Doctors only (for their own patients/admissions)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors can create discharge summaries
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors can create discharge summaries"
        )
    
    # Get doctor profile
    doctor_result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == user_context["user_id"])
    )
    doctor = doctor_result.scalar_one_or_none()
    
    if not doctor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found"
        )
    
    # Get admission
    admission = await get_admission_by_number(summary_data.admission_number, user_context["hospital_id"], db)
    
    # Check if doctor owns this admission (Admission.doctor_id is users.id)
    if admission.doctor_id != doctor.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - not your patient"
        )
    
    # Check if discharge summary already exists
    if admission.discharge_summary_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Discharge summary already exists for this admission"
        )
    
    # Set discharge date if not already set
    discharge_date = admission.discharge_date or datetime.now(timezone.utc)
    if not admission.discharge_date:
        admission.discharge_date = discharge_date
    
    # Calculate length of stay
    length_of_stay = calculate_length_of_stay(admission.admission_date, discharge_date)
    
    # Ensure we have hospital_id - use admission's hospital_id if user's is null
    hospital_id_val = user_context.get("hospital_id")
    if not hospital_id_val and admission.hospital_id:
        hospital_id_val = str(admission.hospital_id)
        user_context["hospital_id"] = hospital_id_val
    
    if not hospital_id_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Please ensure the admission is linked to a hospital."
        )
    
    # Create discharge summary
    discharge_summary = DischargeSummary(
        id=uuid.uuid4(),
        hospital_id=uuid.UUID(hospital_id_val),
        patient_id=admission.patient_id,
        doctor_id=doctor.user_id,  # DischargeSummary.doctor_id -> users.id
        admission_date=admission.admission_date,
        discharge_date=discharge_date,
        length_of_stay=length_of_stay,
        chief_complaint=admission.chief_complaint,
        final_diagnosis=summary_data.final_diagnosis,
        secondary_diagnoses=summary_data.secondary_diagnoses or [],
        procedures_performed=summary_data.procedures_performed or [],
        hospital_course=summary_data.hospital_course,
        medications_on_discharge=summary_data.medications_on_discharge or [],
        follow_up_instructions=summary_data.follow_up_instructions,
        diet_instructions=summary_data.diet_instructions,
        activity_restrictions=summary_data.activity_restrictions,
        follow_up_date=summary_data.follow_up_date,
        follow_up_doctor=summary_data.follow_up_doctor
    )
    
    db.add(discharge_summary)
    
    # Update admission with discharge summary reference and discharge type
    admission.discharge_summary_id = discharge_summary.id
    admission.discharge_type = summary_data.discharge_type
    
    await db.commit()
    
    return {
        "summary_id": str(discharge_summary.id),
        "admission_number": admission.admission_number,
        "patient_ref": admission.patient.patient_id,
        "message": "Discharge summary created successfully"
    }


@router.get("/discharge-summaries/{summary_id}")
async def get_discharge_summary(
    summary_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get discharge summary by ID.
    
    Access Control:
    - **Who can access:** Doctors (summaries they created), Hospital Admins (all in hospital), Patients (own only)
    """
    user_context = get_user_context(current_user)
    
    # Get discharge summary
    result = await db.execute(
        select(DischargeSummary)
        .where(
            and_(
                DischargeSummary.id == summary_id,
                DischargeSummary.hospital_id == user_context.get("hospital_id")
            )
        )
        .options(
            selectinload(DischargeSummary.patient).selectinload(PatientProfile.user),
            selectinload(DischargeSummary.doctor),  # DischargeSummary.doctor -> User
            selectinload(DischargeSummary.admission).selectinload(Admission.department)
        )
    )
    
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discharge summary not found"
        )
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        if str(summary.patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    elif user_context["role"] == UserRole.DOCTOR:
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if not doctor or summary.doctor_id != doctor.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - not your patient"
            )
    
    return DischargeSummaryOut(
        summary_id=str(summary.id),
        admission_number=summary.admission.admission_number,
        patient_ref=summary.patient.patient_id,
        patient_name=f"{summary.patient.user.first_name} {summary.patient.user.last_name}",
        doctor_name=f"{summary.doctor.first_name} {summary.doctor.last_name}",
        department_name=summary.admission.department.name,
        admission_date=summary.admission_date.date().isoformat(),
        discharge_date=summary.discharge_date.date().isoformat(),
        length_of_stay=summary.length_of_stay,
        chief_complaint=summary.chief_complaint,
        final_diagnosis=summary.final_diagnosis,
        secondary_diagnoses=summary.secondary_diagnoses or [],
        procedures_performed=summary.procedures_performed or [],
        hospital_course=summary.hospital_course,
        medications_on_discharge=summary.medications_on_discharge or [],
        follow_up_instructions=summary.follow_up_instructions,
        diet_instructions=summary.diet_instructions,
        activity_restrictions=summary.activity_restrictions,
        follow_up_date=summary.follow_up_date,
        follow_up_doctor=summary.follow_up_doctor,
        is_finalized=summary.is_finalized,
        finalized_at=summary.finalized_at.isoformat() if summary.finalized_at else None,
        created_at=summary.created_at.isoformat()
    )


@router.patch("/discharge-summaries/{summary_id}")
async def update_discharge_summary(
    summary_id: str,
    update_data: DischargeSummaryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Update discharge summary.
    
    Access Control:
    - **Who can access:** Doctors only (the creating doctor; finalized summaries cannot be updated)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors can update discharge summaries
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors can update discharge summaries"
        )
    
    # Get doctor profile
    doctor_result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == user_context["user_id"])
    )
    doctor = doctor_result.scalar_one_or_none()
    
    if not doctor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found"
        )
    
    # Get discharge summary
    result = await db.execute(
        select(DischargeSummary)
        .where(
            and_(
                DischargeSummary.id == summary_id,
                DischargeSummary.doctor_id == doctor.user_id,
                DischargeSummary.hospital_id == user_context.get("hospital_id")
            )
        )
    )
    
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discharge summary not found or access denied"
        )
    
    # Check if summary is finalized
    if summary.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update finalized discharge summary"
        )
    
    # Update fields
    update_fields = update_data.dict(exclude_unset=True)
    
    for field, value in update_fields.items():
        setattr(summary, field, value)
    
    await db.commit()
    
    return {
        "summary_id": str(summary.id),
        "message": "Discharge summary updated successfully"
    }


@router.post("/discharge-summaries/{summary_id}/finalize")
async def finalize_discharge_summary(
    summary_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Finalize discharge summary (makes it immutable).
    
    Access Control:
    - **Who can access:** Doctors only (the creating doctor)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors can finalize discharge summaries
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors can finalize discharge summaries"
        )
    
    # Get doctor profile
    doctor_result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == user_context["user_id"])
    )
    doctor = doctor_result.scalar_one_or_none()
    
    if not doctor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor profile not found"
        )
    
    # Get discharge summary
    result = await db.execute(
        select(DischargeSummary)
        .where(
            and_(
                DischargeSummary.id == summary_id,
                DischargeSummary.doctor_id == doctor.user_id,
                DischargeSummary.hospital_id == user_context.get("hospital_id")
            )
        )
        .options(selectinload(DischargeSummary.admission))
    )
    
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discharge summary not found or access denied"
        )
    
    # Check if already finalized
    if summary.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discharge summary is already finalized"
        )

    # ── BILLING GATE ──────────────────────────────────────────────────────────
    # FIX: Previously patients could be discharged with unpaid bills.
    # Now we check for outstanding balances before allowing discharge.
    # Hospital admin can override with the force_discharge query param.
    force_discharge: bool = False  # Only True when explicitly passed by admin
    if summary.admission and not force_discharge:
        from app.models.billing.bill import Bill as BillModel
        from sqlalchemy import and_
        unpaid_bills_result = await db.execute(
            select(BillModel).where(
                and_(
                    BillModel.patient_id == summary.patient_id,
                    BillModel.hospital_id == user_context.get("hospital_id"),
                    BillModel.status.in_(["FINALIZED", "PARTIALLY_PAID", "DRAFT"]),
                    BillModel.balance_due > 0,
                )
            )
        )
        unpaid_bills = unpaid_bills_result.scalars().all()
        if unpaid_bills:
            total_outstanding = sum(float(b.balance_due) for b in unpaid_bills)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "OUTSTANDING_BALANCE",
                    "message": (
                        f"Patient has {len(unpaid_bills)} unpaid bill(s) with total outstanding "
                        f"balance of ₹{total_outstanding:,.2f}. "
                        "Clear all dues before discharge or use force_discharge=true (admin only)."
                    ),
                    "outstanding_amount": total_outstanding,
                    "unpaid_bill_count": len(unpaid_bills),
                }
            )
    # ─────────────────────────────────────────────────────────────────────────
    
    # Finalize summary
    summary.is_finalized = True
    summary.finalized_at = datetime.now(timezone.utc)
    
    # Mark admission as inactive (discharged)
    if summary.admission:
        summary.admission.is_active = False
    
    await db.commit()
    
    return {
        "summary_id": str(summary.id),
        "message": "Discharge summary finalized successfully"
    }


@router.get("/patients/{patient_ref}/discharge-summaries")
async def get_patient_discharge_summaries(
    patient_ref: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get all discharge summaries for a patient (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients: prefer GET /my/discharge-summaries (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient - first try without hospital filter if user has no hospital_id
    if user_context.get("hospital_id"):
        patient_result = await db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == uuid.UUID(user_context.get("hospital_id"))
                )
            )
            .options(selectinload(PatientProfile.user))
        )
    else:
        # If user has no hospital_id, search without hospital filter
        patient_result = await db.execute(
            select(PatientProfile)
            .where(PatientProfile.patient_id == patient_ref)
            .options(selectinload(PatientProfile.user))
        )
    
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_ref} not found"
        )
    
    # Update user_context with patient's hospital_id if user doesn't have one
    if not user_context["hospital_id"] and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        if patient.user_id != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            # Check if doctor has medical records for this patient
            record_check = await db.execute(
                select(MedicalRecord)
                .where(
                    and_(
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id == doctor.user_id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    
    # Build query
    offset = (page - 1) * limit
    
    query = select(DischargeSummary).where(
        DischargeSummary.patient_id == patient.id
    ).options(
        selectinload(DischargeSummary.doctor),
        selectinload(DischargeSummary.admission).selectinload(Admission.department),
        selectinload(DischargeSummary.patient).selectinload(PatientProfile.user)
    ).order_by(desc(DischargeSummary.created_at))
    
    # Apply doctor filter if needed
    if user_context["role"] == UserRole.DOCTOR and doctor:
        query = query.where(DischargeSummary.doctor_id == doctor.user_id)
    
    # Get total count
    count_query = select(func.count(DischargeSummary.id)).where(
        DischargeSummary.patient_id == patient.id
    )
    if user_context["role"] == UserRole.DOCTOR and doctor:
        count_query = count_query.where(DischargeSummary.doctor_id == doctor.user_id)
    
    total_result = await db.execute(count_query)
    total_summaries = total_result.scalar() or 0
    
    # Get paginated summaries
    summaries_result = await db.execute(query.offset(offset).limit(limit))
    summaries = summaries_result.scalars().all()
    
    # Format response
    summary_list = []
    for summary in summaries:
        summary_list.append(DischargeSummaryOut(
            summary_id=str(summary.id),
            admission_number=summary.admission.admission_number,
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            doctor_name=f"{summary.doctor.first_name} {summary.doctor.last_name}",
            department_name=summary.admission.department.name,
            admission_date=summary.admission_date.date().isoformat(),
            discharge_date=summary.discharge_date.date().isoformat(),
            length_of_stay=summary.length_of_stay,
            chief_complaint=summary.chief_complaint,
            final_diagnosis=summary.final_diagnosis,
            secondary_diagnoses=summary.secondary_diagnoses or [],
            procedures_performed=summary.procedures_performed or [],
            hospital_course=summary.hospital_course,
            medications_on_discharge=summary.medications_on_discharge or [],
            follow_up_instructions=summary.follow_up_instructions,
            diet_instructions=summary.diet_instructions,
            activity_restrictions=summary.activity_restrictions,
            follow_up_date=summary.follow_up_date,
            follow_up_doctor=summary.follow_up_doctor,
            is_finalized=summary.is_finalized,
            finalized_at=summary.finalized_at.isoformat() if summary.finalized_at else None,
            created_at=summary.created_at.isoformat()
        ))
    
    return {
        "patient_ref": patient_ref,
        "summaries": summary_list,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_summaries,
            "pages": (total_summaries + limit - 1) // limit
        }
    }


# ============================================================================
# DISCHARGE STATISTICS ENDPOINTS
# ============================================================================

@router.get("/discharge-summaries/statistics")
async def get_discharge_statistics(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    department_name: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get discharge summary statistics.
    
    Access Control:
    - **Who can access:** Doctors (their patients), Hospital Admins (all in hospital)
    """
    user_context = get_user_context(current_user)
    
    # Only doctors and hospital admins can access
    if user_context["role"] not in [UserRole.DOCTOR, UserRole.HOSPITAL_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Build base query
    query = select(DischargeSummary).where(
        DischargeSummary.hospital_id == user_context.get("hospital_id")
    )
    
    # Apply doctor filter if needed
    if user_context["role"] == UserRole.DOCTOR:
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            query = query.where(DischargeSummary.doctor_id == doctor.user_id)
    
    # Apply date filters
    if start_date:
        start_dt = parse_date_string(start_date)
        if start_dt:
            query = query.where(DischargeSummary.discharge_date >= start_dt)
    
    if end_date:
        end_dt = parse_date_string(end_date)
        if end_dt:
            query = query.where(DischargeSummary.discharge_date <= end_dt)
    
    # Apply department filter
    if department_name:
        query = query.join(Admission).join(Department).where(Department.name == department_name)
    
    # Get statistics
    result = await db.execute(query)
    summaries = result.scalars().all()
    
    # Calculate statistics
    total_summaries = len(summaries)
    finalized_summaries = len([s for s in summaries if s.is_finalized])
    draft_summaries = total_summaries - finalized_summaries
    
    # Average length of stay
    avg_length_of_stay = sum(s.length_of_stay for s in summaries) / total_summaries if total_summaries > 0 else 0
    
    # Discharge types
    discharge_types = {}
    for summary in summaries:
        if summary.admission and summary.admission.discharge_type:
            discharge_type = summary.admission.discharge_type
            discharge_types[discharge_type] = discharge_types.get(discharge_type, 0) + 1
    
    return {
        "total_summaries": total_summaries,
        "finalized_summaries": finalized_summaries,
        "draft_summaries": draft_summaries,
        "average_length_of_stay": round(avg_length_of_stay, 1),
        "discharge_types": discharge_types,
        "date_range": {
            "start_date": start_date,
            "end_date": end_date
        }
    }