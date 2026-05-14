"""
Nurse module schemas.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class NurseProfileUpsertRequest(BaseModel):
    department_id: str
    nurse_id: str
    nursing_license_number: str
    designation: str
    specialization: Optional[str] = None
    experience_years: int = 0
    qualifications: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    shift_type: str = "DAY"
    employment_type: str = "FULL_TIME"
    clinical_skills: List[str] = Field(default_factory=list)
    languages_spoken: List[str] = Field(default_factory=list)
    bio: Optional[str] = None
    is_active: bool = True


class NurseDashboardResponse(BaseModel):
    profile: Dict[str, Any]
    stats: Dict[str, Any]
    pending_tasks: Dict[str, Any]


class NurseVitalsCreateRequest(BaseModel):
    admission_number: str
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    pulse_rate: Optional[int] = None
    temperature_f: Optional[float] = None
    respiratory_rate: Optional[int] = None
    oxygen_saturation: Optional[int] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    pain_scale: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("temperature_f")
    @classmethod
    def validate_temperature(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 95 or v > 105:
            raise ValueError("temperature_f must be between 95 and 105")
        return v

    @field_validator("oxygen_saturation")
    @classmethod
    def validate_oxygen(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 50 or v > 100:
            raise ValueError("oxygen_saturation must be between 50 and 100")
        return v

    @field_validator("pulse_rate")
    @classmethod
    def validate_pulse(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 20 or v > 220:
            raise ValueError("pulse_rate must be between 20 and 220")
        return v

    @field_validator("respiratory_rate")
    @classmethod
    def validate_rr(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 5 or v > 60:
            raise ValueError("respiratory_rate must be between 5 and 60")
        return v

    @field_validator("pain_scale")
    @classmethod
    def validate_pain(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 10:
            raise ValueError("pain_scale must be between 0 and 10")
        return v

    @model_validator(mode="after")
    def require_any_vitals(self):
        values = [
            self.blood_pressure_systolic,
            self.blood_pressure_diastolic,
            self.pulse_rate,
            self.temperature_f,
            self.respiratory_rate,
            self.oxygen_saturation,
            self.weight,
            self.height,
            self.pain_scale,
        ]
        if all(v is None for v in values):
            raise ValueError("At least one vital field is required")
        return self


class NurseMedicationCreateRequest(BaseModel):
    admission_number: str
    medication_name: str
    dose: str
    scheduled_time: str
    frequency: str
    start_date: Optional[str] = None
    instructions: Optional[str] = None
    status: str = "PENDING"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"PENDING", "GIVEN", "MISSED", "DELAYED"}
        s = (v or "").strip().upper()
        if s not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return s

    @field_validator("scheduled_time")
    @classmethod
    def validate_scheduled_time(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError as e:
            raise ValueError("scheduled_time must be in HH:MM format") from e
        return v


class NurseBedCreateRequest(BaseModel):
    ward_id: str
    bed_number: str
    bed_code: str
    status: str = "AVAILABLE"
    bed_type: str = "STANDARD"
    floor: Optional[str] = None
    room_number: Optional[str] = None
    bed_position: Optional[str] = None
    has_oxygen: bool = False
    has_suction: bool = False
    has_cardiac_monitor: bool = False
    has_ventilator: bool = False
    has_iv_pole: bool = True
    daily_rate: float = 0
    notes: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class NurseLabRequestCreateRequest(BaseModel):
    admission_number: str
    test_type: str
    reason_for_test: Optional[str] = None
    priority: str = "ROUTINE"
    requesting_doctor: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        allowed = {"ROUTINE", "URGENT", "STAT"}
        s = (v or "").strip().upper()
        if s not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return s


class NurseNoteCreateRequest(BaseModel):
    admission_number: Optional[str] = None
    patient_ref: Optional[str] = None
    note_type: str
    observation_title: Optional[str] = None
    details: Optional[str] = None
    note_content: str
    priority: str = "NORMAL"
    follow_up_required: bool = False

    @field_validator("priority")
    @classmethod
    def validate_note_priority(cls, v: str) -> str:
        allowed = {"LOW", "NORMAL", "HIGH", "URGENT"}
        s = (v or "").strip().upper()
        if s not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return s


class NurseDischargeCreateRequest(BaseModel):
    admission_number: Optional[str] = None
    patient_ref: Optional[str] = None
    final_diagnosis: str
    secondary_diagnoses: List[str] = Field(default_factory=list)
    procedures_performed: List[str] = Field(default_factory=list)
    hospital_course: Optional[str] = None
    medications_on_discharge: List[Dict[str, Any]] = Field(default_factory=list)
    follow_up_instructions: Optional[str] = None
    diet_instructions: Optional[str] = None
    activity_restrictions: Optional[str] = None
    follow_up_date: Optional[str] = None
    follow_up_doctor: Optional[str] = None
    condition_on_discharge: Optional[str] = None
    discharge_notes: Optional[str] = None
