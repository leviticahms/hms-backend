"""
Test Registration endpoints for Lab portal UI.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES, LAB_MUTATION_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_test_registration import (
    LabPatientSearchResponse,
    RegisterTestRequest,
    RegisterTestResponse,
    TestRegistrationListResponse,
    UpdateTestRegistrationStatusRequest,
    UpdateTestRegistrationStatusResponse,
)
from app.services.lab_test_registration_service import LabTestRegistrationService

router = APIRouter(
    prefix="/lab/test-registration",
    tags=["Lab - Test Registration"],
)


@router.get("/patients", response_model=LabPatientSearchResponse)
async def search_patients_for_lab_registration(
    q: Optional[str] = Query(
        None,
        description="Filter by patient name, hospital patient id, MRN, email, or phone. Omit for recent patients.",
    ),
    limit: int = Query(25, ge=1, le=50),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> LabPatientSearchResponse:
    """Dropdown / autocomplete data for Register New Test (patient name + patient id)."""
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.search_patients(q, limit=limit)


@router.patch("/{test_id}/status", response_model=UpdateTestRegistrationStatusResponse)
async def update_test_registration_status(
    test_id: str,
    body: UpdateTestRegistrationStatusRequest,
    current_user: User = Depends(require_roles(LAB_MUTATION_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> UpdateTestRegistrationStatusResponse:
    """Update workflow status for a registered lab test (e.g. Sample Pending → In Progress)."""
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.update_status(test_id, body.status)


@router.get("", response_model=TestRegistrationListResponse)
async def list_test_registrations(
    for_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="SAMPLE_PENDING|SAMPLE_COLLECTED|IN_PROGRESS|COMPLETED"),
    priority: Optional[str] = Query(None, description="URGENT|ROUTINE"),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> TestRegistrationListResponse:
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.list_tests(
        for_date=for_date,
        search=search,
        status=status,
        priority=priority,
    )


@router.post("", response_model=RegisterTestResponse)
async def register_new_test(
    request: RegisterTestRequest,
    current_user: User = Depends(require_roles(LAB_MUTATION_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> RegisterTestResponse:
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.register_test(request)

