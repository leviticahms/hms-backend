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

from app.models.lab_portal import LabReportRecord, LabReportReadyTest, LabTestRegistration
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
    UpdateReportGenerationRequest,
    UpdateReportGenerationResponse,
)

_ALLOWED_TEMPLATES = {"STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"}


def _normalize_template(value: str) -> str:
    v = (value or "").strip().upper()
    return v if v in _ALLOWED_TEMPLATES else "STANDARD"


def _template_ui_label(tpl: str) -> str:
    """Human-readable ``report_type`` / subtitle for UI."""
    key = _normalize_template(tpl)
    return {
        "STANDARD": "Standard lab report",
        "COMPREHENSIVE": "Comprehensive report",
        "DOCTOR_SUMMARY": "Doctor summary",
        "PATIENT_FRIENDLY": "Patient-friendly report",
        "CUSTOM": "Custom report",
    }.get(key, key.replace("_", " ").title())


def _registration_completed(reg: LabTestRegistration) -> bool:
    return str(reg.status or "").strip().upper() == "COMPLETED"


class LabReportGenerationService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def _find_lab_test_registration(self, sid: str) -> Optional[LabTestRegistration]:
        raw = (sid or "").strip()
        if not raw:
            return None
        stmt = select(LabTestRegistration).where(
            LabTestRegistration.hospital_id == self.hospital_id,
            LabTestRegistration.test_id == raw,
        )
        reg = (await self.db.execute(stmt)).scalar_one_or_none()
        if reg is not None:
            return reg
        try:
            uid = uuid.UUID(raw)
        except (ValueError, AttributeError):
            return None
        stmt2 = select(LabTestRegistration).where(
            LabTestRegistration.hospital_id == self.hospital_id,
            LabTestRegistration.id == uid,
        )
        return (await self.db.execute(stmt2)).scalar_one_or_none()

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
                or (r.patient_ref and q in r.patient_ref.lower())
                or q in (r.report_type or "").lower()
            ]
        summary = ReportGenerationSummary(
            total_reports=len(rows),
            ready_reports=sum(1 for r in rows if str(r.status).strip().upper() == "READY"),
            pending_review=sum(1 for r in rows if str(r.status).strip().upper() == "PENDING_REVIEW"),
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

        patient_name: str
        patient_ref: str
        doctor_name: Optional[str]
        test_type: str

        if ready is not None:
            patient_name = ready.patient_name
            patient_ref = ready.patient_ref or ""
            doctor_name = ready.doctor_name
            test_type = ready.test_type
        else:
            reg = await self._find_lab_test_registration(sid)
            if reg is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"No ready test or registered lab test found for source_test_id={sid!r}. "
                        "Use GET /lab/report-generation/ready-tests for ready rows, or pass "
                        "lab_test_registrations.test_id (e.g. REG-...) or the registration row UUID."
                    ),
                )
            if not _registration_completed(reg):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Registered test {sid!r} has status {reg.status!r}. "
                        "Set status to COMPLETED via PATCH /api/v1/lab/test-registration/{test_id}/status "
                        "before generating a report."
                    ),
                )
            patient_name = reg.patient_name
            patient_ref = reg.patient_ref or ""
            doctor_name = reg.doctor_name
            test_type = reg.test_type

        rid = f"REP-{uuid.uuid4().hex[:12].upper()}"
        tpl = _normalize_template(str(payload.template))
        rec = LabReportRecord(
            hospital_id=self.hospital_id,
            report_id=rid,
            patient_ref=patient_ref,
            patient_name=patient_name,
            doctor_name=doctor_name,
            test_type=test_type,
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
            patient_ref=patient_ref or None,
            patient_name=patient_name,
            test_type=test_type,
            template=tpl,
        )

    async def update_report(
        self, report_id: str, payload: UpdateReportGenerationRequest
    ) -> UpdateReportGenerationResponse:
        rid = (report_id or "").strip()
        if not rid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="report_id is required")
        stmt = select(LabReportRecord).where(
            LabReportRecord.hospital_id == self.hospital_id,
            LabReportRecord.report_id == rid,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        if rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {rid!r} not found for this hospital.",
            )
        data = payload.model_dump(exclude_unset=True, exclude_none=True)
        for key, value in data.items():
            if hasattr(rec, key):
                setattr(rec, key, value)
        await self.db.commit()
        await self.db.refresh(rec)
        return UpdateReportGenerationResponse(
            message="Report updated successfully.",
            report_id=rec.report_id,
        )

    async def preview(self, report_id: str, *, template: str = "STANDARD") -> ReportPreviewResponse:
        stmt = select(LabReportRecord).where(
            LabReportRecord.hospital_id == self.hospital_id,
            LabReportRecord.report_id == report_id,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        tpl = _normalize_template(template)
        stored_tpl = _normalize_template(str(rec.template)) if rec else tpl
        st = str(rec.status) if rec else ""
        return ReportPreviewResponse(
            report_id=report_id,
            title=f"Lab Report - {report_id}",
            patient_ref=(rec.patient_ref or "") if rec else "",
            patient_name=rec.patient_name if rec else "",
            doctor_name=rec.doctor_name if rec else None,
            test_type=rec.test_type if rec else "",
            status=st,
            template=stored_tpl,  # type: ignore[arg-type]
            report_type=_template_ui_label(stored_tpl),
            completion_date=rec.completion_date if rec else None,
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
                patient_ref=r.patient_ref,
                patient_name=r.patient_name,
                doctor_name=r.doctor_name,
                test_type=r.test_type,
                template=_normalize_template(str(r.template)),
                report_type=_template_ui_label(str(r.template)),
                completion_date=r.completion_date,
                status=str(r.status or "").strip().upper() or "DRAFT",
                verified_by=r.verified_by,
            ) for r in recs
        ]

