"""
Patient care schemas for medical records, appointments, documents, and discharge summaries.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============================================================================
# SHARED MODELS
# ============================================================================

class VitalSigns(BaseModel):
    """Vital signs data structure"""
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    pulse_rate: Optional[int] = None
    temperature: Optional[float] = None
    respiratory_rate: Optional[int] = None
    oxygen_saturation: Optional[int] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    bmi: Optional[float] = None


# ============================================================================
# APPOINTMENT INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class AppointmentBookingCreate(BaseModel):
    """Patient appointment booking request - Requires authentication. Uses patient's hospital (assigned at registration)."""
    department_name: str
    doctor_name: str
    appointment_date: str
    appointment_time: str
    chief_complaint: str


class AppointmentCancellationCreate(BaseModel):
    """Appointment cancellation request"""
    cancellation_reason: str


class PatientAppointmentUpdate(BaseModel):
    """Patient portal: reschedule or update booking (send only fields to change)."""

    department_name: Optional[str] = None
    doctor_name: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    chief_complaint: Optional[str] = None


# ============================================================================
# MEDICAL RECORD INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class MedicalRecordCreate(BaseModel):
    """Create new medical record"""
    patient_ref: str  # Patient reference like PAT-STAR-123
    appointment_ref: Optional[str] = None  # Link to appointment if exists
    chief_complaint: str
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[str] = None
    examination_findings: Optional[str] = None
    vital_signs: Optional[VitalSigns] = None
    diagnosis: Optional[str] = None
    differential_diagnosis: Optional[List[str]] = None
    treatment_plan: Optional[str] = None
    follow_up_instructions: Optional[str] = None
    prescriptions: Optional[List[Dict[str, Any]]] = None
    lab_orders: Optional[List[Dict[str, Any]]] = None
    imaging_orders: Optional[List[Dict[str, Any]]] = None


class MedicalRecordUpdate(BaseModel):
    """Update existing medical record"""
    chief_complaint: Optional[str] = None
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[str] = None
    examination_findings: Optional[str] = None
    vital_signs: Optional[VitalSigns] = None
    diagnosis: Optional[str] = None
    differential_diagnosis: Optional[List[str]] = None
    treatment_plan: Optional[str] = None
    follow_up_instructions: Optional[str] = None
    prescriptions: Optional[List[Dict[str, Any]]] = None
    lab_orders: Optional[List[Dict[str, Any]]] = None
    imaging_orders: Optional[List[Dict[str, Any]]] = None


# ============================================================================
# DOCUMENT INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class DocumentUpdate(BaseModel):
    """Document metadata update request"""
    title: Optional[str] = None
    description: Optional[str] = None
    document_type: Optional[str] = None
    document_date: Optional[str] = None
    is_sensitive: Optional[bool] = None


# ============================================================================
# DISCHARGE SUMMARY INPUT SCHEMAS (Create/Update/Filter)
# ============================================================================

class DischargeSummaryCreate(BaseModel):
    """Create discharge summary request"""
    admission_number: str  # Link to existing admission
    final_diagnosis: str
    secondary_diagnoses: Optional[List[str]] = None
    procedures_performed: Optional[List[Dict[str, Any]]] = None
    hospital_course: Optional[str] = None
    medications_on_discharge: Optional[List[Dict[str, Any]]] = None
    follow_up_instructions: Optional[str] = None
    diet_instructions: Optional[str] = None
    activity_restrictions: Optional[str] = None
    follow_up_date: Optional[str] = None  # YYYY-MM-DD
    follow_up_doctor: Optional[str] = None
    discharge_type: Optional[str] = "NORMAL"  # NORMAL, LAMA, DEATH, TRANSFER


class DischargeSummaryUpdate(BaseModel):
    """Update discharge summary request"""
    final_diagnosis: Optional[str] = None
    secondary_diagnoses: Optional[List[str]] = None
    procedures_performed: Optional[List[Dict[str, Any]]] = None
    hospital_course: Optional[str] = None
    medications_on_discharge: Optional[List[Dict[str, Any]]] = None
    follow_up_instructions: Optional[str] = None
    diet_instructions: Optional[str] = None
    activity_restrictions: Optional[str] = None
    follow_up_date: Optional[str] = None
    follow_up_doctor: Optional[str] = None


# ============================================================================
# OUTPUT SCHEMAS (Out/Response)
# ============================================================================

class PatientMedicalSummaryOut(BaseModel):
    """Comprehensive patient medical summary"""
    patient_ref: str
    patient_name: str
    date_of_birth: str
    gender: str
    blood_group: Optional[str]
    allergies: List[str]
    chronic_conditions: List[str]
    current_medications: List[str]
    emergency_contact: Dict[str, str]  # Always present with string values
    total_visits: int
    last_visit_date: Optional[str]
    active_conditions: List[str]


class MedicalRecordOut(BaseModel):
    """Medical record response"""
    id: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    department_name: str
    appointment_ref: Optional[str]
    visit_date: str
    chief_complaint: str
    diagnosis: Optional[str]
    treatment_plan: Optional[str]
    vital_signs: Optional[Dict[str, Any]]
    prescriptions: List[Dict[str, Any]]
    is_finalized: bool
    created_at: str


class MedicalHistoryTimelineOut(BaseModel):
    """Medical history timeline entry"""
    date: str
    type: str  # "appointment", "admission", "discharge", "lab_result"
    title: str
    description: str
    doctor_name: Optional[str]
    department_name: Optional[str]
    status: Optional[str]


class DocumentUploadOut(BaseModel):
    """Document upload response"""
    document_id: str
    patient_ref: str
    document_type: str
    title: str
    file_name: str
    file_size: int
    upload_date: str
    message: str


class DocumentListOut(BaseModel):
    """Document list item response"""
    document_id: str
    document_type: str
    title: str
    description: Optional[str]
    file_name: str
    file_size: int
    mime_type: Optional[str]
    document_date: Optional[str]
    upload_date: str
    uploaded_by: str
    is_sensitive: bool


class DischargeSummaryOut(BaseModel):
    """Discharge summary response"""
    summary_id: str
    admission_number: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    department_name: str
    admission_date: str
    discharge_date: str
    length_of_stay: int
    chief_complaint: str
    final_diagnosis: str
    secondary_diagnoses: List[str]
    procedures_performed: List[Dict[str, Any]]
    hospital_course: Optional[str]
    medications_on_discharge: List[Dict[str, Any]]
    follow_up_instructions: Optional[str]
    diet_instructions: Optional[str]
    activity_restrictions: Optional[str]
    follow_up_date: Optional[str]
    follow_up_doctor: Optional[str]
    is_finalized: bool
    finalized_at: Optional[str]
    created_at: str


class AdmissionForDischargeOut(BaseModel):
    """Admission ready for discharge"""
    admission_id: str
    admission_number: str
    patient_ref: str
    patient_name: str
    doctor_name: str
    department_name: str
    admission_date: str
    admission_type: str
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    ward: Optional[str]
    room_number: Optional[str]
    bed_number: Optional[str]
    length_of_stay: int
    has_discharge_summary: bool


class DischargeSummaryTemplateOut(BaseModel):
    """Discharge summary template with pre-filled data"""
    admission_number: str
    patient_ref: str
    patient_name: str
    admission_date: str
    discharge_date: str
    length_of_stay: int
    chief_complaint: str
    provisional_diagnosis: Optional[str]
    medical_records_summary: List[Dict[str, Any]]
    suggested_final_diagnosis: Optional[str]
    recent_medications: List[Dict[str, Any]]
    recent_procedures: List[Dict[str, Any]]