"""
Doctor schemas for schedule management, prescriptions, treatment plans, and patient records.
Note: This extends the existing app/schemas/doctor.py with additional schemas from router files.
"""
from typing import Optional, List, Dict, Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from enum import Enum


# ============================================================================
# ENUMS
# ============================================================================

class PlanPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TreatmentType(str, Enum):
    MEDICATION = "MEDICATION"
    THERAPY = "THERAPY"
    PROCEDURE = "PROCEDURE"
    LIFESTYLE = "LIFESTYLE"
    MONITORING = "MONITORING"


class MilestoneStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


class TreatmentPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class ReviewFrequency(str, Enum):
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class OutcomeStatus(str, Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"


# ============================================================================
# SCHEDULE INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class ScheduleCreate(BaseModel):
    """Request to create one date-specific doctor availability window."""
    date: str = Field(..., pattern="^\\d{4}-\\d{2}-\\d{2}$", description="Appointment date, YYYY-MM-DD")
    start_time: str = Field(..., pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    end_time: str = Field(..., pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")


class ScheduleUpdate(BaseModel):
    """Request to update one date-specific doctor availability window."""
    date: Optional[str] = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$")
    start_time: Optional[str] = Field(None, pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    end_time: Optional[str] = Field(None, pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")


class AppointmentUpdate(BaseModel):
    """Request to update appointment (doctor portal PUT /doctor-management/appointments/{ref})."""
    appointment_date: Optional[str] = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$")
    appointment_time: Optional[str] = Field(None, pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    status: Optional[str] = None
    notes: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=15, le=180, description="Slot length in minutes")
    appointment_type: Optional[str] = Field(None, max_length=50, description="e.g. IN_PERSON, ONLINE")
    consultation_fee: Optional[float] = Field(None, ge=0, description="Consultation fee if updating")


# ============================================================================
# PRESCRIPTION INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class MedicationTiming(BaseModel):
    """Detailed medication timing instructions"""
    morning: bool = False
    noon: bool = False
    evening: bool = False
    night: bool = False
    before_breakfast: bool = False
    after_breakfast: bool = False
    before_lunch: bool = False
    after_lunch: bool = False
    before_dinner: bool = False
    after_dinner: bool = False
    bedtime: bool = False
    empty_stomach: bool = False
    with_food: bool = False
    custom_timing: Optional[str] = None


class MedicationItem(BaseModel):
    """Individual medication in prescription"""
    drug_id: str
    generic_name: str
    brand_name: Optional[str]
    strength: str
    dosage_form: str
    quantity: int = Field(..., gt=0)
    dosage: str
    frequency: str  # TID, BID, QID, etc.
    duration: str
    route: str
    timing: MedicationTiming
    instructions: Optional[str]
    substitute_allowed: bool = True


class PrescriptionCreate(BaseModel):
    """Advanced prescription creation request"""
    patient_ref: str
    appointment_ref: Optional[str] = None
    chief_complaint: Optional[str] = None
    clinical_diagnosis: str
    provisional_diagnosis: Optional[str] = None
    medications: List[MedicationItem] = Field(..., min_items=1)
    general_instructions: Optional[str] = None
    diet_instructions: Optional[str] = None
    lifestyle_advice: Optional[str] = None
    follow_up_date: Optional[str] = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$")
    follow_up_instructions: Optional[str] = None
    lab_tests_recommended: Optional[List[str]] = None
    precautions: Optional[List[str]] = None
    check_interactions: bool = True
    check_allergies: bool = True
    # emergency_contact_pharmacy: bool = False  # PHARMACY REMOVED


class DrugSearchFilter(BaseModel):
    """Drug search request"""
    query: str = Field(..., min_length=2)
    search_type: str = Field("all", pattern="^(generic|brand|class|all)$")
    therapeutic_category: Optional[str] = None
    dosage_form: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)


class PharmacyDispenseCreate(BaseModel):
    """Pharmacy dispensing request"""
    prescription_number: str
    pharmacy_id: Optional[str] = None
    pharmacist_id: Optional[str] = None
    dispensed_medications: List[Dict[str, Any]]
    partial_dispensing: bool = False
    dispensing_notes: Optional[str] = None


# ============================================================================
# TREATMENT PLAN INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class TreatmentPlanCreate(BaseModel):
    """Request to create new treatment plan"""
    model_config = ConfigDict(populate_by_name=True)

    patient_ref: str = Field(validation_alias=AliasChoices("patient_ref", "patientRef", "patient_id", "patientId"))
    plan_name: str = Field(validation_alias=AliasChoices("plan_name", "planName"))
    primary_diagnosis: str = Field(validation_alias=AliasChoices("primary_diagnosis", "primaryDiagnosis", "diagnosis"))
    secondary_diagnoses: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("secondary_diagnoses", "secondaryDiagnoses"),
    )
    priority: PlanPriority = PlanPriority.MEDIUM
    estimated_duration: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("estimated_duration", "estimatedDuration"),
    )
    review_frequency: ReviewFrequency = Field(
        default=ReviewFrequency.MONTHLY,
        validation_alias=AliasChoices("review_frequency", "reviewFrequency"),
    )
    
    # Initial goals
    short_term_goals: List[Dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("short_term_goals", "shortTermGoals"),
    )
    long_term_goals: List[Dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("long_term_goals", "longTermGoals"),
    )
    
    # Initial interventions
    interventions: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Initial milestones
    milestones: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Notes
    initial_notes: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("initial_notes", "initialNotes", "notes"),
    )


class TreatmentPlanUpdate(BaseModel):
    """Request to update treatment plan"""
    model_config = ConfigDict(populate_by_name=True)

    plan_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("plan_name", "planName"))
    status: Optional[TreatmentPlanStatus] = None
    priority: Optional[PlanPriority] = None
    expected_end_date: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("expected_end_date", "expectedEndDate"),
    )
    review_frequency: Optional[ReviewFrequency] = Field(
        default=None,
        validation_alias=AliasChoices("review_frequency", "reviewFrequency"),
    )
    
    # Updated components
    updated_goals: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        validation_alias=AliasChoices("updated_goals", "updatedGoals"),
    )
    updated_interventions: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        validation_alias=AliasChoices("updated_interventions", "updatedInterventions"),
    )
    updated_milestones: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        validation_alias=AliasChoices("updated_milestones", "updatedMilestones"),
    )
    
    # Progress update
    progress_notes: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("progress_notes", "progressNotes"),
    )
    completion_notes: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("completion_notes", "completionNotes"),
    )


class ProgressUpdate(BaseModel):
    """Request to update treatment progress"""
    model_config = ConfigDict(populate_by_name=True)

    milestone_updates: List[Dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("milestone_updates", "milestoneUpdates"),
    )
    goal_updates: List[Dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("goal_updates", "goalUpdates"),
    )
    intervention_updates: List[Dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("intervention_updates", "interventionUpdates"),
    )
    progress_note: str = Field(validation_alias=AliasChoices("progress_note", "progressNote", "note"))
    significant_change: bool = Field(
        default=False,
        validation_alias=AliasChoices("significant_change", "significantChange"),
    )
    next_review_date: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("next_review_date", "nextReviewDate"),
    )


# ============================================================================
# PATIENT RECORD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class PatientSearchFilter(BaseModel):
    """Patient search request"""
    query: str = Field(..., min_length=2)
    search_type: str = Field("all", pattern="^(name|phone|email|patient_ref|all)$")
    department: Optional[str] = None
    age_range: Optional[Dict[str, int]] = None  # {"min": 18, "max": 65}
    gender: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)


class AdvancedSearchFilter(BaseModel):
    """Advanced search filters"""
    age_range: Optional[Dict[str, int]] = None  # {"min": 18, "max": 65}
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    chronic_conditions: Optional[List[str]] = None
    last_visit_range: Optional[Dict[str, str]] = None  # {"from": "2023-01-01", "to": "2023-12-31"}
    department: Optional[str] = None
    doctor_name: Optional[str] = None
    appointment_status: Optional[str] = None


# ============================================================================
# OUTPUT SCHEMAS (Out/Response)
# ============================================================================

class ScheduleSlotOut(BaseModel):
    """Doctor schedule slot"""
    schedule_id: str
    date: Optional[str] = None
    start_time: str
    end_time: str


class AppointmentDetailsOut(BaseModel):
    """Detailed appointment information"""
    appointment_ref: str
    patient_ref: str
    patient_name: str
    patient_age: int
    patient_phone: str
    appointment_date: str
    appointment_time: str
    duration_minutes: int
    status: str
    appointment_type: str
    chief_complaint: Optional[str]
    notes: Optional[str]
    is_checked_in: bool
    checked_in_at: Optional[str]
    is_completed: bool
    completed_at: Optional[str]
    consultation_fee: Optional[float]
    is_paid: bool


class DrugInfoOut(BaseModel):
    """Drug information from database"""
    drug_id: str
    generic_name: str
    brand_names: List[str]
    drug_class: str
    therapeutic_category: str
    dosage_forms: List[str]  # ["tablet", "capsule", "syrup", "injection"]
    strengths: List[str]     # ["500mg", "250mg", "100ml"]
    route_of_administration: List[str]  # ["oral", "iv", "im", "topical"]
    contraindications: List[str]
    side_effects: List[str]
    drug_interactions: List[str]
    pregnancy_category: Optional[str]
    pediatric_safe: bool
    geriatric_considerations: Optional[str]
    storage_conditions: Optional[str]
    manufacturer: Optional[str]


class DosageRecommendationOut(BaseModel):
    """Dosage recommendation based on patient factors"""
    drug_id: str
    drug_name: str
    recommended_dose: str
    frequency: str
    duration: str
    route: str
    special_instructions: Optional[str]
    warnings: List[str]
    patient_factors_considered: List[str]


class DrugInteractionOut(BaseModel):
    """Drug interaction information"""
    drug1_id: str
    drug1_name: str
    drug2_id: str
    drug2_name: str
    interaction_type: str  # MAJOR, MODERATE, MINOR
    severity: str         # HIGH, MEDIUM, LOW
    description: str
    clinical_significance: str
    management_strategy: Optional[str]


class AllergyCheckOut(BaseModel):
    """Allergy checking result"""
    drug_id: str
    drug_name: str
    has_allergy: bool
    allergy_type: Optional[str]
    severity: Optional[str]
    alternative_drugs: List[str]


class PrescriptionValidationOut(BaseModel):
    """Prescription validation result"""
    is_valid: bool
    warnings: List[str]
    errors: List[str]
    drug_interactions: List[DrugInteractionOut]
    allergy_alerts: List[AllergyCheckOut]
    dosage_recommendations: List[DosageRecommendationOut]
    contraindications: List[str]
    patient_specific_warnings: List[str]


class DigitalPrescriptionOut(BaseModel):
    """Complete digital prescription"""
    prescription_id: str
    prescription_number: str
    patient_ref: str
    patient_name: str
    patient_age: int
    patient_weight: Optional[float]
    doctor_name: str
    doctor_license: str
    hospital_name: str
    prescription_date: str
    clinical_diagnosis: str
    medications: List[MedicationItem]
    general_instructions: Optional[str]
    diet_instructions: Optional[str]
    lifestyle_advice: Optional[str]
    follow_up_date: Optional[str]
    follow_up_instructions: Optional[str]
    lab_tests_recommended: Optional[List[str]]
    precautions: Optional[List[str]]
    is_digitally_signed: bool
    signature_hash: str
    qr_code: Optional[str]
    # pharmacy_instructions: Optional[str]  # PHARMACY REMOVED
    dispensing_status: str
    created_at: str


class TreatmentGoalOut(BaseModel):
    """Individual treatment goal"""
    goal_id: str
    description: str
    target_date: Optional[str] = None
    priority: PlanPriority
    measurable_outcome: str
    current_status: str
    progress_percentage: int = Field(ge=0, le=100)
    notes: Optional[str] = None


class TreatmentInterventionOut(BaseModel):
    """Treatment intervention details"""
    intervention_id: str
    intervention_type: TreatmentType
    description: str
    instructions: str
    frequency: str
    duration: Optional[str] = None
    start_date: str
    end_date: Optional[str] = None
    responsible_provider: Optional[str] = None
    status: str = "ACTIVE"
    notes: Optional[str] = None


class TreatmentMilestoneOut(BaseModel):
    """Treatment plan milestone"""
    milestone_id: str
    title: str
    description: str
    target_date: str
    status: MilestoneStatus
    completion_date: Optional[str] = None
    completion_notes: Optional[str] = None
    dependencies: List[str] = []
    assigned_to: Optional[str] = None


class ProgressNoteOut(BaseModel):
    """Progress tracking note"""
    note_id: str
    date: str
    author: str
    note_type: str  # PROGRESS, ASSESSMENT, PLAN_UPDATE, MILESTONE
    content: str
    milestone_id: Optional[str] = None
    attachments: List[str] = []
    is_significant: bool = False


class TreatmentPlanSummaryOut(BaseModel):
    """Treatment plan summary information"""
    plan_id: str
    plan_name: str
    patient_ref: str
    patient_name: str
    primary_diagnosis: str
    status: TreatmentPlanStatus
    priority: PlanPriority
    start_date: str
    expected_end_date: Optional[str] = None
    completion_date: Optional[str] = None
    progress_percentage: int
    total_goals: int
    completed_goals: int
    total_milestones: int
    completed_milestones: int
    last_review_date: Optional[str] = None
    next_review_date: Optional[str] = None
    created_by: str
    created_date: str


class DetailedTreatmentPlanOut(BaseModel):
    """Comprehensive treatment plan details"""
    plan_id: str
    plan_name: str
    patient_ref: str
    patient_name: str
    patient_age: int
    patient_gender: str
    
    # Clinical information
    primary_diagnosis: str
    secondary_diagnoses: List[str]
    comorbidities: List[str]
    allergies: List[str]
    current_medications: List[str]
    
    # Plan details
    status: TreatmentPlanStatus
    priority: PlanPriority
    start_date: str
    expected_end_date: Optional[str] = None
    completion_date: Optional[str] = None
    estimated_duration: Optional[str] = None
    
    # Goals and objectives
    short_term_goals: List[TreatmentGoalOut]
    long_term_goals: List[TreatmentGoalOut]
    
    # Treatment components
    interventions: List[TreatmentInterventionOut]
    milestones: List[TreatmentMilestoneOut]
    
    # Progress tracking
    progress_percentage: int
    progress_notes: List[ProgressNoteOut]
    
    # Review and monitoring
    review_frequency: ReviewFrequency
    last_review_date: Optional[str] = None
    next_review_date: Optional[str] = None
    
    # Team and collaboration
    primary_doctor: str
    care_team: List[str]
    
    # Metadata
    created_by: str
    created_date: str
    last_modified_by: Optional[str] = None
    last_modified_date: Optional[str] = None


class TreatmentOutcomeOut(BaseModel):
    """Treatment outcome assessment"""
    plan_id: str
    patient_ref: str
    outcome_date: str
    overall_outcome: OutcomeStatus
    
    # Goal achievements
    goals_achieved: int
    goals_partially_achieved: int
    goals_not_achieved: int
    
    # Clinical outcomes
    symptom_improvement: Dict[str, str]
    functional_improvement: Dict[str, str]
    quality_of_life_score: Optional[float] = None
    
    # Patient satisfaction
    patient_satisfaction_score: Optional[float] = None
    patient_feedback: Optional[str] = None
    
    # Provider assessment
    provider_assessment: Optional[str] = None
    lessons_learned: Optional[str] = None
    recommendations: Optional[List[str]] = None