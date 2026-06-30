"""
Lab Test Registration Service
Doctor -> Lab Registration -> Lab Workflow
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabTestRegistration
from app.models.patient import PatientProfile
from app.models.user import Role, User, user_roles
from app.core.enums import UserRole

from app.schemas.lab_test_registration import (
    AssignLabTestRequest,
    AssignLabTestResponse,
    DoctorAssignedTest,
    DoctorAssignedTestResponse,
    LabPatientSearchItem,
    LabPatientSearchResponse,
    ReassignTestRequest,
    ReassignTestResponse,
    TestRegistrationListResponse,
    TestRegistrationMeta,
    TestRegistrationRow,
    TestRegistrationSummary,
    UpdateTestRegistrationStatusRequest,
    UpdateTestRegistrationStatusResponse,
)


def _user_role_names(user: User) -> set[str]:
    """User.roles is a many-to-many relationship (list of Role objects, each
    with a .name). There is no single user.role attribute."""
    return {r.name for r in (user.roles or [])}


def _user_full_name(user: User) -> str:
    """User has first_name/middle_name/last_name, not a full_name column."""
    parts = [user.first_name, user.middle_name, user.last_name]
    return " ".join(p for p in parts if p).strip()

_ALLOWED_REGISTRATION_STATUSES = frozenset(
    {
        "SAMPLE_PENDING",
        "ACCEPTED",
        "REJECTED",
        "SAMPLE_COLLECTED",
        "IN_PROGRESS",
        "COMPLETED",
    }
)


class LabTestRegistrationService:

    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    # =====================================================
    # Doctor Assign Tests
    # =====================================================

    async def assign_test(
        self,
        payload: AssignLabTestRequest,
    ) -> AssignLabTestResponse:

        if not payload.tests:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select at least one lab test.",
            )

        try:
            total = 0

            for item in payload.tests:
                registration = LabTestRegistration(
                    hospital_id=self.hospital_id,
                    test_id=f"REG-{uuid.uuid4().hex[:12].upper()}",
                    patient_ref=payload.patient_ref,
                    patient_name=payload.patient_name,
                    doctor_name=payload.doctor_name,
                    test_type=item.test_name,
                    sample_type=item.sample_type,
                    priority=item.priority,
                    status="SAMPLE_PENDING",
                    special_instructions=item.instructions,
                    registered_date=date.today(),
                )
                self.db.add(registration)
                total += 1

            await self.db.commit()

            return AssignLabTestResponse(
                success=True,
                message="Lab Test Assigned Successfully.",
                total_tests=total,
            )

        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    # =====================================================
    # Doctor Assigned Tests
    # =====================================================

    async def doctor_tests(
        self,
        patient_ref: Optional[str] = None,
    ) -> DoctorAssignedTestResponse:

        stmt = select(LabTestRegistration).where(
            LabTestRegistration.hospital_id == self.hospital_id
        )

        if patient_ref:
            stmt = stmt.where(
                LabTestRegistration.patient_ref == patient_ref
            )

        stmt = stmt.order_by(LabTestRegistration.created_at.desc())

        records = (await self.db.execute(stmt)).scalars().all()

        response = [
            DoctorAssignedTest(
                test_id=row.test_id,
                patient_ref=row.patient_ref,
                patient_name=row.patient_name,
                doctor_name=row.doctor_name,
                test_type=row.test_type,
                sample_type=row.sample_type,
                priority=row.priority,
                status=row.status,
                reject_reason=(
                    row.special_instructions
                    if row.status == "REJECTED"
                    else None
                ),
            )
            for row in records
        ]

        return DoctorAssignedTestResponse(
            total=len(response),
            tests=response,
        )

    # =====================================================
    # Doctor Reassign Test
    # =====================================================

    async def reassign_test(
        self,
        test_id: str,
        payload: ReassignTestRequest,
    ) -> ReassignTestResponse:

        stmt = select(LabTestRegistration).where(
            LabTestRegistration.test_id == test_id,
            LabTestRegistration.hospital_id == self.hospital_id,
        )

        existing = (await self.db.execute(stmt)).scalar_one_or_none()

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lab Test Not Found.",
            )

        try:
            new_test_id = f"REG-{uuid.uuid4().hex[:12].upper()}"

            registration = LabTestRegistration(
                hospital_id=self.hospital_id,
                test_id=new_test_id,
                patient_ref=existing.patient_ref,
                patient_name=existing.patient_name,
                doctor_name=existing.doctor_name,
                test_type=payload.test_name,
                sample_type=payload.sample_type,
                priority=payload.priority,
                status="SAMPLE_PENDING",
                special_instructions=payload.instructions,
                registered_date=date.today(),
            )

            self.db.add(registration)
            await self.db.commit()

            return ReassignTestResponse(
                success=True,
                message="Lab Test Reassigned Successfully.",
                new_test_id=new_test_id,
            )

        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    # =====================================================
    # Lab Accept / Reject / Status Update
    # =====================================================

    async def update_status(
        self,
        test_id: str,
        payload: UpdateTestRegistrationStatusRequest,
    ) -> UpdateTestRegistrationStatusResponse:

        if payload.status not in _ALLOWED_REGISTRATION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid Status.",
            )

        stmt = select(LabTestRegistration).where(
            LabTestRegistration.test_id == test_id,
            LabTestRegistration.hospital_id == self.hospital_id,
        )

        registration = (await self.db.execute(stmt)).scalar_one_or_none()

        if registration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lab Registration Not Found.",
            )

        try:
            registration.status = payload.status

            if payload.status == "REJECTED" and payload.reason:
                registration.special_instructions = payload.reason

            await self.db.commit()

            return UpdateTestRegistrationStatusResponse(
                message="Status Updated Successfully.",
                test_id=registration.test_id,
                status=registration.status,
            )

        except Exception as e:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    # =====================================================
    # Lab Registration List (ROLE BASED)
    # =====================================================

    async def list_tests(
        self,
        *,
        current_user: User,
        for_date: Optional[date] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> TestRegistrationListResponse:

        stmt = select(LabTestRegistration).where(
            LabTestRegistration.hospital_id == self.hospital_id
        )

        # ---------------- ROLE FILTERING ----------------

        user_role_names = _user_role_names(current_user)

        if UserRole.PATIENT.value in user_role_names:
            own_profile_stmt = select(PatientProfile.patient_id).where(
                PatientProfile.user_id == current_user.id
            )
            own_patient_id = (
                await self.db.execute(own_profile_stmt)
            ).scalar_one_or_none()

            stmt = stmt.where(
                LabTestRegistration.patient_ref == own_patient_id
            )

        elif UserRole.DOCTOR.value in user_role_names:
            stmt = stmt.where(
                LabTestRegistration.doctor_name == _user_full_name(current_user)
            )

        elif user_role_names & {
            UserRole.LAB_TECH.value,
            UserRole.HOSPITAL_ADMIN.value,
        }:
            pass  # full access

        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied.",
            )

        stmt = stmt.order_by(LabTestRegistration.created_at.desc())

        registrations = (await self.db.execute(stmt)).scalars().all()

        # ---------------- PATIENT DETAILS (DOB, gender, phone, email) ----------------
        # patient_ref stores PatientProfile.patient_id (e.g. "PAT-ALEX-997"),
        # NOT User.id. Join via PatientProfile.patient_id, then get the
        # linked User through PatientProfile.user_id for phone/email.

        patient_refs = {r.patient_ref for r in registrations if r.patient_ref}

        profiles_by_ref: dict[str, PatientProfile] = {}
        users_by_ref: dict[str, User] = {}

        if patient_refs:
            profile_stmt = select(PatientProfile).where(
                PatientProfile.patient_id.in_(patient_refs),
                PatientProfile.hospital_id == self.hospital_id,
            )
            profiles = (await self.db.execute(profile_stmt)).scalars().all()
            profiles_by_ref = {p.patient_id: p for p in profiles}

            user_ids = {p.user_id for p in profiles if p.user_id}
            if user_ids:
                user_stmt = select(User).where(User.id.in_(user_ids))
                users_by_id = {
                    u.id: u
                    for u in (await self.db.execute(user_stmt)).scalars().all()
                }
                users_by_ref = {
                    p.patient_id: users_by_id[p.user_id]
                    for p in profiles
                    if p.user_id in users_by_id
                }

        rows: list[TestRegistrationRow] = []
        for r in registrations:
            user = users_by_ref.get(r.patient_ref) if r.patient_ref else None
            profile = profiles_by_ref.get(r.patient_ref) if r.patient_ref else None

            rows.append(
                TestRegistrationRow(
                    test_id=r.test_id,
                    patient_ref=r.patient_ref,
                    patient_name=r.patient_name,
                    doctor_name=r.doctor_name,
                    test_type=r.test_type,
                    sample_type=r.sample_type,
                    priority=r.priority,
                    registered_date=r.registered_date,
                    status=r.status,
                    date_of_birth=profile.date_of_birth if profile else None,
                    gender=profile.gender if profile else None,
                    phone=user.phone if user else None,
                    email=user.email if user else None,
                )
            )

        # ---------------- SEARCH FILTER ----------------

        if search:
            keyword = search.lower()
            rows = [
                r for r in rows
                if keyword in r.test_id.lower()
                or keyword in r.patient_name.lower()
                or keyword in r.test_type.lower()
                or (r.patient_ref and keyword in r.patient_ref.lower())
            ]

        if status:
            rows = [r for r in rows if r.status == status.upper()]

        if priority:
            rows = [r for r in rows if r.priority == priority.upper()]

        # ---------------- SUMMARY ----------------

        summary = TestRegistrationSummary(
            total_tests_today=len(rows),
            completed_tests=sum(1 for r in rows if r.status == "COMPLETED"),
            in_progress_tests=sum(1 for r in rows if r.status == "IN_PROGRESS"),
            rejected_tests=sum(1 for r in rows if r.status == "REJECTED"),
            urgent_tests=sum(1 for r in rows if r.priority == "URGENT"),
        )

        return TestRegistrationListResponse(
            meta=TestRegistrationMeta(
                generated_at=datetime.now(timezone.utc),
                for_date=for_date or date.today(),
                live_data=True,
                demo_data=False,
            ),
            summary=summary,
            rows=rows,
        )

    # =====================================================
    # Patient Autocomplete (Lab "Register New Test" form)
    # =====================================================

    async def search_patients(
        self,
        term: Optional[str],
        *,
        limit: int = 25,
    ) -> LabPatientSearchResponse:
        """
        Returns patient suggestions for the lab test registration form.

        ``patient_ref`` is ``PatientProfile.patient_id`` (e.g. "PAT-ALEX-997"),
        the same value stored on LabTestRegistration.patient_ref — NOT the
        user's UUID. Only users who have a PatientProfile can be suggested.
        """

        stmt = (
            select(User, PatientProfile)
            .join(user_roles, User.id == user_roles.c.user_id)
            .join(Role, user_roles.c.role_id == Role.id)
            .join(PatientProfile, PatientProfile.user_id == User.id)
            .where(
                User.hospital_id == self.hospital_id,
                Role.name == UserRole.PATIENT.value,
            )
        )

        if term:
            like = f"%{term.strip()}%"
            stmt = stmt.where(
                or_(
                    User.first_name.ilike(like),
                    User.last_name.ilike(like),
                    User.middle_name.ilike(like),
                    PatientProfile.patient_id.ilike(like),
                )
            )

        stmt = (
            stmt.order_by(User.first_name.asc(), User.last_name.asc())
            .limit(limit)
        )

        rows = (await self.db.execute(stmt)).all()

        patients = [
            LabPatientSearchItem(
                patient_ref=profile.patient_id,
                patient_name=_user_full_name(user),
                contact_number=user.phone,
            )
            for user, profile in rows
        ]

        return LabPatientSearchResponse(
            total=len(patients),
            patients=patients,
        )