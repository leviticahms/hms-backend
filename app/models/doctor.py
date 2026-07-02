"""
Doctor portal models.
Manages doctor profiles, schedules, prescriptions, and treatment plans.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, Text, Boolean, Time, DECIMAL, DateTime
from sqlalchemy.orm import relationship
from app.core.database_types import JSON_TYPE, UUID_TYPE
from app.models.base import TenantBaseModel
from app.core.enums import DayOfWeek


class DoctorProfile(TenantBaseModel):
    """
    Extended profile for doctors.
    Links to User model for authentication and basic info.
    """
    __tablename__ = "doctor_profiles"
    
    user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False, unique=True)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=False)
    
    # Professional identification
    doctor_id = Column(String(50), nullable=False)  # Hospital-specific doctor ID
    medical_license_number = Column(String(100), nullable=False, unique=True)
    
    # Professional details
    designation = Column(String(100), nullable=False)  # "Senior Consultant", "Resident"
    specialization = Column(String(255), nullable=False)
    sub_specialization = Column(String(255))
    
    # Experience and qualifications
    experience_years = Column(Integer, default=0)
    qualifications = Column(JSON_TYPE, nullable=False, default=lambda: [])  # ["MBBS", "MD", "DM"]
    certifications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Professional memberships
    medical_associations = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Consultation details
    consultation_fee = Column(DECIMAL(10, 2), nullable=False)
    follow_up_fee = Column(DECIMAL(10, 2))
    consultation_type = Column(String(100))  # e.g. IN_PERSON, ONLINE, HYBRID
    availability_time = Column(Text)  # e.g. "Mon-Fri 09:00-17:00" or JSON string
    
    # Availability
    is_available_for_emergency = Column(Boolean, default=False)
    is_accepting_new_patients = Column(Boolean, default=True)
    
    # Profile information
    bio = Column(Text)
    languages_spoken = Column(JSON_TYPE, nullable=False, default=lambda: ["English"])
    
    # Relationships
    user = relationship("User")
    department = relationship("Department", back_populates="doctor_profiles")
    # Note: appointments, medical_records, admissions now link directly to users.id, not doctor_profiles.id
    prescriptions = relationship("Prescription", back_populates="doctor")
    treatment_plans = relationship("TreatmentPlan", back_populates="doctor")
    
    def __repr__(self):
        return f"<DoctorProfile(id={self.id}, doctor_id='{self.doctor_id}', specialization='{self.specialization}')>"


class Prescription(TenantBaseModel):
    """
    Digital prescriptions created by doctors.
    Supports structured medication data and e-prescribing.
    """
    __tablename__ = "prescriptions"
    
    # Links
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("doctor_profiles.id"), nullable=False)
    appointment_id = Column(UUID_TYPE, ForeignKey("appointments.id"))
    medical_record_id = Column(UUID_TYPE, ForeignKey("medical_records.id"))
    
    # Prescription details
    prescription_number = Column(String(50), nullable=False, unique=True)
    prescription_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    
    # Clinical information
    diagnosis = Column(Text)
    symptoms = Column(Text)
    
    # Medications (structured data)
    medications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    # Format: [{"name": "Paracetamol", "dosage": "500mg", "frequency": "TID", "duration": "5 days", "instructions": "After meals"}]
    
    # Instructions
    general_instructions = Column(Text)
    diet_instructions = Column(Text)
    follow_up_date = Column(String(10))  # YYYY-MM-DD
    
    # Prescription status
    is_dispensed = Column(Boolean, default=False)
    dispensed_at = Column(String(19))  # YYYY-MM-DD HH:MM:SS
    dispensed_by = Column(UUID_TYPE, ForeignKey("users.id"))
    
    # Digital signature
    is_digitally_signed = Column(Boolean, default=False)
    signature_hash = Column(String(255))
    
    # Relationships
    patient = relationship("PatientProfile")
    doctor = relationship("DoctorProfile", back_populates="prescriptions")
    appointment = relationship("Appointment")
    medical_record = relationship("MedicalRecord")
    dispenser = relationship("User", foreign_keys=[dispensed_by])
    notifications = relationship("PrescriptionNotification", back_populates="prescription", foreign_keys="PrescriptionNotification.prescription_id")

    def __repr__(self):
        return f"<Prescription(id={self.id}, number='{self.prescription_number}', date='{self.prescription_date}')>"


class PrescriptionNotification(TenantBaseModel):
    """
    In-app notification for prescription events (submit, dispensed).
    No SMS/email; store only. Patient, Receptionist, Pharmacy can be notified.
    """
    __tablename__ = "prescription_notifications"

    recipient_user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False, index=True)
    prescription_id = Column(UUID_TYPE, ForeignKey("prescriptions.id"), nullable=False, index=True)
    event_type = Column(String(40), nullable=False, index=True)  # PRESCRIPTION_SUBMITTED, PRESCRIPTION_DISPENSED
    title = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    recipient = relationship("User", foreign_keys=[recipient_user_id])
    prescription = relationship("Prescription", back_populates="notifications")

    def __repr__(self):
        return f"<PrescriptionNotification(id={self.id}, recipient={self.recipient_user_id}, event={self.event_type})>"


class TreatmentPlan(TenantBaseModel):
    """
    Comprehensive treatment plans for patients.
    Supports long-term care planning and progress tracking.
    """
    __tablename__ = "treatment_plans"
    
    # Links
    patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"), nullable=False)
    doctor_id = Column(UUID_TYPE, ForeignKey("doctor_profiles.id"), nullable=False)
    
    # Plan details
    plan_name = Column(String(255), nullable=False)
    primary_diagnosis = Column(Text, nullable=False)
    secondary_diagnoses = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Treatment goals
    short_term_goals = Column(JSON_TYPE, nullable=False, default=lambda: [])
    long_term_goals = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Treatment components
    medications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    procedures = Column(JSON_TYPE, nullable=False, default=lambda: [])
    therapies = Column(JSON_TYPE, nullable=False, default=lambda: [])
    tests = Column(JSON_TYPE, nullable=False, default=lambda: [])
    lifestyle_modifications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Timeline
    start_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    expected_end_date = Column(String(10))
    review_frequency = Column(String(50))  # "Weekly", "Monthly", "Quarterly"
    
    # Progress tracking
    milestones = Column(JSON_TYPE, nullable=False, default=lambda: [])
    progress_notes = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Plan status
    status = Column(String(20), default="ACTIVE")  # ACTIVE, COMPLETED, DISCONTINUED
    completion_date = Column(String(10))
    completion_notes = Column(Text)
    
    # Relationships
    patient = relationship("PatientProfile")
    doctor = relationship("DoctorProfile", back_populates="treatment_plans")
    
    def __repr__(self):
        return f"<TreatmentPlan(id={self.id}, name='{self.plan_name}', status='{self.status}')>"