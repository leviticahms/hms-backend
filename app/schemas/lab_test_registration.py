from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ==========================================================
# ENUMS
# ==========================================================

TestStatus = Literal[
    "SAMPLE_PENDING",
    "ACCEPTED",
    "REJECTED",
    "SAMPLE_COLLECTED",
    "IN_PROGRESS",
    "COMPLETED",
]

PriorityType = Literal[
    "ROUTINE",
    "URGENT",
]

# ==========================================================
# DOCTOR ASSIGN TEST
# ==========================================================

class AssignLabTestItem(BaseModel):
    test_name: str = Field(..., max_length=120)
    sample_type: str = Field(..., max_length=50)
    priority: PriorityType = "ROUTINE"
    instructions: Optional[str] = None


class AssignLabTestRequest(BaseModel):
    patient_ref: str
    patient_name: str
    doctor_name: str
    tests: List[AssignLabTestItem]


class AssignLabTestResponse(BaseModel):
    success: bool
    message: str
    total_tests: int


# ==========================================================
# LAB REGISTRATION GRID
# ==========================================================

class TestRegistrationRow(BaseModel):

    test_id: str

    patient_ref: Optional[str]

    patient_name: str

    doctor_name: Optional[str]

    test_type: str

    sample_type: str

    priority: PriorityType

    registered_date: date

    status: TestStatus

    # Enriched from PatientProfile / User (joined via patient_ref == str(User.id))
    date_of_birth: Optional[str] = None

    gender: Optional[str] = None

    phone: Optional[str] = None

    email: Optional[str] = None


class TestRegistrationSummary(BaseModel):

    total_tests_today: int = 0

    completed_tests: int = 0

    in_progress_tests: int = 0

    rejected_tests: int = 0

    urgent_tests: int = 0


class TestRegistrationMeta(BaseModel):

    generated_at: datetime

    for_date: date

    live_data: bool = True

    demo_data: bool = False


class TestRegistrationListResponse(BaseModel):

    meta: TestRegistrationMeta

    summary: TestRegistrationSummary

    rows: List[TestRegistrationRow]

    model_config = ConfigDict(from_attributes=True)


# ==========================================================
# LAB STATUS UPDATE
# ==========================================================

class UpdateTestRegistrationStatusRequest(BaseModel):

    status: TestStatus

    reason: Optional[str] = None


class UpdateTestRegistrationStatusResponse(BaseModel):

    message: str

    test_id: str

    status: TestStatus


# ==========================================================
# DOCTOR LIST
# ==========================================================

class DoctorAssignedTest(BaseModel):

    test_id: str

    patient_ref: str

    patient_name: str

    doctor_name: Optional[str]

    test_type: str

    sample_type: str

    priority: PriorityType

    status: TestStatus

    reject_reason: Optional[str] = None


class DoctorAssignedTestResponse(BaseModel):

    total: int

    tests: List[DoctorAssignedTest]


# ==========================================================
# REASSIGN
# ==========================================================

class ReassignTestRequest(BaseModel):

    test_name: str

    sample_type: str

    priority: PriorityType = "ROUTINE"

    instructions: Optional[str] = None


class ReassignTestResponse(BaseModel):

    success: bool

    message: str

    new_test_id: str


# ==========================================================
# PATIENT SEARCH (used by lab portal shortcuts: GET /lab/patients)
# ==========================================================

class LabPatientSearchItem(BaseModel):
    """Single autocomplete suggestion for the lab 'Register New Test' form."""

    patient_ref: str = Field(..., description="Patient's user id (string form), used as patient_ref on registration.")
    patient_name: str = Field(..., description="Patient's full name.")
    contact_number: Optional[str] = Field(None, description="Patient's contact number, if available.")

    model_config = ConfigDict(from_attributes=True)


class LabPatientSearchResponse(BaseModel):
    total: int
    patients: List[LabPatientSearchItem]