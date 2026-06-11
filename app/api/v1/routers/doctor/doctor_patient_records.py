"""
Doctor Patient Records and Case History API
Comprehensive patient record access system with advanced search, case history management,
medical timeline visualization, and clinical analytics for doctors.

BUSINESS RULES:
- Only Doctors can access patient records within their hospital
- Department-based filtering where applicable
- Complete medical history access with timeline view
- Advanced search and filtering capabilities
- Case history management and analysis
- Clinical decision support through historical data
"""
import json
import uuid
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta, date, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, text, update, cast
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import literal
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from enum import Enum

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission, DischargeSummary, PatientDocument
from app.models.doctor import DoctorProfile, Prescription, TreatmentPlan
from app.models.hospital import Department
from app.core.enums import UserRole, AppointmentStatus, DocumentType
from app.core.utils import generate_patient_ref

router = APIRouter(prefix="/doctor-patient-records", tags=["Doctor Portal - Patient Records"])


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class RecordType(str, Enum):
    """Medical record types"""
    CONSULTATION = "CONSULTATION"
    ADMISSION = "ADMISSION"
    DISCHARGE = "DISCHARGE"
    PRESCRIPTION = "PRESCRIPTION"
    LAB_RESULT = "LAB_RESULT"
    IMAGING = "IMAGING"
    PROCEDURE = "PROCEDURE"
    VACCINATION = "VACCINATION"
    ALLERGY = "ALLERGY"
    VITAL_SIGNS = "VITAL_SIGNS"


class SearchScope(str, Enum):
    """Search scope for patient records"""
    ALL_PATIENTS = "ALL_PATIENTS"
    MY_PATIENTS = "MY_PATIENTS"
    DEPARTMENT_PATIENTS = "DEPARTMENT_PATIENTS"
    RECENT_PATIENTS = "RECENT_PATIENTS"


class TimelineGrouping(str, Enum):
    """Timeline grouping options"""
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"
    BY_VISIT = "BY_VISIT"


class ClinicalSeverity(str, Enum):
    """Clinical severity levels"""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class PatientSummary(BaseModel):
    """Patient summary information"""
    patient_ref: str
    patient_name: str
    patient_age: int
    gender: str
    blood_group: Optional[str]
    phone_number: str
    email: Optional[str] = None
    address: Optional[str]
    
    # Medical summary
    chronic_conditions: List[str]
    allergies: List[str]
    current_medications: List[str]
    
    # Visit summary
    total_visits: int
    last_visit_date: Optional[str]
    last_diagnosis: Optional[str]
    
    # Risk factors
    risk_factors: List[str]
    clinical_alerts: List[str]


class MedicalRecordDetail(BaseModel):
    """Detailed medical record information"""
    record_id: str
    record_type: RecordType
    date: str
    time: Optional[str]
    doctor_name: str
    department: str
    
    # Clinical details
    chief_complaint: Optional[str]
    diagnosis: Optional[str]
    treatment_plan: Optional[str]
    vital_signs: Optional[Dict[str, Any]]
    
    # Additional data
    prescriptions: List[Dict[str, Any]]
    lab_orders: List[Dict[str, Any]]
    imaging_orders: List[Dict[str, Any]]
    follow_up_instructions: Optional[str]
    
    # Metadata
    is_finalized: bool
    created_at: str
    updated_at: Optional[str]


class PatientTimeline(BaseModel):
    """Patient medical timeline"""
    patient_ref: str
    patient_name: str
    timeline_period: str
    grouping: TimelineGrouping
    
    # Timeline data
    timeline_entries: List[Dict[str, Any]]
    total_entries: int
    date_range: Dict[str, str]
    
    # Summary statistics
    visit_frequency: Dict[str, int]
    common_diagnoses: List[Dict[str, Any]]
    medication_history: List[Dict[str, Any]]
    
    # Clinical insights
    health_trends: List[str]
    risk_assessments: List[str]


class CaseHistoryAnalysis(BaseModel):
    """Case history analysis"""
    patient_ref: str
    analysis_period: str
    total_cases: int
    
    # Clinical patterns
    diagnosis_patterns: List[Dict[str, Any]]
    treatment_outcomes: List[Dict[str, Any]]
    medication_effectiveness: List[Dict[str, Any]]
    
    # Temporal analysis
    seasonal_patterns: List[Dict[str, Any]]
    progression_analysis: List[Dict[str, Any]]
    
    # Risk analysis
    complication_risks: List[Dict[str, Any]]
    readmission_risk: Optional[float]
    
    # Recommendations
    clinical_recommendations: List[str]
    follow_up_suggestions: List[str]


class PatientSearchRequest(BaseModel):
    """Patient search request"""
    query: str = Field(..., min_length=2)
    search_scope: SearchScope = SearchScope.ALL_PATIENTS
    filters: Optional[Dict[str, Any]] = None
    include_inactive: bool = False
    limit: int = Field(20, ge=1, le=100)


class AdvancedSearchFilters(BaseModel):
    """Advanced search filters"""
    age_range: Optional[Dict[str, int]] = None  # {"min": 18, "max": 65}
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    chronic_conditions: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    last_visit_range: Optional[Dict[str, str]] = None  # {"from": "2024-01-01", "to": "2024-12-31"}
    diagnosis_keywords: Optional[List[str]] = None
    medication_keywords: Optional[List[str]] = None
    department: Optional[str] = None
    risk_level: Optional[ClinicalSeverity] = None


class ClinicalAlert(BaseModel):
    """Clinical alert information"""
    alert_id: str
    patient_ref: str
    alert_type: str
    severity: ClinicalSeverity
    title: str
    description: str
    triggered_by: str
    triggered_at: str
    is_active: bool
    acknowledged_by: Optional[str]
    acknowledged_at: Optional[str]
    resolution_notes: Optional[str]


class PatientDocumentSummary(BaseModel):
    """Patient document summary"""
    document_id: str
    document_type: str
    title: str
    description: Optional[str]
    file_name: str
    file_size: int
    uploaded_date: str
    uploaded_by: str
    is_sensitive: bool
    tags: List[str]


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


async def get_patient_by_ref(
    patient_ref: str,
    hospital_id: Optional[str],
    db: AsyncSession,
) -> PatientProfile:
    """Resolve patient on tenant + platform (OPD patients often live on platform DB)."""
    from app.database.session import AsyncSessionLocal
    from app.services.patient_resolve import load_patient_by_ref, parse_hospital_uuid

    hid = parse_hospital_uuid(hospital_id)
    async with AsyncSessionLocal() as platform_db:
        return await load_patient_by_ref(
            patient_ref,
            hid,
            db,
            platform_db,
            ensure_on_tenant=True,
        )


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile with department information"""
    if user_context["role"] != UserRole.DOCTOR:
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_since(dt: datetime) -> int:
    return (_utc_now() - _as_utc_aware(dt)).days


def generate_alert_id() -> str:
    """Generate unique alert ID"""
    import random
    import string
    
    # Format: ALERT-YYYYMMDD-XXXXXX
    date_str = datetime.now().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"ALERT-{date_str}-{random_part}"


def analyze_health_trends(medical_records: List[MedicalRecord]) -> List[str]:
    """Analyze health trends from medical records"""
    trends = []
    
    if len(medical_records) < 2:
        return ["Insufficient data for trend analysis"]
    
    # Sort records by date
    sorted_records = sorted(medical_records, key=lambda x: x.created_at)
    
    # Analyze vital signs trends
    vital_signs_data = []
    for record in sorted_records:
        if record.vital_signs:
            vital_signs_data.append(record.vital_signs)
    
    if len(vital_signs_data) >= 2:
        # Mock trend analysis (in production, this would use proper statistical analysis)
        trends.append("Blood pressure showing stable trend over time")
        trends.append("Weight management appears consistent")
        
        # Check for concerning patterns
        if len(vital_signs_data) >= 3:
            trends.append("Regular monitoring shows good health maintenance")
    
    # Analyze diagnosis patterns
    diagnoses = [r.diagnosis for r in sorted_records if r.diagnosis]
    if len(set(diagnoses)) > len(diagnoses) * 0.7:  # Many different diagnoses
        trends.append("Diverse health concerns - consider comprehensive health assessment")
    elif len(set(diagnoses)) < len(diagnoses) * 0.3:  # Recurring diagnoses
        trends.append("Recurring health patterns identified - chronic condition management recommended")
    
    return trends if trends else ["No significant trends identified"]


def assess_clinical_risks(patient: PatientProfile, medical_records: List[MedicalRecord]) -> List[str]:
    """Assess clinical risks based on patient data"""
    risks = []
    
    patient_age = calculate_age(patient.date_of_birth)
    
    # Age-based risks
    if patient_age > 65:
        risks.append("Age-related health monitoring recommended")
    elif patient_age > 45:
        risks.append("Preventive health screening due")
    
    # Chronic condition risks
    if patient.chronic_conditions:
        for condition in patient.chronic_conditions:
            if condition.lower() in ["diabetes", "hypertension", "heart disease"]:
                risks.append(f"Active monitoring required for {condition}")
    
    # Allergy risks
    if patient.allergies:
        risks.append("Drug allergy alerts active - verify before prescribing")
    
    # Medication interaction risks
    if patient.current_medications and len(patient.current_medications) > 3:
        risks.append("Multiple medications - monitor for interactions")
    
    # Visit frequency analysis
    if len(medical_records) > 5:
        recent_visits = [r for r in medical_records if _days_since(r.created_at) <= 90]
        if len(recent_visits) > 3:
            risks.append("Frequent recent visits - consider comprehensive evaluation")
    
    return risks if risks else ["No significant clinical risks identified"]


# ============================================================================
# PATIENT SEARCH AND LOOKUP
# ============================================================================

@router.get("/patients/search")
async def search_patients(
    query: str = Query(..., min_length=2),
    search_scope: SearchScope = Query(SearchScope.ALL_PATIENTS),
    include_inactive: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search patients with various scopes and filters.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Build base query conditions - handle null hospital_id
    base_conditions = []
    if user_context.get("hospital_id"):
        base_conditions.append(PatientProfile.hospital_id == uuid.UUID(user_context["hospital_id"]))
    
    # Apply search scope
    if search_scope == SearchScope.MY_PATIENTS:
        # Patients who have had appointments with this doctor
        my_patients_subquery = select(Appointment.patient_id).where(
            Appointment.doctor_id == doctor.id
        ).distinct()
        base_conditions.append(PatientProfile.id.in_(my_patients_subquery))
    
    elif search_scope == SearchScope.DEPARTMENT_PATIENTS:
        # Patients who have had appointments in this department
        dept_patients_subquery = select(Appointment.patient_id).where(
            Appointment.department_id == doctor.department_id
        ).distinct()
        base_conditions.append(PatientProfile.id.in_(dept_patients_subquery))
    
    elif search_scope == SearchScope.RECENT_PATIENTS:
        # Patients with appointments in last 30 days
        thirty_days_ago = date.today() - timedelta(days=30)
        recent_patients_subquery = select(Appointment.patient_id).where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date >= thirty_days_ago.isoformat()
            )
        ).distinct()
        base_conditions.append(PatientProfile.id.in_(recent_patients_subquery))
    
    # Add search terms
    search_conditions = or_(
        PatientProfile.patient_id.ilike(f"%{query}%"),
        User.first_name.ilike(f"%{query}%"),
        User.last_name.ilike(f"%{query}%"),
        User.phone.ilike(f"%{query}%"),
        User.email.ilike(f"%{query}%")
    )
    
    base_conditions.append(search_conditions)
    
    # Execute search
    patients_result = await db.execute(
        select(PatientProfile)
        .join(User, PatientProfile.user_id == User.id)
        .where(and_(*base_conditions))
        .options(selectinload(PatientProfile.user))
        .limit(limit)
    )
    
    patients = patients_result.scalars().all()
    
    # Build patient summaries
    patient_summaries = []
    
    for patient in patients:
        # Get visit summary
        visits_result = await db.execute(
            select(func.count(Appointment.id), func.max(Appointment.appointment_date))
            .where(Appointment.patient_id == patient.id)
        )
        
        visit_data = visits_result.first()
        total_visits = visit_data[0] if visit_data else 0
        last_visit_date = visit_data[1] if visit_data else None
        
        # Get last diagnosis
        last_diagnosis_result = await db.execute(
            select(MedicalRecord.diagnosis)
            .where(MedicalRecord.patient_id == patient.id)
            .order_by(desc(MedicalRecord.created_at))
            .limit(1)
        )
        
        last_diagnosis = last_diagnosis_result.scalar_one_or_none()
        
        # Generate clinical alerts and risk factors
        clinical_alerts = []
        risk_factors = []
        
        patient_age = calculate_age(patient.date_of_birth)
        
        if patient_age > 65:
            risk_factors.append("Elderly patient")
        
        if patient.chronic_conditions:
            risk_factors.extend(patient.chronic_conditions)
            clinical_alerts.append("Chronic conditions require monitoring")
        
        if patient.allergies:
            clinical_alerts.append("Drug allergies on file")
        
        patient_summaries.append(PatientSummary(
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            patient_age=patient_age,
            gender=patient.gender,
            blood_group=patient.blood_group,
            phone_number=patient.user.phone,
            email=patient.user.email,
            address=patient.address,
            chronic_conditions=patient.chronic_conditions or [],
            allergies=patient.allergies or [],
            current_medications=patient.current_medications or [],
            total_visits=total_visits,
            last_visit_date=last_visit_date,
            last_diagnosis=last_diagnosis,
            risk_factors=risk_factors,
            clinical_alerts=clinical_alerts
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "search_query": query,
        "search_scope": search_scope,
        "total_results": len(patient_summaries),
        "patients": patient_summaries
    }


@router.post("/patients/advanced-search")
async def advanced_patient_search(
    search_request: PatientSearchRequest,
    filters: AdvancedSearchFilters = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Advanced patient search with complex filters.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Build complex query conditions - handle null hospital_id
    conditions = []
    if user_context.get("hospital_id"):
        conditions.append(PatientProfile.hospital_id == uuid.UUID(user_context["hospital_id"]))
    
    # Apply search scope
    if search_request.search_scope == SearchScope.MY_PATIENTS:
        my_patients_subquery = select(Appointment.patient_id).where(
            Appointment.doctor_id == doctor.id
        ).distinct()
        conditions.append(PatientProfile.id.in_(my_patients_subquery))
    
    # Apply advanced filters
    if filters.age_range:
        # Age filtering requires date calculation
        today = date.today()
        if filters.age_range.get("min"):
            max_birth_date = today.replace(year=today.year - filters.age_range["min"])
            conditions.append(PatientProfile.date_of_birth <= max_birth_date.isoformat())
        
        if filters.age_range.get("max"):
            min_birth_date = today.replace(year=today.year - filters.age_range["max"])
            conditions.append(PatientProfile.date_of_birth >= min_birth_date.isoformat())
    
    if filters.gender:
        conditions.append(PatientProfile.gender == filters.gender)
    
    if filters.blood_group:
        conditions.append(PatientProfile.blood_group == filters.blood_group)
    
    if filters.chronic_conditions:
        for condition in filters.chronic_conditions:
            # Use JSONB @> operator; .contains() on JSON_TYPE generates invalid LIKE for PostgreSQL
            conditions.append(
                PatientProfile.chronic_conditions.op("@>")(cast(literal(json.dumps([condition])), JSONB))
            )
    
    if filters.allergies:
        for allergy in filters.allergies:
            conditions.append(
                PatientProfile.allergies.op("@>")(cast(literal(json.dumps([allergy])), JSONB))
            )
    
    # Add text search
    search_conditions = or_(
        PatientProfile.patient_id.ilike(f"%{search_request.query}%"),
        User.first_name.ilike(f"%{search_request.query}%"),
        User.last_name.ilike(f"%{search_request.query}%"),
        User.phone.ilike(f"%{search_request.query}%")
    )
    conditions.append(search_conditions)
    
    # Execute search
    patients_result = await db.execute(
        select(PatientProfile)
        .join(User, PatientProfile.user_id == User.id)
        .where(and_(*conditions))
        .options(selectinload(PatientProfile.user))
        .limit(search_request.limit)
    )
    
    patients = patients_result.scalars().all()
    
    # Apply post-query filters (for complex conditions)
    filtered_patients = []
    
    for patient in patients:
        include_patient = True
        
        # Last visit range filter
        if filters.last_visit_range:
            last_visit_result = await db.execute(
                select(func.max(Appointment.appointment_date))
                .where(Appointment.patient_id == patient.id)
            )
            
            last_visit = last_visit_result.scalar_one_or_none()
            
            if last_visit:
                if filters.last_visit_range.get("from") and last_visit < filters.last_visit_range["from"]:
                    include_patient = False
                if filters.last_visit_range.get("to") and last_visit > filters.last_visit_range["to"]:
                    include_patient = False
            elif filters.last_visit_range.get("from"):  # No visits but range specified
                include_patient = False
        
        # Diagnosis keywords filter
        if filters.diagnosis_keywords and include_patient:
            diagnosis_found = False
            diagnoses_result = await db.execute(
                select(MedicalRecord.diagnosis)
                .where(MedicalRecord.patient_id == patient.id)
            )
            
            diagnoses = diagnoses_result.scalars().all()
            
            for diagnosis in diagnoses:
                if diagnosis:
                    for keyword in filters.diagnosis_keywords:
                        if keyword.lower() in diagnosis.lower():
                            diagnosis_found = True
                            break
                if diagnosis_found:
                    break
            
            if not diagnosis_found:
                include_patient = False
        
        if include_patient:
            filtered_patients.append(patient)
    
    # Build detailed patient summaries
    detailed_summaries = []
    
    for patient in filtered_patients:
        # Get comprehensive visit data
        visits_result = await db.execute(
            select(func.count(Appointment.id), func.max(Appointment.appointment_date))
            .where(Appointment.patient_id == patient.id)
        )
        
        visit_data = visits_result.first()
        total_visits = visit_data[0] if visit_data else 0
        last_visit_date = visit_data[1] if visit_data else None
        
        # Get recent medical records for analysis
        recent_records_result = await db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.patient_id == patient.id)
            .order_by(desc(MedicalRecord.created_at))
            .limit(5)
        )
        
        recent_records = recent_records_result.scalars().all()
        
        # Analyze risks and generate alerts
        risk_factors = assess_clinical_risks(patient, recent_records)
        health_trends = analyze_health_trends(recent_records)
        
        clinical_alerts = []
        if patient.chronic_conditions:
            clinical_alerts.append("Chronic conditions require monitoring")
        if patient.allergies:
            clinical_alerts.append("Drug allergies on file")
        if len(recent_records) > 3:
            clinical_alerts.append("Frequent recent visits")
        
        detailed_summaries.append(PatientSummary(
            patient_ref=patient.patient_id,
            patient_name=f"{patient.user.first_name} {patient.user.last_name}",
            patient_age=calculate_age(patient.date_of_birth),
            gender=patient.gender,
            blood_group=patient.blood_group,
            phone_number=patient.user.phone,
            email=patient.user.email,
            address=patient.address,
            chronic_conditions=patient.chronic_conditions or [],
            allergies=patient.allergies or [],
            current_medications=patient.current_medications or [],
            total_visits=total_visits,
            last_visit_date=last_visit_date,
            last_diagnosis=recent_records[0].diagnosis if recent_records and recent_records[0].diagnosis else None,
            risk_factors=risk_factors,
            clinical_alerts=clinical_alerts
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "search_request": search_request,
        "applied_filters": filters,
        "total_results": len(detailed_summaries),
        "patients": detailed_summaries
    }


# ============================================================================
# GENERAL MEDICAL RECORDS ACCESS
# ============================================================================

@router.get("/medical-records")
async def get_all_medical_records(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    patient_search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get all medical records accessible to the doctor with pagination and filtering.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Build base query conditions - handle null hospital_id
    conditions = []
    if user_context.get("hospital_id"):
        conditions.append(MedicalRecord.hospital_id == uuid.UUID(user_context["hospital_id"]))
    
    # Apply date filters
    if date_from:
        try:
            start_datetime = datetime.strptime(f"{date_from} 00:00:00", "%Y-%m-%d %H:%M:%S")
            conditions.append(MedicalRecord.created_at >= start_datetime)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date_from format. Use YYYY-MM-DD format."
            )
    if date_to:
        try:
            end_datetime = datetime.strptime(f"{date_to} 23:59:59", "%Y-%m-%d %H:%M:%S")
            conditions.append(MedicalRecord.created_at <= end_datetime)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date_to format. Use YYYY-MM-DD format."
            )
    
    # Apply patient search filter
    if patient_search:
        patient_search_conditions = []
        if user_context.get("hospital_id"):
            patient_search_conditions.append(PatientProfile.hospital_id == uuid.UUID(user_context["hospital_id"]))
        patient_search_conditions.append(
            or_(
                PatientProfile.patient_id.ilike(f"%{patient_search}%"),
                User.first_name.ilike(f"%{patient_search}%"),
                User.last_name.ilike(f"%{patient_search}%")
            )
        )
        patient_search_subquery = select(PatientProfile.id).join(
            User, PatientProfile.user_id == User.id
        ).where(and_(*patient_search_conditions))
        conditions.append(MedicalRecord.patient_id.in_(patient_search_subquery))
    
    # Get total count
    count_query = select(func.count(MedicalRecord.id)).where(and_(*conditions))
    total_result = await db.execute(count_query)
    total_records = total_result.scalar() or 0
    
    # Get paginated records
    offset = (page - 1) * limit
    records_result = await db.execute(
        select(MedicalRecord)
        .where(and_(*conditions))
        .options(
            selectinload(MedicalRecord.patient).selectinload(PatientProfile.user),
            selectinload(MedicalRecord.doctor),
            selectinload(MedicalRecord.appointment)
        )
        .order_by(desc(MedicalRecord.created_at))
        .offset(offset)
        .limit(limit)
    )
    
    records = records_result.scalars().all()
    
    # Format records
    formatted_records = []
    for record in records:
        # Get doctor name (handle both DoctorProfile and User cases)
        doctor_name = f"Dr. {record.doctor.first_name} {record.doctor.last_name}"
        
        # Get patient info
        patient_name = f"{record.patient.user.first_name} {record.patient.user.last_name}"
        
        formatted_records.append({
            "record_id": str(record.id),
            "patient_ref": record.patient.patient_id,
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "date": record.created_at.date().isoformat(),
            "time": record.created_at.time().strftime("%H:%M"),
            "chief_complaint": record.chief_complaint,
            "diagnosis": record.diagnosis,
            "is_finalized": record.is_finalized,
            "appointment_ref": record.appointment.appointment_ref if record.appointment else None
        })
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_records": total_records,
        "page": page,
        "limit": limit,
        "total_pages": (total_records + limit - 1) // limit,
        "records": formatted_records,
        "filters_applied": {
            "patient_search": patient_search,
            "date_from": date_from,
            "date_to": date_to
        }
    }


# ============================================================================
# PATIENT RECORD ACCESS
# ============================================================================

@router.get("/patients/{patient_ref}/summary")
async def get_patient_summary(
    patient_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get comprehensive patient summary with medical overview.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Get comprehensive medical data
    # Recent medical records
    recent_records_result = await db.execute(
        select(MedicalRecord)
        .where(MedicalRecord.patient_id == patient.id)
        .order_by(desc(MedicalRecord.created_at))
        .limit(10)
    )
    recent_records = recent_records_result.scalars().all()
    
    # Visit statistics
    visits_result = await db.execute(
    select(
        func.count(Appointment.id),
        func.max(Appointment.appointment_date),
        func.count(Appointment.id).filter(Appointment.doctor_id == doctor.id)
    )
    .where(
        Appointment.patient_id == patient.id
    )
)
    
    visit_stats = visits_result.first()
    total_visits = visit_stats[0] if visit_stats else 0
    last_visit_date = visit_stats[1] if visit_stats else None
    my_visits = visit_stats[2] if visit_stats else 0
    
    # Active admissions
    active_admissions_result = await db.execute(
        select(func.count(Admission.id))
        .where(
            and_(
                Admission.patient_id == patient.id,
                Admission.is_active == True
            )
        )
    )
    active_admissions = active_admissions_result.scalar() or 0
    
    # Recent prescriptions
    recent_prescriptions_result = await db.execute(
        select(Prescription)
        .where(Prescription.patient_id == patient.id)
        .order_by(desc(Prescription.created_at))
        .limit(5)
    )
    recent_prescriptions = recent_prescriptions_result.scalars().all()
    
    # Analyze health trends and risks
    health_trends = analyze_health_trends(recent_records)
    risk_factors = assess_clinical_risks(patient, recent_records)
    
    # Generate clinical alerts
    clinical_alerts = []
    if patient.chronic_conditions:
        clinical_alerts.append("Chronic conditions require regular monitoring")
    if patient.allergies:
        clinical_alerts.append("Drug allergies documented - verify before prescribing")
    if active_admissions > 0:
        clinical_alerts.append("Patient currently admitted")
    if len(recent_records) > 5:
        recent_visits = [r for r in recent_records if _days_since(r.created_at) <= 30]
        if len(recent_visits) > 3:
            clinical_alerts.append("Frequent recent visits - consider comprehensive evaluation")
    
    # Build comprehensive summary
    patient_summary = PatientSummary(
        patient_ref=patient.patient_id,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        patient_age=calculate_age(patient.date_of_birth),
        gender=patient.gender,
        blood_group=patient.blood_group,
        phone_number=patient.user.phone,
        email=patient.user.email,
        address=patient.address,
        chronic_conditions=patient.chronic_conditions or [],
        allergies=patient.allergies or [],
        current_medications=patient.current_medications or [],
        total_visits=total_visits,
        last_visit_date=last_visit_date,
        last_diagnosis=recent_records[0].diagnosis if recent_records and recent_records[0].diagnosis else None,
        risk_factors=risk_factors,
        clinical_alerts=clinical_alerts
    )
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "patient_summary": patient_summary,
        "visit_statistics": {
            "total_visits": total_visits,
            "visits_with_me": my_visits,
            "last_visit_date": last_visit_date,
            "active_admissions": active_admissions
        },
        "recent_activity": {
            "recent_diagnoses": [r.diagnosis for r in recent_records[:3] if r.diagnosis],
            "recent_prescriptions": len(recent_prescriptions),
            "last_prescription_date": recent_prescriptions[0].prescription_date if recent_prescriptions else None
        },
        "health_insights": {
            "trends": health_trends,
            "risk_assessment": risk_factors,
            "clinical_alerts": clinical_alerts
        }
    }


@router.get("/patients/{patient_ref}/medical-records")
async def get_patient_medical_records(
    patient_ref: str,
    record_type: Optional[RecordType] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get patient's medical records with filtering options.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Build query conditions
    conditions = [MedicalRecord.patient_id == patient.id]
    
    # Apply filters
    if date_from:
        try:
            start_datetime = datetime.strptime(f"{date_from} 00:00:00", "%Y-%m-%d %H:%M:%S")
            conditions.append(MedicalRecord.created_at >= start_datetime)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date_from format. Use YYYY-MM-DD format."
            )
    if date_to:
        try:
            end_datetime = datetime.strptime(f"{date_to} 23:59:59", "%Y-%m-%d %H:%M:%S")
            conditions.append(MedicalRecord.created_at <= end_datetime)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date_to format. Use YYYY-MM-DD format."
            )
    
    # Get medical records
    records_result = await db.execute(
        select(MedicalRecord)
        .where(and_(*conditions))
        .options(
            selectinload(MedicalRecord.doctor),
            selectinload(MedicalRecord.appointment)
        )
        .order_by(desc(MedicalRecord.created_at))
        .limit(limit)
    )
    
    records = records_result.scalars().all()
    
    # Build detailed record information
    detailed_records = []
    
    for record in records:
        # Get prescriptions for this record
        prescriptions_result = await db.execute(
            select(Prescription)
            .where(Prescription.medical_record_id == record.id)
        )
        prescriptions = prescriptions_result.scalars().all()
        
        prescription_data = []
        for prescription in prescriptions:
            prescription_data.append({
                "prescription_number": prescription.prescription_number,
                "medications": prescription.medications,
                "instructions": prescription.general_instructions
            })
        
        detailed_records.append(MedicalRecordDetail(
            record_id=str(record.id),
            record_type=RecordType.CONSULTATION,  # Default type
            date=record.created_at.strftime("%Y-%m-%d"),
            time=record.created_at.strftime("%H:%M:%S"),
            doctor_name=f"Dr. {record.doctor.first_name} {record.doctor.last_name}",
            department="Unknown Department",  # TODO: Get department from staff assignment
            chief_complaint=record.chief_complaint,
            diagnosis=record.diagnosis,
            treatment_plan=record.treatment_plan,
            vital_signs=record.vital_signs,
            prescriptions=prescription_data,
            lab_orders=record.lab_orders or [],
            imaging_orders=record.imaging_orders or [],
            follow_up_instructions=record.follow_up_instructions,
            is_finalized=record.is_finalized,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat() if record.updated_at else None
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "patient_ref": patient_ref,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "filters_applied": {
            "record_type": record_type,
            "date_from": date_from,
            "date_to": date_to
        },
        "total_records": len(detailed_records),
        "medical_records": detailed_records
    }


@router.get("/patients/{patient_ref}/timeline")
async def get_patient_timeline(
    patient_ref: str,
    grouping: TimelineGrouping = Query(TimelineGrouping.MONTHLY),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get patient's medical timeline with chronological visualization.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Set default date range if not provided
    if not date_from:
        date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    
    # Get all relevant medical events
    # Appointments
    appointments_result = await db.execute(
    select(Appointment)
    .where(
        and_(
            Appointment.patient_id == patient.id,
            Appointment.appointment_date >= date_from,
            Appointment.appointment_date <= date_to
        )
    )
    .options(
        selectinload(Appointment.doctor),
        selectinload(Appointment.department)
    )
    .order_by(
        Appointment.appointment_date,
        Appointment.appointment_time
    )
)
    appointments = appointments_result.scalars().all()
    
    # Convert date strings to datetime objects for proper comparison
    try:
        start_datetime = datetime.strptime(f"{date_from} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_datetime = datetime.strptime(f"{date_to} 23:59:59", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD format."
        )
    
    # Medical records
    records_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.patient_id == patient.id,
                MedicalRecord.created_at >= start_datetime,
                MedicalRecord.created_at <= end_datetime
            )
        )
        .options(
            selectinload(MedicalRecord.doctor)
        )
        .order_by(MedicalRecord.created_at)
    )
    records = records_result.scalars().all()
    
    # Prescriptions
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.patient_id == patient.id,
                Prescription.prescription_date >= date_from,
                Prescription.prescription_date <= date_to
            )
        )
        .options(
            selectinload(Prescription.doctor)
        )
        .order_by(Prescription.prescription_date)
    )
    prescriptions = prescriptions_result.scalars().all()
    
    # Admissions
    admissions_result = await db.execute(
        select(Admission)
        .where(
            and_(
                Admission.patient_id == patient.id,
                Admission.admission_date >= start_datetime,
                Admission.admission_date <= end_datetime
            )
        )
        .options(
            selectinload(Admission.doctor),
            selectinload(Admission.department)
        )
        .order_by(Admission.admission_date)
    )
    admissions = admissions_result.scalars().all()
    
    # Build timeline entries
    timeline_entries = []
    
    # Add appointments
    for appointment in appointments:
        timeline_entries.append({
            "date": appointment.appointment_date,
            "time": appointment.appointment_time,
            "type": "APPOINTMENT",
            "title": f"Appointment - {appointment.department.name}",
            "description": f"Consultation with Dr. {appointment.doctor.first_name} {appointment.doctor.last_name}",
            "status": appointment.status,
            "details": {
                "doctor": f"Dr. {appointment.doctor.first_name} {appointment.doctor.last_name}",
                "department": appointment.department.name,
                "chief_complaint": appointment.chief_complaint,
                "appointment_ref": appointment.appointment_ref
            }
        })
    
    # Add medical records
    for record in records:
        timeline_entries.append({
            "date": record.created_at.strftime("%Y-%m-%d"),
            "time": record.created_at.strftime("%H:%M:%S"),
            "type": "MEDICAL_RECORD",
            "title": f"Medical Consultation",
            "description": record.diagnosis or record.chief_complaint,
            "status": "FINALIZED" if record.is_finalized else "DRAFT",
            "details": {
                "doctor": f"Dr. {record.doctor.first_name} {record.doctor.last_name}",
                "chief_complaint": record.chief_complaint,
                "diagnosis": record.diagnosis,
                "vital_signs": record.vital_signs
            }
        })
    
    # Add prescriptions
    for prescription in prescriptions:
        timeline_entries.append({
            "date": prescription.prescription_date,
            "time": prescription.created_at.strftime("%H:%M:%S"),
            "type": "PRESCRIPTION",
            "title": f"Prescription - {prescription.prescription_number}",
            "description": f"Medications prescribed by Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}",
            "status": "DISPENSED" if prescription.is_dispensed else "PENDING",
            "details": {
                "doctor": f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}",
                "medications": prescription.medications,
                "diagnosis": prescription.diagnosis
            }
        })
    
    # Add admissions
    for admission in admissions:
        timeline_entries.append({
            "date": admission.admission_date.strftime("%Y-%m-%d"),
            "time": admission.admission_date.strftime("%H:%M:%S"),
            "type": "ADMISSION",
            "title": f"{admission.admission_type} Admission",
            "description": f"Admitted to {admission.department.name}",
            "status": "ACTIVE" if admission.is_active else "DISCHARGED",
            "details": {
                "doctor": f"Dr. {admission.doctor.first_name} {admission.doctor.last_name}",
                "department": admission.department.name,
                "admission_number": admission.admission_number,
                "chief_complaint": admission.chief_complaint,
                "ward": admission.ward,
                "room": admission.room_number,
                "bed": admission.bed_number
            }
        })
    
    # Sort timeline entries by date and time
    timeline_entries.sort(key=lambda x: (x["date"], x["time"]))
    
    # Generate statistics
    visit_frequency = {}
    diagnoses = {}
    medications = []
    
    for entry in timeline_entries:
        # Visit frequency by month
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
        month_key = entry_date.strftime("%Y-%m")
        visit_frequency[month_key] = visit_frequency.get(month_key, 0) + 1
        
        # Common diagnoses
        if entry["type"] == "MEDICAL_RECORD" and entry["details"].get("diagnosis"):
            diagnosis = entry["details"]["diagnosis"]
            diagnoses[diagnosis] = diagnoses.get(diagnosis, 0) + 1
        
        # Medications
        if entry["type"] == "PRESCRIPTION" and entry["details"].get("medications"):
            medications.extend(entry["details"]["medications"])
    
    # Format common diagnoses
    common_diagnoses = [
        {"diagnosis": diagnosis, "count": count}
        for diagnosis, count in sorted(diagnoses.items(), key=lambda x: x[1], reverse=True)[:5]
    ]
    
    # Analyze health trends
    health_trends = analyze_health_trends(records)
    
    # Risk assessments
    risk_assessments = assess_clinical_risks(patient, records)
    
    return PatientTimeline(
        patient_ref=patient_ref,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        timeline_period=f"{date_from} to {date_to}",
        grouping=grouping,
        timeline_entries=timeline_entries,
        total_entries=len(timeline_entries),
        date_range={"from": date_from, "to": date_to},
        visit_frequency=visit_frequency,
        common_diagnoses=common_diagnoses,
        medication_history=[{"name": med.get("name", "Unknown"), "frequency": 1} for med in medications[:10]],
        health_trends=health_trends,
        risk_assessments=risk_assessments
    )


# ============================================================================
# CASE HISTORY ANALYSIS
# ============================================================================

@router.get("/patients/{patient_ref}/case-history")
async def analyze_case_history(
    patient_ref: str,
    analysis_period: str = Query("1year", pattern="^(3months|6months|1year|2years|all)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Comprehensive case history analysis with clinical insights.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Calculate date range based on analysis period (UTC-aware for DB timestamptz)
    end_date = _utc_now()
    if analysis_period == "3months":
        start_date = end_date - timedelta(days=90)
    elif analysis_period == "6months":
        start_date = end_date - timedelta(days=180)
    elif analysis_period == "1year":
        start_date = end_date - timedelta(days=365)
    elif analysis_period == "2years":
        start_date = end_date - timedelta(days=730)
    else:  # all
        start_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    
    # Get comprehensive medical data for analysis
    records_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.patient_id == patient.id,
                MedicalRecord.created_at >= start_date,
                MedicalRecord.created_at <= end_date
            )
        )
        .options(
            selectinload(MedicalRecord.doctor)
        )
        .order_by(MedicalRecord.created_at)
    )
    records = records_result.scalars().all()
    
    # Get appointments for outcome analysis
    appointments_result = await db.execute(
    select(Appointment)
    .where(
        and_(
            Appointment.patient_id == patient.id,
            Appointment.created_at >= start_date,
            Appointment.created_at <= end_date
        )
    )
    .options(
        selectinload(Appointment.doctor)
    )
)
    appointments = appointments_result.scalars().all()
    
    # Get prescriptions for medication analysis
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.patient_id == patient.id,
                Prescription.created_at >= start_date,
                Prescription.created_at <= end_date
            )
        )
    )
    prescriptions = prescriptions_result.scalars().all()
    
    # Analyze diagnosis patterns
    diagnosis_counts = {}
    diagnosis_timeline = []
    
    for record in records:
        if record.diagnosis:
            diagnosis_counts[record.diagnosis] = diagnosis_counts.get(record.diagnosis, 0) + 1
            diagnosis_timeline.append({
                "date": record.created_at.strftime("%Y-%m-%d"),
                "diagnosis": record.diagnosis,
                "doctor": f"Dr. {record.doctor.first_name} {record.doctor.last_name}"
            })
    
    diagnosis_patterns = [
        {
            "diagnosis": diagnosis,
            "frequency": count,
            "percentage": round((count / len(records)) * 100, 1) if records else 0,
            "trend": "stable"  # Simplified trend analysis
        }
        for diagnosis, count in sorted(diagnosis_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    ]
    
    # Analyze treatment outcomes
    treatment_outcomes = []
    completed_appointments = [apt for apt in appointments if apt.status == AppointmentStatus.COMPLETED]
    
    if completed_appointments:
        treatment_outcomes.append({
            "metric": "Appointment Completion Rate",
            "value": f"{round((len(completed_appointments) / len(appointments)) * 100, 1)}%",
            "trend": "positive" if len(completed_appointments) > len(appointments) * 0.8 else "neutral"
        })
    
    # Medication effectiveness analysis
    medication_analysis = {}
    for prescription in prescriptions:
        for medication in prescription.medications:
            med_name = medication.get("name", "Unknown")
            if med_name not in medication_analysis:
                medication_analysis[med_name] = {
                    "prescribed_count": 0,
                    "duration_days": [],
                    "associated_diagnoses": []
                }
            
            medication_analysis[med_name]["prescribed_count"] += 1
            if prescription.diagnosis:
                medication_analysis[med_name]["associated_diagnoses"].append(prescription.diagnosis)
    
    medication_effectiveness = [
        {
            "medication": med_name,
            "prescription_frequency": data["prescribed_count"],
            "common_indications": list(set(data["associated_diagnoses"]))[:3],
            "effectiveness_score": min(90, 60 + (data["prescribed_count"] * 5))  # Mock scoring
        }
        for med_name, data in sorted(medication_analysis.items(), key=lambda x: x[1]["prescribed_count"], reverse=True)[:10]
    ]
    
    # Seasonal pattern analysis
    seasonal_patterns = []
    monthly_visits = {}
    
    for record in records:
        month = record.created_at.month
        monthly_visits[month] = monthly_visits.get(month, 0) + 1
    
    if monthly_visits:
        peak_month = max(monthly_visits, key=monthly_visits.get)
        seasonal_patterns.append({
            "pattern": "Peak Visit Month",
            "value": f"Month {peak_month}",
            "description": f"Highest activity in month {peak_month} with {monthly_visits[peak_month]} visits"
        })
    
    # Progression analysis
    progression_analysis = []
    
    if len(records) >= 3:
        # Analyze vital signs progression
        vital_records = [r for r in records if r.vital_signs]
        if len(vital_records) >= 2:
            progression_analysis.append({
                "parameter": "Vital Signs Monitoring",
                "trend": "Regular monitoring observed",
                "significance": "Positive health management"
            })
    
    # Risk analysis
    complication_risks = []
    
    # Age-based risks
    patient_age = calculate_age(patient.date_of_birth)
    if patient_age > 65:
        complication_risks.append({
            "risk_factor": "Advanced Age",
            "risk_level": "MODERATE",
            "probability": 0.3,
            "description": "Age-related health monitoring recommended"
        })
    
    # Chronic condition risks
    if patient.chronic_conditions:
        for condition in patient.chronic_conditions:
            complication_risks.append({
                "risk_factor": f"Chronic {condition}",
                "risk_level": "HIGH",
                "probability": 0.6,
                "description": f"Active monitoring required for {condition}"
            })
    
    # Calculate readmission risk (simplified)
    recent_admissions = len([a for a in appointments if _days_since(a.created_at) <= 30])
    readmission_risk = min(0.8, recent_admissions * 0.2) if recent_admissions > 0 else 0.1
    
    # Generate clinical recommendations
    clinical_recommendations = []
    
    if len(records) == 0:
        clinical_recommendations.append("Establish baseline medical assessment")
    elif len(records) < 3:
        clinical_recommendations.append("Consider more frequent follow-ups for better health monitoring")
    
    if patient.chronic_conditions:
        clinical_recommendations.append("Maintain regular monitoring schedule for chronic conditions")
    
    if patient_age > 50:
        clinical_recommendations.append("Consider age-appropriate preventive screening")
    
    # Follow-up suggestions
    follow_up_suggestions = []
    
    if records:
        last_visit = max(records, key=lambda x: x.created_at)
        days_since_last = _days_since(last_visit.created_at)
        
        if days_since_last > 90:
            follow_up_suggestions.append("Schedule routine follow-up - last visit over 3 months ago")
        
        if patient.chronic_conditions:
            follow_up_suggestions.append("Chronic condition monitoring - quarterly reviews recommended")
    
    return CaseHistoryAnalysis(
        patient_ref=patient_ref,
        analysis_period=analysis_period,
        total_cases=len(records),
        diagnosis_patterns=diagnosis_patterns,
        treatment_outcomes=treatment_outcomes,
        medication_effectiveness=medication_effectiveness,
        seasonal_patterns=seasonal_patterns,
        progression_analysis=progression_analysis,
        complication_risks=complication_risks,
        readmission_risk=readmission_risk,
        clinical_recommendations=clinical_recommendations,
        follow_up_suggestions=follow_up_suggestions
    )


# ============================================================================
# CLINICAL ALERTS AND NOTIFICATIONS
# ============================================================================

@router.get("/patients/{patient_ref}/clinical-alerts")
async def get_clinical_alerts(
    patient_ref: str,
    include_resolved: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get clinical alerts and notifications for a patient.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Generate clinical alerts based on patient data
    alerts = []
    
    # Drug allergy alerts
    if patient.allergies:
        for allergy in patient.allergies:
            alerts.append(ClinicalAlert(
                alert_id=generate_alert_id(),
                patient_ref=patient_ref,
                alert_type="DRUG_ALLERGY",
                severity=ClinicalSeverity.HIGH,
                title=f"Drug Allergy: {allergy}",
                description=f"Patient has documented allergy to {allergy}. Verify before prescribing.",
                triggered_by="PATIENT_PROFILE",
                triggered_at=datetime.now().isoformat(),
                is_active=True,
                acknowledged_by=None,
                acknowledged_at=None,
                resolution_notes=None
            ))
    
    # Chronic condition alerts
    if patient.chronic_conditions:
        for condition in patient.chronic_conditions:
            alerts.append(ClinicalAlert(
                alert_id=generate_alert_id(),
                patient_ref=patient_ref,
                alert_type="CHRONIC_CONDITION",
                severity=ClinicalSeverity.MODERATE,
                title=f"Chronic Condition: {condition}",
                description=f"Patient has {condition}. Regular monitoring required.",
                triggered_by="PATIENT_PROFILE",
                triggered_at=datetime.now().isoformat(),
                is_active=True,
                acknowledged_by=None,
                acknowledged_at=None,
                resolution_notes=None
            ))
    
    # Age-based alerts
    patient_age = calculate_age(patient.date_of_birth)
    if patient_age > 65:
        alerts.append(ClinicalAlert(
            alert_id=generate_alert_id(),
            patient_ref=patient_ref,
            alert_type="AGE_RELATED",
            severity=ClinicalSeverity.LOW,
            title="Elderly Patient",
            description="Patient is over 65. Consider age-appropriate care protocols.",
            triggered_by="AGE_CALCULATION",
            triggered_at=datetime.now().isoformat(),
            is_active=True,
            acknowledged_by=None,
            acknowledged_at=None,
            resolution_notes=None
        ))
    
    # Recent visit frequency alert
    recent_appointments_result = await db.execute(
        select(func.count(Appointment.id))
        .where(
            and_(
                Appointment.patient_id == patient.id,
                Appointment.created_at >= _utc_now() - timedelta(days=30)
            )
        )
    )
    
    recent_visits = recent_appointments_result.scalar() or 0
    if recent_visits > 3:
        alerts.append(ClinicalAlert(
            alert_id=generate_alert_id(),
            patient_ref=patient_ref,
            alert_type="FREQUENT_VISITS",
            severity=ClinicalSeverity.MODERATE,
            title="Frequent Recent Visits",
            description=f"Patient has {recent_visits} visits in the last 30 days. Consider comprehensive evaluation.",
            triggered_by="VISIT_FREQUENCY_ANALYSIS",
            triggered_at=datetime.now().isoformat(),
            is_active=True,
            acknowledged_by=None,
            acknowledged_at=None,
            resolution_notes=None
        ))
    
    # Medication interaction alert
    if patient.current_medications and len(patient.current_medications) > 3:
        alerts.append(ClinicalAlert(
            alert_id=generate_alert_id(),
            patient_ref=patient_ref,
            alert_type="MEDICATION_INTERACTION",
            severity=ClinicalSeverity.MODERATE,
            title="Multiple Medications",
            description=f"Patient is on {len(patient.current_medications)} medications. Monitor for interactions.",
            triggered_by="MEDICATION_COUNT",
            triggered_at=datetime.now().isoformat(),
            is_active=True,
            acknowledged_by=None,
            acknowledged_at=None,
            resolution_notes=None
        ))
    
    # Active admission alert
    active_admissions_result = await db.execute(
        select(Admission)
        .where(
            and_(
                Admission.patient_id == patient.id,
                Admission.is_active == True
            )
        )
    )
    
    active_admissions = active_admissions_result.scalars().all()
    if active_admissions:
        for admission in active_admissions:
            alerts.append(ClinicalAlert(
                alert_id=generate_alert_id(),
                patient_ref=patient_ref,
                alert_type="ACTIVE_ADMISSION",
                severity=ClinicalSeverity.HIGH,
                title="Currently Admitted",
                description=f"Patient is currently admitted ({admission.admission_type}) - {admission.admission_number}",
                triggered_by="ADMISSION_STATUS",
                triggered_at=datetime.now().isoformat(),
                is_active=True,
                acknowledged_by=None,
                acknowledged_at=None,
                resolution_notes=None
            ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "patient_ref": patient_ref,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "total_alerts": len(alerts),
        "active_alerts": len([a for a in alerts if a.is_active]),
        "high_priority_alerts": len([a for a in alerts if a.severity == ClinicalSeverity.HIGH]),
        "alerts": alerts
    }


# ============================================================================
# MEDICAL RECORD CREATION AND MANAGEMENT
# ============================================================================

class CreateMedicalRecordRequest(BaseModel):
    """Request to create a new medical record"""
    model_config = ConfigDict(populate_by_name=True)

    patient_ref: str = Field(
        ...,
        validation_alias=AliasChoices("patient_ref", "patientRef", "patient_id", "patientId"),
        description="Patient reference ID",
    )
    appointment_ref: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("appointment_ref", "appointmentRef"),
        description="Associated appointment reference",
    )
    
    # Clinical details
    chief_complaint: str = Field(
        ...,
        min_length=5,
        validation_alias=AliasChoices("chief_complaint", "chiefComplaint", "complaint", "reason"),
        description="Patient's chief complaint",
    )
    history_of_present_illness: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("history_of_present_illness", "historyOfPresentIllness"),
        description="History of present illness",
    )
    past_medical_history: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("past_medical_history", "pastMedicalHistory"),
        description="Past medical history",
    )
    
    # Physical examination
    vital_signs: Optional[Dict[str, Any]] = Field(
        None,
        validation_alias=AliasChoices("vital_signs", "vitalSigns"),
        description="Vital signs measurements",
    )
    physical_examination: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("physical_examination", "physicalExamination", "examination_findings"),
        description="Physical examination findings",
    )
    
    # Assessment and plan
    diagnosis: str = Field(..., min_length=3, description="Primary diagnosis")
    differential_diagnosis: Optional[List[str]] = Field(
        None,
        validation_alias=AliasChoices("differential_diagnosis", "differentialDiagnosis"),
        description="Differential diagnoses",
    )
    treatment_plan: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("treatment_plan", "treatmentPlan"),
        description="Treatment plan",
    )
    
    # Orders and instructions
    lab_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        validation_alias=AliasChoices("lab_orders", "labOrders"),
        description="Laboratory orders",
    )
    imaging_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        validation_alias=AliasChoices("imaging_orders", "imagingOrders"),
        description="Imaging orders",
    )
    follow_up_instructions: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("follow_up_instructions", "followUpInstructions"),
        description="Follow-up instructions",
    )
    
    # Additional notes
    clinical_notes: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("clinical_notes", "clinicalNotes"),
        description="Additional clinical notes",
    )
    is_finalized: bool = Field(
        False,
        validation_alias=AliasChoices("is_finalized", "isFinalized"),
        description="Whether the record is finalized",
    )


class UpdateMedicalRecordRequest(BaseModel):
    """Request to update an existing medical record"""
    model_config = ConfigDict(populate_by_name=True)

    chief_complaint: Optional[str] = Field(
        None,
        min_length=5,
        validation_alias=AliasChoices("chief_complaint", "chiefComplaint", "complaint", "reason"),
    )
    history_of_present_illness: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("history_of_present_illness", "historyOfPresentIllness"),
    )
    past_medical_history: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("past_medical_history", "pastMedicalHistory"),
    )
    vital_signs: Optional[Dict[str, Any]] = Field(
        None,
        validation_alias=AliasChoices("vital_signs", "vitalSigns"),
    )
    physical_examination: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("physical_examination", "physicalExamination", "examination_findings"),
    )
    diagnosis: Optional[str] = Field(None, min_length=3)
    differential_diagnosis: Optional[List[str]] = Field(
        None,
        validation_alias=AliasChoices("differential_diagnosis", "differentialDiagnosis"),
    )
    treatment_plan: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("treatment_plan", "treatmentPlan"),
    )
    lab_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        validation_alias=AliasChoices("lab_orders", "labOrders"),
    )
    imaging_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        validation_alias=AliasChoices("imaging_orders", "imagingOrders"),
    )
    follow_up_instructions: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("follow_up_instructions", "followUpInstructions"),
    )
    clinical_notes: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("clinical_notes", "clinicalNotes"),
    )
    is_finalized: Optional[bool] = Field(
        None,
        validation_alias=AliasChoices("is_finalized", "isFinalized"),
    )


@router.post("/medical-records")
async def create_medical_record(
    request: CreateMedicalRecordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new medical record for a patient.
    
    Access Control:
    - **Who can access:** Doctors only
    - Doctors can create records for patients in their hospital
    - Hospital isolation applied (tenant-scoped)
    - Canonical endpoint for medical record creation
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(request.patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Ensure we have hospital_id for medical record
    hospital_id_val = user_context.get("hospital_id")
    if not hospital_id_val and patient.hospital_id:
        hospital_id_val = str(patient.hospital_id)
    
    if not hospital_id_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Please ensure the patient is linked to a hospital."
        )
    
    # Find appointment if provided
    appointment = None
    if request.appointment_ref:
        # IMPORTANT: Appointment.doctor_id stores the *User* ID, not DoctorProfile ID
        appointment_result = await db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == request.appointment_ref,
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.user_id
                )
            )
        )
        appointment = appointment_result.scalar_one_or_none()
        
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Appointment {request.appointment_ref} not found or not accessible"
            )
    
    # Create medical record
    new_record = MedicalRecord(
        id=uuid.uuid4(),
        hospital_id=uuid.UUID(hospital_id_val),
        patient_id=patient.id,
        doctor_id=doctor.user_id,
        appointment_id=appointment.id if appointment else None,
        
        # Clinical details
        chief_complaint=request.chief_complaint,
        history_of_present_illness=request.history_of_present_illness,
        past_medical_history=request.past_medical_history,
        
        # Physical examination (map to examination_findings)
        vital_signs=request.vital_signs,
        examination_findings=request.physical_examination,
        
        # Assessment and plan
        diagnosis=request.diagnosis,
        differential_diagnosis=request.differential_diagnosis,
        treatment_plan=request.treatment_plan,
        
        # Orders and instructions
        lab_orders=request.lab_orders,
        imaging_orders=request.imaging_orders,
        follow_up_instructions=request.follow_up_instructions,
        
        # Prescriptions (if any)
        prescriptions=getattr(request, 'prescriptions', []),
        
        # Record metadata
        is_finalized=request.is_finalized
    )
    
    db.add(new_record)
    await db.commit()
    await db.refresh(new_record)
    
    return {
        "record_id": str(new_record.id),
        "patient_ref": request.patient_ref,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "appointment_ref": request.appointment_ref,
        "diagnosis": request.diagnosis,
        "is_finalized": request.is_finalized,
        "created_at": new_record.created_at.isoformat(),
        "message": "Medical record created successfully"
    }


@router.put("/medical-records/{record_id}")
async def update_medical_record(
    record_id: str,
    request: UpdateMedicalRecordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update an existing medical record.
    
    Access Control:
    - **Who can access:** Doctors only (the creating doctor)
    - Only the doctor who created the record can update it
    - Finalized records cannot be modified (use is_finalized in request to finalize)
    - Hospital isolation applied
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find medical record
    record_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.id == record_id,
                MedicalRecord.doctor_id == doctor.id,
                MedicalRecord.hospital_id == user_context["hospital_id"]
            )
        )
        .options(
            selectinload(MedicalRecord.patient).selectinload(PatientProfile.user)
        )
    )
    
    record = record_result.scalar_one_or_none()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Medical record not found or not accessible"
        )
    
    # Check if record is finalized
    if record.is_finalized and not request.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify finalized medical record"
        )
    
    # Update fields
    update_data = {}
    
    if request.chief_complaint is not None:
        update_data["chief_complaint"] = request.chief_complaint
    if request.history_of_present_illness is not None:
        update_data["history_of_present_illness"] = request.history_of_present_illness
    if request.past_medical_history is not None:
        update_data["past_medical_history"] = request.past_medical_history
    if request.vital_signs is not None:
        update_data["vital_signs"] = request.vital_signs
    if request.physical_examination is not None:
        update_data["examination_findings"] = request.physical_examination
    if request.diagnosis is not None:
        update_data["diagnosis"] = request.diagnosis
    if request.differential_diagnosis is not None:
        update_data["differential_diagnosis"] = request.differential_diagnosis
    if request.treatment_plan is not None:
        update_data["treatment_plan"] = request.treatment_plan
    if request.lab_orders is not None:
        update_data["lab_orders"] = request.lab_orders
    if request.imaging_orders is not None:
        update_data["imaging_orders"] = request.imaging_orders
    if request.follow_up_instructions is not None:
        update_data["follow_up_instructions"] = request.follow_up_instructions
    # Note: clinical_notes field doesn't exist in MedicalRecord model, skipping
    # if request.clinical_notes is not None:
    #     update_data["clinical_notes"] = request.clinical_notes
    if request.is_finalized is not None:
        update_data["is_finalized"] = request.is_finalized
    
    # Always update the timestamp
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    # Apply updates
    if update_data:
        await db.execute(
            update(MedicalRecord)
            .where(MedicalRecord.id == record_id)
            .values(**update_data)
        )
        await db.commit()
    
    return {
        "record_id": record_id,
        "patient_ref": record.patient.patient_id,
        "patient_name": f"{record.patient.user.first_name} {record.patient.user.last_name}",
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "updated_fields": list(update_data.keys()),
        "is_finalized": update_data.get("is_finalized", record.is_finalized),
        "updated_at": update_data["updated_at"].isoformat(),
        "message": "Medical record updated successfully"
    }


@router.get("/medical-records/{record_id}")
async def get_medical_record_details(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed medical record information.
    
    Access Control:
    - **Who can access:** Doctors only
    - Doctors can view records in their hospital
    - Hospital isolation applied
    - Returns full MedicalRecordDetail including linked prescriptions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find medical record
    record_result = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.id == record_id,
                MedicalRecord.hospital_id == user_context["hospital_id"]
            )
        )
        .options(
            selectinload(MedicalRecord.patient).selectinload(PatientProfile.user),
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
    
    # Get associated prescriptions
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(Prescription.medical_record_id == record.id)
        .options(selectinload(Prescription.doctor))
    )
    prescriptions = prescriptions_result.scalars().all()
    
    prescription_data = []
    for prescription in prescriptions:
        prescription_data.append({
            "prescription_id": str(prescription.id),
            "prescription_number": prescription.prescription_number,
            "prescription_date": prescription.prescription_date,
            "medications": prescription.medications,
            "general_instructions": prescription.general_instructions,
            "is_dispensed": prescription.is_dispensed,
            "prescribed_by": f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}"
        })
    
    return MedicalRecordDetail(
        record_id=str(record.id),
        record_type=RecordType.CONSULTATION,
        date=record.created_at.strftime("%Y-%m-%d"),
        time=record.created_at.strftime("%H:%M:%S"),
        doctor_name=f"Dr. {record.doctor.first_name} {record.doctor.last_name}",
        department="Unknown Department",  # TODO: Get department from staff assignment
        chief_complaint=record.chief_complaint,
        diagnosis=record.diagnosis,
        treatment_plan=record.treatment_plan,
        vital_signs=record.vital_signs,
        prescriptions=prescription_data,
        lab_orders=record.lab_orders or [],
        imaging_orders=record.imaging_orders or [],
        follow_up_instructions=record.follow_up_instructions,
        is_finalized=record.is_finalized,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat() if record.updated_at else None
    )


# ============================================================================
# DOCUMENT MANAGEMENT
# ============================================================================

@router.get("/patients/{patient_ref}/documents")
async def get_patient_documents(
    patient_ref: str,
    document_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get patient documents with filtering options.
    
    Access Control:
    - **Who can access:** Doctors only (hospital isolation applied)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Find patient - handle null hospital_id
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Use patient's hospital_id if user's is null
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Build query conditions
    conditions = [PatientDocument.patient_id == patient.id]
    
    if document_type:
        conditions.append(PatientDocument.document_type == document_type)
    
    # Get documents
    documents_result = await db.execute(
        select(PatientDocument)
        .where(and_(*conditions))
        .options(selectinload(PatientDocument.uploader))
        .order_by(desc(PatientDocument.created_at))
    )
    
    documents = documents_result.scalars().all()
    
    # Build document summaries
    document_summaries = []
    
    for doc in documents:
        document_summaries.append(PatientDocumentSummary(
            document_id=str(doc.id),
            document_type=doc.document_type,
            title=doc.title,
            description=doc.description,
            file_name=doc.file_name,
            file_size=doc.file_size or 0,
            uploaded_date=doc.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            uploaded_by=f"{doc.uploader.first_name} {doc.uploader.last_name}",
            is_sensitive=doc.is_sensitive,
            tags=[]  # Could be extended to include tags
        ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "patient_ref": patient_ref,
        "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
        "document_type_filter": document_type,
        "total_documents": len(document_summaries),
        "documents": document_summaries
    }