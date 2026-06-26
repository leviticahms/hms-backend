from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# =========================================================
# Search Lab Tests
# =========================================================

class LabTestSearchResponse(BaseModel):
    test_id: UUID
    test_code: str
    test_name: str
    category: Optional[str] = None
    sample_type: Optional[str] = None
    turnaround_time: Optional[str] = None
    price: Optional[float] = None
    is_available: bool = True


class LabTestSearchResponseWrapper(BaseModel):
    success: bool
    message: str
    data: List[LabTestSearchResponse]


# =========================================================
# Prescription Items
# =========================================================

class LabTestPrescriptionItemCreate(BaseModel):
    test_id: UUID
    test_name: Optional[str] = Field(
        None,
        description="Name of the test, e.g. '12 Lead ECG Machine'"
    )
    instructions: Optional[str] = None
    remarks: Optional[str] = None


class LabTestPrescriptionItemResponse(BaseModel):
    test_id: UUID
    test_name: str
    instructions: Optional[str] = None
    remarks: Optional[str] = None


# =========================================================
# Create Prescription
# =========================================================

class CreateLabTestPrescriptionRequest(BaseModel):
    patient_ref: str = Field(
        ...,
        description="Human-readable patient reference. Example: PAT-ALEX-997"
    )
    clinical_notes: Optional[str] = None
    tests: List[LabTestPrescriptionItemCreate]


class CreateLabTestPrescriptionData(BaseModel):
    prescription_id: UUID
    test_id: UUID
    test_name: str
    patient_ref: str
    status: str
    total_tests: int
    created_at: datetime


class CreateLabTestPrescriptionResponse(BaseModel):
    success: bool
    message: str
    data: CreateLabTestPrescriptionData


# =========================================================
# Update Prescription
# =========================================================

class UpdateLabTestPrescriptionRequest(BaseModel):
    clinical_notes: Optional[str] = None
    tests: Optional[List[LabTestPrescriptionItemCreate]] = None


class UpdatePrescriptionData(BaseModel):
    prescription_id: UUID
    patient_ref: str
    status: str
    total_tests: int
    test_names: List[str]
    updated_at: datetime


class UpdatePrescriptionResponse(BaseModel):
    success: bool
    message: str
    data: UpdatePrescriptionData


# =========================================================
# Cancel Prescription
# =========================================================

class CancelPrescriptionData(BaseModel):
    prescription_id: UUID
    status: str
    updated_at: datetime


class CancelPrescriptionResponse(BaseModel):
    success: bool
    message: str
    data: CancelPrescriptionData


# =========================================================
# Prescription Detail
# =========================================================

class PrescriptionDetailItem(BaseModel):
    test_id: UUID
    test_name: str
    instructions: Optional[str] = None
    remarks: Optional[str] = None


class PrescriptionDetail(BaseModel):
    prescription_id: UUID
    patient_ref: str
    clinical_notes: Optional[str] = None
    status: str
    tests: List[PrescriptionDetailItem]
    created_at: datetime
    updated_at: Optional[datetime] = None


class PrescriptionDetailResponse(BaseModel):
    success: bool
    message: str
    data: PrescriptionDetail


# =========================================================
# Doctor / Patient Prescription List
# =========================================================

class PrescriptionSummary(BaseModel):
    prescription_id: UUID
    patient_ref: Optional[str] = None
    status: str
    test_names: List[str]
    total_tests: int
    created_at: datetime


class PrescriptionListData(BaseModel):
    total: int
    page: int
    limit: int
    prescriptions: List[PrescriptionSummary]


class DoctorPrescriptionListResponse(BaseModel):
    success: bool
    message: str
    data: PrescriptionListData


class PatientPrescriptionListResponse(BaseModel):
    success: bool
    message: str
    data: PrescriptionListData