"""
Service layer for Report Generation UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabReportReadyTest, LabReportRecord
from app.schemas.lab_report_generation import (
    GenerateReportRequest,
    GenerateReportResponse,
    PrintReportResponse,
    ReadyTestForReportRow,
    ReadyTestsResponse,
    ReportGenerationListResponse,
    ReportGenerationMeta,
    ReportGenerationRow,
    ReportGenerationSummary,
    ReportPreviewResponse,
)

_ALLOWED_TEMPLATES = {"STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"}


def _normalize_template(value: str) -> str:
    v = (value or "").strip().upper()
    return v if v in _ALLOWED_TEMPLATES else "STANDARD"


class LabReportGenerationService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def list_reports(
        self,
        *,
        search: Optional[str] = None,
        template: str = "STANDARD",
    ) -> ReportGenerationListResponse:
        rows = await self._db_rows()
        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.patient_name.lower()
                or q in r.report_id.lower()
                or q in r.test_type.lower()
            ]
        summary = ReportGenerationSummary(
            total_reports=len(rows),
            ready_reports=sum(1 for r in rows if r.status == "READY"),
            pending_review=sum(1 for r in rows if r.status == "PENDING_REVIEW"),
            test_types=len({r.test_type for r in rows}),
        )
        return ReportGenerationListResponse(
            meta=ReportGenerationMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=True,
                demo_data=False,
            ),
            selected_template=_normalize_template(template),  # type: ignore[arg-type]
            templates=["STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"],
            summary=summary,
            rows=rows,
        )

    async def ready_tests(self) -> ReadyTestsResponse:
        stmt = select(LabReportReadyTest).where(LabReportReadyTest.hospital_id == self.hospital_id)
        recs = (await self.db.execute(stmt)).scalars().all()
        return ReadyTestsResponse(rows=[
            ReadyTestForReportRow(
                patient_name=r.patient_name,
                patient_ref=r.patient_ref or "",
                test_type=r.test_type,
                completed_on=r.completed_on,
                source_test_id=r.source_test_id,
            ) for r in recs
        ])

    async def generate(self, payload: GenerateReportRequest) -> GenerateReportResponse:
        sid = payload.source_test_id.strip()
        stmt = select(LabReportReadyTest).where(
            LabReportReadyTest.hospital_id == self.hospital_id,
            LabReportReadyTest.source_test_id == sid,
        )
        ready = (await self.db.execute(stmt)).scalar_one_or_none()
        if ready is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No ready test found for source_test_id={sid!r}.",
            )
        rid = f"REP-{uuid.uuid4().hex[:12].upper()}"
        tpl = _normalize_template(payload.template)
        rec = LabReportRecord(
            hospital_id=self.hospital_id,
            report_id=rid,
            patient_ref=ready.patient_ref or "",
            patient_name=ready.patient_name,
            doctor_name=ready.doctor_name,
            test_type=ready.test_type,
            completion_date=datetime.now(timezone.utc).date(),
            status="READY",
            verified_by=None,
            template=tpl,
        )
        self.db.add(rec)
        await self.db.commit()
        return GenerateReportResponse(
            message="Report generated successfully.",
            report_id=rid,
            status="READY",
        )

    async def preview(self, report_id: str, *, template: str = "STANDARD") -> ReportPreviewResponse:
        stmt = select(LabReportRecord).where(
            LabReportRecord.hospital_id == self.hospital_id,
            LabReportRecord.report_id == report_id,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        return ReportPreviewResponse(
            report_id=report_id,
            title=f"Lab Report - {report_id}",
            patient_name=rec.patient_name if rec else "",
            test_type=rec.test_type if rec else "",
            status=rec.status if rec else "",
            template=_normalize_template(template),  # type: ignore[arg-type]
            preview_text="",
        )

    async def print_report(self, report_id: str) -> PrintReportResponse:
        return PrintReportResponse(
            message="Report sent to print queue.",
            report_id=report_id,
            print_job_id=f"PRINT-{uuid.uuid4().hex[:10].upper()}",
        )

    async def _db_rows(self) -> list[ReportGenerationRow]:
        stmt = select(LabReportRecord).where(LabReportRecord.hospital_id == self.hospital_id)
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            ReportGenerationRow(
                report_id=r.report_id,
                patient_name=r.patient_name,
                test_type=r.test_type,
                completion_date=r.completion_date,
                status=r.status,
                verified_by=r.verified_by,
            ) for r in recs
        ]

