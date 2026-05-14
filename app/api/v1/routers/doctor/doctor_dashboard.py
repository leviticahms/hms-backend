"""
Doctor Dashboard API
Comprehensive dashboard for doctors showing appointments, patient records, pending tasks, and practice metrics.
Provides a centralized view of doctor's daily activities and patient management.

BUSINESS RULES:
- Only Doctors can access the dashboard
- Department-based data filtering (doctor sees only their department's data)
- Hospital isolation (doctor sees only their hospital's data)
- Real-time metrics and notifications
- Quick access to common doctor tasks
"""
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User, Role, user_roles
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission, DischargeSummary
from app.models.hospital import Department, StaffDepartmentAssignment
from app.core.enums import UserRole, AppointmentStatus, AdmissionStatus
from app.core.utils import generate_patient_ref

router = APIRouter(prefix="/doctor-dashboard", tags=["Doctor Portal - Dashboard"])


# ============================================================================
# TEMPORARY PYDANTIC MODELS (TO BE MIGRATED TO DOCTOR SCHEMA)
# ============================================================================

class DashboardOverview(BaseModel):
    """Doctor dashboard overview"""
    doctor_name: str
    doctor_id: str
    specialization: str
    department: str
    hospital_id: str
    dashboard_date: str
    statistics: Dict[str, Any]
    quick_actions: List[str]


class TodayAppointment(BaseModel):
    """Today's appointment summary"""
    appointment_ref: str
    patient_ref: str
    patient_name: str
    patient_age: int
    appointment_time: str
    appointment_type: str
    chief_complaint: Optional[str]
    status: str
    is_checked_in: bool
    is_completed: bool


class RecentPatient(BaseModel):
    """Recent patient summary"""
    patient_ref: str
    patient_name: str
    patient_age: int
    last_visit_date: str
    last_diagnosis: Optional[str]
    chronic_conditions: List[str]
    total_visits: int
    needs_follow_up: bool


class PendingTask(BaseModel):
    """Pending task for doctor"""
    task_id: str
    task_type: str  # DISCHARGE_SUMMARY, FOLLOW_UP, PRESCRIPTION_RENEWAL, REPORT_REVIEW
    patient_ref: str
    patient_name: str
    description: str
    priority: str  # LOW, NORMAL, HIGH, URGENT
    due_date: Optional[str]
    created_date: str


class AdmittedPatient(BaseModel):
    """Currently admitted patient"""
    admission_number: str
    patient_ref: str
    patient_name: str
    patient_age: int
    admission_date: str
    length_of_stay: int
    ward: Optional[str]
    room_number: Optional[str]
    current_condition: Optional[str]
    last_assessment_date: Optional[str]
    needs_rounds: bool


class UpcomingSchedule(BaseModel):
    """Upcoming schedule item"""
    schedule_id: str
    day_of_week: str
    start_time: str
    end_time: str
    schedule_type: str  # CONSULTATION, SURGERY, ROUNDS
    location: Optional[str]
    notes: Optional[str]


class PatientAlert(BaseModel):
    """Patient alert/notification"""
    alert_id: str
    patient_ref: str
    patient_name: str
    alert_type: str  # CRITICAL_VITALS, MISSED_APPOINTMENT, OVERDUE_FOLLOW_UP
    message: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    created_at: str


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
    
    # Get doctor user and their department assignment
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
    assignment_result = await db.execute(
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
            self.doctor_id = user.staff_id or f"DOC-{str(user.id)[:8]}"  # Add doctor_id attribute
            # Add commonly used attributes with default values
            self.specialization = "General Medicine"
            self.designation = "Doctor"
            self.experience_years = 5
            self.consultation_fee = 500.0
            self.medical_license_number = f"LIC-{user.id}"
            self.is_available = True
    
    return MockDoctorProfile(doctor_user, assignment.department)


def ensure_doctor_access(user_context: dict):
    """Ensure user is a doctor"""
    if UserRole.DOCTOR.value not in user_context.get("all_roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )


def calculate_age(date_of_birth: str) -> int:
    """Calculate age from date of birth"""
    try:
        birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except:
        return 0


# ============================================================================
# MAIN DASHBOARD
# ============================================================================

@router.get("/overview")
async def get_doctor_dashboard_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get comprehensive doctor dashboard overview.
    
    Access Control:
    - Only Doctors can access dashboard
    - Shows department and hospital specific data
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    today = date.today().isoformat()
    
    # Get today's appointments count
    todays_appointments_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date == today
            )
        )
    )
    todays_appointments = todays_appointments_result.scalar() or 0
    
    # Get completed appointments today
    completed_today_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date == today,
                Appointment.status == AppointmentStatus.COMPLETED
            )
        )
    )
    completed_today = completed_today_result.scalar() or 0
    
    # Get pending appointments (checked in but not completed)
    pending_appointments_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date == today,
                Appointment.checked_in_at.isnot(None),
                Appointment.status != AppointmentStatus.COMPLETED
            )
        )
    )
    pending_appointments = pending_appointments_result.scalar() or 0
    
    # Get admitted patients count
    admitted_patients_result = await db.execute(
        select(func.count(Admission.id))
        .where(
            and_(
                Admission.doctor_id == doctor.id,
                Admission.is_active == True
            )
        )
    )
    admitted_patients = admitted_patients_result.scalar() or 0
    
    # Get total patients treated (lifetime)
    total_patients_result = await db.execute(
        select(func.count(func.distinct(Appointment.patient_id)))
        .where(Appointment.doctor_id == doctor.id)
    )
    total_patients = total_patients_result.scalar() or 0
    
    # Get pending discharge summaries
    pending_discharge_result = await db.execute(
        select(func.count(Admission.id))
        .where(
            and_(
                Admission.doctor_id == doctor.id,
                Admission.is_active == False,
                Admission.discharge_date.isnot(None),
                Admission.discharge_summary_id.is_(None)
            )
        )
    )
    pending_discharge = pending_discharge_result.scalar() or 0
    
    # Get this week's statistics
    week_start = datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())
    week_appointments_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.created_at >= week_start
            )
        )
    )
    week_appointments = week_appointments_result.scalar() or 0
    
    return DashboardOverview(
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        doctor_id=doctor.doctor_id,
        specialization=doctor.specialization,
        department=doctor.department.name,
        hospital_id=user_context.get("hospital_id") or str(doctor.hospital_id) if doctor.hospital_id else "",
        dashboard_date=today,
        statistics={
            "todays_appointments": todays_appointments,
            "completed_today": completed_today,
            "pending_appointments": pending_appointments,
            "admitted_patients": admitted_patients,
            "total_patients_lifetime": total_patients,
            "pending_discharge_summaries": pending_discharge,
            "week_appointments": week_appointments
        },
        quick_actions=[
            "View today's appointments",
            "Check admitted patients",
            "Create medical record",
            "Write discharge summary",
            "View patient history",
            "Update schedule"
        ]
    )


# ============================================================================
# TODAY'S APPOINTMENTS
# ============================================================================

@router.get("/appointments/today")
async def get_todays_appointments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get today's appointments for the doctor.
    
    Access Control:
    - Only Doctors can access their appointments
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    today = date.today().isoformat()
    
    # Get today's appointments
    appointments_result = await db.execute(
        select(Appointment)
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date == today
            )
        )
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user)
        )
        .order_by(asc(Appointment.appointment_time))
    )
    
    appointments = appointments_result.scalars().all()
    
    # Format appointments
    appointment_list = []
    for appointment in appointments:
        patient_age = calculate_age(appointment.patient.date_of_birth)
        
        appointment_list.append(TodayAppointment(
            appointment_ref=appointment.appointment_ref,
            patient_ref=appointment.patient.patient_id,
            patient_name=f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            patient_age=patient_age,
            appointment_time=appointment.appointment_time,
            appointment_type=appointment.appointment_type,
            chief_complaint=appointment.chief_complaint,
            status=appointment.status,
            is_checked_in=appointment.checked_in_at is not None,
            is_completed=appointment.status == AppointmentStatus.COMPLETED
        ))
    
    return {
        "date": today,
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_appointments": len(appointment_list),
        "appointments": appointment_list
    }


# ============================================================================
# RECENT PATIENTS
# ============================================================================

@router.get("/patients/recent")
async def get_recent_patients(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get recently treated patients.
    
    Access Control:
    - Only Doctors can access their patient history
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get recent patients (from appointments in last 30 days)
    thirty_days_ago = date.today() - timedelta(days=30)
    
    recent_patients_result = await db.execute(
        select(
            PatientProfile,
            func.max(Appointment.appointment_date).label('last_visit'),
            func.count(Appointment.id).label('total_visits')
        )
        .join(Appointment, PatientProfile.id == Appointment.patient_id)
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date >= thirty_days_ago.isoformat()
            )
        )
        .options(selectinload(PatientProfile.user))
        .group_by(PatientProfile.id)
        .order_by(desc('last_visit'))
        .limit(limit)
    )
    
    recent_patients = recent_patients_result.all()
    
    # Format patients
    patient_list = []
    for patient_data in recent_patients:
        patient = patient_data[0]
        last_visit = patient_data[1]
        total_visits = patient_data[2]
        
        # Get last diagnosis
        last_diagnosis_result = await db.execute(
            select(MedicalRecord.diagnosis)
            .where(
                and_(
                    MedicalRecord.patient_id == patient.id,
                    MedicalRecord.doctor_id == doctor.id,
                    MedicalRecord.diagnosis.isnot(None)
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(1)
        )
        last_diagnosis = last_diagnosis_result.scalar_one_or_none()
        
        # Check if needs follow-up (has follow-up instructions in recent records)
        follow_up_check = await db.execute(
            select(MedicalRecord.follow_up_instructions)
            .where(
                and_(
                    MedicalRecord.patient_id == patient.id,
                    MedicalRecord.doctor_id == doctor.id,
                    MedicalRecord.follow_up_instructions.isnot(None),
                    MedicalRecord.created_at >= datetime.now(timezone.utc) - timedelta(days=30)
                )
            )
            .limit(1)
        )
        needs_follow_up = follow_up_check.scalar_one_or_none() is not None
        
        patient_age = calculate_age(patient.date_of_birth)
        
        patient_list.append(RecentPatient(
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            patient_age=patient_age,
            last_visit_date=last_visit,
            last_diagnosis=last_diagnosis,
            chronic_conditions=patient.chronic_conditions or [],
            total_visits=total_visits,
            needs_follow_up=needs_follow_up
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "period": "Last 30 days",
        "total_recent_patients": len(patient_list),
        "patients": patient_list
    }


# ============================================================================
# ADMITTED PATIENTS
# ============================================================================

@router.get("/patients/admitted")
async def get_admitted_patients(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get currently admitted patients under doctor's care.
    
    Access Control:
    - Only Doctors can access their admitted patients
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get admitted patients
    admissions_result = await db.execute(
        select(Admission)
        .where(
            and_(
                Admission.doctor_id == doctor.id,
                Admission.is_active == True
            )
        )
        .options(
            selectinload(Admission.patient).selectinload(PatientProfile.user)
        )
        .order_by(desc(Admission.admission_date))
    )
    
    admissions = admissions_result.scalars().all()
    
    # Format admitted patients
    patient_list = []
    for admission in admissions:
        patient_age = calculate_age(admission.patient.date_of_birth)
        length_of_stay = (datetime.now(timezone.utc) - admission.admission_date).days
        
        # Get latest assessment
        latest_assessment_result = await db.execute(
            select(MedicalRecord.created_at, MedicalRecord.vital_signs)
            .where(
                and_(
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.doctor_id == doctor.id,
                    MedicalRecord.created_at >= admission.admission_date
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(1)
        )
        
        latest_assessment = latest_assessment_result.first()
        last_assessment_date = None
        current_condition = None
        
        if latest_assessment:
            last_assessment_date = latest_assessment.created_at.date().isoformat()
            if latest_assessment.vital_signs:
                current_condition = latest_assessment.vital_signs.get("patient_condition")
        
        # Check if needs rounds (no assessment today)
        today = date.today()
        needs_rounds = last_assessment_date != today.isoformat() if last_assessment_date else True
        
        patient_list.append(AdmittedPatient(
            admission_number=admission.admission_number,
            patient_ref=admission.patient.patient_id,
            patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
            patient_age=patient_age,
            admission_date=admission.admission_date.date().isoformat(),
            length_of_stay=length_of_stay,
            ward=admission.ward,
            room_number=admission.room_number,
            current_condition=current_condition,
            last_assessment_date=last_assessment_date,
            needs_rounds=needs_rounds
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_admitted": len(patient_list),
        "patients": patient_list
    }


# ============================================================================
# PENDING TASKS
# ============================================================================

@router.get("/tasks/pending")
async def get_pending_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get pending tasks for the doctor.
    
    Access Control:
    - Only Doctors can access their pending tasks
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    pending_tasks = []
    
    # Task 1: Pending discharge summaries
    pending_discharge_result = await db.execute(
        select(Admission)
        .where(
            and_(
                Admission.doctor_id == doctor.id,
                Admission.is_active == False,
                Admission.discharge_date.isnot(None),
                Admission.discharge_summary_id.is_(None)
            )
        )
        .options(selectinload(Admission.patient).selectinload(PatientProfile.user))
        .order_by(desc(Admission.discharge_date))
    )
    
    for admission in pending_discharge_result.scalars():
        days_since_discharge = (datetime.now(timezone.utc).date() - admission.discharge_date.date()).days
        priority = "HIGH" if days_since_discharge > 2 else "NORMAL"
        
        pending_tasks.append(PendingTask(
            task_id=f"discharge-{admission.id}",
            task_type="DISCHARGE_SUMMARY",
            patient_ref=admission.patient.patient_id,
            patient_name=f"{admission.patient.user.first_name} {admission.patient.user.last_name}",
            description=f"Complete discharge summary for admission {admission.admission_number}",
            priority=priority,
            due_date=None,
            created_date=admission.discharge_date.isoformat()
        ))
    
    # Task 2: Follow-up appointments needed
    follow_up_result = await db.execute(
        select(MedicalRecord, PatientProfile)
        .join(PatientProfile, MedicalRecord.patient_id == PatientProfile.id)
        .where(
            and_(
                MedicalRecord.doctor_id == doctor.id,
                MedicalRecord.follow_up_instructions.isnot(None),
                MedicalRecord.created_at >= datetime.now(timezone.utc) - timedelta(days=30)
            )
        )
        .options(selectinload(PatientProfile.user))
        .order_by(desc(MedicalRecord.created_at))
        .limit(10)
    )
    
    for record, patient in follow_up_result:
        # Check if follow-up appointment already scheduled
        follow_up_scheduled = await db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date > date.today().isoformat(),
                    Appointment.appointment_type == "FOLLOW_UP"
                )
            )
            .limit(1)
        )
        
        if not follow_up_scheduled.scalar_one_or_none():
            pending_tasks.append(PendingTask(
                task_id=f"followup-{record.id}",
                task_type="FOLLOW_UP",
                patient_ref=patient.patient_id,
                patient_name=f"{patient.user.first_name} {patient.user.last_name}",
                description=f"Schedule follow-up appointment: {record.follow_up_instructions[:100]}...",
                priority="NORMAL",
                due_date=None,
                created_date=record.created_at.isoformat()
            ))
    
    # Sort by priority and date
    priority_order = {"URGENT": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
    pending_tasks.sort(key=lambda x: (priority_order.get(x.priority, 2), x.created_date), reverse=True)
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_pending_tasks": len(pending_tasks),
        "tasks": pending_tasks[:20]  # Limit to 20 most important tasks
    }


# ============================================================================
# QUICK STATS
# ============================================================================

@router.get("/stats/quick")
async def get_quick_stats(
    period: str = Query("week", pattern="^(today|week|month)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get quick statistics for different time periods.
    
    Access Control:
    - Only Doctors can access their statistics
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Calculate date range
    today = date.today()
    if period == "today":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    else:  # month
        start_date = today.replace(day=1)
        end_date = today
    
    # Get appointments in period
    appointments_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date >= start_date.isoformat(),
                Appointment.appointment_date <= end_date.isoformat()
            )
        )
    )
    total_appointments = appointments_result.scalar() or 0
    
    # Get completed appointments
    completed_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date >= start_date.isoformat(),
                Appointment.appointment_date <= end_date.isoformat(),
                Appointment.status == AppointmentStatus.COMPLETED
            )
        )
    )
    completed_appointments = completed_result.scalar() or 0
    
    # Get unique patients treated
    patients_result = await db.execute(
        select(func.count(func.distinct(Appointment.patient_id)))
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date >= start_date.isoformat(),
                Appointment.appointment_date <= end_date.isoformat(),
                Appointment.status == AppointmentStatus.COMPLETED
            )
        )
    )
    unique_patients = patients_result.scalar() or 0
    
    # Get medical records created
    records_result = await db.execute(
        select(func.count(MedicalRecord.id))
        .where(
            and_(
                MedicalRecord.doctor_id == doctor.id,
                MedicalRecord.created_at >= datetime.combine(start_date, datetime.min.time()),
                MedicalRecord.created_at <= datetime.combine(end_date, datetime.max.time())
            )
        )
    )
    medical_records = records_result.scalar() or 0
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "period": period,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "statistics": {
            "total_appointments": total_appointments,
            "completed_appointments": completed_appointments,
            "unique_patients_treated": unique_patients,
            "medical_records_created": medical_records,
            "completion_rate": round((completed_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        }
    }