from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import (
    LAB_GET_ROLES,
    LAB_MUTATION_ROLES,
    PATIENT_LAB_ROLES,
    DOCTOR_LAB_ROLES,
)
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.core.enums import UserRole

from app.schemas.lab_test_registration import (
    AssignLabTestRequest,
    AssignLabTestResponse,
    DoctorAssignedTestResponse,
    ReassignTestRequest,
    ReassignTestResponse,
    TestRegistrationListResponse,
    UpdateTestRegistrationStatusRequest,
    UpdateTestRegistrationStatusResponse,
)

from app.services.lab_test_registration_service import (
    LabTestRegistrationService,
)

router = APIRouter(
    tags=["Lab Test Registration"],
)

# ==========================================================
# Doctor Assign Lab Tests
# ==========================================================

@router.post(
    "/doctor/lab-tests",
    response_model=AssignLabTestResponse,
)
async def assign_lab_tests(
    request: AssignLabTestRequest,
    current_user: User = Depends(require_roles([UserRole.DOCTOR])),
    db: AsyncSession = Depends(get_db_session),
):
    service = LabTestRegistrationService(db, current_user.hospital_id)
    return await service.assign_test(request)

# ==========================================================
# Doctor Assigned Tests
# ==========================================================

@router.get(
    "/doctor/lab-tests",
    response_model=DoctorAssignedTestResponse,
)
async def doctor_lab_tests(
    patient_ref: Optional[str] = Query(None),
    current_user: User = Depends(require_roles([UserRole.DOCTOR])),
    db: AsyncSession = Depends(get_db_session),
):
    service = LabTestRegistrationService(db, current_user.hospital_id)
    return await service.doctor_tests(patient_ref)
# ==========================================================
# Doctor Reassign Test
# ==========================================================

@router.patch(
    "/doctor/lab-tests/{test_id}",
    response_model=ReassignTestResponse,
)
async def reassign_lab_test(
    test_id: str,
    request: ReassignTestRequest,
    current_user: User = Depends(require_roles([UserRole.DOCTOR])),
    db: AsyncSession = Depends(get_db_session),
):
    service = LabTestRegistrationService(db, current_user.hospital_id)
    return await service.reassign_test(test_id, request)

# ==========================================================
# Lab Registration List
# ==========================================================

@router.get(
    "/lab/test-registration",
    response_model=TestRegistrationListResponse,
)
async def get_lab_registrations(
    for_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),

    current_user: User = Depends(
        require_roles(LAB_GET_ROLES + PATIENT_LAB_ROLES + DOCTOR_LAB_ROLES)
    ),

    db: AsyncSession = Depends(get_db_session),
):
    service = LabTestRegistrationService(db, current_user.hospital_id)

    return await service.list_tests(
        for_date=for_date,
        search=search,
        status=status,
        priority=priority,
        current_user=current_user,   # IMPORTANT
    )
# ==========================================================
# Lab Accept / Reject
# ==========================================================

@router.patch(
    "/lab/test-registration/{test_id}/status",
    response_model=UpdateTestRegistrationStatusResponse,
)
async def update_registration_status(
    test_id: str,
    request: UpdateTestRegistrationStatusRequest,
    current_user: User = Depends(require_roles(LAB_MUTATION_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    service = LabTestRegistrationService(db, current_user.hospital_id)
    return await service.update_status(test_id, request)