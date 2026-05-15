"""
Service layer for Sample Tracking UI with barcode lookup and quick status actions.

``lab_sample_tracking`` rows are optional: when empty, the list is built from
``lab_test_registrations`` (same tenant DB) so registered tests appear as trackable samples.
The first status action persists a ``LabSampleTracking`` row for that barcode/test_id.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabSampleTracking, LabTestRegistration
from app.schemas.lab_sample_tracking import (
    BarcodeLookupResponse,
    SampleActionRequest,
    SampleActionResponse,
    SampleTrackingListResponse,
    SampleTrackingMeta,
    SampleTrackingRow,
)


def _registration_status_display(reg_status: str) -> str:
    """Expose registration lifecycle in the tracking STATUS column."""
    u = (reg_status or "").strip().upper()
    return u if u else "UNKNOWN"


def _collection_time_from_registration(reg: LabTestRegistration) -> str:
    rd = getattr(reg, "registered_date", None)
    if rd is None:
        return ""
    if hasattr(rd, "isoformat"):
        return rd.isoformat()
    return str(rd)


class LabSampleTrackingService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id if isinstance(hospital_id, uuid.UUID) else uuid.UUID(str(hospital_id))

    async def list_samples(self, *, search: Optional[str] = None) -> SampleTrackingListResponse:
        rows = await self._db_rows()
        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.barcode.lower()
                or q in r.patient_name.lower()
                or q in r.test_id.lower()
            ]
        return SampleTrackingListResponse(
            meta=SampleTrackingMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=True,
                demo_data=False,
            ),
            rows=rows,
        )

    async def lookup_barcode(self, barcode: str) -> BarcodeLookupResponse:
        rows = await self._db_rows()
        key = barcode.upper().strip()
        sample = next(
            (r for r in rows if r.barcode.upper().strip() == key or r.test_id.upper().strip() == key),
            None,
        )
        if not sample:
            return BarcodeLookupResponse(found=False, sample=None, message="Barcode not found.")
        return BarcodeLookupResponse(found=True, sample=sample, message="Sample found.")

    async def apply_action(self, payload: SampleActionRequest) -> SampleActionResponse:
        status_map = {
            "MARK_COLLECTED": "COLLECTED",
            "MARK_IN_TRANSIT": "IN_TRANSIT",
            "START_PROCESSING": "IN_LAB",
            "COMPLETE_TEST": "COMPLETED",
        }
        new_status = status_map[payload.action]
        location = payload.location or self._default_location_for_status(new_status)
        row = await self._ensure_tracking_row(payload.barcode.strip())
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No sample tracking row or lab test registration found for barcode "
                    f"{payload.barcode!r}. Register the test first or use the registration test_id as barcode."
                ),
            )
        row.status = new_status
        row.current_location = location
        await self.db.commit()
        return SampleActionResponse(
            message=f"{payload.barcode} updated successfully.",
            barcode=row.barcode,
            status=new_status,
            current_location=location,
        )

    def _default_location_for_status(self, status: str) -> str:
        defaults = {
            "COLLECTED": "Collection Desk",
            "IN_TRANSIT": "Corridor - Transfer",
            "IN_LAB": "Lab Processing Area",
            "PROCESSED": "Analyzer Completed Rack",
            "COMPLETED": "Result Dispatch Queue",
        }
        return defaults.get(status, "Lab")

    async def _ensure_tracking_row(self, barcode: str) -> Optional[LabSampleTracking]:
        """Return existing tracking row, or create one from ``LabTestRegistration`` (matched by test_id / barcode)."""
        b = (barcode or "").strip()
        if not b:
            return None

        stmt = select(LabSampleTracking).where(
            LabSampleTracking.hospital_id == self.hospital_id,
            LabSampleTracking.barcode == b,
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row:
            return row

        stmt_tid = select(LabSampleTracking).where(
            LabSampleTracking.hospital_id == self.hospital_id,
            LabSampleTracking.test_id == b,
        )
        row = (await self.db.execute(stmt_tid)).scalar_one_or_none()
        if row:
            return row

        reg_stmt = select(LabTestRegistration).where(
            LabTestRegistration.hospital_id == self.hospital_id,
            LabTestRegistration.test_id == b,
        )
        reg = (await self.db.execute(reg_stmt)).scalar_one_or_none()
        if reg is None:
            return None

        disp = _registration_status_display(reg.status)
        nt = LabSampleTracking(
            hospital_id=self.hospital_id,
            barcode=b,
            test_id=reg.test_id,
            patient_ref=reg.patient_ref,
            patient_name=reg.patient_name,
            doctor_name=reg.doctor_name,
            test_type=reg.test_type,
            sample_type=reg.sample_type,
            collection_time=_collection_time_from_registration(reg),
            status=disp,
            current_location="Registration",
        )
        self.db.add(nt)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            row = (await self.db.execute(stmt)).scalar_one_or_none()
            if row:
                return row
            row = (await self.db.execute(stmt_tid)).scalar_one_or_none()
            return row
        await self.db.refresh(nt)
        return nt

    async def _db_rows(self) -> list[SampleTrackingRow]:
        stmt = (
            select(LabSampleTracking)
            .where(LabSampleTracking.hospital_id == self.hospital_id)
            .order_by(LabSampleTracking.created_at.desc())
        )
        recs = list((await self.db.execute(stmt)).scalars().all())
        tracked_test_ids = {r.test_id for r in recs}
        out: list[SampleTrackingRow] = [
            SampleTrackingRow(
                barcode=r.barcode,
                test_id=r.test_id,
                patient_name=r.patient_name,
                test_type=r.test_type,
                sample_type=r.sample_type,
                collection_time=r.collection_time,
                status=r.status,
                current_location=r.current_location,
            )
            for r in recs
        ]

        reg_stmt = (
            select(LabTestRegistration)
            .where(LabTestRegistration.hospital_id == self.hospital_id)
            .order_by(LabTestRegistration.registered_date.desc(), LabTestRegistration.created_at.desc())
        )
        regs = list((await self.db.execute(reg_stmt)).scalars().all())
        for reg in regs:
            if reg.test_id in tracked_test_ids:
                continue
            tracked_test_ids.add(reg.test_id)
            out.append(
                SampleTrackingRow(
                    barcode=reg.test_id,
                    test_id=reg.test_id,
                    patient_name=reg.patient_name,
                    test_type=reg.test_type,
                    sample_type=reg.sample_type,
                    collection_time=_collection_time_from_registration(reg),
                    status=_registration_status_display(reg.status),
                    current_location="Registration queue",
                )
            )
        return out
