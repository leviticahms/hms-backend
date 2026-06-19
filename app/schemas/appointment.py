"""
Pydantic schemas for appointment management.
"""
from datetime import datetime, time
from typing import Optional, List
from pydantic import BaseModel, Field, validator
from decimal import Decimal

from app.core.enums import AppointmentStatus, UserRole


class AppointmentCreate(BaseModel):
    """Schema for creating an appointment"""
    doctor_slug: str = Field(..., description="Doctor identifier (doctor_id or username)")
    appointment_date: str = Field(..., description="Appointment date (YYYY-MM-DD)")
    appointment_time: str = Field(..., description="Appointment time (HH:MM:SS)")
    chief_complaint: str = Field(..., description="Patient's chief complaint")
    duration_minutes: Optional[int] = Field(30, description="Appointment duration in minutes")
    
    @validator('appointment_date')
    def validate_date_format(cls, v):
        try:
            datetime.fromisoformat(v)
            return v
        except ValueError:
            raise ValueError('Date must be in YYYY-MM-DD format')
    
    @validator('appointment_time')
    def validate_time_format(cls, v):
        try:
            datetime.strptime(v, '%H:%M:%S').time()
            return v
        except ValueError:
            raise ValueError('Time must be in HH:MM:SS format')


class AppointmentUpdate(BaseModel):
    """Schema for updating appointment status"""
    status: AppointmentStatus = Field(..., description="New appointment status")
    cancellation_reason: Optional[str] = Field(None, description="Reason for cancellation")


class AppointmentReschedule(BaseModel):
    """Schema for rescheduling an appointment"""
    new_date: str = Field(..., description="New appointment date (YYYY-MM-DD)")
    new_time: str = Field(..., description="New appointment time (HH:MM:SS)")
    
    @validator('new_date')
    def validate_date_format(cls, v):
        try:
            datetime.fromisoformat(v)
            return v
        except ValueError:
            raise ValueError('Date must be in YYYY-MM-DD format')
    
    @validator('new_time')
    def validate_time_format(cls, v):
        try:
            datetime.strptime(v, '%H:%M:%S').time()
            return v
        except ValueError:
            raise ValueError('Time must be in HH:MM:SS format')


class DoctorReassign(BaseModel):
    """Schema for reassigning appointment to different doctor"""
    new_doctor_ref: str = Field(..., description="New doctor ref (e.g. DOC-xxx) or doctor name")


class UserInfo(BaseModel):
    """Basic user information"""
    id: str
    username: str
    first_name: str
    last_name: str
    email: str


class PatientInfo(BaseModel):
    """Patient information for appointments"""
    id: str
    patient_id: str
    user: UserInfo


class DoctorInfo(BaseModel):
    """Doctor information for appointments"""
    id: str
    doctor_id: str
    designation: str
    specialization: str
    consultation_fee: Decimal
    user: UserInfo


class DepartmentInfo(BaseModel):
    """Department information"""
    id: str
    name: str
    description: Optional[str]


class AppointmentResponse(BaseModel):
    """Schema for appointment response"""
    id: str
    appointment_ref: str
    patient: PatientInfo
    doctor: DoctorInfo
    department: DepartmentInfo
    appointment_date: str
    appointment_time: str
    duration_minutes: int
    status: AppointmentStatus
    appointment_type: str
    chief_complaint: Optional[str]
    notes: Optional[str]
    consultation_fee: Optional[Decimal]
    is_paid: bool
    created_by_role: str
    created_at: datetime
    updated_at: datetime
    checked_in_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]

    class Config:
        from_attributes = True


class AppointmentListResponse(BaseModel):
    """Schema for appointment list response"""
    appointments: List[AppointmentResponse]
    total: int
    page: int
    per_page: int


class TimeSlot(BaseModel):
    """Schema for available time slots"""
    time: str = Field(..., description="Time slot (HH:MM:SS)")
    is_available: bool = Field(..., description="Whether the slot is available")
    duration_minutes: int = Field(..., description="Slot duration in minutes")


class DoctorSlotsResponse(BaseModel):
    """Schema for doctor available slots response"""
    doctor_id: str
    doctor_name: str
    date: str
    slots: List[TimeSlot]


class AppointmentStatsResponse(BaseModel):
    """Schema for appointment statistics"""
    total_appointments: int
    requested: int
    confirmed: int
    completed: int
    cancelled: int
    today_appointments: int
    upcoming_appointments: int
    

# class CheckedInPatientResponse(BaseModel):
#     appointment_ref: str
#     patient_id: str
#     patient_name: str
#     phone: str
#     doctor_name: str
#     department: str
#     appointment_date: str
#     appointment_time: str
#     checked_in_at: Optional[datetime]


# class CheckedInPatientsListResponse(BaseModel):
#     patients: List[CheckedInPatientResponse]