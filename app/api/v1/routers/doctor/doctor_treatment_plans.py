"""
Doctor Treatment Plan Documentation API
Comprehensive treatment plan management system for doctors with plan creation,
progress tracking, milestone management, and outcome documentation.

BUSINESS RULES:
- Only Doctors can create and manage treatment plans
- Hospital isolation applied to all data
- Treatment plans are linked to specific patients
- Progress tracking with milestone management
- Collaborative care planning support
- Evidence-based treatment protocols
"""
import uuid
from typing import Any, List, Optional, Dict, Union, Tuple
from datetime import datetime, timedelta, date, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, text
from sqlalchemy.orm import selectinload, joinedload
from app.services.clinical_service import ClinicalService
from app.core.database import get_db_session, get_platform_db_session
from app.services.patient_resolve import (
    clinical_db_sessions as _clinical_db_sessions,
    load_patient_by_ref,
    parse_hospital_uuid,
    resolve_staff_hospital_id,
)
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile, Appointment, MedicalRecord
from app.models.doctor import DoctorProfile, TreatmentPlan
from app.models.hospital import Department
from app.core.enums import UserRole
from app.core.utils import generate_patient_ref
from app.schemas.doctor import (
    TreatmentGoalOut, TreatmentInterventionOut, TreatmentMilestoneOut,
    ProgressNoteOut, TreatmentPlanSummaryOut, DetailedTreatmentPlanOut,
    TreatmentPlanCreate, TreatmentPlanUpdate, ProgressUpdate, TreatmentOutcomeOut,
    PlanPriority, TreatmentType, MilestoneStatus, OutcomeStatus, ReviewFrequency,
    TreatmentPlanStatus
)
# from app.services.clinical_service import get_checked_in_patients, ClinicalService
# from app.dependencies.auth import require_doctor
# from app.schemas.appointment import CheckedInPatientsListResponse

router = APIRouter(prefix="/doctor-treatment-plans", tags=["Doctor Portal - Treatment Plans"])


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    primary_role = UserRole.DOCTOR.value if UserRole.DOCTOR.value in user_roles else (user_roles[0] if user_roles else None)
    
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": primary_role,
        "all_roles": user_roles
    }


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile with department information"""
    if UserRole.DOCTOR.value not in user_context.get("all_roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )
    
    # First try to get DoctorProfile
    result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == user_context["user_id"])
        .options(
            selectinload(DoctorProfile.user),
            selectinload(DoctorProfile.department)
        )
    )
    
    doctor = result.scalar_one_or_none()
    
    # If no DoctorProfile exists, create a mock profile using User and department assignment
    if not doctor:
        # Get doctor user
        doctor_result = await db.execute(
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
        from app.models.hospital import StaffDepartmentAssignment
        assignment_result = await db.execute(
            select(StaffDepartmentAssignment)
            .where(StaffDepartmentAssignment.staff_id == user_context["user_id"])
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = assignment_result.scalar_one_or_none()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not assigned to any department. Please contact administrator."
            )
            
        profile_pk_result = await db.execute(
            select(DoctorProfile.id).where(
                and_(
                    DoctorProfile.user_id == doctor_user.id,
                    DoctorProfile.hospital_id == doctor_user.hospital_id,
                )
            )
        )
        profile_pk = profile_pk_result.scalar_one_or_none()

        class MockDoctorProfile:
            def __init__(self, user, department, profile_id: Optional[uuid.UUID]):
                self.user = user
                self.department = department
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                self.department_id = department.id
                self.id = profile_id or user.id
                
                # Professional details (mock values)
                self.doctor_id = f"DOC-{user.id}"
                self.medical_license_number = f"LIC-{user.id}"
                self.designation = "General Practitioner"
                self.specialization = department.name or "General Medicine"
                self.sub_specialization = None
                
                # Experience and qualifications (mock values)
                self.experience_years = 5
                self.qualifications = ["MBBS"]
                self.certifications = []
                self.medical_associations = []
                
                # Consultation details (mock values)
                self.consultation_fee = 500.00
                self.follow_up_fee = 300.00
                
                # Availability (mock values)
                self.is_available_for_emergency = True
                self.is_accepting_new_patients = True
                
                # Profile information (mock values)
                self.bio = f"Experienced doctor in {department.name}"
                self.languages_spoken = ["English"]
        
        doctor = MockDoctorProfile(doctor_user, assignment.department, profile_pk)
    
    return doctor


async def _treatment_plan_doctor_ids(
    doctor: Any,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    hospital_id: uuid.UUID,
) -> List[uuid.UUID]:
    """TreatmentPlan.doctor_id -> doctor_profiles.id (include legacy user.id rows)."""
    user_id = getattr(doctor, "user_id", None) or getattr(doctor, "id", None)
    ids: List[uuid.UUID] = []
    if getattr(doctor, "id", None):
        ids.append(doctor.id)
    if user_id and user_id not in ids:
        ids.append(user_id)
    if user_id:
        for session in _clinical_db_sessions(tenant_db, platform_db):
            profile_result = await session.execute(
                select(DoctorProfile.id).where(
                    and_(
                        DoctorProfile.user_id == user_id,
                        DoctorProfile.hospital_id == hospital_id,
                    )
                )
            )
            profile_id = profile_result.scalar_one_or_none()
            if profile_id and profile_id not in ids:
                ids.append(profile_id)
    return ids


async def _resolve_doctor_hospital(
    doctor: Any,
    current_user: User,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
) -> uuid.UUID:
    fallback = getattr(doctor, "hospital_id", None) or current_user.hospital_id
    return await resolve_staff_hospital_id(
        current_user, tenant_db, platform_db, fallback=fallback
    )


async def _fetch_plan_for_doctor(
    plan_id: str,
    doctor: Any,
    hid: uuid.UUID,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
) -> Tuple[TreatmentPlan, AsyncSession]:
    try:
        plan_uuid = uuid.UUID(str(plan_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan_id",
        )

    doctor_ids = await _treatment_plan_doctor_ids(doctor, tenant_db, platform_db, hid)
    load_opts = (
        joinedload(TreatmentPlan.patient).joinedload(PatientProfile.user),
        joinedload(TreatmentPlan.doctor).joinedload(DoctorProfile.user),
    )
    for session in _clinical_db_sessions(tenant_db, platform_db):
        plan_result = await session.execute(
            select(TreatmentPlan)
            .where(
                and_(
                    TreatmentPlan.id == plan_uuid,
                    TreatmentPlan.hospital_id == hid,
                    TreatmentPlan.doctor_id.in_(doctor_ids),
                )
            )
            .options(*load_opts)
        )
        plan = plan_result.unique().scalar_one_or_none()
        if plan:
            return plan, session

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Treatment plan not found",
    )


def _safe_patient_display(patient: Optional[PatientProfile]) -> tuple[str, str]:
    if not patient:
        return "", "Unknown"
    pref = patient.patient_id or ""
    user = getattr(patient, "user", None)
    if not user:
        return pref, "Unknown"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return pref, name or "Unknown"


def _safe_doctor_display(doctor_profile: Optional[DoctorProfile]) -> str:
    if not doctor_profile:
        return "Dr."
    user = getattr(doctor_profile, "user", None)
    if not user:
        return "Dr."
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return f"Dr. {name}" if name else "Dr."


def _doctor_header_name(doctor: Any, current_user: User) -> str:
    user = getattr(doctor, "user", None) or current_user
    return f"Dr. {user.first_name or ''} {user.last_name or ''}".strip() or "Dr."


async def _fetch_treatment_plans(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    hospital_id: uuid.UUID,
    doctor_ids: List[uuid.UUID],
    status: Optional[TreatmentPlanStatus],
    patient_id: Optional[uuid.UUID],
    limit: int,
    offset: int,
) -> List[TreatmentPlan]:
    seen: set[uuid.UUID] = set()
    plans: List[TreatmentPlan] = []
    conditions: List[Any] = [
        TreatmentPlan.hospital_id == hospital_id,
        TreatmentPlan.doctor_id.in_(doctor_ids),
    ]
    if status:
        conditions.append(TreatmentPlan.status == status.value if hasattr(status, "value") else status)
    if patient_id:
        conditions.append(TreatmentPlan.patient_id == patient_id)

    load_opts = (
        joinedload(TreatmentPlan.patient).joinedload(PatientProfile.user),
        joinedload(TreatmentPlan.doctor).joinedload(DoctorProfile.user),
    )

    for session in _clinical_db_sessions(tenant_db, platform_db):
        result = await session.execute(
            select(TreatmentPlan)
            .where(and_(*conditions))
            .options(*load_opts)
            .order_by(desc(TreatmentPlan.created_at))
            .limit(limit)
            .offset(offset)
        )
        for plan in result.unique().scalars().all():
            if plan.id in seen:
                continue
            seen.add(plan.id)
            plans.append(plan)
    return plans


async def _fetch_all_plans_for_doctor(
    doctor: Any,
    hid: uuid.UUID,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    *,
    extra_conditions: Optional[List[Any]] = None,
) -> List[TreatmentPlan]:
    doctor_ids = await _treatment_plan_doctor_ids(doctor, tenant_db, platform_db, hid)
    conditions: List[Any] = [
        TreatmentPlan.hospital_id == hid,
        TreatmentPlan.doctor_id.in_(doctor_ids),
    ]
    if extra_conditions:
        conditions.extend(extra_conditions)
    seen: set[uuid.UUID] = set()
    merged: List[TreatmentPlan] = []
    load_opts = (
        joinedload(TreatmentPlan.patient).joinedload(PatientProfile.user),
        joinedload(TreatmentPlan.doctor).joinedload(DoctorProfile.user),
    )
    for session in _clinical_db_sessions(tenant_db, platform_db):
        result = await session.execute(
            select(TreatmentPlan).where(and_(*conditions)).options(*load_opts)
        )
        for plan in result.unique().scalars().all():
            if plan.id in seen:
                continue
            seen.add(plan.id)
            merged.append(plan)
    return merged


def ensure_doctor_access(user_context: dict):
    """Ensure user is a doctor"""
    if UserRole.DOCTOR.value not in user_context.get("all_roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )


def generate_plan_id() -> str:
    """Generate unique treatment plan ID"""
    import random
    import string
    
    # Format: PLAN-YYYYMMDD-XXXXXX
    date_str = datetime.now().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"PLAN-{date_str}-{random_part}"


def generate_milestone_id() -> str:
    """Generate unique milestone ID"""
    import random
    import string
    
    # Format: MILE-XXXXXX
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"MILE-{random_part}"


def generate_goal_id() -> str:
    """Generate unique goal ID"""
    import random
    import string
    
    # Format: GOAL-XXXXXX
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"GOAL-{random_part}"


def calculate_progress_percentage(goals: List[dict], milestones: List[dict]) -> int:
    """Calculate overall progress percentage"""
    if not goals and not milestones:
        return 0
    
    total_items = len(goals) + len(milestones)
    completed_items = 0
    
    # Count completed goals
    for goal in goals:
        if goal.get('progress_percentage', 0) >= 100:
            completed_items += 1
    
    # Count completed milestones
    for milestone in milestones:
        if milestone.get('status') == MilestoneStatus.COMPLETED:
            completed_items += 1
    
    return int((completed_items / total_items) * 100) if total_items > 0 else 0


def calculate_age_from_dob(date_of_birth: str) -> int:
    """Calculate age from date of birth"""
    try:
        birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except:
        return 0


# ============================================================================
# TREATMENT PLAN MANAGEMENT
# ============================================================================

@router.get("/plans")
async def get_treatment_plans(
    status: Optional[TreatmentPlanStatus] = Query(None),
    priority: Optional[PlanPriority] = Query(None),
    patient_ref: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get treatment plans with filtering options.
    
    Access Control:
    - **Who can access:** Doctors only (own hospital)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    doctor_ids = await _treatment_plan_doctor_ids(doctor, tenant_db, platform_db, hid)

    patient_uuid: Optional[uuid.UUID] = None
    if patient_ref:
        try:
            resolved = await load_patient_by_ref(
                patient_ref,
                hid,
                tenant_db,
                platform_db,
            )
            patient_uuid = resolved.id
        except HTTPException:
            patient_uuid = None
        if not patient_uuid:
            return {
                "doctor_name": _doctor_header_name(doctor, current_user),
                "total_plans": 0,
                "plans": [],
                "filters_applied": {
                    "status": status,
                    "priority": priority,
                    "patient_ref": patient_ref,
                },
            }

    plans = await _fetch_treatment_plans(
        tenant_db,
        platform_db,
        hid,
        doctor_ids,
        status,
        patient_uuid,
        limit,
        offset,
    )

    plan_summaries = []
    for plan in plans:
        short_term_goals = plan.short_term_goals or []
        long_term_goals = plan.long_term_goals or []
        milestones = plan.milestones or []
        all_goals = short_term_goals + long_term_goals
        total_goals = len(all_goals)
        completed_goals = sum(
            1 for goal in all_goals if goal.get("progress_percentage", 0) >= 100
        )
        total_milestones = len(milestones)
        completed_milestones = sum(
            1 for milestone in milestones if milestone.get("status") == "COMPLETED"
        )
        progress_percentage = calculate_progress_percentage(all_goals, milestones)
        patient_ref_out, patient_name = _safe_patient_display(plan.patient)
        created_by = _safe_doctor_display(plan.doctor)
        created_at = plan.created_at
        created_date = (
            created_at.strftime("%Y-%m-%d")
            if created_at
            else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )

        plan_summaries.append(
            TreatmentPlanSummaryOut(
                plan_id=str(plan.id),
                plan_name=plan.plan_name,
                patient_ref=patient_ref_out,
                patient_name=patient_name,
                primary_diagnosis=plan.primary_diagnosis,
                status=TreatmentPlanStatus(plan.status),
                priority=PlanPriority.MEDIUM,
                start_date=plan.start_date,
                expected_end_date=plan.expected_end_date,
                completion_date=plan.completion_date,
                progress_percentage=progress_percentage,
                total_goals=total_goals,
                completed_goals=completed_goals,
                total_milestones=total_milestones,
                completed_milestones=completed_milestones,
                last_review_date=None,
                next_review_date=None,
                created_by=created_by,
                created_date=created_date,
            )
        )

    return {
        "doctor_name": _doctor_header_name(doctor, current_user),
        "filters_applied": {
            "status": status,
            "priority": priority,
            "patient_ref": patient_ref,
        },
        "total_plans": len(plan_summaries),
        "plans": plan_summaries,
    }
    
@router.get("/checkin-patients")
async def get_checkedin_patients(
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
 
    service = ClinicalService(db=tenant_db, platform_db=platform_db, tenant_db=tenant_db)
    result = await service.get_doctor_checkedin_patients(current_user)
    return result


@router.get("/plans/{plan_id}")
async def get_treatment_plan_details(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get detailed treatment plan information.
    
    Access Control:
    - Only Doctors can access treatment plan details
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, _plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)

    # Process goals
    short_term_goals = []
    for goal_data in (plan.short_term_goals or []):
        short_term_goals.append(TreatmentGoalOut(
            goal_id=goal_data.get('goal_id', generate_goal_id()),
            description=goal_data.get('description', ''),
            target_date=goal_data.get('target_date'),
            priority=PlanPriority(goal_data.get('priority', 'MEDIUM')),
            measurable_outcome=goal_data.get('measurable_outcome', ''),
            current_status=goal_data.get('current_status', 'In Progress'),
            progress_percentage=goal_data.get('progress_percentage', 0),
            notes=goal_data.get('notes')
        ))
    
    long_term_goals = []
    for goal_data in (plan.long_term_goals or []):
        long_term_goals.append(TreatmentGoalOut(
            goal_id=goal_data.get('goal_id', generate_goal_id()),
            description=goal_data.get('description', ''),
            target_date=goal_data.get('target_date'),
            priority=PlanPriority(goal_data.get('priority', 'MEDIUM')),
            measurable_outcome=goal_data.get('measurable_outcome', ''),
            current_status=goal_data.get('current_status', 'In Progress'),
            progress_percentage=goal_data.get('progress_percentage', 0),
            notes=goal_data.get('notes')
        ))
    
    # Process interventions
    interventions = []
    for intervention_data in (plan.medications or []):  # Using medications as interventions
        interventions.append(TreatmentInterventionOut(
            intervention_id=str(uuid.uuid4()),
            intervention_type=TreatmentType.MEDICATION,
            description=intervention_data.get('name', ''),
            instructions=intervention_data.get('instructions', ''),
            frequency=intervention_data.get('frequency', ''),
            duration=intervention_data.get('duration'),
            start_date=plan.start_date,
            end_date=plan.expected_end_date,
            responsible_provider=_safe_doctor_display(plan.doctor),
            status="ACTIVE",
            notes=None
        ))
    
    # Process milestones
    milestones = []
    for milestone_data in (plan.milestones or []):
        milestones.append(TreatmentMilestoneOut(
            milestone_id=milestone_data.get('milestone_id', generate_milestone_id()),
            title=milestone_data.get('title', ''),
            description=milestone_data.get('description', ''),
            target_date=milestone_data.get('target_date', plan.expected_end_date or plan.start_date),
            status=MilestoneStatus(milestone_data.get('status', 'PENDING')),
            completion_date=milestone_data.get('completion_date'),
            completion_notes=milestone_data.get('completion_notes'),
            dependencies=milestone_data.get('dependencies', []),
            assigned_to=milestone_data.get('assigned_to')
        ))
    
    patient_ref_out, patient_name = _safe_patient_display(plan.patient)
    doctor_label = _safe_doctor_display(plan.doctor)

    # Process progress notes
    progress_notes = []
    for note_data in (plan.progress_notes or []):
        progress_notes.append(ProgressNoteOut(
            note_id=str(uuid.uuid4()),
            date=note_data.get('date', datetime.now().strftime("%Y-%m-%d")),
            author=note_data.get('author', doctor_label),
            note_type=note_data.get('note_type', 'PROGRESS'),
            content=note_data.get('content', ''),
            milestone_id=note_data.get('milestone_id'),
            attachments=note_data.get('attachments', []),
            is_significant=note_data.get('is_significant', False)
        ))
    
    # Calculate progress
    all_goals = short_term_goals + long_term_goals
    progress_percentage = calculate_progress_percentage(
        [goal.dict() for goal in all_goals],
        [milestone.dict() for milestone in milestones]
    )
    
    return DetailedTreatmentPlanOut(
        plan_id=str(plan.id),
        plan_name=plan.plan_name,
        patient_ref=patient_ref_out,
        patient_name=patient_name,
        patient_age=calculate_age_from_dob(plan.patient.date_of_birth if plan.patient else None),
        patient_gender=plan.patient.gender if plan.patient else None,
        primary_diagnosis=plan.primary_diagnosis,
        secondary_diagnoses=plan.secondary_diagnoses or [],
        comorbidities=plan.patient.chronic_conditions or [],
        allergies=plan.patient.allergies or [],
        current_medications=plan.patient.current_medications or [],
        status=TreatmentPlanStatus(plan.status),
        priority=PlanPriority.MEDIUM,  # Default
        start_date=plan.start_date,
        expected_end_date=plan.expected_end_date,
        completion_date=plan.completion_date,
        estimated_duration=None,  # Would need to be calculated
        short_term_goals=short_term_goals,
        long_term_goals=long_term_goals,
        interventions=interventions,
        milestones=milestones,
        progress_percentage=progress_percentage,
        progress_notes=progress_notes,
        review_frequency=ReviewFrequency.MONTHLY,  # Default
        last_review_date=None,
        next_review_date=None,
        primary_doctor=doctor_label,
        care_team=[doctor_label],
        created_by=doctor_label,
        created_date=plan.created_at.strftime("%Y-%m-%d"),
        last_modified_by=None,
        last_modified_date=plan.updated_at.strftime("%Y-%m-%d") if plan.updated_at else None
    )

@router.post("/plans")
async def create_treatment_plan(
    request: TreatmentPlanCreate,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Create a new treatment plan for a patient.
    
    Access Control:
    - Only Doctors can create treatment plans
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)

    if hasattr(doctor, "user"):
        existing_profile = await tenant_db.execute(
            select(DoctorProfile).where(DoctorProfile.user_id == doctor.user_id)
        )
        actual_profile = existing_profile.scalar_one_or_none()

        if not actual_profile:
            hospital_id_val = user_context.get("hospital_id") or (
                str(doctor.hospital_id) if doctor.hospital_id else None
            )
            if not hospital_id_val:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Hospital ID is required. Please ensure your account is linked to a hospital.",
                )
            new_profile = DoctorProfile(
                hospital_id=uuid.UUID(str(hospital_id_val)),
                user_id=doctor.user_id,
                department_id=doctor.department_id,
                doctor_id=doctor.doctor_id,
                medical_license_number=doctor.medical_license_number,
                designation=doctor.designation,
                specialization=doctor.specialization,
                experience_years=doctor.experience_years,
                qualifications=doctor.qualifications,
                consultation_fee=doctor.consultation_fee,
                follow_up_fee=doctor.follow_up_fee,
                is_available_for_emergency=doctor.is_available_for_emergency,
                is_accepting_new_patients=doctor.is_accepting_new_patients,
                bio=doctor.bio,
                languages_spoken=doctor.languages_spoken,
            )
            tenant_db.add(new_profile)
            await tenant_db.commit()
            await tenant_db.refresh(new_profile)
            doctor = new_profile

    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    hospital_for_patient = user_context.get("hospital_id") or (
        str(doctor.hospital_id) if doctor.hospital_id else None
    )
    patient = await load_patient_by_ref(
        request.patient_ref,
        parse_hospital_uuid(hospital_for_patient) or hid,
        tenant_db,
        platform_db,
        ensure_on_tenant=True,
    )
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Process goals with IDs
    processed_short_term_goals = []
    for goal in request.short_term_goals:
        goal['goal_id'] = generate_goal_id()
        goal['progress_percentage'] = goal.get('progress_percentage', 0)
        goal['current_status'] = goal.get('current_status', 'Not Started')
        processed_short_term_goals.append(goal)
    
    processed_long_term_goals = []
    for goal in request.long_term_goals:
        goal['goal_id'] = generate_goal_id()
        goal['progress_percentage'] = goal.get('progress_percentage', 0)
        goal['current_status'] = goal.get('current_status', 'Not Started')
        processed_long_term_goals.append(goal)
    
    # Process interventions with IDs
    processed_interventions = []
    for intervention in request.interventions:
        intervention['intervention_id'] = str(uuid.uuid4())
        intervention['status'] = intervention.get('status', 'ACTIVE')
        processed_interventions.append(intervention)
    
    # Process milestones with IDs
    processed_milestones = []
    for milestone in request.milestones:
        milestone['milestone_id'] = generate_milestone_id()
        milestone['status'] = milestone.get('status', 'PENDING')
        processed_milestones.append(milestone)
    
    # Create initial progress note
    initial_progress_notes = []
    if request.initial_notes:
        initial_progress_notes.append({
            'note_id': str(uuid.uuid4()),
            'date': datetime.now().strftime("%Y-%m-%d"),
            'author': _safe_doctor_display(doctor),
            'note_type': 'PLAN_CREATION',
            'content': request.initial_notes,
            'is_significant': True
        })
    
    # Ensure we have hospital_id - use patient's if user's is null
    hospital_id_val = user_context.get("hospital_id")
    if not hospital_id_val and patient.hospital_id:
        hospital_id_val = str(patient.hospital_id)
        user_context["hospital_id"] = hospital_id_val
    
    if not hospital_id_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Please ensure the patient is linked to a hospital."
        )
    
    # Create treatment plan
    new_plan = TreatmentPlan(
        hospital_id=uuid.UUID(hospital_id_val),
        patient_id=patient.id,
        doctor_id=doctor.id,
        plan_name=request.plan_name,
        primary_diagnosis=request.primary_diagnosis,
        secondary_diagnoses=request.secondary_diagnoses,
        short_term_goals=processed_short_term_goals,
        long_term_goals=processed_long_term_goals,
        medications=processed_interventions,  # Using medications field for interventions
        milestones=processed_milestones,
        progress_notes=initial_progress_notes,
        start_date=datetime.now().strftime("%Y-%m-%d"),
        expected_end_date=None,  # Can be set later
        review_frequency=request.review_frequency.value if hasattr(request.review_frequency, "value") else request.review_frequency,
        status="ACTIVE"
    )
    
    tenant_db.add(new_plan)
    await tenant_db.commit()
    await tenant_db.refresh(new_plan)

    _, patient_name = _safe_patient_display(patient)

    return {
        "message": "Treatment plan created successfully",
        "plan_id": str(new_plan.id),
        "plan_name": new_plan.plan_name,
        "patient_ref": request.patient_ref,
        "patient_name": patient_name,
        "status": new_plan.status,
        "created_date": new_plan.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "total_goals": len(processed_short_term_goals) + len(processed_long_term_goals),
        "total_interventions": len(processed_interventions),
        "total_milestones": len(processed_milestones)
    }


@router.put("/plans/{plan_id}")
async def update_treatment_plan(
    plan_id: str,
    request: TreatmentPlanUpdate,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Update an existing treatment plan.
    
    Access Control:
    - Only Doctors can update treatment plans
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)
    
    # Update basic fields
    if request.plan_name:
        plan.plan_name = request.plan_name
    
    if request.status:
        plan.status = request.status
        if request.status == TreatmentPlanStatus.COMPLETED:
            plan.completion_date = datetime.now().strftime("%Y-%m-%d")
    
    if request.expected_end_date:
        plan.expected_end_date = request.expected_end_date
    
    # Update goals
    if request.updated_goals:
        # Merge with existing goals, preserving IDs
        existing_short_term = plan.short_term_goals or []
        existing_long_term = plan.long_term_goals or []
        
        for updated_goal in request.updated_goals:
            goal_id = updated_goal.get('goal_id')
            goal_type = updated_goal.get('goal_type', 'short_term')
            
            if goal_type == 'short_term':
                # Find and update existing goal or add new one
                found = False
                for i, existing_goal in enumerate(existing_short_term):
                    if existing_goal.get('goal_id') == goal_id:
                        existing_short_term[i].update(updated_goal)
                        found = True
                        break
                if not found and not goal_id:
                    updated_goal['goal_id'] = generate_goal_id()
                    existing_short_term.append(updated_goal)
            else:
                # Similar logic for long-term goals
                found = False
                for i, existing_goal in enumerate(existing_long_term):
                    if existing_goal.get('goal_id') == goal_id:
                        existing_long_term[i].update(updated_goal)
                        found = True
                        break
                if not found and not goal_id:
                    updated_goal['goal_id'] = generate_goal_id()
                    existing_long_term.append(updated_goal)
        
        plan.short_term_goals = existing_short_term
        plan.long_term_goals = existing_long_term
    
    # Update interventions
    if request.updated_interventions:
        existing_interventions = plan.medications or []
        
        for updated_intervention in request.updated_interventions:
            intervention_id = updated_intervention.get('intervention_id')
            
            found = False
            for i, existing_intervention in enumerate(existing_interventions):
                if existing_intervention.get('intervention_id') == intervention_id:
                    existing_interventions[i].update(updated_intervention)
                    found = True
                    break
            
            if not found and not intervention_id:
                updated_intervention['intervention_id'] = str(uuid.uuid4())
                existing_interventions.append(updated_intervention)
        
        plan.medications = existing_interventions
    
    # Update milestones
    if request.updated_milestones:
        existing_milestones = plan.milestones or []
        
        for updated_milestone in request.updated_milestones:
            milestone_id = updated_milestone.get('milestone_id')
            
            found = False
            for i, existing_milestone in enumerate(existing_milestones):
                if existing_milestone.get('milestone_id') == milestone_id:
                    existing_milestones[i].update(updated_milestone)
                    found = True
                    break
            
            if not found and not milestone_id:
                updated_milestone['milestone_id'] = generate_milestone_id()
                existing_milestones.append(updated_milestone)
        
        plan.milestones = existing_milestones
    
    # Add progress note
    if request.progress_notes:
        existing_notes = plan.progress_notes or []
        new_note = {
            'note_id': str(uuid.uuid4()),
            'date': datetime.now().strftime("%Y-%m-%d"),
            'author': _safe_doctor_display(doctor),
            'note_type': 'PLAN_UPDATE',
            'content': request.progress_notes,
            'is_significant': True
        }
        existing_notes.append(new_note)
        plan.progress_notes = existing_notes
    
    # Add completion notes
    if request.completion_notes and request.status == TreatmentPlanStatus.COMPLETED:
        existing_notes = plan.progress_notes or []
        completion_note = {
            'note_id': str(uuid.uuid4()),
            'date': datetime.now().strftime("%Y-%m-%d"),
            'author': _safe_doctor_display(doctor),
            'note_type': 'COMPLETION',
            'content': request.completion_notes,
            'is_significant': True
        }
        existing_notes.append(completion_note)
        plan.progress_notes = existing_notes
    
    await plan_db.commit()
    await plan_db.refresh(plan)

    return {
        "message": "Treatment plan updated successfully",
        "plan_id": str(plan.id),
        "plan_name": plan.plan_name,
        "status": plan.status,
        "last_modified": plan.updated_at.strftime("%Y-%m-%d %H:%M:%S") if plan.updated_at else None,
        "progress_notes_count": len(plan.progress_notes or [])
    }


@router.post("/plans/{plan_id}/progress")
async def update_treatment_progress(
    plan_id: str,
    request: ProgressUpdate,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Update treatment plan progress with milestone and goal updates.
    
    Access Control:
    - Only Doctors can update treatment progress
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)

    # Update milestones
    if request.milestone_updates:
        existing_milestones = plan.milestones or []
        
        for milestone_update in request.milestone_updates:
            milestone_id = milestone_update.get('milestone_id')
            
            for i, existing_milestone in enumerate(existing_milestones):
                if existing_milestone.get('milestone_id') == milestone_id:
                    existing_milestones[i].update(milestone_update)
                    
                    # If milestone is completed, add completion date
                    if milestone_update.get('status') == MilestoneStatus.COMPLETED:
                        existing_milestones[i]['completion_date'] = datetime.now().strftime("%Y-%m-%d")
                    break
        
        plan.milestones = existing_milestones
    
    # Update goals
    if request.goal_updates:
        existing_short_term = plan.short_term_goals or []
        existing_long_term = plan.long_term_goals or []
        
        for goal_update in request.goal_updates:
            goal_id = goal_update.get('goal_id')
            
            # Check short-term goals
            for i, existing_goal in enumerate(existing_short_term):
                if existing_goal.get('goal_id') == goal_id:
                    existing_short_term[i].update(goal_update)
                    break
            
            # Check long-term goals
            for i, existing_goal in enumerate(existing_long_term):
                if existing_goal.get('goal_id') == goal_id:
                    existing_long_term[i].update(goal_update)
                    break
        
        plan.short_term_goals = existing_short_term
        plan.long_term_goals = existing_long_term
    
    # Update interventions
    if request.intervention_updates:
        existing_interventions = plan.medications or []
        
        for intervention_update in request.intervention_updates:
            intervention_id = intervention_update.get('intervention_id')
            
            for i, existing_intervention in enumerate(existing_interventions):
                if existing_intervention.get('intervention_id') == intervention_id:
                    existing_interventions[i].update(intervention_update)
                    break
        
        plan.medications = existing_interventions
    
    # Add progress note
    existing_notes = plan.progress_notes or []
    progress_note = {
        'note_id': str(uuid.uuid4()),
        'date': datetime.now().strftime("%Y-%m-%d"),
        'author': _safe_doctor_display(doctor),
        'note_type': 'PROGRESS',
        'content': request.progress_note,
        'is_significant': request.significant_change
    }
    existing_notes.append(progress_note)
    plan.progress_notes = existing_notes
    
    await plan_db.commit()
    await plan_db.refresh(plan)

    # Calculate updated progress
    all_goals = (plan.short_term_goals or []) + (plan.long_term_goals or [])
    milestones = plan.milestones or []
    progress_percentage = calculate_progress_percentage(all_goals, milestones)

    return {
        "message": "Treatment progress updated successfully",
        "plan_id": str(plan.id),
        "progress_percentage": progress_percentage,
        "milestones_updated": len(request.milestone_updates),
        "goals_updated": len(request.goal_updates),
        "interventions_updated": len(request.intervention_updates),
        "progress_note_added": True,
        "next_review_date": request.next_review_date,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


# ============================================================================
# TREATMENT PLAN STATUS AND INFORMATION
# ============================================================================

@router.get("/plans/{plan_id}/status")
async def get_treatment_plan_status(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get treatment plan status and available actions.
    
    Access Control:
    - Only Doctors can access treatment plan status
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, _plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)

    # Check for outcome assessment
    has_outcome_assessment = False
    outcome_date = None
    for note in (plan.progress_notes or []):
        if note.get('note_type') == 'OUTCOME_ASSESSMENT':
            has_outcome_assessment = True
            outcome_date = note.get('date')
            break
    
    # Calculate progress
    all_goals = (plan.short_term_goals or []) + (plan.long_term_goals or [])
    milestones = plan.milestones or []
    progress_percentage = calculate_progress_percentage(all_goals, milestones)
    
    # Determine available actions
    available_actions = []
    if plan.status == "ACTIVE":
        available_actions.extend([
            "Update treatment progress",
            "Add progress notes",
            "Modify treatment goals"
        ])
        if not has_outcome_assessment:
            available_actions.append("Record outcome assessment")
        if progress_percentage >= 80:
            available_actions.append("Complete treatment plan")
    
    return {
        "plan_id": str(plan.id),
        "plan_name": plan.plan_name,
        "patient_ref": _safe_patient_display(plan.patient)[0],
        "patient_name": _safe_patient_display(plan.patient)[1],
        "status": plan.status,
        "start_date": plan.start_date,
        "expected_end_date": plan.expected_end_date,
        "progress_percentage": progress_percentage,
        "total_goals": len(all_goals),
        "total_milestones": len(milestones),
        "total_progress_notes": len(plan.progress_notes or []),
        "has_outcome_assessment": has_outcome_assessment,
        "outcome_assessment_date": outcome_date,
        "available_actions": available_actions,
        "endpoints": {
            "get_details": f"/plans/{plan_id}",
            "update_progress": f"/plans/{plan_id}/progress",
            "record_outcome": f"/plans/{plan_id}/outcome",
            "get_outcome": f"/plans/{plan_id}/outcome"
        }
    }


# ============================================================================
# TREATMENT OUTCOMES AND ASSESSMENT
# ============================================================================

@router.post("/plans/{plan_id}/outcome")
async def record_treatment_outcome(
    plan_id: str,
    outcome: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Record treatment outcome assessment for a completed plan.
    
    Access Control:
    - Only Doctors can record treatment outcomes
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)

    # Add outcome as a special progress note (outcome is Dict[str, Any])
    existing_notes = plan.progress_notes or []
    outcome_note = {
        'note_id': str(uuid.uuid4()),
        'date': outcome.get('outcome_date'),
        'author': _safe_doctor_display(doctor),
        'note_type': 'OUTCOME_ASSESSMENT',
        'content': outcome.get('outcome_summary'),
        'is_significant': True,
        'outcome_data': {
            'overall_outcome': outcome.get('overall_outcome'),
            'goals_achieved': outcome.get('goals_achieved'),
            'goals_partially_achieved': outcome.get('goals_partially_achieved'),
            'goals_not_achieved': outcome.get('goals_not_achieved'),
            'clinical_effectiveness': outcome.get('clinical_effectiveness'),
            'treatment_adherence': outcome.get('treatment_adherence'),
            'patient_satisfaction_score': outcome.get('patient_satisfaction_score'),
            'quality_of_life_score': outcome.get('quality_of_life_score'),
            'complications': outcome.get('complications', []),
            'future_recommendations': outcome.get('future_recommendations', []),
            'follow_up_plan': outcome.get('follow_up_plan'),
            'lessons_learned': outcome.get('lessons_learned')
        }
    }
    existing_notes.append(outcome_note)
    plan.progress_notes = existing_notes
    
    # Update plan status if not already completed
    outcome_date = outcome.get('outcome_date')
    if plan.status != "COMPLETED":
        plan.status = "COMPLETED"
        plan.completion_date = outcome_date
    
    await plan_db.commit()
    await plan_db.refresh(plan)

    goals_achieved = outcome.get('goals_achieved', 0)
    goals_partially_achieved = outcome.get('goals_partially_achieved', 0)
    goals_not_achieved = outcome.get('goals_not_achieved', 0)
    complications = outcome.get('complications', [])
    future_recommendations = outcome.get('future_recommendations', [])
    
    return {
        "message": "Treatment outcome recorded successfully",
        "plan_id": str(plan.id),
        "outcome_date": outcome_date,
        "overall_outcome": outcome.get('overall_outcome'),
        "clinical_effectiveness": outcome.get('clinical_effectiveness'),
        "patient_satisfaction": outcome.get('patient_satisfaction_score'),
        "goals_achieved": goals_achieved,
        "total_goals": goals_achieved + goals_partially_achieved + goals_not_achieved,
        "treatment_adherence": outcome.get('treatment_adherence'),
        "complications_count": len(complications),
        "recommendations_count": len(future_recommendations)
    }


@router.get("/plans/{plan_id}/outcome")
async def get_treatment_outcome(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get treatment outcome assessment for a plan.
    
    Access Control:
    - Only Doctors can access treatment outcomes
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plan, _plan_db = await _fetch_plan_for_doctor(plan_id, doctor, hid, tenant_db, platform_db)

    # Find outcome assessment in progress notes
    outcome_note = None
    for note in (plan.progress_notes or []):
        if note.get('note_type') == 'OUTCOME_ASSESSMENT':
            outcome_note = note
            break
    
    if not outcome_note:
        # Return helpful information instead of 404 when no outcome assessment exists
        return {
            "message": "No treatment outcome assessment found for this plan",
            "plan_id": str(plan.id),
            "plan_name": plan.plan_name,
            "plan_status": plan.status,
            "patient_ref": _safe_patient_display(plan.patient)[0],
            "patient_name": _safe_patient_display(plan.patient)[1],
            "start_date": plan.start_date,
            "has_outcome_assessment": False,
            "suggestion": "Create an outcome assessment using POST /plans/{plan_id}/outcome endpoint",
            "available_actions": [
                "Record treatment outcome assessment",
                "Update treatment progress", 
                "Complete treatment plan"
            ]
        }
    
    outcome_data = outcome_note.get('outcome_data', {})
    
    return TreatmentOutcome(
        plan_id=str(plan.id),
        patient_ref=plan.patient.patient_id,
        outcome_date=outcome_note.get('date'),
        overall_outcome=OutcomeStatus(outcome_data.get('overall_outcome', 'UNKNOWN')),
        goals_achieved=outcome_data.get('goals_achieved', 0),
        goals_partially_achieved=outcome_data.get('goals_partially_achieved', 0),
        goals_not_achieved=outcome_data.get('goals_not_achieved', 0),
        symptom_improvement=outcome_data.get('symptom_improvement', {}),
        functional_improvement=outcome_data.get('functional_improvement', {}),
        quality_of_life_score=outcome_data.get('quality_of_life_score'),
        patient_satisfaction_score=outcome_data.get('patient_satisfaction_score'),
        patient_feedback=outcome_data.get('patient_feedback'),
        clinical_effectiveness=OutcomeStatus(outcome_data.get('clinical_effectiveness', 'UNKNOWN')),
        treatment_adherence=outcome_data.get('treatment_adherence', 0.0),
        complications=outcome_data.get('complications', []),
        future_recommendations=outcome_data.get('future_recommendations', []),
        follow_up_plan=outcome_data.get('follow_up_plan'),
        outcome_summary=outcome_note.get('content', ''),
        lessons_learned=outcome_data.get('lessons_learned')
    )


# ============================================================================
# PLAN TEMPLATES AND PROTOCOLS
# ============================================================================

@router.get("/templates")
async def get_treatment_plan_templates(
    diagnosis: Optional[str] = Query(None),
    specialty: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get treatment plan templates for common conditions.
    
    Access Control:
    - Only Doctors can access treatment templates
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Mock treatment plan templates (in production, these would be stored in database)
    templates = [
        {
            "template_id": "TMPL-DIABETES-001",
            "template_name": "Type 2 Diabetes Management",
            "diagnosis": "Type 2 Diabetes Mellitus",
            "specialty": "Endocrinology",
            "description": "Comprehensive diabetes management plan with lifestyle modifications and medication management",
            "estimated_duration": "6 months",
            "short_term_goals": [
                {
                    "description": "Achieve HbA1c < 7%",
                    "target_date": "3 months",
                    "priority": "HIGH",
                    "measurable_outcome": "HbA1c test result"
                },
                {
                    "description": "Weight reduction of 5-10%",
                    "target_date": "3 months",
                    "priority": "MEDIUM",
                    "measurable_outcome": "Body weight measurement"
                }
            ],
            "long_term_goals": [
                {
                    "description": "Prevent diabetic complications",
                    "target_date": "12 months",
                    "priority": "HIGH",
                    "measurable_outcome": "Annual screening results"
                }
            ],
            "interventions": [
                {
                    "intervention_type": "MEDICATION",
                    "description": "Metformin therapy",
                    "instructions": "Start with 500mg twice daily with meals",
                    "frequency": "Twice daily"
                },
                {
                    "intervention_type": "LIFESTYLE",
                    "description": "Dietary counseling",
                    "instructions": "Low carbohydrate diet with portion control",
                    "frequency": "Ongoing"
                }
            ],
            "milestones": [
                {
                    "title": "Initial Assessment Complete",
                    "description": "Baseline labs and physical exam completed",
                    "target_date": "1 week"
                },
                {
                    "title": "3-Month Follow-up",
                    "description": "HbA1c and weight reassessment",
                    "target_date": "3 months"
                }
            ]
        },
        {
            "template_id": "TMPL-HTN-001",
            "template_name": "Hypertension Management",
            "diagnosis": "Essential Hypertension",
            "specialty": "Cardiology",
            "description": "Evidence-based hypertension management with lifestyle and pharmacological interventions",
            "estimated_duration": "3 months",
            "short_term_goals": [
                {
                    "description": "Achieve BP < 140/90 mmHg",
                    "target_date": "6 weeks",
                    "priority": "HIGH",
                    "measurable_outcome": "Blood pressure readings"
                }
            ],
            "long_term_goals": [
                {
                    "description": "Maintain optimal BP control",
                    "target_date": "12 months",
                    "priority": "HIGH",
                    "measurable_outcome": "Sustained BP control"
                }
            ],
            "interventions": [
                {
                    "intervention_type": "MEDICATION",
                    "description": "ACE inhibitor therapy",
                    "instructions": "Start with low dose, titrate as needed",
                    "frequency": "Once daily"
                },
                {
                    "intervention_type": "LIFESTYLE",
                    "description": "DASH diet implementation",
                    "instructions": "Low sodium, high potassium diet",
                    "frequency": "Ongoing"
                }
            ],
            "milestones": [
                {
                    "title": "Medication Initiation",
                    "description": "Start antihypertensive therapy",
                    "target_date": "1 week"
                },
                {
                    "title": "BP Target Achievement",
                    "description": "Reach target blood pressure",
                    "target_date": "6 weeks"
                }
            ]
        }
    ]
    
    # Filter templates based on query parameters
    filtered_templates = templates
    
    if diagnosis:
        filtered_templates = [t for t in filtered_templates if diagnosis.lower() in t["diagnosis"].lower()]
    
    if specialty:
        filtered_templates = [t for t in filtered_templates if specialty.lower() in t["specialty"].lower()]
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "specialty": doctor.specialization,
        "filters_applied": {
            "diagnosis": diagnosis,
            "specialty": specialty
        },
        "total_templates": len(filtered_templates),
        "templates": filtered_templates
    }


@router.post("/plans/from-template")
async def create_plan_from_template(
    template_id: str = Body(...),
    patient_ref: str = Body(...),
    customizations: Optional[Dict[str, Any]] = Body(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Create a treatment plan from a template.
    
    Access Control:
    - Only Doctors can create plans from templates
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)

    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    patient = await load_patient_by_ref(
        patient_ref,
        hid,
        tenant_db,
        platform_db,
        ensure_on_tenant=True,
    )
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Mock template lookup (in production, would query database)
    template = None
    if template_id == "TMPL-DIABETES-001":
        template = {
            "template_name": "Type 2 Diabetes Management",
            "diagnosis": "Type 2 Diabetes Mellitus",
            "short_term_goals": [
                {
                    "description": "Achieve HbA1c < 7%",
                    "target_date": (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d"),
                    "priority": "HIGH",
                    "measurable_outcome": "HbA1c test result"
                }
            ],
            "long_term_goals": [
                {
                    "description": "Prevent diabetic complications",
                    "target_date": (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"),
                    "priority": "HIGH",
                    "measurable_outcome": "Annual screening results"
                }
            ],
            "interventions": [
                {
                    "intervention_type": "MEDICATION",
                    "description": "Metformin therapy",
                    "instructions": "Start with 500mg twice daily with meals",
                    "frequency": "Twice daily"
                }
            ],
            "milestones": [
                {
                    "title": "Initial Assessment Complete",
                    "description": "Baseline labs and physical exam completed",
                    "target_date": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                }
            ]
        }
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # Apply customizations if provided
    if customizations:
        template.update(customizations)
    
    # Create treatment plan from template
    create_request = TreatmentPlanCreate(
        patient_ref=patient_ref,
        plan_name=template["template_name"],
        primary_diagnosis=template["diagnosis"],
        short_term_goals=template.get("short_term_goals", []),
        long_term_goals=template.get("long_term_goals", []),
        interventions=template.get("interventions", []),
        milestones=template.get("milestones", []),
        initial_notes=f"Treatment plan created from template {template_id}"
    )
    
    return await create_treatment_plan(
        create_request, current_user, tenant_db, platform_db
    )


# ============================================================================
# DATA CLEANUP AND MAINTENANCE
# ============================================================================

@router.post("/maintenance/cleanup-dates")
async def cleanup_invalid_dates(
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Clean up invalid date values in treatment plans.
    
    Access Control:
    - Only Doctors can clean up their own treatment plan data
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)
    plans = await _fetch_all_plans_for_doctor(doctor, hid, tenant_db, platform_db)

    cleaned_count = 0
    sessions_to_commit: set[int] = set()
    for plan in plans:
        updated = False
        
        # Clean up completion_date
        if plan.completion_date in ['string', 'null', 'None', '', 'NULL']:
            plan.completion_date = None
            updated = True
        
        # Clean up start_date (though this should be less likely)
        if plan.start_date in ['string', 'null', 'None', '', 'NULL']:
            plan.start_date = datetime.now().strftime("%Y-%m-%d")
            updated = True
        
        # Clean up expected_end_date
        if plan.expected_end_date in ['string', 'null', 'None', '', 'NULL']:
            plan.expected_end_date = None
            updated = True
        
        if updated:
            cleaned_count += 1
            from sqlalchemy.orm import object_session

            sess = object_session(plan)
            if sess is not None:
                sessions_to_commit.add(id(sess))

    for session in _clinical_db_sessions(tenant_db, platform_db):
        if id(session) in sessions_to_commit:
            await session.commit()

    return {
        "message": "Data cleanup completed successfully",
        "total_plans_checked": len(plans),
        "plans_cleaned": cleaned_count,
        "cleanup_actions": [
            "Removed invalid completion_date values",
            "Fixed invalid start_date values", 
            "Cleaned up invalid expected_end_date values"
        ]
    }


# ============================================================================
# ANALYTICS AND REPORTING
# ============================================================================

@router.get("/analytics/summary")
async def get_treatment_plan_analytics(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get treatment plan analytics and summary statistics.
    
    Access Control:
    - Only Doctors can access treatment plan analytics
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)
    hid = await _resolve_doctor_hospital(doctor, current_user, tenant_db, platform_db)

    # Set default date range if not provided
    if not date_from:
        date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    
    # Convert string dates to datetime objects for proper PostgreSQL comparison
    try:
        start_datetime = datetime.strptime(f"{date_from} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_datetime = datetime.strptime(f"{date_to} 23:59:59", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD format."
        )
    
    plans = await _fetch_all_plans_for_doctor(
        doctor,
        hid,
        tenant_db,
        platform_db,
        extra_conditions=[
            TreatmentPlan.created_at >= start_datetime,
            TreatmentPlan.created_at <= end_datetime,
        ],
    )
    
    # Calculate analytics
    total_plans = len(plans)
    active_plans = len([p for p in plans if p.status == "ACTIVE"])
    completed_plans = len([p for p in plans if p.status == "COMPLETED"])
    discontinued_plans = len([p for p in plans if p.status == "DISCONTINUED"])
    
    # Diagnosis breakdown
    diagnosis_counts = {}
    for plan in plans:
        diagnosis = plan.primary_diagnosis
        diagnosis_counts[diagnosis] = diagnosis_counts.get(diagnosis, 0) + 1
    
    top_diagnoses = sorted(diagnosis_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Calculate average completion time for completed plans
    completion_times = []
    invalid_dates_count = 0
    
    for plan in plans:
        if plan.status == "COMPLETED" and plan.completion_date and plan.start_date:
            try:
                # Skip invalid date values
                if plan.completion_date in ['string', 'null', 'None', '', 'NULL']:
                    invalid_dates_count += 1
                    continue
                if plan.start_date in ['string', 'null', 'None', '', 'NULL']:
                    invalid_dates_count += 1
                    continue
                    
                start = datetime.strptime(plan.start_date, "%Y-%m-%d")
                end = datetime.strptime(plan.completion_date, "%Y-%m-%d")
                completion_times.append((end - start).days)
            except ValueError as e:
                # Skip plans with invalid date formats and count them
                invalid_dates_count += 1
                continue
    
    avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0
    
    # Success rate calculation
    success_rate = (completed_plans / total_plans * 100) if total_plans > 0 else 0
    
    return {
        "doctor_name": _doctor_header_name(doctor, current_user),
        "analysis_period": f"{date_from} to {date_to}",
        "summary_statistics": {
            "total_plans": total_plans,
            "active_plans": active_plans,
            "completed_plans": completed_plans,
            "discontinued_plans": discontinued_plans,
            "success_rate": round(success_rate, 1),
            "average_completion_time_days": round(avg_completion_time, 1)
        },
        "diagnosis_breakdown": [
            {"diagnosis": diagnosis, "count": count, "percentage": round((count / total_plans) * 100, 1)}
            for diagnosis, count in top_diagnoses
        ],
        "status_distribution": {
            "ACTIVE": active_plans,
            "COMPLETED": completed_plans,
            "DISCONTINUED": discontinued_plans,
            "DRAFT": len([p for p in plans if p.status == "DRAFT"])
        },
        "performance_metrics": {
            "plans_per_month": round(total_plans / 3, 1),  # Assuming 3-month period
            "completion_rate": round((completed_plans / total_plans) * 100, 1) if total_plans > 0 else 0,
            "average_goals_per_plan": round(
                sum(len((p.short_term_goals or []) + (p.long_term_goals or [])) for p in plans) / total_plans, 1
            ) if total_plans > 0 else 0
        },
        "data_quality": {
            "total_completion_times_calculated": len(completion_times),
            "invalid_dates_skipped": invalid_dates_count,
            "data_quality_score": round(((total_plans - invalid_dates_count) / total_plans * 100), 1) if total_plans > 0 else 100
        }
    }