"""
Nurse module service (tenant-first reads + mirrored writes).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.hospital import Bed, Department, StaffDepartmentAssignment, Ward
from app.models.nurse import NurseProfile
from app.models.patient import Admission, DischargeSummary, MedicalRecord, PatientProfile
from app.models.user import User


def _jsonify(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonify(val) for k, val in v.items()}
    return v


def _serialize_model(obj: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in obj.__table__.columns:
        out[col.name] = _jsonify(getattr(obj, col.name))
    return out


def _model_values_raw(obj: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in obj.__table__.columns:
        out[col.name] = getattr(obj, col.name)
    return out


def _json_items(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _is_pending_item(item: Dict[str, Any], done_statuses: set[str]) -> bool:
    status_value = item.get("status") or item.get("result_status") or item.get("notify_status")
    if status_value is None:
        return True
    status_text = str(status_value).strip().upper()
    return status_text not in done_statuses


def _require_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID for {field}",
        )


class NurseService:
    def __init__(self, tenant_db: AsyncSession, platform_db: AsyncSession):
        self.tenant_db = tenant_db
        self.platform_db = platform_db

    @staticmethod
    def _admission_load_options():
        return (
            selectinload(Admission.patient).selectinload(PatientProfile.user),
            selectinload(Admission.doctor),
            selectinload(Admission.department),
        )

    async def _patient_display_name(self, patient: Optional[PatientProfile]) -> Optional[str]:
        if not patient:
            return None
        user = patient.user
        if user is None and patient.user_id:
            user = await self.platform_db.get(User, patient.user_id)
            if user is None:
                user = await self.tenant_db.get(User, patient.user_id)
        if user:
            name = f"{user.first_name or ''} {user.last_name or ''}".strip()
            if name:
                return name
        return patient.id_name or patient.patient_id

    @staticmethod
    def _doctor_display_name(doctor: Optional[User]) -> Optional[str]:
        if not doctor:
            return None
        name = f"{doctor.first_name or ''} {doctor.last_name or ''}".strip()
        return name or None

    async def _format_admission_row(self, admission: Admission) -> Dict[str, Any]:
        d = _serialize_model(admission)
        d["patient_ref"] = admission.patient.patient_id if admission.patient else None
        d["patient_name"] = await self._patient_display_name(admission.patient)
        d["doctor_name"] = self._doctor_display_name(admission.doctor)
        d["department_name"] = (
            admission.department.name if getattr(admission, "department", None) else None
        )
        return d

    async def _get_medical_record_for_nurse(
        self,
        record_id: uuid.UUID,
        current_user: User,
        *,
        expected_complaints: Optional[List[str]] = None,
    ) -> MedicalRecord:
        rec = await self.tenant_db.get(MedicalRecord, record_id)
        if not rec or rec.hospital_id != current_user.hospital_id:
            raise HTTPException(status_code=404, detail="Record not found")
        if expected_complaints and rec.chief_complaint not in expected_complaints:
            raise HTTPException(status_code=404, detail="Record not found for this resource type")
        adm_res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.patient_id == rec.patient_id,
                    Admission.hospital_id == current_user.hospital_id,
                )
            )
            .order_by(Admission.is_active.desc(), desc(Admission.created_at))
            .limit(1)
        )
        admission = adm_res.scalars().first()
        if not admission:
            raise HTTPException(status_code=404, detail="No admission found for this patient")
        dept_ids, dept_names, _ = await self._nurse_department_scope(current_user)
        if not await self._nurse_can_access_admission(admission, current_user, dept_ids, dept_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this patient's department",
            )
        return rec

    @staticmethod
    def _norm_dept_name(name: Optional[str]) -> str:
        return (name or "").strip().lower()

    async def _load_staff_assignments(
        self,
        current_user: User,
        session: AsyncSession,
    ) -> List[StaffDepartmentAssignment]:
        row = await session.execute(
            select(StaffDepartmentAssignment)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == current_user.id,
                    StaffDepartmentAssignment.hospital_id == current_user.hospital_id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .order_by(
                StaffDepartmentAssignment.is_primary.desc(),
                StaffDepartmentAssignment.created_at.desc(),
            )
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        return list(row.scalars().all())

    async def _nurse_department_scope(
        self,
        current_user: User,
    ) -> tuple[List[uuid.UUID], List[str], Optional[StaffDepartmentAssignment]]:
        """
        Collect department ids/names for the nurse from tenant DB, then platform DB,
        then NurseProfile — IPD data lives on tenant but admin UIs sometimes write platform only.
        """
        hid = current_user.hospital_id
        dept_ids: List[uuid.UUID] = []
        dept_names: List[str] = []
        primary: Optional[StaffDepartmentAssignment] = None

        def _add_dept(dep_id: Optional[uuid.UUID], dep_name: Optional[str]) -> None:
            if dep_id and dep_id not in dept_ids:
                dept_ids.append(dep_id)
            norm = self._norm_dept_name(dep_name)
            if norm and norm not in dept_names:
                dept_names.append(norm)

        for session in (self.tenant_db, self.platform_db):
            try:
                assignments = await self._load_staff_assignments(current_user, session)
            except Exception as exc:
                logger.warning("Nurse department assignment lookup failed: %s", exc)
                assignments = []
            for assignment in assignments:
                if primary is None:
                    primary = assignment
                _add_dept(assignment.department_id, getattr(assignment.department, "name", None))

        for session in (self.tenant_db, self.platform_db):
            try:
                np_row = await session.execute(
                    select(NurseProfile)
                    .where(
                        and_(
                            NurseProfile.user_id == current_user.id,
                            NurseProfile.hospital_id == hid,
                            NurseProfile.is_active == True,
                        )
                    )
                    .options(selectinload(NurseProfile.department))
                )
                profile = np_row.scalar_one_or_none()
            except Exception as exc:
                logger.warning("Nurse profile department lookup failed: %s", exc)
                profile = None
            if profile:
                _add_dept(profile.department_id, getattr(profile.department, "name", None))

        if not dept_ids and not dept_names:
            raise HTTPException(status_code=404, detail="Nurse department assignment not found")
        return dept_ids, dept_names, primary

    async def _get_nurse_department(self, current_user: User) -> StaffDepartmentAssignment:
        _dept_ids, _dept_names, primary = await self._nurse_department_scope(current_user)
        if primary is not None:
            return primary
        raise HTTPException(status_code=404, detail="Nurse department assignment not found")

    async def _department_row(
        self,
        department_id: uuid.UUID,
        hospital_id: uuid.UUID,
    ) -> Optional[Department]:
        for session in (self.tenant_db, self.platform_db):
            row = await session.execute(
                select(Department).where(
                    and_(
                        Department.id == department_id,
                        Department.hospital_id == hospital_id,
                    )
                )
            )
            dept = row.scalar_one_or_none()
            if dept:
                return dept
        return None

    async def _department_ids_for_names(
        self,
        hospital_id: uuid.UUID,
        names: List[str],
    ) -> List[uuid.UUID]:
        if not names:
            return []
        ids: List[uuid.UUID] = []
        for session in (self.tenant_db, self.platform_db):
            row = await session.execute(
                select(Department.id).where(
                    and_(
                        Department.hospital_id == hospital_id,
                        func.lower(func.trim(Department.name)).in_(names),
                    )
                )
            )
            for dep_id in row.scalars().all():
                if dep_id not in ids:
                    ids.append(dep_id)
        return ids

    def _admission_department_match_conditions(
        self,
        hospital_id: uuid.UUID,
        dept_ids: List[uuid.UUID],
        dept_names: List[str],
    ) -> list:
        """Match admissions when dept UUID or department name aligns (tenant + platform UUID drift)."""
        conditions: list = []
        if dept_ids:
            conditions.append(Admission.department_id.in_(dept_ids))
        if dept_names:
            conditions.append(
                Admission.department_id.in_(
                    select(Department.id).where(
                        Department.hospital_id == hospital_id,
                        func.lower(func.trim(Department.name)).in_(dept_names),
                    )
                )
            )
        return conditions

    async def _nurse_can_access_admission(
        self,
        admission: Admission,
        current_user: User,
        dept_ids: List[uuid.UUID],
        dept_names: List[str],
    ) -> bool:
        hid = current_user.hospital_id
        if admission.department_id in dept_ids:
            return True
        adm_dept = await self._department_row(admission.department_id, hid)
        adm_name = self._norm_dept_name(adm_dept.name if adm_dept else None)
        if adm_name and adm_name in dept_names:
            return True
        expanded_ids = await self._department_ids_for_names(hid, dept_names)
        return admission.department_id in expanded_ids

    async def _resolve_admission(self, admission_number: str, current_user: User) -> Admission:
        hid = current_user.hospital_id
        dept_ids, dept_names, _primary = await self._nurse_department_scope(current_user)
        dept_match = self._admission_department_match_conditions(hid, dept_ids, dept_names)
        if not dept_match:
            raise HTTPException(status_code=404, detail="Nurse department assignment not found")

        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.admission_number == admission_number,
                    Admission.hospital_id == hid,
                    or_(*dept_match),
                )
            )
            .options(selectinload(Admission.patient))
            .order_by(desc(Admission.created_at))
            .limit(1)
        )
        admission = res.scalars().first()
        if admission:
            return admission

        other_res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.admission_number == admission_number,
                    Admission.hospital_id == hid,
                )
            )
            .options(selectinload(Admission.patient))
            .order_by(desc(Admission.created_at))
            .limit(1)
        )
        other = other_res.scalars().first()
        if other and await self._nurse_can_access_admission(other, current_user, dept_ids, dept_names):
            return other
        if other:
            adm_dept = await self._department_row(other.department_id, hid)
            adm_label = adm_dept.name if adm_dept else str(other.department_id)
            nurse_label = ", ".join(dept_names) if dept_names else ", ".join(str(x) for x in dept_ids)
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Admission is in department '{adm_label}' but your nurse assignment is "
                    f"'{nurse_label}'. Re-save the nurse profile/assignment or admit the patient "
                    f"under the same department."
                ),
            )
        raise HTTPException(status_code=404, detail="Admission not found in nurse department")

    async def _mirror_upsert(self, model: Type[Any], model_id: uuid.UUID, values: Dict[str, Any]) -> None:
        # Enforce persistence in both DBs.
        existing = await self.platform_db.get(model, model_id)
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            self.platform_db.add(model(**values))
        await self.platform_db.commit()

    async def _mirror_staff_assignment(self, assignment: StaffDepartmentAssignment) -> None:
        """Keep platform StaffDepartmentAssignment in sync when nurse profile is saved on tenant."""
        if assignment is None:
            return
        existing = await self.platform_db.get(StaffDepartmentAssignment, assignment.id)
        if existing:
            for col in assignment.__table__.columns:
                name = col.name
                if name in ("created_at", "updated_at"):
                    continue
                setattr(existing, name, getattr(assignment, name))
        else:
            values = _model_values_raw(assignment)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            self.platform_db.add(StaffDepartmentAssignment(**values))
        await self.platform_db.commit()

    async def _resolve_admission_by_patient_ref(self, patient_ref: str, current_user: User) -> Admission:
        hid = current_user.hospital_id
        dept_ids, dept_names, _primary = await self._nurse_department_scope(current_user)
        dept_match = self._admission_department_match_conditions(hid, dept_ids, dept_names)
        if not dept_match:
            raise HTTPException(status_code=404, detail="Nurse department assignment not found")
        patient_row = await self.tenant_db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == hid,
                )
            )
        )
        patient = patient_row.scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.patient_id == patient.id,
                    Admission.hospital_id == hid,
                    or_(*dept_match),
                    Admission.is_active == True,
                )
            )
            .order_by(desc(Admission.created_at))
            .options(selectinload(Admission.patient))
        )
        admission = res.scalars().first()
        if not admission:
            raise HTTPException(status_code=404, detail="Active admission not found for patient")
        return admission

    async def get_profile(self, current_user: User) -> Dict[str, Any]:
        q = await self.tenant_db.execute(
            select(NurseProfile)
            .where(and_(NurseProfile.user_id == current_user.id, NurseProfile.hospital_id == current_user.hospital_id))
            .options(selectinload(NurseProfile.department))
        )
        profile = q.scalar_one_or_none()
        if not profile:
            return {}
        out = _serialize_model(profile)
        out["department_name"] = profile.department.name if profile.department else None
        return out

    async def upsert_profile(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        dep_id = _require_uuid(payload.get("department_id"), "department_id")
        q = await self.tenant_db.execute(
            select(NurseProfile).where(
                and_(NurseProfile.user_id == current_user.id, NurseProfile.hospital_id == current_user.hospital_id)
            )
        )
        profile = q.scalar_one_or_none()
        if profile:
            for k, v in payload.items():
                if k == "department_id":
                    setattr(profile, k, dep_id)
                else:
                    setattr(profile, k, v)
            await self.tenant_db.commit()
            assignment_q = await self.tenant_db.execute(
                select(StaffDepartmentAssignment).where(
                    and_(
                        StaffDepartmentAssignment.staff_id == current_user.id,
                        StaffDepartmentAssignment.hospital_id == current_user.hospital_id,
                    )
                )
            )

            assignment = assignment_q.scalars().first()

            if assignment:
                assignment.department_id = dep_id
                assignment.is_active = True
            else:
                assignment = StaffDepartmentAssignment(
                    id=uuid.uuid4(),
                    hospital_id=current_user.hospital_id,
                    staff_id=current_user.id,
                    department_id=dep_id,
                    is_primary=True,
                    is_active=True,
                )
                self.tenant_db.add(assignment)
                self.tenant_db.add(profile)
                await self.tenant_db.commit()

            await self._mirror_staff_assignment(assignment)
            await self.tenant_db.commit()
            await self._mirror_upsert(NurseProfile, profile.id, _model_values_raw(profile))
            return _serialize_model(profile)

        profile_id = uuid.uuid4()
        profile = NurseProfile(
            id=profile_id,
            hospital_id=current_user.hospital_id,
            user_id=current_user.id,
            department_id=dep_id,
            nurse_id=payload["nurse_id"],
            nursing_license_number=payload["nursing_license_number"],
            designation=payload["designation"],
            specialization=payload.get("specialization"),
            experience_years=payload.get("experience_years", 0),
            qualifications=payload.get("qualifications", []),
            certifications=payload.get("certifications", []),
            shift_type=payload.get("shift_type", "DAY"),
            employment_type=payload.get("employment_type", "FULL_TIME"),
            clinical_skills=payload.get("clinical_skills", []),
            languages_spoken=payload.get("languages_spoken", []),
            bio=payload.get("bio"),
            is_active=payload.get("is_active", True),
        )
        assignment = StaffDepartmentAssignment(
            id=uuid.uuid4(),
            hospital_id=current_user.hospital_id,
            staff_id=current_user.id,
            department_id=dep_id,
            is_primary=True,
            is_active=True,
        )
        self.tenant_db.add(profile)
        self.tenant_db.add(assignment)
        await self.tenant_db.commit()
        await self._mirror_staff_assignment(assignment)
        await self._mirror_upsert(NurseProfile, profile_id, _model_values_raw(profile))
        return _serialize_model(profile)

    async def patch_profile(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        if not payload:
            return await self.get_profile(current_user)
        q = await self.tenant_db.execute(
            select(NurseProfile).where(
                and_(NurseProfile.user_id == current_user.id, NurseProfile.hospital_id == current_user.hospital_id)
            )
        )
        profile = q.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail="Nurse profile not found. Create profile first.")
        if payload.get("department_id") is not None:
            dep_id = _require_uuid(payload["department_id"], "department_id")
            profile.department_id = dep_id
            assignment_q = await self.tenant_db.execute(
                select(StaffDepartmentAssignment).where(
                    and_(
                        StaffDepartmentAssignment.staff_id == current_user.id,
                        StaffDepartmentAssignment.hospital_id == current_user.hospital_id,
                    )
                )
            )
            assignment = assignment_q.scalars().first()
            if assignment:
                assignment.department_id = dep_id
                assignment.is_active = True
            else:
                assignment = StaffDepartmentAssignment(
                    id=uuid.uuid4(),
                    hospital_id=current_user.hospital_id,
                    staff_id=current_user.id,
                    department_id=dep_id,
                    is_primary=True,
                    is_active=True,
                )
                self.tenant_db.add(assignment)
            await self._mirror_staff_assignment(assignment)
        scalar_fields = (
            "nurse_id",
            "nursing_license_number",
            "designation",
            "specialization",
            "experience_years",
            "shift_type",
            "employment_type",
            "bio",
            "is_active",
        )
        list_fields = ("qualifications", "certifications", "clinical_skills", "languages_spoken")
        for key in scalar_fields:
            if key in payload and payload[key] is not None:
                setattr(profile, key, payload[key])
        for key in list_fields:
            if key in payload and payload[key] is not None:
                setattr(profile, key, payload[key])
        await self.tenant_db.commit()
        await self.tenant_db.refresh(profile)
        await self._mirror_upsert(NurseProfile, profile.id, _model_values_raw(profile))
        return await self.get_profile(current_user)

    async def get_dashboard(self, current_user: User) -> Dict[str, Any]:
        hid = current_user.hospital_id
        dept_ids, dept_names, _primary = await self._nurse_department_scope(current_user)
        dept_match = self._admission_department_match_conditions(hid, dept_ids, dept_names)
        admission_filters = [
            Admission.hospital_id == hid,
            Admission.is_active == True,
        ]
        if dept_match:
            admission_filters.append(or_(*dept_match))
        admissions_res = await self.tenant_db.execute(
            select(Admission.patient_id, Admission.discharge_summary_id).where(
                and_(*admission_filters)
            )
        )
        active_admissions = admissions_res.all()
        patient_ids = [row.patient_id for row in active_admissions]
        active_count = len(active_admissions)
        discharge_preparations = sum(1 for row in active_admissions if row.discharge_summary_id is None)

        critical_count = 0
        pending_meds = 0
        pending_lab_reports = 0
        if patient_ids:
            records_res = await self.tenant_db.execute(
                select(MedicalRecord.vital_signs, MedicalRecord.prescriptions, MedicalRecord.lab_orders)
                .where(
                    and_(
                        MedicalRecord.hospital_id == current_user.hospital_id,
                        MedicalRecord.patient_id.in_(patient_ids),
                    )
                )
                .order_by(desc(MedicalRecord.created_at))
                .limit(500)
            )

            medication_done_statuses = {"ADMINISTERED", "GIVEN", "COMPLETED", "CANCELLED", "STOPPED", "DISCONTINUED"}
            lab_done_statuses = {"RESULT_ENTERED", "APPROVED", "REPORTED", "COMPLETED", "CANCELLED", "REJECTED"}
            for vital_signs, prescriptions, lab_orders in records_res.all():
                if self._is_critical_vitals(vital_signs or {}):
                    critical_count += 1
                for med in _json_items(prescriptions):
                    if _is_pending_item(med, medication_done_statuses):
                        pending_meds += 1
                for lab_order in _json_items(lab_orders):
                    if _is_pending_item(lab_order, lab_done_statuses):
                        pending_lab_reports += 1

        profile = await self.get_profile(current_user)
        stats = {
            "assigned_patients": active_count,
            "active_patients": active_count,
            "critical_alerts": critical_count,
            "medication_rounds": pending_meds,
            "lab_reports_pending": pending_lab_reports,
            "discharge_preparations": discharge_preparations,
        }
        pending_tasks = {
            "medication_rounds": pending_meds,
            "lab_reports_pending": pending_lab_reports,
            "discharge_preparations": discharge_preparations,
        }
        return {
            "profile": profile,
            "stats": stats,
            "pending_tasks": pending_tasks,
            # Compatibility aliases for dashboard clients that read counters at top level.
            "assigned_patients": active_count,
            "active_patients": active_count,
            "critical_alerts": critical_count,
            "medication_rounds": pending_meds,
            "lab_reports_pending": pending_lab_reports,
            "discharge_preparations": discharge_preparations,
        }

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            text = str(value).strip()
            if not text:
                return None
            return float(text)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _is_critical_vitals(cls, vital_signs: Dict[str, Any]) -> bool:
        alert_flags = vital_signs.get("alert_flags") if isinstance(vital_signs, dict) else None
        if isinstance(alert_flags, dict) and alert_flags.get("critical") is True:
            return True

        temp = cls._as_float(vital_signs.get("temperature_f"))
        spo2 = cls._as_float(vital_signs.get("oxygen_saturation"))
        systolic = cls._as_float(vital_signs.get("blood_pressure_systolic"))
        diastolic = cls._as_float(vital_signs.get("blood_pressure_diastolic"))
        if temp is not None and temp >= 102:
            return True
        if spo2 is not None and spo2 < 92:
            return True
        if systolic is not None and systolic >= 180:
            return True
        if diastolic is not None and diastolic >= 120:
            return True
        return False

    async def list_assigned_patients(self, current_user: User) -> List[Dict[str, Any]]:
        hid = current_user.hospital_id
        dept_ids, dept_names, _primary = await self._nurse_department_scope(current_user)
        dept_match = self._admission_department_match_conditions(hid, dept_ids, dept_names)
        if not dept_match:
            return []
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.hospital_id == hid,
                    or_(*dept_match),
                    Admission.is_active == True,
                )
            )
            .options(*self._admission_load_options())
            .order_by(desc(Admission.created_at))
        )
        rows = []
        for admission in res.scalars().all():
            rows.append(await self._format_admission_row(admission))
        return rows

    async def create_vitals(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        admission = await self._resolve_admission(payload["admission_number"], current_user)
        rec_id = uuid.uuid4()
        vital_signs = {
            "blood_pressure_systolic": payload.get("blood_pressure_systolic"),
            "blood_pressure_diastolic": payload.get("blood_pressure_diastolic"),
            "pulse_rate": payload.get("pulse_rate"),
            "temperature_f": payload.get("temperature_f"),
            "respiratory_rate": payload.get("respiratory_rate"),
            "oxygen_saturation": payload.get("oxygen_saturation"),
            "weight": payload.get("weight"),
            "height": payload.get("height"),
            "pain_scale": payload.get("pain_scale"),
            "notes": payload.get("notes"),
            "recorded_by": str(current_user.id),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        vital_signs["alert_flags"] = {
            "critical": self._is_critical_vitals(vital_signs),
            "reasons": [
                reason
                for reason, cond in [
                    ("HIGH_FEVER", isinstance(vital_signs.get("temperature_f"), (int, float)) and vital_signs.get("temperature_f") >= 102),
                    ("LOW_OXYGEN", isinstance(vital_signs.get("oxygen_saturation"), (int, float)) and vital_signs.get("oxygen_saturation") < 92),
                    ("HIGH_BP", (
                        isinstance(vital_signs.get("blood_pressure_systolic"), (int, float)) and vital_signs.get("blood_pressure_systolic") >= 180
                    ) or (
                        isinstance(vital_signs.get("blood_pressure_diastolic"), (int, float)) and vital_signs.get("blood_pressure_diastolic") >= 120
                    )),
                ]
                if cond
            ],
        }
        rec = MedicalRecord(
            id=rec_id,
            hospital_id=current_user.hospital_id,
            patient_id=admission.patient_id,
            doctor_id=admission.doctor_id,
            chief_complaint="NURSE_VITALS_UPDATE",
            vital_signs=vital_signs,
            diagnosis=None,
            prescriptions=[],
            lab_orders=[],
            imaging_orders=[],
        )
        self.tenant_db.add(rec)
        await self.tenant_db.commit()
        # DO NOT MIRROR CLINICAL RECORDS TO PLATFORM DB
        #await self._mirror_upsert(MedicalRecord, rec_id, _model_values_raw(rec))
        return _serialize_model(rec)

    async def get_vitals(self, admission_number: str, current_user: User) -> List[Dict[str, Any]]:
        admission = await self._resolve_admission(admission_number, current_user)
        res = await self.tenant_db.execute(
            select(MedicalRecord)
            .where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.patient_id == admission.patient_id,
                    MedicalRecord.vital_signs.isnot(None),
                )
            )
            .order_by(desc(MedicalRecord.created_at))
        )
        return [_serialize_model(x) for x in res.scalars().all()]

    async def create_medication(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        admission = await self._resolve_admission(payload["admission_number"], current_user)
        rec_id = uuid.uuid4()
        meds = [
            {
                "medication_name": payload["medication_name"],
                "dose": payload["dose"],
                "scheduled_time": payload["scheduled_time"],
                "frequency": payload["frequency"],
                "start_date": payload.get("start_date"),
                "instructions": payload.get("instructions"),
                "status": payload.get("status", "PENDING"),
                "administered_by": str(current_user.id),
            }
        ]
        rec = MedicalRecord(
            id=rec_id,
            hospital_id=current_user.hospital_id,
            patient_id=admission.patient_id,
            doctor_id=admission.doctor_id,
            chief_complaint="NURSE_MEDICATION_ENTRY",
            vital_signs={},
            prescriptions=meds,
            lab_orders=[],
            imaging_orders=[],
        )
        self.tenant_db.add(rec)
        await self.tenant_db.commit()
        return _serialize_model(rec)

    async def get_medications(self, admission_number: str, current_user: User) -> List[Dict[str, Any]]:
        admission = await self._resolve_admission(admission_number, current_user)
        res = await self.tenant_db.execute(
            select(MedicalRecord)
            .where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.patient_id == admission.patient_id,
                )
            )
            .order_by(desc(MedicalRecord.created_at))
        )
        out: List[Dict[str, Any]] = []
        for r in res.scalars().all():
            row = _serialize_model(r)
            if row.get("prescriptions"):
                out.append(row)
        return out

    async def list_beds(self, current_user: User, ward_id: Optional[str], status_filter: Optional[str]) -> List[Dict[str, Any]]:
        query = select(Bed).where(Bed.hospital_id == current_user.hospital_id).options(selectinload(Bed.ward))
        if ward_id is not None and str(ward_id).strip() != "":
            query = query.where(Bed.ward_id == _require_uuid(ward_id, "ward_id"))
        if status_filter:
            query = query.where(Bed.status == status_filter)
        res = await self.tenant_db.execute(query.order_by(Bed.bed_number))
        rows = []
        for b in res.scalars().all():
            d = _serialize_model(b)
            d["ward_name"] = b.ward.name if b.ward else None
            rows.append(d)
        return rows

    async def create_bed(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        bed_id = uuid.uuid4()
        bed = Bed(
            id=bed_id,
            hospital_id=current_user.hospital_id,
            ward_id=_require_uuid(payload.get("ward_id"), "ward_id"),
            bed_number=payload["bed_number"],
            bed_code=payload["bed_code"],
            status=payload.get("status", "AVAILABLE"),
            bed_type=payload.get("bed_type", "STANDARD"),
            floor=payload.get("floor"),
            room_number=payload.get("room_number"),
            bed_position=payload.get("bed_position"),
            has_oxygen=payload.get("has_oxygen", False),
            has_suction=payload.get("has_suction", False),
            has_cardiac_monitor=payload.get("has_cardiac_monitor", False),
            has_ventilator=payload.get("has_ventilator", False),
            has_iv_pole=payload.get("has_iv_pole", True),
            daily_rate=payload.get("daily_rate", 0),
            notes=payload.get("notes"),
            settings=payload.get("settings", {}),
        )
        self.tenant_db.add(bed)
        await self.tenant_db.commit()
        await self._mirror_upsert(Bed, bed_id, _model_values_raw(bed))
        return _serialize_model(bed)

    async def list_lab_tests(self, current_user: User, admission_number: Optional[str]) -> List[Dict[str, Any]]:
        if admission_number:
            admission = await self._resolve_admission(admission_number, current_user)
            query = select(MedicalRecord).where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.patient_id == admission.patient_id,
                )
            )
        else:
            query = select(MedicalRecord).where(MedicalRecord.hospital_id == current_user.hospital_id)
        res = await self.tenant_db.execute(query.order_by(desc(MedicalRecord.created_at)))
        out: List[Dict[str, Any]] = []
        for r in res.scalars().all():
            row = _serialize_model(r)
            if row.get("lab_orders"):
                out.append(row)
        return out

    async def create_lab_request(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        admission = await self._resolve_admission(payload["admission_number"], current_user)
        rec_id = uuid.uuid4()
        rec = MedicalRecord(
            id=rec_id,
            hospital_id=current_user.hospital_id,
            patient_id=admission.patient_id,
            doctor_id=admission.doctor_id,
            chief_complaint="NURSE_LAB_REQUEST",
            vital_signs={},
            prescriptions=[],
            lab_orders=[
                {
                    "test_type": payload["test_type"],
                    "reason_for_test": payload.get("reason_for_test"),
                    "priority": payload.get("priority", "ROUTINE"),
                    "requesting_doctor": payload.get("requesting_doctor"),
                    "notes": payload.get("notes"),
                    "requested_by": str(current_user.id),
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            imaging_orders=[],
        )
        self.tenant_db.add(rec)
        await self.tenant_db.commit()
        await self._mirror_upsert(MedicalRecord, rec_id, _model_values_raw(rec))
        return _serialize_model(rec)

    async def create_note(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        if payload.get("admission_number"):
            admission = await self._resolve_admission(payload["admission_number"], current_user)
        elif payload.get("patient_ref"):
            admission = await self._resolve_admission_by_patient_ref(payload["patient_ref"], current_user)
        else:
            raise HTTPException(status_code=400, detail="admission_number or patient_ref is required")
        rec_id = uuid.uuid4()
        note_text = payload.get("note_content") or payload.get("details") or ""
        if not note_text:
            raise HTTPException(status_code=400, detail="note_content/details is required")
        note_block = {
            "note_type": payload["note_type"],
            "observation_title": payload.get("observation_title"),
            "details": payload.get("details"),
            "note_content": note_text,
            "priority": payload.get("priority", "NORMAL"),
            "follow_up_required": payload.get("follow_up_required", False),
            "recorded_by": str(current_user.id),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        rec = MedicalRecord(
            id=rec_id,
            hospital_id=current_user.hospital_id,
            patient_id=admission.patient_id,
            doctor_id=admission.doctor_id,
            chief_complaint="NURSE_NOTE",
            history_of_present_illness=note_text,
            vital_signs={"nursing_note": note_block},
            prescriptions=[],
            lab_orders=[],
            imaging_orders=[],
        )
        self.tenant_db.add(rec)
        await self.tenant_db.commit()
        await self._mirror_upsert(MedicalRecord, rec_id, _model_values_raw(rec))
        return _serialize_model(rec)

    async def list_notes(self, admission_number: str, current_user: User) -> List[Dict[str, Any]]:
        admission = await self._resolve_admission(admission_number, current_user)
        res = await self.tenant_db.execute(
            select(MedicalRecord)
            .where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.patient_id == admission.patient_id,
                )
            )
            .order_by(desc(MedicalRecord.created_at))
        )
        rows = []
        for r in res.scalars().all():
            d = _serialize_model(r)
            if d.get("chief_complaint") == "NURSE_NOTE" or (d.get("vital_signs") or {}).get("nursing_note"):
                rows.append(d)
        return rows

    async def create_discharge_summary(self, payload: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        if payload.get("admission_number"):
            admission = await self._resolve_admission(payload["admission_number"], current_user)
        elif payload.get("patient_ref"):
            admission = await self._resolve_admission_by_patient_ref(payload["patient_ref"], current_user)
        else:
            raise HTTPException(status_code=400, detail="admission_number or patient_ref is required")
        ds_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        summary = DischargeSummary(
            id=ds_id,
            hospital_id=current_user.hospital_id,
            patient_id=admission.patient_id,
            doctor_id=admission.doctor_id,
            admission_date=admission.admission_date,
            discharge_date=now,
            length_of_stay=max((now - admission.admission_date).days, 0),
            chief_complaint=admission.chief_complaint,
            final_diagnosis=payload["final_diagnosis"],
            secondary_diagnoses=payload.get("secondary_diagnoses", []),
            procedures_performed=payload.get("procedures_performed", []),
            hospital_course=payload.get("hospital_course"),
            medications_on_discharge=payload.get("medications_on_discharge", []),
            follow_up_instructions=payload.get("follow_up_instructions"),
            diet_instructions=payload.get("diet_instructions"),
            activity_restrictions=payload.get("activity_restrictions"),
            follow_up_date=payload.get("follow_up_date"),
            follow_up_doctor=payload.get("follow_up_doctor"),
            condition_on_discharge=payload.get("condition_on_discharge"),
            discharge_notes=payload.get("discharge_notes"),
        )
        self.tenant_db.add(summary)
        admission.discharge_summary_id = ds_id
        admission.discharge_date = now
        admission.is_active = False
        await self.tenant_db.commit()
        await self._mirror_upsert(DischargeSummary, ds_id, _model_values_raw(summary))
        await self._mirror_upsert(Admission, admission.id, _model_values_raw(admission))
        return _serialize_model(summary)

    async def list_discharge_support(self, current_user: User) -> List[Dict[str, Any]]:
        hid = current_user.hospital_id
        dept_ids, dept_names, _primary = await self._nurse_department_scope(current_user)
        dept_match = self._admission_department_match_conditions(hid, dept_ids, dept_names)
        if not dept_match:
            return []
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.hospital_id == hid,
                    or_(*dept_match),
                    Admission.is_active == True,
                )
            )
            .options(*self._admission_load_options())
            .order_by(desc(Admission.created_at))
        )
        rows: List[Dict[str, Any]] = []
        for admission in res.scalars().all():
            rows.append(await self._format_admission_row(admission))
        return rows

    async def update_vitals(
        self,
        record_id: uuid.UUID,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        rec = await self._get_medical_record_for_nurse(
            record_id, current_user, expected_complaints=["NURSE_VITALS_UPDATE"]
        )
        vital_signs = dict(rec.vital_signs or {})
        for key in (
            "blood_pressure_systolic",
            "blood_pressure_diastolic",
            "pulse_rate",
            "temperature_f",
            "respiratory_rate",
            "oxygen_saturation",
            "weight",
            "height",
            "pain_scale",
            "notes",
        ):
            if key in payload and payload[key] is not None:
                vital_signs[key] = payload[key]
        vital_signs["recorded_by"] = str(current_user.id)
        vital_signs["recorded_at"] = datetime.now(timezone.utc).isoformat()
        vital_signs["alert_flags"] = {
            "critical": self._is_critical_vitals(vital_signs),
            "reasons": [
                reason
                for reason, cond in [
                    ("HIGH_FEVER", isinstance(vital_signs.get("temperature_f"), (int, float)) and vital_signs.get("temperature_f") >= 102),
                    ("LOW_OXYGEN", isinstance(vital_signs.get("oxygen_saturation"), (int, float)) and vital_signs.get("oxygen_saturation") < 92),
                    ("HIGH_BP", (
                        isinstance(vital_signs.get("blood_pressure_systolic"), (int, float)) and vital_signs.get("blood_pressure_systolic") >= 180
                    ) or (
                        isinstance(vital_signs.get("blood_pressure_diastolic"), (int, float)) and vital_signs.get("blood_pressure_diastolic") >= 120
                    )),
                ]
                if cond
            ],
        }
        rec.vital_signs = vital_signs
        await self.tenant_db.commit()
        await self.tenant_db.refresh(rec)
        return _serialize_model(rec)

    async def update_medication(
        self,
        record_id: uuid.UUID,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        rec = await self._get_medical_record_for_nurse(
            record_id, current_user, expected_complaints=["NURSE_MEDICATION_ENTRY"]
        )
        meds = _json_items(rec.prescriptions)
        if not meds:
            raise HTTPException(status_code=404, detail="No medication entry on this record")
        med = dict(meds[0])
        for key in (
            "medication_name",
            "dose",
            "scheduled_time",
            "frequency",
            "start_date",
            "instructions",
            "status",
        ):
            if key in payload and payload[key] is not None:
                med[key] = payload[key]
        med["updated_by"] = str(current_user.id)
        med["updated_at"] = datetime.now(timezone.utc).isoformat()
        rec.prescriptions = [med]
        await self.tenant_db.commit()
        await self.tenant_db.refresh(rec)
        return _serialize_model(rec)

    async def update_bed(
        self,
        bed_id: uuid.UUID,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        bed = await self.tenant_db.get(Bed, bed_id)
        if not bed or bed.hospital_id != current_user.hospital_id:
            raise HTTPException(status_code=404, detail="Bed not found")
        for key in (
            "bed_number",
            "bed_code",
            "status",
            "bed_type",
            "floor",
            "room_number",
            "bed_position",
            "has_oxygen",
            "has_suction",
            "has_cardiac_monitor",
            "has_ventilator",
            "has_iv_pole",
            "daily_rate",
            "notes",
            "settings",
        ):
            if key in payload and payload[key] is not None:
                setattr(bed, key, payload[key])
        await self.tenant_db.commit()
        await self.tenant_db.refresh(bed)
        await self._mirror_upsert(Bed, bed.id, _model_values_raw(bed))
        d = _serialize_model(bed)
        ward_res = await self.tenant_db.execute(select(Ward).where(Ward.id == bed.ward_id))
        ward = ward_res.scalar_one_or_none()
        d["ward_name"] = ward.name if ward else None
        return d

    async def update_lab_request(
        self,
        record_id: uuid.UUID,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        rec = await self._get_medical_record_for_nurse(
            record_id, current_user, expected_complaints=["NURSE_LAB_REQUEST"]
        )
        orders = _json_items(rec.lab_orders)
        if not orders:
            raise HTTPException(status_code=404, detail="No lab request on this record")
        order = dict(orders[0])
        for key in ("test_type", "reason_for_test", "priority", "requesting_doctor", "notes"):
            if key in payload and payload[key] is not None:
                order[key] = payload[key]
        order["updated_by"] = str(current_user.id)
        order["updated_at"] = datetime.now(timezone.utc).isoformat()
        rec.lab_orders = [order]
        await self.tenant_db.commit()
        await self.tenant_db.refresh(rec)
        return _serialize_model(rec)

    async def update_note(
        self,
        record_id: uuid.UUID,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        rec = await self._get_medical_record_for_nurse(
            record_id, current_user, expected_complaints=["NURSE_NOTE"]
        )
        note_block = dict((rec.vital_signs or {}).get("nursing_note") or {})
        for key in ("note_type", "observation_title", "details", "priority", "follow_up_required"):
            if key in payload and payload[key] is not None:
                note_block[key] = payload[key]
        if payload.get("note_content") is not None:
            note_block["note_content"] = payload["note_content"]
            rec.history_of_present_illness = payload["note_content"]
        elif payload.get("details") is not None:
            note_block["note_content"] = payload["details"]
            rec.history_of_present_illness = payload["details"]
        note_block["updated_by"] = str(current_user.id)
        note_block["updated_at"] = datetime.now(timezone.utc).isoformat()
        vs = dict(rec.vital_signs or {})
        vs["nursing_note"] = note_block
        rec.vital_signs = vs
        await self.tenant_db.commit()
        await self.tenant_db.refresh(rec)
        return _serialize_model(rec)

    async def update_discharge_summary(
        self,
        admission_number: str,
        payload: Dict[str, Any],
        current_user: User,
    ) -> Dict[str, Any]:
        admission = await self._resolve_admission(admission_number, current_user)
        if not admission.discharge_summary_id:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        summary = await self.tenant_db.get(DischargeSummary, admission.discharge_summary_id)
        if not summary:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        for key in (
            "final_diagnosis",
            "secondary_diagnoses",
            "procedures_performed",
            "hospital_course",
            "medications_on_discharge",
            "follow_up_instructions",
            "diet_instructions",
            "activity_restrictions",
            "follow_up_date",
            "follow_up_doctor",
            "condition_on_discharge",
            "discharge_notes",
        ):
            if key in payload and payload[key] is not None:
                setattr(summary, key, payload[key])
        await self.tenant_db.commit()
        await self.tenant_db.refresh(summary)
        await self._mirror_upsert(DischargeSummary, summary.id, _model_values_raw(summary))
        return _serialize_model(summary)

    async def get_discharge_summary(self, admission_number: str, current_user: User) -> Dict[str, Any]:
        admission = await self._resolve_admission(admission_number, current_user)
        if not admission.discharge_summary_id:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        s = await self.tenant_db.get(DischargeSummary, admission.discharge_summary_id)
        if not s:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        return _serialize_model(s)
