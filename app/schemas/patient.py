"""
Schemas for patient management models.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime
from app.schemas.base import BaseSchema, TenantBaseSchema, TimestampMixin
from app.core.enums import Gender, BloodGroup, AppointmentStatus, AdmissionType, DocumentType


# Patient Profile Schemas
class PatientProfileBase(BaseModel):
    """Base patient profile fields"""
    patient_id: str = Field(..., min_length=3, max_length=50)
    mrn: Optional[str] = Field(None, max_length=50)
    date_of_birth: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    gender: Gender
    blood_group: Optional[BloodGroup] = None
    blood_group_value: Optional[str] = Field(None, max_length=50)
    
    # Government / Facility ID
    id_type: Optional[str] = Field(None, max_length=50)
    id_number: Optional[str] = Field(None, max_length=100)
    id_name: Optional[str] = Field(None, max_length=255)
    
    # Contact details
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    district: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, pattern=r'^\d{5,10}$')
    
    # Emergency contact
    emergency_contact_name: Optional[str] = Field(None, max_length=100)
    emergency_contact_phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    emergency_contact_relation: Optional[str] = Field(None, max_length=50)
    
    # Medical information
    medical_history: Optional[str] = None
    allergies: Optional[List[str]] = Field(default_factory=list)
    chronic_conditions: Optional[List[str]] = Field(default_factory=list)
    current_medications: Optional[List[str]] = Field(default_factory=list)
    
    # Insurance
    insurance_provider: Optional[str] = Field(None, max_length=100)
    insurance_policy_number: Optional[str] = Field(None, max_length=100)
    insurance_expiry: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')


class PatientProfileCreate(PatientProfileBase):
    """Schema for creating a patient profile"""
    user_id: int


class PatientProfileUpdate(BaseModel):
    """Schema for updating a patient profile"""
    mrn: Optional[str] = Field(None, max_length=50)
    blood_group: Optional[BloodGroup] = None
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, pattern=r'^\d{5,10}$')
    emergency_contact_name: Optional[str] = Field(None, max_length=100)
    emergency_contact_phone: Optional[str] = Field(None, pattern=r'^\+?[\d\s\-\(\)]{10,20}$')
    emergency_contact_relation: Optional[str] = Field(None, max_length=50)
    allergies: Optional[List[str]] = None
    chronic_conditions: Optional[List[str]] = None
    current_medications: Optional[List[str]] = None
    insurance_provider: Optional[str] = Field(None, max_length=100)
    insurance_policy_number: Optional[str] = Field(None, max_length=100)
    insurance_expiry: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')


class PatientProfileResponse(PatientProfileBase, TenantBaseSchema, TimestampMixin):
    """Schema for patient profile API responses"""
    id: int
    user_id: int
    
    # User information
    full_name: Optional[str] = None
    user_email: Optional[str] = None
    user_phone: Optional[str] = None
    avatar_url: Optional[str] = None


# Appointment Schemas
class AppointmentBase(BaseModel):
    """Base appointment fields"""
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    appointment_time: str = Field(..., pattern=r'^\d{2}:\d{2}:\d{2}$')
    duration_minutes: int = Field(30, ge=15, le=180)
    appointment_type: str = Field("CONSULTATION", max_length=50)
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None
    consultation_fee: Optional[Decimal] = Field(None, ge=0, decimal_places=2)


class AppointmentCreate(AppointmentBase):
    """Schema for creating an appointment"""
    pass


class AppointmentUpdate(BaseModel):
    """Schema for updating an appointment"""
    appointment_date: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    appointment_time: Optional[str] = Field(None, pattern=r'^\d{2}:\d{2}:\d{2}$')
    duration_minutes: Optional[int] = Field(None, ge=15, le=180)
    status: Optional[AppointmentStatus] = None
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None
    cancellation_reason: Optional[str] = None


class AppointmentResponse(AppointmentBase, TenantBaseSchema, TimestampMixin):
    """Schema for appointment API responses"""
    id: int
    status: AppointmentStatus
    checked_in_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    is_paid: bool
    
    # Related information
    patient_name: Optional[str] = None
    doctor_name: Optional[str] = None
    department_name: Optional[str] = None


# Medical Record Schemas
class MedicalRecordBase(BaseModel):
    """Base medical record fields"""
    patient_id: int
    doctor_id: int
    appointment_id: Optional[int] = None
    chief_complaint: str = Field(..., min_length=5)
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[str] = None
    examination_findings: Optional[str] = None
    vital_signs: Optional[Dict[str, Any]] = Field(default_factory=dict)
    diagnosis: Optional[str] = None
    differential_diagnosis: Optional[List[str]] = Field(default_factory=list)
    treatment_plan: Optional[str] = None
    follow_up_instructions: Optional[str] = None
    prescriptions: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    lab_orders: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    imaging_orders: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


class MedicalRecordCreate(MedicalRecordBase):
    """Schema for creating a medical record"""
    pass


class MedicalRecordUpdate(BaseModel):
    """Schema for updating a medical record"""
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[str] = None
    examination_findings: Optional[str] = None
    vital_signs: Optional[Dict[str, Any]] = None
    diagnosis: Optional[str] = None
    differential_diagnosis: Optional[List[str]] = None
    treatment_plan: Optional[str] = None
    follow_up_instructions: Optional[str] = None
    prescriptions: Optional[List[Dict[str, Any]]] = None
    lab_orders: Optional[List[Dict[str, Any]]] = None
    imaging_orders: Optional[List[Dict[str, Any]]] = None


class MedicalRecordResponse(MedicalRecordBase, TenantBaseSchema, TimestampMixin):
    """Schema for medical record API responses"""
    id: int
    is_finalized: bool
    finalized_at: Optional[datetime] = None
    
    # Related information
    patient_name: Optional[str] = None
    doctor_name: Optional[str] = None


# Admission Schemas
class AdmissionBase(BaseModel):
    """Base admission fields"""
    patient_id: int
    doctor_id: int
    department_id: int
    admission_number: str = Field(..., min_length=5, max_length=50)
    admission_type: AdmissionType
    admission_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
    chief_complaint: str = Field(..., min_length=5)
    provisional_diagnosis: Optional[str] = None
    admission_notes: Optional[str] = None
    
    # Bed assignment (for IPD)
    ward: Optional[str] = Field(None, max_length=100)
    room_number: Optional[str] = Field(None, max_length=20)
    bed_number: Optional[str] = Field(None, max_length=20)


class AdmissionCreate(AdmissionBase):
    """Schema for creating an admission"""
    pass


class AdmissionUpdate(BaseModel):
    """Schema for updating an admission"""
    provisional_diagnosis: Optional[str] = None
    admission_notes: Optional[str] = None
    ward: Optional[str] = Field(None, max_length=100)
    room_number: Optional[str] = Field(None, max_length=20)
    bed_number: Optional[str] = Field(None, max_length=20)
    discharge_date: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
    discharge_type: Optional[str] = Field(None, max_length=50)


class AdmissionResponse(AdmissionBase, TenantBaseSchema, TimestampMixin):
    """Schema for admission API responses"""
    id: int
    discharge_date: Optional[datetime] = None
    discharge_type: Optional[str] = None
    
    # Related information
    patient_name: Optional[str] = None
    doctor_name: Optional[str] = None
    department_name: Optional[str] = None


# Patient Document Schemas
class PatientDocumentBase(BaseModel):
    """Base patient document fields"""
    patient_id: int
    document_type: DocumentType
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    document_date: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    is_sensitive: bool = True


class PatientDocumentCreate(PatientDocumentBase):
    """Schema for creating a patient document"""
    file_name: str = Field(..., min_length=1, max_length=255)
    file_path: str = Field(..., min_length=1, max_length=500)
    file_size: Optional[int] = Field(None, ge=0)
    mime_type: Optional[str] = Field(None, max_length=100)


class PatientDocumentUpdate(BaseModel):
    """Schema for updating a patient document"""
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    document_date: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')


class PatientDocumentResponse(PatientDocumentBase, TenantBaseSchema, TimestampMixin):
    """Schema for patient document API responses"""
    id: int
    uploaded_by: int
    file_name: str
    file_path: str
    file_size: Optional[int]
    mime_type: Optional[str]
    
    # Related information
    uploader_name: Optional[str] = None