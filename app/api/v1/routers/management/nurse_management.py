"""
Nurse management module.
Real-data endpoints only (no dummy payloads).
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_nurse
from app.core.database import get_platform_db_session
from app.core.response_utils import success_response
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.nurse import (
    NurseBedCreateRequest,
    NurseBedUpdateRequest,
    NurseDischargeCreateRequest,
    NurseDischargeUpdateRequest,
    NurseLabRequestCreateRequest,
    NurseLabRequestUpdateRequest,
    NurseMedicationCreateRequest,
    NurseMedicationUpdateRequest,
    NurseNoteCreateRequest,
    NurseNoteUpdateRequest,
    NurseProfileUpdateRequest,
    NurseProfileUpsertRequest,
    NurseVitalsCreateRequest,
    NurseVitalsUpdateRequest,
)
from app.services.nurse_service import NurseService

router = APIRouter(prefix="/nurse", tags=["Nurse Module"])


def _svc(tenant_db: AsyncSession, platform_db: AsyncSession) -> NurseService:
    return NurseService(tenant_db=tenant_db, platform_db=platform_db)


@router.get("/dashboard")
async def nurse_dashboard(
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).get_dashboard(current_user)
    return success_response(message="Nurse dashboard fetched successfully", data=data)


@router.get("/profile")
async def get_nurse_profile(
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).get_profile(current_user)
    return success_response(message="Nurse profile fetched successfully", data=data)


@router.put("/profile")
async def upsert_nurse_profile(
    request: NurseProfileUpsertRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).upsert_profile(request.model_dump(), current_user)
    return success_response(message="Nurse profile saved successfully", data=data)


@router.patch("/profile")
async def patch_nurse_profile(
    request: NurseProfileUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).patch_profile(
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Nurse profile updated successfully", data=data)


@router.get("/assigned-patients")
async def assigned_patients(
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).list_assigned_patients(current_user)
    return success_response(message="Assigned patients fetched successfully", data=data)


@router.post("/vitals")
async def add_vitals(
    request: NurseVitalsCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_vitals(request.model_dump(), current_user)
    return success_response(message="Vitals saved successfully", data=data)


@router.get("/vitals")
async def get_vitals(
    admission_number: str = Query(...),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).get_vitals(admission_number, current_user)
    return success_response(message="Vitals fetched successfully", data=data)


@router.patch("/vitals/{record_id}")
async def update_vitals(
    record_id: uuid.UUID,
    request: NurseVitalsUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_vitals(
        record_id,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Vitals updated successfully", data=data)


@router.post("/medications")
async def add_medication(
    request: NurseMedicationCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_medication(request.model_dump(), current_user)
    return success_response(message="Medication schedule saved successfully", data=data)


@router.get("/medications")
async def get_medications(
    admission_number: str = Query(...),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).get_medications(admission_number, current_user)
    return success_response(message="Medication schedules fetched successfully", data=data)


@router.patch("/medications/{record_id}")
async def update_medication(
    record_id: uuid.UUID,
    request: NurseMedicationUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_medication(
        record_id,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Medication schedule updated successfully", data=data)


@router.get("/beds")
async def get_beds(
    ward_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).list_beds(current_user, ward_id, status)
    return success_response(message="Beds fetched successfully", data=data)


@router.post("/beds")
async def add_bed(
    request: NurseBedCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_bed(request.model_dump(), current_user)
    return success_response(message="Bed created successfully", data=data)


@router.patch("/beds/{bed_id}")
async def update_bed(
    bed_id: uuid.UUID,
    request: NurseBedUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_bed(
        bed_id,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Bed updated successfully", data=data)


@router.get("/lab-tests")
async def get_lab_tests(
    admission_number: Optional[str] = Query(None),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).list_lab_tests(current_user, admission_number)
    return success_response(message="Lab tests fetched successfully", data=data)


@router.post("/lab-tests")
async def request_lab_test(
    request: NurseLabRequestCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_lab_request(request.model_dump(), current_user)
    return success_response(message="Lab test request saved successfully", data=data)


@router.patch("/lab-tests/{record_id}")
async def update_lab_test(
    record_id: uuid.UUID,
    request: NurseLabRequestUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_lab_request(
        record_id,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Lab test request updated successfully", data=data)


@router.get("/nursing-notes")
async def get_nursing_notes(
    admission_number: str = Query(...),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).list_notes(admission_number, current_user)
    return success_response(message="Nursing notes fetched successfully", data=data)


@router.post("/nursing-notes")
async def add_nursing_note(
    request: NurseNoteCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_note(request.model_dump(), current_user)
    return success_response(message="Nursing note saved successfully", data=data)


@router.patch("/nursing-notes/{record_id}")
async def update_nursing_note(
    record_id: uuid.UUID,
    request: NurseNoteUpdateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_note(
        record_id,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Nursing note updated successfully", data=data)


@router.get("/discharge-support")
async def get_discharge_support(
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).list_discharge_support(current_user)
    return success_response(message="Discharge support fetched successfully", data=data)


@router.post("/discharge-summary")
async def create_discharge_summary(
    request: NurseDischargeCreateRequest,
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).create_discharge_summary(request.model_dump(), current_user)
    return success_response(message="Discharge summary created successfully", data=data)


@router.get("/discharge-summary")
async def get_discharge_summary(
    admission_number: str = Query(...),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).get_discharge_summary(admission_number, current_user)
    return success_response(message="Discharge summary fetched successfully", data=data)


@router.patch("/discharge-summary")
async def update_discharge_summary(
    request: NurseDischargeUpdateRequest,
    admission_number: str = Query(...),
    current_user: User = Depends(require_nurse()),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    data = await _svc(tenant_db, platform_db).update_discharge_summary(
        admission_number,
        request.model_dump(exclude_unset=True),
        current_user,
    )
    return success_response(message="Discharge summary updated successfully", data=data)
