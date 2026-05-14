"""
Lab portal shortcuts used by some frontends (alternate URL paths).

- ``GET /lab/patients`` — patient autocomplete (canonical: ``/lab/test-registration/patients``).
- ``GET /lab/ready-tests`` — tests ready for report (canonical: ``/lab/report-generation/ready-tests``).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.lab.rbac import LAB_GET_ROLES
from app.core.security import require_roles
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.lab_report_generation import ReadyTestsResponse
from app.schemas.lab_test_registration import LabPatientSearchResponse
from app.services.lab_report_generation_service import LabReportGenerationService
from app.services.lab_test_registration_service import LabTestRegistrationService

router = APIRouter(prefix="/lab", tags=["Lab - Portal shortcuts"])


def _resolve_search_term(
    q: Optional[str],
    search: Optional[str],
    query: Optional[str],
) -> Optional[str]:
    for raw in (q, search, query):
        if raw is None:
            continue
        s = str(raw).strip()
        if s:
            return s
    return None


@router.get("/patients", response_model=LabPatientSearchResponse)
async def lab_patients_search(
    q: Optional[str] = Query(None, description="Search text (alias used by some clients)."),
    search: Optional[str] = Query(None, description="Search text (common UI param)."),
    query: Optional[str] = Query(None, description="Search text (alternate UI param)."),
    limit: int = Query(25, ge=1, le=50),
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> LabPatientSearchResponse:
    """
    Patient name / ID suggestions for lab forms (e.g. Register New Test).

    Accepts ``q``, ``search``, or ``query`` — first non-empty wins (matches various frontends).
    """
    term = _resolve_search_term(q, search, query)
    svc = LabTestRegistrationService(db, current_user.hospital_id)
    return await svc.search_patients(term, limit=limit)


@router.get("/ready-tests", response_model=ReadyTestsResponse)
async def lab_ready_tests_shortcut(
    current_user: User = Depends(require_roles(LAB_GET_ROLES)),
    db: AsyncSession = Depends(get_db_session),
) -> ReadyTestsResponse:
    """
    Alias for ``GET /lab/report-generation/ready-tests`` (some UIs call ``/lab/ready-tests``).
    """
    svc = LabReportGenerationService(db, current_user.hospital_id)
    return await svc.ready_tests()
