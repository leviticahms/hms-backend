from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session as get_db
from app.core.enums import UserRole
from app.dependencies.auth import require_roles

from app.models.user import User

from app.schemas.lab_test_prescription import (
    LabTestSearchResponse,
    CreateLabTestPrescriptionRequest,
    CreateLabTestPrescriptionResponse,
    UpdateLabTestPrescriptionRequest,
    UpdatePrescriptionResponse,
    CancelPrescriptionResponse,
    PrescriptionDetailResponse,
    DoctorPrescriptionListResponse,
    PatientPrescriptionListResponse,
)

from app.services.lab_test_prescription_service import (
    LabTestPrescriptionService,
)

router = APIRouter(
    prefix="/lab-test-prescription",
    tags=["Lab Test Prescription"],
)

# ------------------------------------------------------------------
# Role Dependencies
# ------------------------------------------------------------------

_doctor_only = require_roles(UserRole.DOCTOR)

_patient_only = require_roles(UserRole.PATIENT)

_all_access = require_roles(
    UserRole.DOCTOR,
    UserRole.PATIENT,
    UserRole.LAB_TECH,
    UserRole.HOSPITAL_ADMIN,
)

# ------------------------------------------------------------------
# Search Lab Tests
# ------------------------------------------------------------------

@router.get(
    "/doctor/tests/search",
    response_model=list[LabTestSearchResponse],
    summary="Search Lab Tests",
    description="Search available laboratory tests.",
)
async def search_lab_tests(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.search_tests(
        current_user=current_user,
        query=q,
        category=category,
        page=page,
        limit=limit,
    )


# ------------------------------------------------------------------
# Create Lab Test Prescription
# ------------------------------------------------------------------

@router.post(
    "/doctor/prescriptions/create",
    response_model=CreateLabTestPrescriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Lab Test Prescription",
    description="Doctor creates a new lab test prescription.",
)
async def create_lab_test_prescription(
    request: CreateLabTestPrescriptionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.create_prescription(
        current_user=current_user,
        request=request,
    )


# ------------------------------------------------------------------
# Get Doctor Prescriptions
# ------------------------------------------------------------------

@router.get(
    "/doctor/prescriptions",
    response_model=DoctorPrescriptionListResponse,
    summary="Get Doctor Prescriptions",
    description="Retrieve all prescriptions created by the logged-in doctor.",
)
async def get_doctor_prescriptions(
    patient_ref: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.get_doctor_prescriptions(
        current_user=current_user,
        patient_ref=patient_ref,
        prescription_status=status,
        page=page,
        limit=limit,
    )


# ------------------------------------------------------------------
# Get Prescription (doctor)
# ------------------------------------------------------------------

@router.get(
    "/doctor/prescriptions/{prescription_id}",
    response_model=PrescriptionDetailResponse,
    summary="Get Prescription",
    description="Doctor retrieves a specific prescription.",
)
async def get_prescription(
    prescription_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.get_prescription(
        current_user=current_user,
        prescription_id=prescription_id,
    )


# ------------------------------------------------------------------
# Update Prescription
# ------------------------------------------------------------------

@router.patch(
    "/doctor/prescriptions/{prescription_id}",
    response_model=UpdatePrescriptionResponse,
    summary="Update Prescription",
    description="Doctor updates a lab test prescription.",
)
async def update_prescription(
    prescription_id: UUID,
    request: UpdateLabTestPrescriptionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.update_prescription(
        current_user=current_user,
        prescription_id=prescription_id,
        request=request,
    )


# ------------------------------------------------------------------
# Cancel Prescription
# ------------------------------------------------------------------

@router.post(
    "/doctor/prescriptions/{prescription_id}/cancel",
    response_model=CancelPrescriptionResponse,
    summary="Cancel Prescription",
    description="Doctor cancels a lab test prescription.",
)
async def cancel_prescription(
    prescription_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_doctor_only),
):
    service = LabTestPrescriptionService(db)
    return await service.cancel_prescription(
        current_user=current_user,
        prescription_id=prescription_id,
    )


# ------------------------------------------------------------------
# Get Patient Prescriptions
# ------------------------------------------------------------------

@router.get(
    "/patient/prescriptions",
    response_model=PatientPrescriptionListResponse,
    summary="Get Patient Prescriptions",
    description="Patient retrieves all of their lab test prescriptions.",
)
async def get_patient_prescriptions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_patient_only),
):
    service = LabTestPrescriptionService(db)
    return await service.get_patient_prescriptions(
        current_user=current_user,
        page=page,
        limit=limit,
    )


# ------------------------------------------------------------------
# Get Prescription Detail
# ------------------------------------------------------------------

@router.get(
    "/prescriptions/{prescription_id}",
    response_model=PrescriptionDetailResponse,
    summary="Get Prescription Detail",
    description="Doctor, Patient, Lab Technician, or Hospital Admin retrieves prescription details.",
)
async def get_prescription_detail(
    prescription_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_all_access),
):
    service = LabTestPrescriptionService(db)
    return await service.get_prescription_detail(
        current_user=current_user,
        prescription_id=prescription_id,
    )


# ------------------------------------------------------------------
# Download PDF
# ------------------------------------------------------------------

@router.get(
    "/prescriptions/{prescription_id}/pdf",
    summary="Download PDF",
    description="Download Lab Test Prescription PDF.",
)
async def download_pdf(
    prescription_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_all_access),
):
    service = LabTestPrescriptionService(db)
    pdf_bytes = await service.generate_pdf(
        current_user=current_user,
        prescription_id=prescription_id,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="prescription_{prescription_id}.pdf"'
        },
    )