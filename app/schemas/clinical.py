"""
Clinical operations schemas for OPD, IPD, and nursing management.
"""
import re
from typing import Optional, List, Dict, Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator


# ============================================================================
# OPD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class PatientRegistrationCreate(BaseModel):
    """Register new patient for OPD (receptionist). Insurance fields are not collected here."""
    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(validation_alias=AliasChoices("first_name", "firstName"))
    last_name: str = Field(validation_alias=AliasChoices("last_name", "lastName"))
    phone: str
    email: Optional[EmailStr] = None
    date_of_birth: str = Field(validation_alias=AliasChoices("date_of_birth", "dob"))  # YYYY-MM-DD
    gender: str  # MALE, FEMALE, OTHER (or Male / Female / Other — normalized server-side)
    address: Optional[str] = None
    pincode: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    id_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("id_type", "idType"),
        description="e.g. Aadhaar Card, Passport, Other",
    )
    id_number: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("id_number", "idNumber"),
        description="ID document number when applicable",
    )
    id_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("id_name", "idName"),
        description="Name or label on ID when id_type is Other",
    )
    emergency_contact_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emergency_contact_name", "emergencyContactName"),
    )
    emergency_contact_phone: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_contact_phone",
            "emergencyContact",
            "emergency_contact_number",
            "emergencyContactNumber",
        ),
    )
    emergency_contact_relation: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_contact_relation",
            "emergencyContactRelationship",
            "relationship",
        ),
    )
    medical_history: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("medical_history", "medicalHistory"),
        description="Known conditions, allergies, medications (free text)",
    )
    blood_group: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("blood_group", "bloodGroup"),
        description="A+, A-, B+, B-, AB+, AB-, O+, O-, OTHER",
    )
    blood_group_value: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("blood_group_value", "bloodGroupValue"),
        description="Required when blood_group is OTHER — specify the group",
    )
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description=(
            "Optional. If set, patient can log in via POST /auth/patient/login with this email and password "
            "(same as online registration). Requires email."
        ),
    )

    @field_validator("password", mode="before")
    @classmethod
    def empty_password_to_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @model_validator(mode="after")
    def password_requires_email(self):
        if self.password and not self.email:
            raise ValueError("email is required when password is set so the patient can use patient login")
        return self

    @model_validator(mode="after")
    def blood_group_other_value(self):
        bg = (self.blood_group or "").strip().upper()
        if bg == "OTHER" and not (self.blood_group_value or "").strip():
            raise ValueError("blood_group_value is required when blood_group is OTHER")
        return self

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def normalize_date_of_birth(cls, v: Any) -> Any:
        """Accept YYYY-MM-DD or DD-MM-YYYY from UI date pickers."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return v
        s = str(v).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s
        m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", s)
        if m:
            day, month, year = m.groups()
            return f"{year}-{month}-{day}"
        return s

    @field_validator("gender", mode="after")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        val = (v or "").strip().upper()
        allowed = {"MALE", "FEMALE", "OTHER"}
        if val not in allowed:
            raise ValueError("gender must be one of: Male, Female, Other")
        return val

    @field_validator("id_type", mode="after")
    @classmethod
    def validate_id_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.strip()
        if not raw:
            return None
        canon = {
            "AADHAAR CARD": "Aadhaar Card",
            "AADHAR CARD": "Aadhaar Card",
            "PASSPORT": "Passport",
            "OTHER": "Other",
            "OTHERS": "Other",
        }
        key = raw.upper()
        if key not in canon:
            raise ValueError("idType must be one of: Aadhaar Card, Passport, Other")
        return canon[key]

    @model_validator(mode="after")
    def id_type_other_requires_id_name(self):
        idt = (self.id_type or "").strip().upper()
        if idt == "OTHER" and not (self.id_name or "").strip():
            raise ValueError("idName is required when idType is Other")
        return self

    @field_validator("blood_group", mode="after")
    @classmethod
    def validate_blood_group(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.strip()
        if not raw:
            return None
        normalized = raw.upper().replace(" ", "")
        allowed = {"A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-", "OTHER"}
        if normalized not in allowed:
            raise ValueError("bloodGroup must be one of: A+, A-, B+, B-, O+, O-, AB+, AB-, Other")
        return normalized

    send_credentials_email: bool = Field(
        default=True,
        description=(
            "If true (default), attempts to email portal login details after registration. "
            "Registration always saves even if SMTP is misconfigured or sending fails — check `credentials_email_sent` in the response."
        ),
    )


class ReceptionistPatientPatch(BaseModel):
    """Partial update for OPD patient (receptionist). Only sent fields are applied."""

    model_config = ConfigDict(populate_by_name=True)

    first_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("first_name", "firstName"))
    last_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("last_name", "lastName"))
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    date_of_birth: Optional[str] = Field(default=None, validation_alias=AliasChoices("date_of_birth", "dob"))
    gender: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    id_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_type", "idType"))
    id_number: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_number", "idNumber"))
    id_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_name", "idName"))
    emergency_contact_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emergency_contact_name", "emergencyContactName"),
    )
    emergency_contact_phone: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_contact_phone",
            "emergencyContact",
            "emergency_contact_number",
            "emergencyContactNumber",
        ),
    )
    emergency_contact_relation: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_contact_relation",
            "emergencyContactRelationship",
            "relationship",
        ),
    )
    medical_history: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("medical_history", "medicalHistory"),
    )
    blood_group: Optional[str] = Field(default=None, validation_alias=AliasChoices("blood_group", "bloodGroup"))
    blood_group_value: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("blood_group_value", "bloodGroupValue"),
    )
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="New portal password; requires email on file or email in same request.",
    )
    send_credentials_email: bool = Field(
        default=True,
        description="When password is updated, queue credential email if SMTP configured.",
    )

    @field_validator("password", mode="before")
    @classmethod
    def empty_password_to_none_patch(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("phone", mode="before")
    @classmethod
    def strip_phone_patch(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("gender", mode="after")
    @classmethod
    def validate_gender_optional_patch(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        val = v.strip().upper()
        allowed = {"MALE", "FEMALE", "OTHER"}
        if val not in allowed:
            raise ValueError("gender must be one of: Male, Female, Other")
        return val

    @field_validator("id_type", mode="after")
    @classmethod
    def validate_id_type_optional_patch(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.strip()
        if not raw:
            return None
        canon = {
            "AADHAAR CARD": "Aadhaar Card",
            "AADHAR CARD": "Aadhaar Card",
            "PASSPORT": "Passport",
            "OTHER": "Other",
            "OTHERS": "Other",
        }
        key = raw.upper()
        if key not in canon:
            raise ValueError("idType must be one of: Aadhaar Card, Passport, Other")
        return canon[key]

    @field_validator("blood_group", mode="after")
    @classmethod
    def validate_blood_group_optional_patch(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.strip()
        if not raw:
            return None
        normalized = raw.upper().replace(" ", "")
        allowed = {"A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-", "OTHER"}
        if normalized not in allowed:
            raise ValueError("bloodGroup must be one of: A+, A-, B+, B-, O+, O-, AB+, AB-, Other")
        return normalized


class AppointmentSchedulingCreate(BaseModel):
    """Schedule appointment for an existing patient. Provide patient_ref and/or patient_name."""
    model_config = ConfigDict(populate_by_name=True)

    patient_ref: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("patient_ref", "patientId", "patient_id", "patientRef"),
        description="Patient ID from registration (e.g. PAT-...). Omit if patient_name uniquely resolves.",
    )
    patient_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("patient_name", "patientName", "name"),
        description="Full name as registered (e.g. 'Jane Doe'). Used to resolve patient when patient_ref is omitted.",
    )
    doctor_name: str = Field(
        validation_alias=AliasChoices("doctor_name", "doctorName", "doctor"),
        description="Doctor display name, e.g. Dr. John Smith (must match a doctor in this hospital).",
    )
    department_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("department_name", "departmentName", "department"),
        description="Department name or code (e.g. Cardiology). If omitted, derived from the doctor's assignment.",
    )
    appointment_date: str = Field(validation_alias=AliasChoices("appointment_date", "appointmentDate", "date"))
    appointment_time: str = Field(validation_alias=AliasChoices("appointment_time", "appointmentTime", "time"))
    appointment_type: str = Field(
        default="CONSULTATION",
        validation_alias=AliasChoices("appointment_type", "appointmentType", "type"),
    )
    chief_complaint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("chief_complaint", "chiefComplaint", "reason", "complaint"),
    )
    notes: Optional[str] = None

    @model_validator(mode="after")
    def require_patient_and_doctor_identifiers(self):
        ref = (self.patient_ref or "").strip()
        name = (self.patient_name or "").strip()
        if not ref and not name:
            raise ValueError("Either patient_ref or patient_name is required")
        if not (self.doctor_name or "").strip():
            raise ValueError("doctor_name is required")
        return self

class ReceptionistPatientDetailOut(BaseModel):
    """Full OPD patient profile for receptionist UI. Password is never returned (always null)."""

    patient_ref: str
    first_name: str
    last_name: str
    patient_name: str
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    phone: str
    email: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    id_name: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    # Legacy / form aliases (same DB columns as above)
    emergency_contact_relationship: Optional[str] = None
    relationship: Optional[str] = None
    emergency_contact_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    medical_history: Optional[str] = None
    blood_group: Optional[str] = None
    blood_group_value: Optional[str] = None
    password: Optional[str] = Field(
        default=None,
        description="Always null — passwords are never exposed. Omit in PATCH forms or leave blank.",
    )
    portal_login_enabled: bool = Field(
        default=False,
        description="True when patient email is verified and suitable for POST /auth/patient/login.",
    )


class DebugPatientEditUpdate(BaseModel):
    """Debug-only patient update payload for IPD patient list tooling."""
    model_config = ConfigDict(populate_by_name=True)

    first_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("first_name", "firstName"))
    last_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("last_name", "lastName"))
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    date_of_birth: Optional[str] = Field(default=None, validation_alias=AliasChoices("date_of_birth", "dob"))
    gender: Optional[str] = None
    id_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_type", "idType"))
    id_number: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_number", "idNumber"))
    id_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("id_name", "idName"))
    address: Optional[str] = None
    pincode: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    emergency_contact_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emergency_contact_name", "emergencyContactName"),
    )
    emergency_contact_phone: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emergency_contact_phone", "emergencyContact"),
    )
    emergency_contact_relation: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emergency_contact_relation", "emergencyContactRelationship"),
    )
    medical_history: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("medical_history", "medicalHistory"),
    )
    blood_group: Optional[str] = Field(default=None, validation_alias=AliasChoices("blood_group", "bloodGroup"))
    blood_group_value: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("blood_group_value", "bloodGroupValue"),
    )


class AppointmentUpdate(BaseModel):
    """Modify existing appointment"""
    model_config = ConfigDict(populate_by_name=True)

    appointment_date: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("appointment_date", "appointmentDate", "date"),
    )
    appointment_time: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("appointment_time", "appointmentTime", "time"),
    )
    doctor_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("doctor_name", "doctorName", "doctor"),
        description="Doctor display name to reassign the appointment.",
    )
    department_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("department_id", "departmentId"),
        description="Optional legacy field; prefer department_name.",
    )
    department_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("department_name", "departmentName", "department"),
        description="Optional department name/code when changing department.",
    )
    patient_ref: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("patient_ref", "patientId", "patient_id", "patientRef"),
    )
    appointment_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("appointment_type", "appointmentType", "type"),
    )
    chief_complaint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("chief_complaint", "chiefComplaint", "reason", "complaint"),
    )
    notes: Optional[str] = None
    status: Optional[str] = None  # CONFIRMED, CANCELLED, RESCHEDULED

    @model_validator(mode="after")
    def normalize_department_input(self):
        import uuid as uuid_mod

        dname = (self.department_name or "").strip()
        did = (self.department_id or "").strip()
        if dname and not did:
            try:
                uuid_mod.UUID(dname)
                return self.model_copy(update={"department_id": dname, "department_name": None})
            except ValueError:
                pass
        return self


class PatientCheckInCreate(BaseModel):
    """Check-in patient for appointment"""
    appointment_ref: Optional[str] = None
    arrival_time: Optional[str] = None  # HH:MM, defaults to current time
    notes: Optional[str] = None
    checked_in_by: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("checked_in_by", "checkedInBy"),
        description="Optional receptionist/staff display id for audit",
    )


class AppointmentStatusUpdate(BaseModel):
    """Update appointment workflow status (receptionist)."""
    status: str = Field(
        description="SCHEDULED, CONFIRMED, CHECKED_IN, WAITING, IN_CONSULTATION, COMPLETED, CANCELLED, NO_SHOW",
    )


class AppointmentCancelUpdate(BaseModel):
    """Cancel an appointment."""
    status: str = Field(default="CANCELLED")
    cancel_reason: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("cancel_reason", "cancelReason", "cancellation_reason"),
    )


# ============================================================================
# IPD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class PatientAdmissionCreate(BaseModel):
    """Admit patient to IPD"""
    patient_ref: str
    admission_type: str = "IPD"  # IPD, EMERGENCY
    chief_complaint: str
    provisional_diagnosis: Optional[str] = None
    admission_notes: Optional[str] = None
    ward: Optional[str] = None
    room_number: Optional[str] = None
    bed_number: Optional[str] = None
    expected_length_of_stay: Optional[int] = None  # days


class BedAssignmentCreate(BaseModel):
    """Assign bed to patient"""
    admission_number: str
    ward: str
    room_number: str
    bed_number: str
    notes: Optional[str] = None


class TreatmentPlanCreate(BaseModel):
    """Create treatment plan for admitted patient"""
    admission_number: str
    treatment_objectives: List[str]
    medications: List[Dict[str, Any]]
    procedures: List[Dict[str, Any]]
    diet_instructions: Optional[str] = None
    activity_restrictions: Optional[str] = None
    monitoring_requirements: List[str]
    expected_outcomes: Optional[str] = None


class MedicationAdministrationCreate(BaseModel):
    """Record medication administration"""
    admission_number: str
    medication_name: str
    dosage: str
    route: str  # ORAL, IV, IM, SC
    administered_time: str  # HH:MM
    administered_by: Optional[str] = None  # Auto-filled from JWT
    patient_response: Optional[str] = None
    side_effects: Optional[str] = None
    notes: Optional[str] = None


class NursingAssessmentCreate(BaseModel):
    """Comprehensive nursing assessment"""
    admission_number: str
    assessment_type: str  # ADMISSION, DAILY, SHIFT_CHANGE, DISCHARGE
    general_condition: str  # STABLE, CRITICAL, IMPROVING, DETERIORATING
    consciousness_level: str  # ALERT, DROWSY, UNCONSCIOUS
    mobility_status: str  # AMBULATORY, BEDBOUND, ASSISTED
    pain_assessment: Dict[str, Any]  # {"level": 3, "location": "chest", "type": "sharp"}
    skin_condition: Optional[str] = None
    wound_assessment: Optional[List[Dict[str, Any]]] = None
    nutritional_status: Optional[str] = None
    elimination_status: Optional[Dict[str, str]] = None  # {"bowel": "normal", "bladder": "normal"}
    psychosocial_status: Optional[str] = None
    family_involvement: Optional[str] = None
    discharge_planning_needs: Optional[List[str]] = None
    nursing_interventions: List[str]
    goals_for_next_shift: Optional[List[str]] = None


class DoctorRoundsCreate(BaseModel):
    """Doctor rounds documentation"""
    admission_number: str
    round_type: str  # MORNING, EVENING, EMERGENCY, CONSULTATION
    patient_condition: str  # STABLE, CRITICAL, IMPROVING, DETERIORATING
    clinical_findings: str
    assessment_and_plan: str
    medication_changes: Optional[List[Dict[str, Any]]] = None
    new_orders: Optional[List[str]] = None
    follow_up_instructions: Optional[str] = None
    discharge_planning: Optional[str] = None
    family_discussion: Optional[str] = None


# ============================================================================
# NURSING INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class VitalSignsUpdate(BaseModel):
    """Update patient vital signs"""
    patient_ref: str
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    pulse_rate: Optional[int] = None
    temperature: Optional[float] = None  # Celsius
    respiratory_rate: Optional[int] = None
    oxygen_saturation: Optional[int] = None  # Percentage
    weight: Optional[float] = None  # kg
    height: Optional[float] = None  # cm
    pain_scale: Optional[int] = None  # 1-10 scale
    notes: Optional[str] = None


class NursingNoteCreate(BaseModel):
    """Create nursing note"""
    patient_ref: str
    note_type: str  # "ASSESSMENT", "INTERVENTION", "OBSERVATION", "MEDICATION_ADMIN", "DISCHARGE_PREP"
    note_content: str
    priority: Optional[str] = "NORMAL"  # "LOW", "NORMAL", "HIGH", "URGENT"
    follow_up_required: Optional[bool] = False


# ============================================================================
# OUTPUT SCHEMAS (Out/Response)
# ============================================================================

class OPDAppointmentOut(BaseModel):
    """OPD appointment response"""
    appointment_ref: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    department_name: str
    appointment_date: str
    appointment_time: str
    appointment_type: str
    status: str
    chief_complaint: Optional[str]
    is_checked_in: bool
    checked_in_at: Optional[str]
    created_at: str


class OPDPatientOut(BaseModel):
    """OPD patient response"""
    patient_ref: str
    patient_name: str
    phone: str
    email: Optional[str]
    date_of_birth: str
    gender: str
    address: Optional[str]
    emergency_contact: Optional[Dict[str, str]]
    total_appointments: int
    last_appointment_date: Optional[str]
    registration_date: str


class IPDPatientOut(BaseModel):
    """IPD patient response"""
    patient_ref: str
    patient_name: str
    admission_number: str
    admission_date: str
    admission_type: str
    department_name: str
    attending_doctor: str
    assigned_nurse: Optional[str]
    ward: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    current_condition: Optional[str]
    length_of_stay: int
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    is_active: bool


class IPDAdmissionDetailsOut(BaseModel):
    """Detailed IPD admission information"""
    admission_number: str
    patient_ref: str
    patient_name: str
    patient_age: int
    patient_gender: str
    admission_date: str
    admission_type: str
    department_name: str
    attending_doctor: str
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    admission_notes: Optional[str]
    ward: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    length_of_stay: int
    current_condition: Optional[str]
    vital_signs_summary: Dict[str, Any]
    current_medications: List[Dict[str, Any]]
    recent_assessments: List[Dict[str, Any]]


class PatientProfileViewOut(BaseModel):
    """Patient profile view for nurses"""
    patient_ref: str
    patient_name: str
    date_of_birth: str
    gender: str
    blood_group: Optional[str]
    allergies: List[str]
    chronic_conditions: List[str]
    current_medications: List[str]
    emergency_contact: Optional[Dict[str, str]]
    admission_status: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    attending_doctor: Optional[str]


class VitalSignsHistoryOut(BaseModel):
    """Vital signs history entry"""
    recorded_at: str
    recorded_by: str
    blood_pressure: Optional[str]
    pulse_rate: Optional[int]
    temperature: Optional[float]
    respiratory_rate: Optional[int]
    oxygen_saturation: Optional[int]
    weight: Optional[float]
    height: Optional[float]
    pain_scale: Optional[int]
    notes: Optional[str]


class NursingNoteOut(BaseModel):
    """Nursing note response"""
    note_id: str
    patient_ref: str
    patient_name: str
    note_type: str
    note_content: str
    priority: str
    follow_up_required: bool
    recorded_by: str
    recorded_at: str