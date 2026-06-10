"""
Hospital administration models.
Manages departments, staff, and organizational structure.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, Text, Boolean, Time, DateTime, DECIMAL
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import TenantBaseModel
from app.core.database_types import JSON_TYPE, UUID_TYPE


class Department(TenantBaseModel):
    """
    Hospital departments for organizational structure.
    Examples: Cardiology, Orthopedics, Emergency, etc.
    """
    __tablename__ = "departments"
    
    name = Column(String(100), nullable=False)
    code = Column(String(20), nullable=False)  # e.g., "CARD", "ORTHO"
    description = Column(Text)
    
    # Department details
    head_doctor_id = Column(UUID_TYPE, ForeignKey("users.id"))
    location = Column(String(100))  # Floor/Wing/Building
    phone = Column(String(20))
    email = Column(String(255))
    
    # Operational details
    is_emergency = Column(Boolean, default=False)
    is_icu = Column(Boolean, default=False)
    bed_capacity = Column(Integer, default=0)
    
    # Working hours
    opening_time = Column(Time)
    closing_time = Column(Time)
    is_24x7 = Column(Boolean, default=False)
    
    # Metadata
    settings = Column(JSON_TYPE, nullable=False, default=lambda: {})
    
    # Relationships
    hospital = relationship("Hospital", back_populates="departments")
    head_doctor = relationship("User", foreign_keys=[head_doctor_id])
    staff_profiles = relationship("StaffProfile", back_populates="department")
    doctor_profiles = relationship("DoctorProfile", back_populates="department")
    nurse_profiles = relationship("NurseProfile", back_populates="department")
    receptionist_profiles = relationship("ReceptionistProfile", back_populates="department")
    appointments = relationship("Appointment", back_populates="department")
    
    def __repr__(self):
        return f"<Department(id={self.id}, name='{self.name}', hospital_id={self.hospital_id})>"


class StaffProfile(TenantBaseModel):
    """
    Extended profile for hospital staff (non-doctor users).
    Links to User model for authentication and basic info.
    """
    __tablename__ = "staff_profiles"
    
    user_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False, unique=True)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=False)
    
    # Employment details
    employee_id = Column(String(50), nullable=False)
    staff_name = Column(String(255), nullable=False, index=True)
    designation = Column(String(100), nullable=False)
    joining_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    
    # Professional details
    qualification = Column(String(255))
    experience_years = Column(Integer, default=0)
    specialization = Column(String(255))
    
    # Contact details
    emergency_contact_name = Column(String(100))
    emergency_contact_phone = Column(String(20))
    emergency_contact_relation = Column(String(50))
    
    # Employment status
    is_full_time = Column(Boolean, default=True)
    salary = Column(String(20))  # Encrypted/hashed
    
    # Metadata
    skills = Column(JSON_TYPE, nullable=False, default=lambda: [])  # ["nursing", "patient_care"]
    certifications = Column(JSON_TYPE, nullable=False, default=lambda: [])
    
    # Relationships
    user = relationship("User")
    department = relationship("Department", back_populates="staff_profiles")
    
    def __repr__(self):
        return f"<StaffProfile(id={self.id}, employee_id='{self.employee_id}', hospital_id={self.hospital_id})>"


class Ward(TenantBaseModel):
    """
    Hospital wards/units for bed management.
    Examples: ICU, General Ward, Emergency, Private Rooms, etc.
    """
    __tablename__ = "wards"
    
    name = Column(String(100), nullable=False)
    code = Column(String(100), nullable=False)  # e.g., "ICU-1", "GEN-A", "CARDIOLOGY_INTENSIVE_CARE_UNIT_F3"
    ward_type = Column(String(20), nullable=False)  # Maps to WardType enum
    description = Column(Text)
    
    # Ward details
    floor = Column(String(10))  # Floor number/name
    building = Column(String(50))  # Building name/code
    location_details = Column(String(255))  # Detailed location
    
    # Capacity and staffing
    total_beds = Column(Integer, default=0)
    nurse_station_phone = Column(String(20))
    head_nurse_id = Column(UUID_TYPE, ForeignKey("users.id"))
    
    # Operational details
    is_isolation_ward = Column(Boolean, default=False)
    is_emergency_accessible = Column(Boolean, default=True)
    visiting_hours_start = Column(Time)
    visiting_hours_end = Column(Time)
    
    # Equipment and facilities
    has_oxygen_supply = Column(Boolean, default=False)
    has_suction = Column(Boolean, default=False)
    has_cardiac_monitor = Column(Boolean, default=False)
    has_ventilator_support = Column(Boolean, default=False)
    
    # Metadata
    settings = Column(JSON_TYPE, nullable=False, default=lambda: {})
    
    # Relationships
    hospital = relationship("Hospital", back_populates="wards")
    head_nurse = relationship("User", foreign_keys=[head_nurse_id])
    beds = relationship("Bed", back_populates="ward", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Ward(id={self.id}, name='{self.name}', type='{self.ward_type}', hospital_id={self.hospital_id})>"


class StaffDepartmentAssignment(TenantBaseModel):
    """
    Assignment of staff members to departments.
    Mandatory for staff to work within the hospital - staff can only operate within assigned departments.
    """
    __tablename__ = "staff_department_assignments"
    
    staff_id = Column(UUID_TYPE, ForeignKey("users.id"), nullable=False)
    department_id = Column(UUID_TYPE, ForeignKey("departments.id"), nullable=False)
    
    # Assignment details
    is_primary = Column(Boolean, default=True)  # Primary department for the staff member
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True))  # Null means currently active
    
    # Assignment metadata
    notes = Column(Text)  # Assignment notes
    unassignment_reason = Column(Text)  # Reason for unassignment
    
    # Relationships
    staff = relationship("User", foreign_keys=[staff_id])
    department = relationship("Department", foreign_keys=[department_id])
    
    def __repr__(self):
        return f"<StaffDepartmentAssignment(staff_id={self.staff_id}, department_id={self.department_id}, is_active={self.is_active})>"


class Bed(TenantBaseModel):
    """
    Individual beds within hospital wards.
    Tracks availability, status, and patient assignments.
    """
    __tablename__ = "beds"
    
    ward_id = Column(UUID_TYPE, ForeignKey("wards.id"), nullable=False)
    
    # Bed identification
    bed_number = Column(String(20), nullable=False)  # e.g., "101", "A-12"
    bed_code = Column(String(50), nullable=False)  # Unique code: "ICU-1-BED-01"
    
    # Bed status and type
    status = Column(String(20), nullable=False, default="AVAILABLE")  # Maps to BedStatus enum
    bed_type = Column(String(20), default="STANDARD")  # STANDARD, ICU, ISOLATION, PRIVATE
    
    # Physical details
    floor = Column(String(10))
    room_number = Column(String(20))
    bed_position = Column(String(20))  # Window, Door, Center, etc.
    
    # Equipment and features
    has_oxygen = Column(Boolean, default=False)
    has_suction = Column(Boolean, default=False)
    has_cardiac_monitor = Column(Boolean, default=False)
    has_ventilator = Column(Boolean, default=False)
    has_iv_pole = Column(Boolean, default=True)
    
    # Occupancy tracking
    current_patient_id = Column(UUID_TYPE, ForeignKey("patient_profiles.id"))
    occupied_since = Column(DateTime(timezone=True))
    last_cleaned = Column(DateTime(timezone=True))
    maintenance_notes = Column(Text)
    
    # Pricing (for private beds)
    daily_rate = Column(DECIMAL(10, 2), default=0)
    
    # Metadata
    notes = Column(Text)
    settings = Column(JSON_TYPE, nullable=False, default=lambda: {})
    
    # Relationships
    ward = relationship("Ward", back_populates="beds")
    current_patient = relationship("PatientProfile", foreign_keys=[current_patient_id])
    
    def __repr__(self):
        return f"<Bed(id={self.id}, code='{self.bed_code}', status='{self.status}', ward_id={self.ward_id})>"
    

    #123