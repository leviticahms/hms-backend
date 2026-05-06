"""
Patient Medical History Management API
Comprehensive medical records, history tracking, and health data management.
READ-ONLY for patients; doctors/admins use /patients/{patient_ref}/... endpoints.
Medical record CREATE/UPDATE/FINALIZE: use /api/v1/doctor-patient-records/medical-records
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.core.security import get_current_user
from app.dependencies.auth import get_current_patient
from app.models.user import User
from app.models.patient import PatientProfile, MedicalRecord, Appointment, Admission, DischargeSummary
from app.models.doctor import DoctorProfile
from app.models.hospital import Department, StaffDepartmentAssignment
from app.core.enums import UserRole, AppointmentStatus
from app.schemas.patient_care import (
    PatientMedicalSummaryOut, MedicalRecordOut, MedicalHistoryTimelineOut
)

router = APIRouter(prefix="/patient-medical-history", tags=["Patient Portal - Medical History"])


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


async def get_patient_by_ref(patient_ref: str, hospital_id: Optional[str], db: AsyncSession) -> PatientProfile:
    """Get patient by reference with hospital isolation"""
    if hospital_id:
        result = await db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == hospital_id
                )
            )
            .options(selectinload(PatientProfile.user))
        )
    else:
        # FIX: NEVER allow a query without hospital_id filter — this was a
        # cross-tenant data leakage vulnerability that could expose any patient's
        # records to another hospital's staff. Deny the request instead.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "HOSPITAL_CONTEXT_REQUIRED",
                "message": (
                    "Hospital context is required to access patient records. "
                    "Your token must include hospital_id."
                )
            }
        )
    
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_ref} not found"
        )
    
    return patient


# ============================================================================
# PATIENT SELF-SERVICE ENDPOINTS (no patient_ref needed - from JWT token)
# ============================================================================

@router.get("/my/summary")
async def get_my_medical_summary(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get comprehensive medical summary for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own data from JWT token)
    - No patient_ref needed - identity from token
    """
    patient = current_patient
    # Get visit count
    visit_count_result = await db.execute(
        select(func.count(MedicalRecord.id))
        .where(MedicalRecord.patient_id == patient.id)
    )
    total_visits = visit_count_result.scalar() or 0
    
    # Get last visit date
    last_visit_result = await db.execute(
        select(MedicalRecord.created_at)
        .where(MedicalRecord.patient_id == patient.id)
        .order_by(desc(MedicalRecord.created_at))
        .limit(1)
    )
    last_visit = last_visit_result.scalar_one_or_none()
    
    # Get active conditions from recent records
    recent_records_result = await db.execute(
        select(MedicalRecord.diagnosis)
        .where(
            and_(
                MedicalRecord.patient_id == patient.id,
                MedicalRecord.diagnosis.isnot(None)
            )
        )
        .order_by(desc(MedicalRecord.created_at))
        .limit(5)
    )
    
    active_conditions = []
    for record in recent_records_result.scalars():
        if record and record.strip():
            active_conditions.append(record.strip())
    
    return PatientMedicalSummaryOut(
        patient_ref=patient.patient_id,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        date_of_birth=patient.date_of_birth or "",
        gender=patient.gender or "",
        blood_group=patient.blood_group,
        allergies=patient.allergies or [],
        chronic_conditions=patient.chronic_conditions or [],
        current_medications=patient.current_medications or [],
        emergency_contact={
            "name": patient.emergency_contact_name or "",
            "phone": patient.emergency_contact_phone or "",
            "relation": patient.emergency_contact_relation or ""
        } if patient.emergency_contact_name else {
            "name": "",
            "phone": "",
            "relation": ""
        },
        total_visits=total_visits,
        last_visit_date=last_visit.date().isoformat() if last_visit else None,
        active_conditions=list(set(active_conditions))
    )


@router.get("/my/medical-records")
async def get_my_medical_records(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get paginated medical records for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own records from JWT token)
    - No patient_ref needed - identity from token
    """
    patient = current_patient
    offset = (page - 1) * limit
    
    query = select(MedicalRecord).where(
        MedicalRecord.patient_id == patient.id
    ).options(
        selectinload(MedicalRecord.doctor),
        selectinload(MedicalRecord.appointment)
    ).order_by(desc(MedicalRecord.created_at))
    
    count_query = select(func.count(MedicalRecord.id)).where(
        MedicalRecord.patient_id == patient.id
    )
    total_result = await db.execute(count_query)
    total_records = total_result.scalar() or 0
    
    records_result = await db.execute(query.offset(offset).limit(limit))
    records = records_result.scalars().all()
    
    medical_records = []
    for record in records:
        dept_result = await db.execute(
            select(Department.name)
            .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
            .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
        )
        department_name = dept_result.scalar_one_or_none() or "Unknown"
        
        medical_records.append(MedicalRecordOut(
            id=str(record.id),
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            doctor_name=f"{record.doctor.first_name} {record.doctor.last_name}",
            department_name=department_name,
            appointment_ref=record.appointment.appointment_ref if record.appointment else None,
            visit_date=record.created_at.date().isoformat(),
            chief_complaint=record.chief_complaint,
            diagnosis=record.diagnosis,
            treatment_plan=record.treatment_plan,
            vital_signs=record.vital_signs,
            prescriptions=record.prescriptions or [],
            is_finalized=record.is_finalized,
            created_at=record.created_at.isoformat()
        ))
    
    return {
        "records": medical_records,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_records,
            "pages": (total_records + limit - 1) // limit
        }
    }


@router.get("/my/medical-records/{record_id}")
async def get_my_medical_record_details(
    record_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get detailed medical record by ID for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own records from JWT token)
    - No patient_ref needed - identity from token
    """
    patient = current_patient
    
    record_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.id == record_id,
                MedicalRecord.patient_id == patient.id
            )
        )
        .options(
            selectinload(MedicalRecord.doctor),
            selectinload(MedicalRecord.appointment)
        )
    )
    
    record = record_result.scalar_one_or_none()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Medical record not found"
        )
    
    dept_result = await db.execute(
        select(Department.name)
        .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
        .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
    )
    department_name = dept_result.scalar_one_or_none() or "Unknown"
    
    return {
        "id": str(record.id),
        "patient_ref": patient.patient_id,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "doctor_name": f"{record.doctor.first_name} {record.doctor.last_name}",
        "department_name": department_name,
        "appointment_ref": record.appointment.appointment_ref if record.appointment else None,
        "visit_date": record.created_at.date().isoformat(),
        "chief_complaint": record.chief_complaint,
        "history_of_present_illness": record.history_of_present_illness,
        "past_medical_history": record.past_medical_history,
        "examination_findings": record.examination_findings,
        "vital_signs": record.vital_signs,
        "diagnosis": record.diagnosis,
        "differential_diagnosis": record.differential_diagnosis or [],
        "treatment_plan": record.treatment_plan,
        "follow_up_instructions": record.follow_up_instructions,
        "prescriptions": record.prescriptions or [],
        "lab_orders": record.lab_orders or [],
        "imaging_orders": record.imaging_orders or [],
        "is_finalized": record.is_finalized,
        "finalized_at": record.finalized_at.isoformat() if record.finalized_at else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat()
    }


@router.get("/my/timeline")
async def get_my_medical_timeline(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get medical timeline for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own timeline from JWT token)
    - No patient_ref needed - identity from token
    """
    patient = current_patient
    timeline_events = []
    
    appointments_query = select(Appointment).where(
        Appointment.patient_id == patient.id
    ).options(
        selectinload(Appointment.doctor),
        selectinload(Appointment.department)
    ).order_by(desc(Appointment.appointment_date))
    
    appointments_result = await db.execute(appointments_query)
    appointments = appointments_result.scalars().all()
    
    for appointment in appointments:
        timeline_events.append(MedicalHistoryTimelineOut(
            date=appointment.appointment_date,
            type="appointment",
            title=f"Appointment - {appointment.department.name}",
            description=f"Appointment with {appointment.doctor.first_name} {appointment.doctor.last_name}",
            doctor_name=f"{appointment.doctor.first_name} {appointment.doctor.last_name}",
            department_name=appointment.department.name,
            status=appointment.status
        ))
    
    records_query = select(MedicalRecord).where(
        MedicalRecord.patient_id == patient.id
    ).options(
        selectinload(MedicalRecord.doctor)
    ).order_by(desc(MedicalRecord.created_at))
    
    records_result = await db.execute(records_query)
    records = records_result.scalars().all()
    
    for record in records:
        dept_result = await db.execute(
            select(Department.name)
            .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
            .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
        )
        department_name = dept_result.scalar_one_or_none() or "Unknown"
        
        timeline_events.append(MedicalHistoryTimelineOut(
            date=record.created_at.date().isoformat(),
            type="medical_record",
            title=f"Medical Record - {record.chief_complaint[:50]}...",
            description=record.diagnosis or "Consultation completed",
            doctor_name=f"{record.doctor.first_name} {record.doctor.last_name}",
            department_name=department_name,
            status="completed" if record.is_finalized else "draft"
        ))
    
    timeline_events.sort(key=lambda x: x.date, reverse=True)
    
    return {
        "patient_ref": patient.patient_id,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "timeline": timeline_events
    }


# ============================================================================
# MEDICAL HISTORY ENDPOINTS (for doctors/staff - patient_ref required)
# ============================================================================

@router.get("/patients/{patient_ref}/summary")
async def get_patient_medical_summary(
    patient_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get comprehensive medical summary for a patient.
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients viewing own data: prefer GET /my/summary (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        # Patients can view their own medical summary
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
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    
    # Get visit count
    visit_count_result = await db.execute(
        select(func.count(MedicalRecord.id))
        .where(MedicalRecord.patient_id == patient.id)
    )
    total_visits = visit_count_result.scalar() or 0
    
    # Get last visit date
    last_visit_result = await db.execute(
        select(MedicalRecord.created_at)
        .where(MedicalRecord.patient_id == patient.id)
        .order_by(desc(MedicalRecord.created_at))
        .limit(1)
    )
    last_visit = last_visit_result.scalar_one_or_none()
    
    # Get active conditions from recent records
    recent_records_result = await db.execute(
        select(MedicalRecord.diagnosis)
        .where(
            and_(
                MedicalRecord.patient_id == patient.id,
                MedicalRecord.diagnosis.isnot(None)
            )
        )
        .order_by(desc(MedicalRecord.created_at))
        .limit(5)
    )
    
    active_conditions = []
    for record in recent_records_result.scalars():
        if record and record.strip():
            active_conditions.append(record.strip())
    
    return PatientMedicalSummaryOut(
        patient_ref=patient.patient_id,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        date_of_birth=patient.date_of_birth or "",
        gender=patient.gender or "",
        blood_group=patient.blood_group,
        allergies=patient.allergies or [],
        chronic_conditions=patient.chronic_conditions or [],
        current_medications=patient.current_medications or [],
        emergency_contact={
            "name": patient.emergency_contact_name or "",
            "phone": patient.emergency_contact_phone or "",
            "relation": patient.emergency_contact_relation or ""
        } if patient.emergency_contact_name else {
            "name": "",
            "phone": "",
            "relation": ""
        },
        total_visits=total_visits,
        last_visit_date=last_visit.date().isoformat() if last_visit else None,
        active_conditions=list(set(active_conditions))  # Remove duplicates
    )


@router.get("/patients/{patient_ref}/medical-records")
async def get_patient_medical_records(
    patient_ref: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get paginated medical records for a patient.
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients viewing own data: prefer GET /my/medical-records (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        # Patients can view their own medical records
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
        
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doctor profile not found"
            )
    
    # Build query
    offset = (page - 1) * limit
    
    query = select(MedicalRecord).where(
        MedicalRecord.patient_id == patient.id
    ).options(
        selectinload(MedicalRecord.doctor),
        selectinload(MedicalRecord.appointment)
    ).order_by(desc(MedicalRecord.created_at))
    
    # Apply doctor filter if needed
    if user_context["role"] == UserRole.DOCTOR:
        query = query.where(MedicalRecord.doctor_id == doctor.id)
    
    # Get total count
    count_query = select(func.count(MedicalRecord.id)).where(
        MedicalRecord.patient_id == patient.id
    )
    if user_context["role"] == UserRole.DOCTOR:
        count_query = count_query.where(MedicalRecord.doctor_id == doctor.id)
    
    total_result = await db.execute(count_query)
    total_records = total_result.scalar() or 0
    
    # Get paginated records
    records_result = await db.execute(query.offset(offset).limit(limit))
    records = records_result.scalars().all()
    
    # Format response
    medical_records = []
    for record in records:
        # Get department name from staff assignment
        dept_result = await db.execute(
            select(Department.name)
            .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
            .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
        )
        department_name = dept_result.scalar_one_or_none() or "Unknown"
        
        medical_records.append(MedicalRecordOut(
            id=str(record.id),
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            doctor_name=f"{record.doctor.first_name} {record.doctor.last_name}",
            department_name=department_name,
            appointment_ref=record.appointment.appointment_ref if record.appointment else None,
            visit_date=record.created_at.date().isoformat(),
            chief_complaint=record.chief_complaint,
            diagnosis=record.diagnosis,
            treatment_plan=record.treatment_plan,
            vital_signs=record.vital_signs,
            prescriptions=record.prescriptions or [],
            is_finalized=record.is_finalized,
            created_at=record.created_at.isoformat()
        ))
    
    return {
        "records": medical_records,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_records,
            "pages": (total_records + limit - 1) // limit
        }
    }


@router.get("/patients/{patient_ref}/medical-records/{record_id}")
async def get_medical_record_details(
    patient_ref: str,
    record_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get detailed medical record by ID.
    
    Access Control:
    - **Who can access:** Doctors (records they created), Hospital Admins (all in hospital), Patients (own only)
    - For patients viewing own data: prefer GET /my/medical-records/{record_id}, no patient_ref needed
    """
    user_context = get_user_context(current_user)
    
    # Get patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Get medical record
    record_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.id == record_id,
                MedicalRecord.patient_id == patient.id
            )
        )
        .options(
            selectinload(MedicalRecord.doctor),
            selectinload(MedicalRecord.appointment)
        )
    )
    
    record = record_result.scalar_one_or_none()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Medical record not found"
        )
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        # Patients can view their own medical record details
        if patient.user_id != user_context["user_id"]:
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
        
        if not doctor or record.doctor_id != doctor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Get department name from staff assignment
    dept_result = await db.execute(
        select(Department.name)
        .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
        .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
    )
    department_name = dept_result.scalar_one_or_none() or "Unknown"
    
    return {
        "id": str(record.id),
        "patient_ref": patient.patient_id,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "doctor_name": f"{record.doctor.first_name} {record.doctor.last_name}",
        "department_name": department_name,
        "appointment_ref": record.appointment.appointment_ref if record.appointment else None,
        "visit_date": record.created_at.date().isoformat(),
        "chief_complaint": record.chief_complaint,
        "history_of_present_illness": record.history_of_present_illness,
        "past_medical_history": record.past_medical_history,
        "examination_findings": record.examination_findings,
        "vital_signs": record.vital_signs,
        "diagnosis": record.diagnosis,
        "differential_diagnosis": record.differential_diagnosis or [],
        "treatment_plan": record.treatment_plan,
        "follow_up_instructions": record.follow_up_instructions,
        "prescriptions": record.prescriptions or [],
        "lab_orders": record.lab_orders or [],
        "imaging_orders": record.imaging_orders or [],
        "is_finalized": record.is_finalized,
        "finalized_at": record.finalized_at.isoformat() if record.finalized_at else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat()
    }


@router.get("/patients/{patient_ref}/timeline")
async def get_patient_medical_timeline(
    patient_ref: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get patient's medical timeline with appointments, admissions, and records.
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients viewing own data: prefer GET /my/timeline (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        # Patients can view their own medical timeline
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
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    
    timeline_events = []
    
    # Get appointments
    appointments_query = select(Appointment).where(
        Appointment.patient_id == patient.id
    ).options(
        selectinload(Appointment.doctor),
        selectinload(Appointment.department)
    ).order_by(desc(Appointment.appointment_date))
    
    if user_context["role"] == UserRole.DOCTOR and doctor:
        appointments_query = appointments_query.where(Appointment.doctor_id == doctor.id)
    
    appointments_result = await db.execute(appointments_query)
    appointments = appointments_result.scalars().all()
    
    for appointment in appointments:
        timeline_events.append(MedicalHistoryTimelineOut(
            date=appointment.appointment_date,
            type="appointment",
            title=f"Appointment - {appointment.department.name}",
            description=f"Appointment with {appointment.doctor.first_name} {appointment.doctor.last_name}",
            doctor_name=f"{appointment.doctor.first_name} {appointment.doctor.last_name}",
            department_name=appointment.department.name,
            status=appointment.status
        ))
    
    # Get medical records
    records_query = select(MedicalRecord).where(
        MedicalRecord.patient_id == patient.id
    ).options(
        selectinload(MedicalRecord.doctor)
    ).order_by(desc(MedicalRecord.created_at))
    
    if user_context["role"] == UserRole.DOCTOR and doctor:
        records_query = records_query.where(MedicalRecord.doctor_id == doctor.id)
    
    records_result = await db.execute(records_query)
    records = records_result.scalars().all()
    
    for record in records:
        # Get department name from staff assignment
        dept_result = await db.execute(
            select(Department.name)
            .join(StaffDepartmentAssignment, Department.id == StaffDepartmentAssignment.department_id)
            .where(StaffDepartmentAssignment.staff_id == record.doctor_id)
        )
        department_name = dept_result.scalar_one_or_none() or "Unknown"
        
        timeline_events.append(MedicalHistoryTimelineOut(
            date=record.created_at.date().isoformat(),
            type="medical_record",
            title=f"Medical Record - {record.chief_complaint[:50]}...",
            description=record.diagnosis or "Consultation completed",
            doctor_name=f"{record.doctor.first_name} {record.doctor.last_name}",
            department_name=department_name,
            status="completed" if record.is_finalized else "draft"
        ))
    
    # Sort timeline by date (most recent first)
    timeline_events.sort(key=lambda x: x.date, reverse=True)
    
    return {
        "patient_ref": patient.patient_id,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "timeline": timeline_events
    }