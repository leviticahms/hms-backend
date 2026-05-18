"""
Doctor Reports & Analytics API
Comprehensive reporting and analytics system for doctors with practice insights,
performance metrics, clinical analytics, and administrative reports.

BUSINESS RULES:
- Only Doctors can access reports and analytics
- Hospital isolation applied to all data
- Department-based filtering where applicable
- Comprehensive practice analytics and insights
- Export capabilities for reports
- Real-time and historical analytics
"""
import uuid
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta, date, time, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, text, case
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from enum import Enum
import calendar

from app.core.database import get_db_session, get_platform_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission, DischargeSummary
from app.models.doctor import DoctorProfile, Prescription, TreatmentPlan
from app.models.hospital import Department
from app.core.enums import UserRole, AppointmentStatus, AdmissionType
from app.core.utils import generate_patient_ref

router = APIRouter(prefix="/doctor-reports-analytics", tags=["Doctor Portal - Reports & Analytics"])


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class ReportType(str, Enum):
    """Report types available for doctors"""
    PRACTICE_SUMMARY = "PRACTICE_SUMMARY"
    PATIENT_ANALYTICS = "PATIENT_ANALYTICS"
    APPOINTMENT_ANALYTICS = "APPOINTMENT_ANALYTICS"
    PRESCRIPTION_ANALYTICS = "PRESCRIPTION_ANALYTICS"
    CLINICAL_OUTCOMES = "CLINICAL_OUTCOMES"
    FINANCIAL_SUMMARY = "FINANCIAL_SUMMARY"
    PERFORMANCE_METRICS = "PERFORMANCE_METRICS"
    COMPARATIVE_ANALYSIS = "COMPARATIVE_ANALYSIS"


class ReportPeriod(str, Enum):
    """Report time periods"""
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_QUARTER = "THIS_QUARTER"
    LAST_QUARTER = "LAST_QUARTER"
    THIS_YEAR = "THIS_YEAR"
    LAST_YEAR = "LAST_YEAR"
    CUSTOM = "CUSTOM"


class MetricType(str, Enum):
    """Analytics metric types"""
    COUNT = "COUNT"
    PERCENTAGE = "PERCENTAGE"
    AVERAGE = "AVERAGE"
    TOTAL = "TOTAL"
    RATE = "RATE"
    RATIO = "RATIO"
    TREND = "TREND"


class ExportFormat(str, Enum):
    """Export format options"""
    JSON = "JSON"
    CSV = "CSV"
    PDF = "PDF"
    EXCEL = "EXCEL"


class TrendDirection(str, Enum):
    """Trend direction indicators"""
    UP = "UP"
    DOWN = "DOWN"
    STABLE = "STABLE"
    VOLATILE = "VOLATILE"


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class MetricValue(BaseModel):
    """Individual metric value"""
    metric_name: str
    metric_type: MetricType
    current_value: Union[int, float, str]
    previous_value: Optional[Union[int, float, str]] = None
    change_percentage: Optional[float] = None
    trend_direction: Optional[TrendDirection] = None
    unit: Optional[str] = None
    description: Optional[str] = None


class PracticeOverview(BaseModel):
    """Practice overview summary"""
    doctor_name: str
    department: str
    specialization: str
    report_period: str
    generated_at: str
    
    # Core metrics
    total_patients_seen: int
    total_appointments: int
    total_prescriptions: int
    total_admissions: int
    
    # Performance metrics
    appointment_completion_rate: float
    average_consultation_time: Optional[float]
    patient_satisfaction_score: Optional[float]
    
    # Clinical metrics
    most_common_diagnoses: List[Dict[str, Any]]
    most_prescribed_medications: List[Dict[str, Any]]
    
    # Trends
    patient_growth_trend: TrendDirection
    appointment_trend: TrendDirection
    prescription_trend: TrendDirection


class PatientAnalytics(BaseModel):
    """Patient analytics and demographics"""
    total_unique_patients: int
    new_patients: int
    returning_patients: int
    
    # Demographics
    age_distribution: Dict[str, int]
    gender_distribution: Dict[str, int]
    
    # Clinical patterns
    chronic_conditions_breakdown: List[Dict[str, Any]]
    allergy_patterns: List[Dict[str, Any]]
    
    # Visit patterns
    average_visits_per_patient: float
    patient_retention_rate: float
    
    # Risk analysis
    high_risk_patients: int
    patients_requiring_follow_up: int


class AppointmentAnalytics(BaseModel):
    """Appointment analytics and patterns"""
    total_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    
    # Rates
    completion_rate: float
    cancellation_rate: float
    no_show_rate: float
    
    # Time patterns
    peak_appointment_hours: List[Dict[str, Any]]
    peak_appointment_days: List[Dict[str, Any]]
    
    # Duration analysis
    average_appointment_duration: Optional[float]
    consultation_time_distribution: Dict[str, int]
    
    # Scheduling efficiency
    schedule_utilization_rate: float
    average_wait_time: Optional[float]


class PrescriptionAnalytics(BaseModel):
    """Prescription analytics and patterns"""
    total_prescriptions: int
    unique_medications_prescribed: int
    average_medications_per_prescription: float
    
    # Top medications
    most_prescribed_drugs: List[Dict[str, Any]]
    drug_category_breakdown: List[Dict[str, Any]]
    
    # Prescription patterns
    prescription_frequency_by_diagnosis: List[Dict[str, Any]]
    generic_vs_brand_ratio: Dict[str, float]
    
    # Safety metrics
    drug_interactions_detected: int
    allergy_alerts_triggered: int
    
    # Compliance
    digital_signature_rate: float
    prescription_modification_rate: float


class ClinicalOutcomes(BaseModel):
    """Clinical outcomes and quality metrics"""
    total_cases_treated: int
    successful_treatment_rate: float
    
    # Outcome analysis
    diagnosis_accuracy_indicators: List[Dict[str, Any]]
    treatment_effectiveness_scores: List[Dict[str, Any]]
    
    # Follow-up metrics
    follow_up_compliance_rate: float
    readmission_rate: float
    
    # Quality indicators
    clinical_guidelines_adherence: float
    patient_safety_incidents: int
    
    # Improvement areas
    areas_for_improvement: List[str]
    quality_recommendations: List[str]


class FinancialSummary(BaseModel):
    """Financial summary and revenue analytics"""
    total_revenue: float
    consultation_revenue: float
    procedure_revenue: float
    
    # Revenue patterns
    revenue_by_service_type: Dict[str, float]
    revenue_trend: List[Dict[str, Any]]
    
    # Patient value
    average_revenue_per_patient: float
    high_value_patients: int
    
    # Collection metrics
    collection_rate: float
    outstanding_payments: float
    
    # Comparative analysis
    revenue_vs_previous_period: float
    revenue_growth_rate: float


class PerformanceMetrics(BaseModel):
    """Doctor performance metrics and KPIs"""
    overall_performance_score: float
    
    # Efficiency metrics
    patients_per_day: float
    appointments_per_hour: float
    consultation_efficiency: float
    
    # Quality metrics
    patient_satisfaction_score: float
    clinical_quality_score: float
    safety_score: float
    
    # Professional development
    continuing_education_hours: int
    certifications_maintained: int
    
    # Peer comparison
    department_ranking: Optional[int]
    hospital_ranking: Optional[int]
    
    # Goals and targets
    monthly_targets: Dict[str, Any]
    achievement_rate: float


class ComparativeAnalysis(BaseModel):
    """Comparative analysis with peers and benchmarks"""
    comparison_period: str
    
    # Peer comparison
    department_average_metrics: Dict[str, float]
    hospital_average_metrics: Dict[str, float]
    
    # Performance ranking
    department_rank: int
    total_doctors_in_department: int
    
    # Key differentiators
    strengths: List[str]
    improvement_areas: List[str]
    
    # Benchmarking
    industry_benchmarks: Dict[str, float]
    performance_vs_benchmark: Dict[str, str]


class ReportRequest(BaseModel):
    """Report generation request"""
    report_type: ReportType
    report_period: ReportPeriod
    custom_date_from: Optional[str] = None
    custom_date_to: Optional[str] = None
    include_comparisons: bool = True
    include_trends: bool = True
    export_format: Optional[ExportFormat] = None


class AnalyticsFilter(BaseModel):
    """Analytics filtering options"""
    patient_age_range: Optional[Dict[str, int]] = None
    patient_gender: Optional[str] = None
    diagnosis_categories: Optional[List[str]] = None
    appointment_types: Optional[List[str]] = None
    include_emergency: bool = True
    include_follow_ups: bool = True


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": user_roles[0] if user_roles else None,
        "all_roles": user_roles
    }


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile with department information"""
    roles = user_context.get("all_roles") or []
    if UserRole.DOCTOR.value not in roles:
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
            
        # Create a mock object that has the same interface as DoctorProfile
        class MockDoctorProfile:
            def __init__(self, user, department):
                self.user = user
                self.department = department
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                self.department_id = department.id
                self.id = user.id  # Use user.id as profile id for compatibility
                
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
        
        doctor = MockDoctorProfile(doctor_user, assignment.department)
    
    return doctor


def ensure_doctor_access(user_context: dict):
    """Ensure user is a doctor"""
    roles = user_context.get("all_roles") or []
    if UserRole.DOCTOR.value not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )


def _clinical_db_sessions(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
) -> List[AsyncSession]:
    seen: set[int] = set()
    sessions: List[AsyncSession] = []
    for sess in (tenant_db, platform_db):
        key = id(sess)
        if key in seen:
            continue
        seen.add(key)
        sessions.append(sess)
    return sessions


def _doctor_user_id(doctor: Any) -> uuid.UUID:
    """Appointments / medical records FK -> users.id (not doctor_profiles.id)."""
    return getattr(doctor, "user_id", None) or doctor.id


async def _doctor_scope_ids(
    db: AsyncSession,
    user_id: uuid.UUID,
    hospital_id: uuid.UUID,
) -> List[uuid.UUID]:
    ids: List[uuid.UUID] = [user_id]
    profile_result = await db.execute(
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


def _period_bounds(date_from: date, date_to: date) -> tuple[str, str, datetime, datetime]:
    """Appointment dates are YYYY-MM-DD strings; clinical rows use timestamptz."""
    start_s = date_from.isoformat()
    end_s = date_to.isoformat()
    start_dt = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
    return start_s, end_s, start_dt, end_dt


async def _collect_patient_ids_for_doctor_period(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    hospital_id: uuid.UUID,
    scope_ids: List[uuid.UUID],
    date_from: date,
    date_to: date,
) -> set[uuid.UUID]:
    """Distinct patients from appointments + medical records + admissions (tenant + platform)."""
    start_s, end_s, start_dt, end_dt = _period_bounds(date_from, date_to)
    patient_ids: set[uuid.UUID] = set()
    appt_status_ok = Appointment.status != AppointmentStatus.CANCELLED.value

    for session in _clinical_db_sessions(tenant_db, platform_db):
        appt_rows = await session.execute(
            select(func.distinct(Appointment.patient_id)).where(
                and_(
                    Appointment.hospital_id == hospital_id,
                    Appointment.doctor_id.in_(scope_ids),
                    Appointment.appointment_date >= start_s,
                    Appointment.appointment_date <= end_s,
                    appt_status_ok,
                )
            )
        )
        patient_ids.update(row[0] for row in appt_rows.all() if row[0])

        record_rows = await session.execute(
            select(func.distinct(MedicalRecord.patient_id)).where(
                and_(
                    MedicalRecord.hospital_id == hospital_id,
                    MedicalRecord.doctor_id.in_(scope_ids),
                    MedicalRecord.created_at >= start_dt,
                    MedicalRecord.created_at <= end_dt,
                )
            )
        )
        patient_ids.update(row[0] for row in record_rows.all() if row[0])

        admission_rows = await session.execute(
            select(func.distinct(Admission.patient_id)).where(
                and_(
                    Admission.hospital_id == hospital_id,
                    Admission.doctor_id.in_(scope_ids),
                    Admission.admission_date >= start_dt,
                    Admission.admission_date <= end_dt,
                )
            )
        )
        patient_ids.update(row[0] for row in admission_rows.all() if row[0])

    return patient_ids


async def _load_patients_by_ids(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    patient_ids: set[uuid.UUID],
) -> List[PatientProfile]:
    if not patient_ids:
        return []
    seen: set[uuid.UUID] = set()
    patients: List[PatientProfile] = []
    id_list = list(patient_ids)
    for session in _clinical_db_sessions(tenant_db, platform_db):
        result = await session.execute(
            select(PatientProfile)
            .where(PatientProfile.id.in_(id_list))
            .options(selectinload(PatientProfile.user))
        )
        for patient in result.scalars().all():
            if patient.id in seen:
                continue
            seen.add(patient.id)
            patients.append(patient)
    return patients


@dataclass
class ReportAnalyticsScope:
    """Tenant + platform scoped queries for doctor reports."""

    tenant_db: AsyncSession
    platform_db: AsyncSession
    doctor: Any
    hospital_id: uuid.UUID
    scope_ids: List[uuid.UUID]
    date_from: date
    date_to: date

    def sessions(self) -> List[AsyncSession]:
        return _clinical_db_sessions(self.tenant_db, self.platform_db)

    def appointment_filters(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        *,
        status: Optional[str] = None,
        exclude_cancelled: bool = False,
    ) -> List[Any]:
        d0 = date_from or self.date_from
        d1 = date_to or self.date_to
        start_s, end_s, _, _ = _period_bounds(d0, d1)
        filters: List[Any] = [
            Appointment.hospital_id == self.hospital_id,
            Appointment.doctor_id.in_(self.scope_ids),
            Appointment.appointment_date >= start_s,
            Appointment.appointment_date <= end_s,
        ]
        if status is not None:
            filters.append(Appointment.status == status)
        elif exclude_cancelled:
            filters.append(Appointment.status != AppointmentStatus.CANCELLED.value)
        return filters

    def prescription_filters(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Any]:
        d0 = date_from or self.date_from
        d1 = date_to or self.date_to
        start_s, end_s, _, _ = _period_bounds(d0, d1)
        return [
            Prescription.hospital_id == self.hospital_id,
            Prescription.doctor_id.in_(self.scope_ids),
            Prescription.prescription_date >= start_s,
            Prescription.prescription_date <= end_s,
        ]

    def medical_record_filters(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Any]:
        d0 = date_from or self.date_from
        d1 = date_to or self.date_to
        _, _, start_dt, end_dt = _period_bounds(d0, d1)
        return [
            MedicalRecord.hospital_id == self.hospital_id,
            MedicalRecord.doctor_id.in_(self.scope_ids),
            MedicalRecord.created_at >= start_dt,
            MedicalRecord.created_at <= end_dt,
        ]

    def admission_filters(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Any]:
        d0 = date_from or self.date_from
        d1 = date_to or self.date_to
        _, _, start_dt, end_dt = _period_bounds(d0, d1)
        return [
            Admission.hospital_id == self.hospital_id,
            Admission.doctor_id.in_(self.scope_ids),
            Admission.admission_date >= start_dt,
            Admission.admission_date <= end_dt,
        ]

    async def count_appointments(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        *,
        status: Optional[str] = None,
        exclude_cancelled: bool = False,
    ) -> int:
        total = 0
        filters = self.appointment_filters(
            date_from, date_to, status=status, exclude_cancelled=exclude_cancelled
        )
        for session in self.sessions():
            total += (
                await session.execute(select(func.count(Appointment.id)).where(and_(*filters)))
            ).scalar() or 0
        return total

    async def list_appointments(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        *,
        status: Optional[str] = None,
        exclude_cancelled: bool = False,
    ) -> List[Appointment]:
        seen: set[uuid.UUID] = set()
        rows: List[Appointment] = []
        filters = self.appointment_filters(
            date_from, date_to, status=status, exclude_cancelled=exclude_cancelled
        )
        for session in self.sessions():
            result = await session.execute(select(Appointment).where(and_(*filters)))
            for appointment in result.scalars().all():
                if appointment.id in seen:
                    continue
                seen.add(appointment.id)
                rows.append(appointment)
        return rows

    async def count_prescriptions(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> int:
        total = 0
        filters = self.prescription_filters(date_from, date_to)
        for session in self.sessions():
            total += (
                await session.execute(select(func.count(Prescription.id)).where(and_(*filters)))
            ).scalar() or 0
        return total

    async def list_prescriptions(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[Prescription]:
        seen: set[uuid.UUID] = set()
        rows: List[Prescription] = []
        filters = self.prescription_filters(date_from, date_to)
        for session in self.sessions():
            result = await session.execute(select(Prescription).where(and_(*filters)))
            for prescription in result.scalars().all():
                if prescription.id in seen:
                    continue
                seen.add(prescription.id)
                rows.append(prescription)
        return rows

    async def count_admissions(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> int:
        total = 0
        filters = self.admission_filters(date_from, date_to)
        for session in self.sessions():
            total += (
                await session.execute(select(func.count(Admission.id)).where(and_(*filters)))
            ).scalar() or 0
        return total

    async def count_medical_records(self, **extra_filters: Any) -> int:
        total = 0
        filters = list(self.medical_record_filters())
        filters.extend(extra_filters)
        for session in self.sessions():
            total += (
                await session.execute(select(func.count(MedicalRecord.id)).where(and_(*filters)))
            ).scalar() or 0
        return total

    async def diagnosis_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        filters = list(self.medical_record_filters())
        filters.append(MedicalRecord.diagnosis.isnot(None))
        for session in self.sessions():
            result = await session.execute(
                select(MedicalRecord.diagnosis, func.count(MedicalRecord.id))
                .where(and_(*filters))
                .group_by(MedicalRecord.diagnosis)
            )
            for diagnosis, count in result.all():
                if diagnosis:
                    counts[diagnosis] = counts.get(diagnosis, 0) + int(count or 0)
        return counts

    async def count_department_doctors(self, department_id: uuid.UUID) -> int:
        total = 0
        for session in self.sessions():
            total += (
                await session.execute(
                    select(func.count(DoctorProfile.id)).where(
                        and_(
                            DoctorProfile.department_id == department_id,
                            DoctorProfile.hospital_id == self.hospital_id,
                        )
                    )
                )
            ).scalar() or 0
        return max(total, 1)


async def _build_report_scope(
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
    user_context: dict,
    current_user: User,
    date_from: date,
    date_to: date,
) -> ReportAnalyticsScope:
    doctor = await get_doctor_profile(user_context, tenant_db)
    hospital_id = doctor.hospital_id or current_user.hospital_id
    if not hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Doctor must be associated with a hospital",
        )
    hid = hospital_id if isinstance(hospital_id, uuid.UUID) else uuid.UUID(str(hospital_id))
    scope_ids = await _doctor_scope_ids(tenant_db, _doctor_user_id(doctor), hid)
    return ReportAnalyticsScope(
        tenant_db, platform_db, doctor, hid, scope_ids, date_from, date_to
    )


def get_date_range(period: ReportPeriod, custom_from: str = None, custom_to: str = None) -> tuple:
    """Get date range based on report period"""
    today = date.today()
    
    if period == ReportPeriod.TODAY:
        return today, today
    elif period == ReportPeriod.YESTERDAY:
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    elif period == ReportPeriod.THIS_WEEK:
        start_of_week = today - timedelta(days=today.weekday())
        return start_of_week, today
    elif period == ReportPeriod.LAST_WEEK:
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        return start_of_last_week, end_of_last_week
    elif period == ReportPeriod.THIS_MONTH:
        start_of_month = today.replace(day=1)
        return start_of_month, today
    elif period == ReportPeriod.LAST_MONTH:
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end
    elif period == ReportPeriod.THIS_QUARTER:
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)
        return quarter_start, today
    elif period == ReportPeriod.THIS_YEAR:
        year_start = today.replace(month=1, day=1)
        return year_start, today
    elif period == ReportPeriod.CUSTOM:
        if custom_from and custom_to:
            return datetime.strptime(custom_from, "%Y-%m-%d").date(), datetime.strptime(custom_to, "%Y-%m-%d").date()
        else:
            raise HTTPException(status_code=400, detail="Custom date range requires both from and to dates")
    else:
        # Default to this month
        start_of_month = today.replace(day=1)
        return start_of_month, today


def calculate_trend(current_value: float, previous_value: float) -> tuple:
    """Calculate trend direction and percentage change"""
    if previous_value == 0:
        if current_value > 0:
            return TrendDirection.UP, float('inf')
        else:
            return TrendDirection.STABLE, 0.0
    
    change_percentage = ((current_value - previous_value) / previous_value) * 100
    
    if abs(change_percentage) < 5:
        direction = TrendDirection.STABLE
    elif change_percentage > 0:
        direction = TrendDirection.UP
    else:
        direction = TrendDirection.DOWN
    
    return direction, change_percentage


def format_currency(amount: float) -> str:
    """Format currency amount"""
    return f"${amount:,.2f}"


def calculate_age_from_dob(date_of_birth: str) -> int:
    """Calculate age from date of birth"""
    try:
        birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except:
        return 0


# ============================================================================
# PRACTICE OVERVIEW AND SUMMARY
# ============================================================================

@router.get("/practice-overview")
async def get_practice_overview(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comprehensive practice overview with key metrics and trends.
    
    Access Control:
    - Only Doctors can access practice overview
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    doctor = scope.doctor

    period_days = (date_to - date_from).days + 1
    prev_date_to = date_from - timedelta(days=1)
    prev_date_from = prev_date_to - timedelta(days=period_days - 1)

    total_patients = len(
        await _collect_patient_ids_for_doctor_period(
            tenant_db,
            platform_db,
            scope.hospital_id,
            scope.scope_ids,
            date_from,
            date_to,
        )
    )
    total_appointments = await scope.count_appointments()
    completed_appointments = await scope.count_appointments(
        status=AppointmentStatus.COMPLETED.value
    )
    total_prescriptions = await scope.count_prescriptions()
    total_admissions = await scope.count_admissions()
    completion_rate = (
        (completed_appointments / total_appointments * 100) if total_appointments > 0 else 0
    )

    diagnosis_map = await scope.diagnosis_counts()
    most_common_diagnoses = [
        {
            "diagnosis": diagnosis,
            "count": count,
            "percentage": round((count / completed_appointments) * 100, 1)
            if completed_appointments > 0
            else 0,
        }
        for diagnosis, count in sorted(diagnosis_map.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    all_medications: List[str] = []
    for prescription in await scope.list_prescriptions():
        if prescription.medications:
            for med in prescription.medications:
                if isinstance(med, dict) and med.get("name"):
                    all_medications.append(med["name"])
    med_counts: Dict[str, int] = {}
    for med in all_medications:
        med_counts[med] = med_counts.get(med, 0) + 1
    most_prescribed_medications = [
        {
            "medication": med,
            "count": count,
            "percentage": round((count / len(all_medications)) * 100, 1) if all_medications else 0,
        }
        for med, count in sorted(med_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    prev_total_patients = len(
        await _collect_patient_ids_for_doctor_period(
            tenant_db,
            platform_db,
            scope.hospital_id,
            scope.scope_ids,
            prev_date_from,
            prev_date_to,
        )
    )
    prev_total_appointments = await scope.count_appointments(
        prev_date_from, prev_date_to
    )
    prev_total_prescriptions = await scope.count_prescriptions(
        prev_date_from, prev_date_to
    )

    patient_trend, _ = calculate_trend(total_patients, prev_total_patients)
    appointment_trend, _ = calculate_trend(total_appointments, prev_total_appointments)
    prescription_trend, _ = calculate_trend(total_prescriptions, prev_total_prescriptions)

    return PracticeOverview(
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        department=doctor.department.name,
        specialization=getattr(doctor, "specialization", "General Medicine"),
        report_period=f"{date_from} to {date_to}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_patients_seen=total_patients,
        total_appointments=total_appointments,
        total_prescriptions=total_prescriptions,
        total_admissions=total_admissions,
        appointment_completion_rate=round(completion_rate, 1),
        average_consultation_time=None,
        patient_satisfaction_score=None,
        most_common_diagnoses=most_common_diagnoses,
        most_prescribed_medications=most_prescribed_medications,
        patient_growth_trend=patient_trend,
        appointment_trend=appointment_trend,
        prescription_trend=prescription_trend,
    )


# ============================================================================
# PATIENT ANALYTICS
# ============================================================================

@router.get("/patient-analytics")
async def get_patient_analytics(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comprehensive patient analytics and demographics.
    
    Access Control:
    - Only Doctors can access patient analytics
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)
    hospital_id = doctor.hospital_id or current_user.hospital_id
    if not hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Doctor must be associated with a hospital",
        )

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    doctor_user_id = _doctor_user_id(doctor)
    scope_ids = await _doctor_scope_ids(tenant_db, doctor_user_id, hospital_id)
    start_s, end_s, start_dt, end_dt = _period_bounds(date_from, date_to)

    patient_ids_in_period = await _collect_patient_ids_for_doctor_period(
        tenant_db,
        platform_db,
        hospital_id,
        scope_ids,
        date_from,
        date_to,
    )
    total_unique_patients = len(patient_ids_in_period)

    if total_unique_patients == 0:
        return PatientAnalytics(
            total_unique_patients=0,
            new_patients=0,
            returning_patients=0,
            age_distribution={},
            gender_distribution={},
            chronic_conditions_breakdown=[],
            allergy_patterns=[],
            average_visits_per_patient=0.0,
            patient_retention_rate=0.0,
            high_risk_patients=0,
            patients_requiring_follow_up=0
        )

    patients = await _load_patients_by_ids(tenant_db, platform_db, patient_ids_in_period)

    new_patients = 0
    returning_patients = 0
    appt_status_ok = Appointment.status != AppointmentStatus.CANCELLED.value

    for patient in patients:
        had_prior = False
        for session in _clinical_db_sessions(tenant_db, platform_db):
            prev_appt = await session.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.hospital_id == hospital_id,
                        Appointment.patient_id == patient.id,
                        Appointment.doctor_id.in_(scope_ids),
                        Appointment.appointment_date < start_s,
                        appt_status_ok,
                    )
                )
            )
            if (prev_appt.scalar() or 0) > 0:
                had_prior = True
                break
            prev_record = await session.execute(
                select(func.count(MedicalRecord.id)).where(
                    and_(
                        MedicalRecord.hospital_id == hospital_id,
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id.in_(scope_ids),
                        MedicalRecord.created_at < start_dt,
                    )
                )
            )
            if (prev_record.scalar() or 0) > 0:
                had_prior = True
                break

        if had_prior:
            returning_patients += 1
        else:
            new_patients += 1
    
    # Age distribution analysis
    age_groups = {"0-18": 0, "19-30": 0, "31-45": 0, "46-60": 0, "61-75": 0, "75+": 0}
    gender_distribution = {"MALE": 0, "FEMALE": 0, "OTHER": 0}
    
    chronic_conditions_count = {}
    allergy_count = {}
    high_risk_count = 0
    
    for patient in patients:
        # Age analysis
        age = calculate_age_from_dob(patient.date_of_birth)
        
        if age <= 18:
            age_groups["0-18"] += 1
        elif age <= 30:
            age_groups["19-30"] += 1
        elif age <= 45:
            age_groups["31-45"] += 1
        elif age <= 60:
            age_groups["46-60"] += 1
        elif age <= 75:
            age_groups["61-75"] += 1
        else:
            age_groups["75+"] += 1
        
        # Gender analysis
        gender_distribution[patient.gender] = gender_distribution.get(patient.gender, 0) + 1
        
        # Chronic conditions analysis
        if patient.chronic_conditions:
            for condition in patient.chronic_conditions:
                chronic_conditions_count[condition] = chronic_conditions_count.get(condition, 0) + 1
            
            # High risk if multiple chronic conditions
            if len(patient.chronic_conditions) >= 2:
                high_risk_count += 1
        
        # Allergy analysis
        if patient.allergies:
            for allergy in patient.allergies:
                allergy_count[allergy] = allergy_count.get(allergy, 0) + 1
        
        # High risk if elderly with chronic conditions
        if age > 65 and patient.chronic_conditions:
            high_risk_count += 1
    
    # Format chronic conditions breakdown
    chronic_conditions_breakdown = [
        {
            "condition": condition,
            "patient_count": count,
            "percentage": round((count / total_unique_patients) * 100, 1)
        }
        for condition, count in sorted(chronic_conditions_count.items(), key=lambda x: x[1], reverse=True)[:10]
    ]
    
    # Format allergy patterns
    allergy_patterns = [
        {
            "allergy": allergy,
            "patient_count": count,
            "percentage": round((count / total_unique_patients) * 100, 1)
        }
        for allergy, count in sorted(allergy_count.items(), key=lambda x: x[1], reverse=True)[:10]
    ]
    
    total_visits = 0
    patient_id_list = list(patient_ids_in_period)
    for session in _clinical_db_sessions(tenant_db, platform_db):
        visits_result = await session.execute(
            select(func.count(Appointment.id)).where(
                and_(
                    Appointment.hospital_id == hospital_id,
                    Appointment.doctor_id.in_(scope_ids),
                    Appointment.patient_id.in_(patient_id_list),
                    Appointment.appointment_date >= start_s,
                    Appointment.appointment_date <= end_s,
                    appt_status_ok,
                )
            )
        )
        total_visits += visits_result.scalar() or 0
    average_visits_per_patient = total_visits / total_unique_patients if total_unique_patients > 0 else 0
    
    # Calculate retention rate (simplified)
    retention_rate = (returning_patients / total_unique_patients * 100) if total_unique_patients > 0 else 0
    
    # Patients requiring follow-up (simplified - patients with recent visits)
    follow_up_needed = min(high_risk_count + (new_patients // 2), total_unique_patients)
    
    return PatientAnalytics(
        total_unique_patients=total_unique_patients,
        new_patients=new_patients,
        returning_patients=returning_patients,
        age_distribution=age_groups,
        gender_distribution=gender_distribution,
        chronic_conditions_breakdown=chronic_conditions_breakdown,
        allergy_patterns=allergy_patterns,
        average_visits_per_patient=round(average_visits_per_patient, 1),
        patient_retention_rate=round(retention_rate, 1),
        high_risk_patients=high_risk_count,
        patients_requiring_follow_up=follow_up_needed
    )

# ============================================================================
# APPOINTMENT ANALYTICS
# ============================================================================

@router.get("/appointment-analytics")
async def get_appointment_analytics(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comprehensive appointment analytics and scheduling patterns.
    
    Access Control:
    - Only Doctors can access appointment analytics
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    appointments = await scope.list_appointments()

    total_appointments = len(appointments)
    completed_appointments = sum(
        1 for a in appointments if a.status == AppointmentStatus.COMPLETED.value
    )
    cancelled_appointments = sum(
        1 for a in appointments if a.status == AppointmentStatus.CANCELLED.value
    )
    no_show_appointments = sum(
        1
        for a in appointments
        if a.status == AppointmentStatus.REQUESTED.value and not a.checked_in_at
    )

    completion_rate = (completed_appointments / total_appointments * 100) if total_appointments > 0 else 0
    cancellation_rate = (cancelled_appointments / total_appointments * 100) if total_appointments > 0 else 0
    no_show_rate = (no_show_appointments / total_appointments * 100) if total_appointments > 0 else 0

    hourly_counts: Dict[str, int] = {}
    daily_counts: Dict[int, int] = {}
    for appointment in appointments:
        if appointment.appointment_time:
            hour_key = str(appointment.appointment_time)[:2]
            hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + 1
        if appointment.appointment_date:
            try:
                dow = datetime.strptime(appointment.appointment_date, "%Y-%m-%d").weekday()
                daily_counts[dow] = daily_counts.get(dow, 0) + 1
            except ValueError:
                pass

    peak_appointment_hours = [
        {
            "hour": f"{hour}:00",
            "appointment_count": count,
            "percentage": round((count / total_appointments) * 100, 1) if total_appointments > 0 else 0,
        }
        for hour, count in sorted(hourly_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    ]

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    peak_appointment_days = [
        {
            "day": day_names[day_num] if 0 <= day_num < 7 else str(day_num),
            "appointment_count": count,
            "percentage": round((count / total_appointments) * 100, 1) if total_appointments > 0 else 0,
        }
        for day_num, count in sorted(daily_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    
    # Consultation time distribution (simplified)
    consultation_time_distribution = {
        "15-30 min": total_appointments // 4,
        "30-45 min": total_appointments // 2,
        "45-60 min": total_appointments // 4,
        "60+ min": total_appointments // 8
    }
    
    # Schedule utilization (simplified calculation)
    # Assuming 8-hour workday with 30-minute slots = 16 slots per day
    working_days = (date_to - date_from).days + 1
    total_available_slots = working_days * 16
    schedule_utilization_rate = (total_appointments / total_available_slots * 100) if total_available_slots > 0 else 0
    
    return AppointmentAnalytics(
        total_appointments=total_appointments,
        completed_appointments=completed_appointments,
        cancelled_appointments=cancelled_appointments,
        no_show_appointments=no_show_appointments,
        completion_rate=round(completion_rate, 1),
        cancellation_rate=round(cancellation_rate, 1),
        no_show_rate=round(no_show_rate, 1),
        peak_appointment_hours=peak_appointment_hours,
        peak_appointment_days=peak_appointment_days,
        average_appointment_duration=35.0,  # Mock data
        consultation_time_distribution=consultation_time_distribution,
        schedule_utilization_rate=round(schedule_utilization_rate, 1),
        average_wait_time=12.5  # Mock data
    )


# ============================================================================
# PRESCRIPTION ANALYTICS
# ============================================================================

# Disabled: prescription endpoints are exposed only through Doctor Portal sidebar routes.
async def get_prescription_analytics(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comprehensive prescription analytics and medication patterns.
    
    Access Control:
    - Only Doctors can access prescription analytics
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    prescriptions = await scope.list_prescriptions()
    total_prescriptions = len(prescriptions)
    
    if total_prescriptions == 0:
        return PrescriptionAnalytics(
            total_prescriptions=0,
            unique_medications_prescribed=0,
            average_medications_per_prescription=0.0,
            most_prescribed_drugs=[],
            drug_category_breakdown=[],
            prescription_frequency_by_diagnosis=[],
            generic_vs_brand_ratio={"generic": 0.0, "brand": 0.0},
            drug_interactions_detected=0,
            allergy_alerts_triggered=0,
            digital_signature_rate=0.0,
            prescription_modification_rate=0.0
        )
    
    # Analyze medications
    all_medications = []
    medication_counts = {}
    total_medication_count = 0
    
    for prescription in prescriptions:
        if prescription.medications:
            for med in prescription.medications:
                if isinstance(med, dict) and med.get('name'):
                    med_name = med['name']
                    all_medications.append(med_name)
                    medication_counts[med_name] = medication_counts.get(med_name, 0) + 1
                    total_medication_count += 1
    
    unique_medications_prescribed = len(set(all_medications))
    average_medications_per_prescription = total_medication_count / total_prescriptions if total_prescriptions > 0 else 0
    
    # Most prescribed drugs
    most_prescribed_drugs = [
        {
            "medication": med,
            "prescription_count": count,
            "percentage": round((count / total_medication_count) * 100, 1) if total_medication_count > 0 else 0
        }
        for med, count in sorted(medication_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    ]
    
    # Drug category breakdown (simplified)
    drug_categories = {
        "Antibiotics": 0,
        "Pain Relievers": 0,
        "Cardiovascular": 0,
        "Diabetes": 0,
        "Respiratory": 0,
        "Other": 0
    }
    
    # Simple categorization based on common drug names
    for med_name in all_medications:
        med_lower = med_name.lower()
        if any(antibiotic in med_lower for antibiotic in ['amoxicillin', 'azithromycin', 'ciprofloxacin']):
            drug_categories["Antibiotics"] += 1
        elif any(pain in med_lower for pain in ['paracetamol', 'ibuprofen', 'aspirin']):
            drug_categories["Pain Relievers"] += 1
        elif any(cardio in med_lower for cardio in ['atenolol', 'amlodipine', 'metoprolol']):
            drug_categories["Cardiovascular"] += 1
        elif any(diabetes in med_lower for diabetes in ['metformin', 'insulin', 'glimepiride']):
            drug_categories["Diabetes"] += 1
        elif any(resp in med_lower for resp in ['salbutamol', 'montelukast', 'prednisolone']):
            drug_categories["Respiratory"] += 1
        else:
            drug_categories["Other"] += 1
    
    drug_category_breakdown = [
        {
            "category": category,
            "count": count,
            "percentage": round((count / total_medication_count) * 100, 1) if total_medication_count > 0 else 0
        }
        for category, count in drug_categories.items() if count > 0
    ]
    
    # Prescription frequency by diagnosis
    diagnosis_prescription_map = {}
    for prescription in prescriptions:
        if prescription.diagnosis:
            diagnosis = prescription.diagnosis
            if diagnosis not in diagnosis_prescription_map:
                diagnosis_prescription_map[diagnosis] = 0
            diagnosis_prescription_map[diagnosis] += 1
    
    prescription_frequency_by_diagnosis = [
        {
            "diagnosis": diagnosis,
            "prescription_count": count,
            "percentage": round((count / total_prescriptions) * 100, 1)
        }
        for diagnosis, count in sorted(diagnosis_prescription_map.items(), key=lambda x: x[1], reverse=True)[:10]
    ]
    
    # Generic vs brand ratio (simplified)
    generic_count = total_medication_count * 0.7  # Assume 70% generic
    brand_count = total_medication_count * 0.3    # Assume 30% brand
    
    generic_vs_brand_ratio = {
        "generic": round((generic_count / total_medication_count) * 100, 1) if total_medication_count > 0 else 0,
        "brand": round((brand_count / total_medication_count) * 100, 1) if total_medication_count > 0 else 0
    }
    
    # Safety metrics (mock data)
    drug_interactions_detected = total_prescriptions // 10  # Assume 10% have interactions
    allergy_alerts_triggered = total_prescriptions // 20    # Assume 5% trigger allergy alerts
    
    # Digital signature rate
    digitally_signed = sum(1 for p in prescriptions if p.is_digitally_signed)
    digital_signature_rate = (digitally_signed / total_prescriptions * 100) if total_prescriptions > 0 else 0
    
    # Prescription modification rate (mock)
    prescription_modification_rate = 15.0  # Mock 15% modification rate
    
    return PrescriptionAnalytics(
        total_prescriptions=total_prescriptions,
        unique_medications_prescribed=unique_medications_prescribed,
        average_medications_per_prescription=round(average_medications_per_prescription, 1),
        most_prescribed_drugs=most_prescribed_drugs,
        drug_category_breakdown=drug_category_breakdown,
        prescription_frequency_by_diagnosis=prescription_frequency_by_diagnosis,
        generic_vs_brand_ratio=generic_vs_brand_ratio,
        drug_interactions_detected=drug_interactions_detected,
        allergy_alerts_triggered=allergy_alerts_triggered,
        digital_signature_rate=round(digital_signature_rate, 1),
        prescription_modification_rate=prescription_modification_rate
    )


# ============================================================================
# CLINICAL OUTCOMES AND QUALITY METRICS
# ============================================================================

@router.get("/clinical-outcomes")
async def get_clinical_outcomes(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get clinical outcomes and quality metrics analysis.
    
    Access Control:
    - Only Doctors can access clinical outcomes
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )

    total_cases_treated = await scope.count_appointments(
        status=AppointmentStatus.COMPLETED.value
    )
    if total_cases_treated == 0:
        total_cases_treated = await scope.count_medical_records()
    
    if total_cases_treated == 0:
        return ClinicalOutcomes(
            total_cases_treated=0,
            successful_treatment_rate=0.0,
            diagnosis_accuracy_indicators=[],
            treatment_effectiveness_scores=[],
            follow_up_compliance_rate=0.0,
            readmission_rate=0.0,
            clinical_guidelines_adherence=0.0,
            patient_safety_incidents=0,
            areas_for_improvement=[],
            quality_recommendations=[]
        )
    
    # Calculate successful treatment rate (simplified)
    # Assume success based on completed appointments without immediate readmissions
    successful_treatment_rate = 85.0  # Mock 85% success rate
    
    # Diagnosis accuracy indicators (mock data based on common patterns)
    diagnosis_accuracy_indicators = [
        {
            "diagnosis_category": "Respiratory Infections",
            "accuracy_score": 92.5,
            "confidence_level": "High",
            "cases_analyzed": total_cases_treated // 4
        },
        {
            "diagnosis_category": "Cardiovascular Conditions",
            "accuracy_score": 88.0,
            "confidence_level": "High",
            "cases_analyzed": total_cases_treated // 5
        },
        {
            "diagnosis_category": "Diabetes Management",
            "accuracy_score": 94.0,
            "confidence_level": "Very High",
            "cases_analyzed": total_cases_treated // 6
        }
    ]
    
    # Treatment effectiveness scores
    treatment_effectiveness_scores = [
        {
            "treatment_type": "Medication Therapy",
            "effectiveness_score": 87.5,
            "patient_improvement_rate": 82.0,
            "cases_evaluated": total_cases_treated * 0.8
        },
        {
            "treatment_type": "Lifestyle Modifications",
            "effectiveness_score": 75.0,
            "patient_improvement_rate": 68.0,
            "cases_evaluated": total_cases_treated * 0.4
        },
        {
            "treatment_type": "Combination Therapy",
            "effectiveness_score": 91.0,
            "patient_improvement_rate": 88.0,
            "cases_evaluated": total_cases_treated * 0.3
        }
    ]
    
    follow_up_recommended = await scope.count_medical_records(
        MedicalRecord.follow_up_instructions.isnot(None),
        MedicalRecord.follow_up_instructions != "",
    )
    follow_up_compliance_rate = 72.0 if follow_up_recommended > 0 else 0.0  # Mock 72% compliance
    
    # Readmission rate (simplified calculation)
    readmission_rate = 8.5  # Mock 8.5% readmission rate
    
    # Clinical guidelines adherence (mock)
    clinical_guidelines_adherence = 89.0  # Mock 89% adherence
    
    # Patient safety incidents (mock)
    patient_safety_incidents = max(0, total_cases_treated // 100)  # 1% incident rate
    
    # Areas for improvement
    areas_for_improvement = []
    if follow_up_compliance_rate < 80:
        areas_for_improvement.append("Improve patient follow-up compliance")
    if readmission_rate > 10:
        areas_for_improvement.append("Reduce readmission rates")
    if clinical_guidelines_adherence < 90:
        areas_for_improvement.append("Enhance clinical guidelines adherence")
    if not areas_for_improvement:
        areas_for_improvement.append("Maintain current high standards")
    
    # Quality recommendations
    quality_recommendations = [
        "Continue current best practices in patient care",
        "Implement patient education programs for better compliance",
        "Regular review of treatment protocols",
        "Enhanced documentation of clinical decisions"
    ]
    
    return ClinicalOutcomes(
        total_cases_treated=total_cases_treated,
        successful_treatment_rate=successful_treatment_rate,
        diagnosis_accuracy_indicators=diagnosis_accuracy_indicators,
        treatment_effectiveness_scores=treatment_effectiveness_scores,
        follow_up_compliance_rate=follow_up_compliance_rate,
        readmission_rate=readmission_rate,
        clinical_guidelines_adherence=clinical_guidelines_adherence,
        patient_safety_incidents=patient_safety_incidents,
        areas_for_improvement=areas_for_improvement,
        quality_recommendations=quality_recommendations
    )


# ============================================================================
# FINANCIAL SUMMARY AND REVENUE ANALYTICS
# ============================================================================

@router.get("/financial-summary")
async def get_financial_summary(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get financial summary and revenue analytics.
    
    Access Control:
    - Only Doctors can access financial summary
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    doctor = scope.doctor

    completed_appointments = await scope.list_appointments(
        status=AppointmentStatus.COMPLETED.value
    )
    if not completed_appointments:
        completed_appointments = await scope.list_appointments(exclude_cancelled=True)
    
    # Calculate revenue
    consultation_revenue = 0.0
    for appointment in completed_appointments:
        if appointment.consultation_fee:
            consultation_revenue += float(appointment.consultation_fee)
        else:
            # Use doctor's default consultation fee
            consultation_revenue += float(doctor.consultation_fee)
    
    # Mock procedure revenue (would come from separate procedures table)
    procedure_revenue = consultation_revenue * 0.3  # Assume 30% additional from procedures
    
    total_revenue = consultation_revenue + procedure_revenue
    
    # Revenue by service type
    revenue_by_service_type = {
        "Consultations": consultation_revenue,
        "Procedures": procedure_revenue,
        "Follow-ups": consultation_revenue * 0.2,  # Mock follow-up revenue
        "Emergency": consultation_revenue * 0.1    # Mock emergency revenue
    }
    
    # Revenue trend (mock daily breakdown)
    days_in_period = (date_to - date_from).days + 1
    daily_revenue = total_revenue / days_in_period if days_in_period > 0 else 0
    
    revenue_trend = []
    current_date = date_from
    while current_date <= date_to:
        # Add some variation to daily revenue
        variation = 0.8 + (hash(str(current_date)) % 40) / 100  # 0.8 to 1.2 multiplier
        revenue_trend.append({
            "date": current_date.isoformat(),
            "revenue": round(daily_revenue * variation, 2)
        })
        current_date += timedelta(days=1)
    
    # Patient value analysis
    total_patients = len(set(appointment.patient_id for appointment in completed_appointments))
    average_revenue_per_patient = total_revenue / total_patients if total_patients > 0 else 0
    
    # High value patients (top 20% by revenue)
    high_value_patients = max(1, total_patients // 5)
    
    # Collection metrics (mock)
    collection_rate = 92.5  # Mock 92.5% collection rate
    outstanding_payments = total_revenue * 0.075  # 7.5% outstanding
    
    # Previous period comparison (mock)
    revenue_vs_previous_period = 8.5  # Mock 8.5% growth
    revenue_growth_rate = 12.0  # Mock 12% annual growth rate
    
    return FinancialSummary(
        total_revenue=round(total_revenue, 2),
        consultation_revenue=round(consultation_revenue, 2),
        procedure_revenue=round(procedure_revenue, 2),
        revenue_by_service_type={k: round(v, 2) for k, v in revenue_by_service_type.items()},
        revenue_trend=revenue_trend,
        average_revenue_per_patient=round(average_revenue_per_patient, 2),
        high_value_patients=high_value_patients,
        collection_rate=collection_rate,
        outstanding_payments=round(outstanding_payments, 2),
        revenue_vs_previous_period=revenue_vs_previous_period,
        revenue_growth_rate=revenue_growth_rate
    )


# ============================================================================
# PERFORMANCE METRICS AND KPIs
# ============================================================================

@router.get("/performance-metrics")
async def get_performance_metrics(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comprehensive performance metrics and KPIs.
    
    Access Control:
    - Only Doctors can access performance metrics
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    doctor = scope.doctor

    total_appointments = await scope.count_appointments()
    completed_appointments = await scope.count_appointments(
        status=AppointmentStatus.COMPLETED.value
    )
    if completed_appointments == 0:
        completed_appointments = await scope.count_appointments(exclude_cancelled=True)
    
    # Calculate working days
    working_days = (date_to - date_from).days + 1
    
    # Efficiency metrics
    patients_per_day = completed_appointments / working_days if working_days > 0 else 0
    appointments_per_hour = patients_per_day / 8 if patients_per_day > 0 else 0  # Assume 8-hour workday
    consultation_efficiency = 85.0  # Mock efficiency score
    
    # Quality metrics (mock data)
    patient_satisfaction_score = 4.2  # Out of 5
    clinical_quality_score = 88.5     # Percentage
    safety_score = 95.0               # Percentage
    
    # Professional development (mock)
    continuing_education_hours = 24
    certifications_maintained = 3
    
    # Peer comparison (mock)
    department_ranking = 3  # 3rd out of doctors in department
    hospital_ranking = 15   # 15th out of all doctors in hospital
    
    # Monthly targets and achievement
    monthly_targets = {
        "patient_consultations": 120,
        "patient_satisfaction": 4.0,
        "revenue_target": 50000.0,
        "follow_up_compliance": 80.0
    }
    
    # Calculate achievement rate
    actual_consultations = completed_appointments
    actual_satisfaction = patient_satisfaction_score
    actual_revenue = completed_appointments * float(doctor.consultation_fee)
    actual_follow_up = 75.0  # Mock follow-up compliance
    
    achievements = [
        actual_consultations / monthly_targets["patient_consultations"] * 100,
        actual_satisfaction / monthly_targets["patient_satisfaction"] * 100,
        actual_revenue / monthly_targets["revenue_target"] * 100,
        actual_follow_up / monthly_targets["follow_up_compliance"] * 100
    ]
    
    achievement_rate = sum(achievements) / len(achievements)
    
    # Overall performance score calculation
    efficiency_score = min(100, (patients_per_day / 15) * 100)  # Target 15 patients/day
    quality_score = (patient_satisfaction_score / 5) * 100
    safety_component = safety_score
    
    overall_performance_score = (efficiency_score * 0.3 + quality_score * 0.4 + safety_component * 0.3)
    
    return PerformanceMetrics(
        overall_performance_score=round(overall_performance_score, 1),
        patients_per_day=round(patients_per_day, 1),
        appointments_per_hour=round(appointments_per_hour, 1),
        consultation_efficiency=consultation_efficiency,
        patient_satisfaction_score=patient_satisfaction_score,
        clinical_quality_score=clinical_quality_score,
        safety_score=safety_score,
        continuing_education_hours=continuing_education_hours,
        certifications_maintained=certifications_maintained,
        department_ranking=department_ranking,
        hospital_ranking=hospital_ranking,
        monthly_targets=monthly_targets,
        achievement_rate=round(achievement_rate, 1)
    )


# ============================================================================
# COMPARATIVE ANALYSIS AND BENCHMARKING
# ============================================================================

@router.get("/comparative-analysis")
async def get_comparative_analysis(
    report_period: ReportPeriod = Query(ReportPeriod.THIS_MONTH),
    custom_date_from: Optional[str] = Query(None),
    custom_date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get comparative analysis with peers and industry benchmarks.
    
    Access Control:
    - Only Doctors can access comparative analysis
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)

    date_from, date_to = get_date_range(report_period, custom_date_from, custom_date_to)
    scope = await _build_report_scope(
        tenant_db, platform_db, user_context, current_user, date_from, date_to
    )
    doctor = scope.doctor
    dept_id = getattr(doctor, "department_id", None) or doctor.department.id

    total_doctors_in_department = await scope.count_department_doctors(dept_id)
    
    # Mock department and hospital averages
    department_average_metrics = {
        "patients_per_day": 12.5,
        "appointment_completion_rate": 82.0,
        "patient_satisfaction": 4.0,
        "revenue_per_day": 3500.0,
        "prescription_accuracy": 94.0
    }
    
    hospital_average_metrics = {
        "patients_per_day": 11.8,
        "appointment_completion_rate": 80.5,
        "patient_satisfaction": 3.9,
        "revenue_per_day": 3200.0,
        "prescription_accuracy": 92.5
    }
    
    # Mock ranking
    department_rank = 3
    
    # Identify strengths and improvement areas
    strengths = []
    improvement_areas = []
    
    # Compare with department averages
    if 15.0 > department_average_metrics["patients_per_day"]:  # Mock current performance
        strengths.append("Above average patient volume")
    else:
        improvement_areas.append("Increase patient consultation volume")
    
    if 4.2 > department_average_metrics["patient_satisfaction"]:
        strengths.append("Excellent patient satisfaction scores")
    else:
        improvement_areas.append("Improve patient satisfaction")
    
    if 88.0 > department_average_metrics["appointment_completion_rate"]:
        strengths.append("High appointment completion rate")
    else:
        improvement_areas.append("Reduce appointment cancellations")
    
    # Industry benchmarks
    industry_benchmarks = {
        "patient_satisfaction": 4.1,
        "appointment_completion_rate": 85.0,
        "prescription_accuracy": 95.0,
        "follow_up_compliance": 78.0,
        "revenue_per_patient": 280.0
    }
    
    # Performance vs benchmark
    performance_vs_benchmark = {
        "patient_satisfaction": "Above Benchmark",
        "appointment_completion_rate": "At Benchmark",
        "prescription_accuracy": "Below Benchmark",
        "follow_up_compliance": "Above Benchmark",
        "revenue_per_patient": "Above Benchmark"
    }
    
    return ComparativeAnalysis(
        comparison_period=f"{date_from} to {date_to}",
        department_average_metrics=department_average_metrics,
        hospital_average_metrics=hospital_average_metrics,
        department_rank=department_rank,
        total_doctors_in_department=total_doctors_in_department,
        strengths=strengths,
        improvement_areas=improvement_areas,
        industry_benchmarks=industry_benchmarks,
        performance_vs_benchmark=performance_vs_benchmark
    )


# ============================================================================
# CUSTOM REPORTS AND EXPORT
# ============================================================================

@router.post("/generate-custom-report")
async def generate_custom_report(
    report_request: ReportRequest,
    filters: Optional[AnalyticsFilter] = Body(None),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Generate custom report based on specified parameters.
    
    Access Control:
    - Only Doctors can generate custom reports
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)

    date_from, date_to = get_date_range(
        report_request.report_period,
        report_request.custom_date_from,
        report_request.custom_date_to,
    )

    report_data = {}

    if report_request.report_type == ReportType.PRACTICE_SUMMARY:
        report_data = await get_practice_overview(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.PATIENT_ANALYTICS:
        report_data = await get_patient_analytics(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.APPOINTMENT_ANALYTICS:
        report_data = await get_appointment_analytics(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.PRESCRIPTION_ANALYTICS:
        report_data = await get_prescription_analytics(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.CLINICAL_OUTCOMES:
        report_data = await get_clinical_outcomes(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.FINANCIAL_SUMMARY:
        report_data = await get_financial_summary(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.PERFORMANCE_METRICS:
        report_data = await get_performance_metrics(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )

    elif report_request.report_type == ReportType.COMPARATIVE_ANALYSIS:
        report_data = await get_comparative_analysis(
            report_request.report_period,
            report_request.custom_date_from,
            report_request.custom_date_to,
            current_user,
            tenant_db,
            platform_db,
        )
    
    # Add metadata
    report_metadata = {
        "report_type": report_request.report_type,
        "report_period": report_request.report_period,
        "date_range": f"{date_from} to {date_to}",
        "generated_at": datetime.now().isoformat(),
        "generated_by": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "hospital": doctor.hospital_id,
        "department": doctor.department.name,
        "filters_applied": filters.dict() if filters else None,
        "export_format": report_request.export_format
    }
    
    return {
        "report_metadata": report_metadata,
        "report_data": report_data,
        "status": "generated",
        "message": f"Custom {report_request.report_type} report generated successfully"
    }


@router.get("/export-options")
async def get_export_options(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get available export options and formats.
    
    Access Control:
    - Only Doctors can access export options
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    return {
        "available_formats": [format.value for format in ExportFormat],
        "available_report_types": [report_type.value for report_type in ReportType],
        "available_periods": [period.value for period in ReportPeriod],
        "export_features": {
            "JSON": "Raw data export for API integration",
            "CSV": "Spreadsheet-compatible format",
            "PDF": "Professional report format",
            "EXCEL": "Advanced spreadsheet with charts"
        },
        "limitations": {
            "max_date_range": "2 years",
            "max_records": 10000,
            "file_size_limit": "50MB"
        }
    }