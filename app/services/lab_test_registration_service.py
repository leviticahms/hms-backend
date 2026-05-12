"""Service layer for lab test registration UI."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabTestRegistration
from app.models.patient import PatientProfile
from app.models.user import User
from app.schemas.lab_test_registration import (
    LabPatientOption,
    LabPatientSearchResponse,
    RegisterTestRequest,
    RegisterTestResponse,
    TestRegistrationListResponse,
    TestRegistrationMeta,
    TestRegistrationRow,
    TestRegistrationSummary,
    UpdateTestRegistrationStatusResponse,
)

_ALLOWED_REGISTRATION_STATUSES = frozenset(
    {"SAMPLE_PENDING", "SAMPLE_COLLECTED", "IN_PROGRESS", "COMPLETED"}
)


class LabTestRegistrationService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def list_tests(
        self,
        *,
        for_date: Optional[date] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> TestRegistrationListResponse:
        d = for_date or datetime.now(timezone.utc).date()
        rows = await self._db_rows()

        if search:
            q = search.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.patient_name.lower()
                or q in r.test_id.lower()
                or q in r.test_type.lower()
                or (r.patient_ref and q in r.patient_ref.lower())
            ]
        if status:
            s = status.strip().upper()
            rows = [r for r in rows if r.status == s]
        if priority:
            p = priority.strip().upper()
            rows = [r for r in rows if r.priority == p]

        summary = TestRegistrationSummary(
            total_tests_today=len(rows),
            completed_tests=sum(1 for r in rows if r.status == "COMPLETED"),
            in_progress_tests=sum(1 for r in rows if r.status == "IN_PROGRESS"),
            urgent_tests=sum(1 for r in rows if r.priority == "URGENT"),
        )

        return TestRegistrationListResponse(
            meta=TestRegistrationMeta(
                generated_at=datetime.now(timezone.utc),
                for_date=d,
                live_data=True,
                demo_data=False,
            ),
            summary=summary,
            rows=rows,
        )

    async def register_test(self, payload: RegisterTestRequest) -> RegisterTestResponse:
        test_id = f"REG-{uuid.uuid4().hex[:16].upper()}"
        row = LabTestRegistration(
            hospital_id=self.hospital_id,
            test_id=test_id,
            patient_ref=payload.patient_ref,
            patient_name=payload.patient_name,
            doctor_name=payload.referring_doctor,
            test_type=payload.test_type,
            sample_type=payload.sample_type,
            priority=payload.priority,
            status="SAMPLE_PENDING",
            special_instructions=payload.special_instructions,
            registered_date=datetime.now(timezone.utc).date(),
        )
        self.db.add(row)
        await self.db.commit()
        return RegisterTestResponse(
            message="Test registered successfully.",
            test_id=test_id,
            status="SAMPLE_PENDING",
            patient_ref=payload.patient_ref,
            patient_name=payload.patient_name,
            test_type=payload.test_type,
            sample_type=payload.sample_type,
            priority=payload.priority,
            referring_doctor=payload.referring_doctor,
            special_instructions=payload.special_instructions,
        )

    async def search_patients(self, q: Optional[str], *, limit: int = 25) -> LabPatientSearchResponse:
        """Patients in this hospital for lab registration autocomplete (name + patient_id)."""
        cap = max(1, min(limit, 50))
        stmt = (
            select(PatientProfile, User)
            .join(User, PatientProfile.user_id == User.id)
            .where(PatientProfile.hospital_id == self.hospital_id)
            .order_by(PatientProfile.created_at.desc())
        )
        if q and q.strip():
            term = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(PatientProfile.patient_id).like(term),
                    func.lower(func.coalesce(PatientProfile.mrn, "")).like(term),
                    func.lower(User.first_name).like(term),
                    func.lower(User.last_name).like(term),
                    func.lower(User.email).like(term),
                    func.lower(func.coalesce(User.phone, "")).like(term),
                )
            )
        stmt = stmt.limit(cap)
        rows = (await self.db.execute(stmt)).all()
        out: list[LabPatientOption] = []
        for profile, user in rows:
            name = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or (
                user.email or profile.patient_id
            )
            out.append(
                LabPatientOption(
                    patient_id=profile.patient_id,
                    patient_name=name,
                    patient_profile_id=str(profile.id),
                    email=user.email,
                    phone=user.phone,
                    gender=profile.gender,
                    date_of_birth=profile.date_of_birth,
                    mrn=profile.mrn,
                )
            )
        return LabPatientSearchResponse(patients=out)

    async def update_status(self, test_id: str, new_status: str) -> UpdateTestRegistrationStatusResponse:
        if new_status not in _ALLOWED_REGISTRATION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status. Allowed: {', '.join(sorted(_ALLOWED_REGISTRATION_STATUSES))}",
            )
        stmt = select(LabTestRegistration).where(
            LabTestRegistration.test_id == test_id,
            LabTestRegistration.hospital_id == self.hospital_id,
        )
        rec = (await self.db.execute(stmt)).scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test registration not found")
        rec.status = new_status
        await self.db.commit()
        return UpdateTestRegistrationStatusResponse(
            message="Status updated successfully.",
            test_id=rec.test_id,
            status=new_status,
        )

    async def _db_rows(self) -> list[TestRegistrationRow]:
        stmt = (
            select(LabTestRegistration)
            .where(LabTestRegistration.hospital_id == self.hospital_id)
            .order_by(LabTestRegistration.created_at.desc())
        )
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            TestRegistrationRow(
                test_id=r.test_id,
                patient_ref=r.patient_ref,
                patient_name=r.patient_name,
                test_type=r.test_type,
                sample_type=r.sample_type,
                registered_date=r.registered_date,
                status=r.status,
                priority=r.priority,
            )
            for r in recs
        ]

