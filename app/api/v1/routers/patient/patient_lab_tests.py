from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_platform_db_session
from app.dependencies.auth import get_current_patient
from app.models.lab_portal import LabReportRecord, LabTestRegistration
from app.models.patient import PatientProfile

router = APIRouter(prefix="/patient-lab-tests", tags=["Patient Portal - Lab Tests"])


@router.get("/my/results")
async def get_my_lab_results(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(LabReportRecord)
        .where(LabReportRecord.patient_ref == current_patient.patient_id)
        .order_by(desc(LabReportRecord.completion_date))
    )
    rows = result.scalars().all()
    return {
        "results": [
            {
                "test_id": r.report_id,
                "test_type": r.test_type,
                "status": r.status,
                "completion_date": r.completion_date.isoformat() if r.completion_date else None,
                "verified_by": r.verified_by,
            }
            for r in rows
        ]
    }


@router.get("/my/results/{test_id}")
async def get_lab_result_details(
    test_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(LabReportRecord).where(
            LabReportRecord.report_id == test_id, LabReportRecord.patient_ref == current_patient.patient_id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Lab result not found")
    return {
        "test_id": row.report_id,
        "test_type": row.test_type,
        "status": row.status,
        "completion_date": row.completion_date.isoformat() if row.completion_date else None,
        "parameters": [],
    }


@router.get("/my/results/{test_id}/download")
async def download_lab_result(
    test_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(LabReportRecord).where(
            LabReportRecord.report_id == test_id, LabReportRecord.patient_ref == current_patient.patient_id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Lab result not found")
    return {
        "test_id": test_id,
        "download_url": f"/uploads/lab_reports/{test_id}.pdf",
    }


@router.get("/my/trends/{parameter_name}")
async def get_lab_trends(
    parameter_name: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    reg_result = await db.execute(
        select(LabTestRegistration)
        .where(LabTestRegistration.patient_ref == current_patient.patient_id)
        .order_by(desc(LabTestRegistration.registered_date))
        .limit(20)
    )
    rows = reg_result.scalars().all()
    return {
        "parameter_name": parameter_name,
        "points": [
            {"date": r.registered_date.isoformat() if r.registered_date else None, "value": None, "test_id": r.test_id}
            for r in rows
        ],
    }
