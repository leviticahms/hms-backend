"""
Nurse module service (tenant-first reads + mirrored writes).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.hospital import Bed, StaffDepartmentAssignment, Ward
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


class NurseService:
    def __init__(self, tenant_db: AsyncSession, platform_db: AsyncSession):
        self.tenant_db = tenant_db
        self.platform_db = platform_db

    async def _get_nurse_department(self, current_user: User) -> StaffDepartmentAssignment:
        row = await self.tenant_db.execute(
            select(StaffDepartmentAssignment)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == current_user.id,
                    StaffDepartmentAssignment.hospital_id == current_user.hospital_id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .order_by(StaffDepartmentAssignment.is_primary.desc(), StaffDepartmentAssignment.created_at.desc())
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = row.scalars().first()
        if not assignment:
            raise HTTPException(status_code=404, detail="Nurse department assignment not found")
        return assignment

    async def _resolve_admission(self, admission_number: str, current_user: User) -> Admission:
        assignment = await self._get_nurse_department(current_user)
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.admission_number == admission_number,
                    Admission.hospital_id == current_user.hospital_id,
                    Admission.department_id == assignment.department_id,
                )
            )
            .options(selectinload(Admission.patient).selectinload(PatientProfile.user))
        )
        admission = res.scalar_one_or_none()
        if not admission:
            raise HTTPException(status_code=404, detail="Admission not found in nurse department")
        return admission

    async def _mirror_upsert(self, model: Type[Any], model_id: uuid.UUID, values: Dict[str, Any]) -> None:
        # Enforce persistence in both DBs.
        existing = await self.platform_db.get(model, model_id)
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            self.platform_db.add(model(**values))
        await self.platform_db.commit()

    async def _resolve_admission_by_patient_ref(self, patient_ref: str, current_user: User) -> Admission:
        assignment = await self._get_nurse_department(current_user)
        patient_row = await self.tenant_db.execute(
            select(PatientProfile).where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == current_user.hospital_id,
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
                    Admission.hospital_id == current_user.hospital_id,
                    Admission.department_id == assignment.department_id,
                    Admission.is_active == True,
                )
            )
            .order_by(desc(Admission.created_at))
            .options(selectinload(Admission.patient).selectinload(PatientProfile.user))
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
        dep_id = uuid.UUID(payload["department_id"])
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
        self.tenant_db.add(profile)
        await self.tenant_db.commit()
        await self._mirror_upsert(NurseProfile, profile_id, _model_values_raw(profile))
        return _serialize_model(profile)

    async def get_dashboard(self, current_user: User) -> Dict[str, Any]:
        assignment = await self._get_nurse_department(current_user)
        admissions = await self.tenant_db.execute(
            select(func.count(Admission.id)).where(
                and_(
                    Admission.hospital_id == current_user.hospital_id,
                    Admission.department_id == assignment.department_id,
                    Admission.is_active == True,
                )
            )
        )
        active_count = admissions.scalar() or 0
        recent_vitals = await self.tenant_db.execute(
            select(MedicalRecord.vital_signs)
            .where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.vital_signs.isnot(None),
                )
            )
            .order_by(desc(MedicalRecord.created_at))
            .limit(200)
        )
        critical_count = 0
        for row in recent_vitals.all():
            vs = row[0] or {}
            if self._is_critical_vitals(vs):
                critical_count += 1

        pending_meds_q = await self.tenant_db.execute(
            select(MedicalRecord.prescriptions).where(
                and_(
                    MedicalRecord.hospital_id == current_user.hospital_id,
                    MedicalRecord.prescriptions.isnot(None),
                )
            )
        )
        pending_meds = 0
        for row in pending_meds_q.all():
            for med in row[0] or []:
                if str(med.get("status", "")).upper() == "PENDING":
                    pending_meds += 1
        profile = await self.get_profile(current_user)
        return {
            "profile": profile,
            "stats": {
                "active_patients": active_count,
                "critical_alerts": critical_count,
            },
            "pending_tasks": {
                "medication_rounds": pending_meds,
                "lab_reports_pending": 0,
                "discharge_preparations": 0,
            },
        }

    @staticmethod
    def _is_critical_vitals(vital_signs: Dict[str, Any]) -> bool:
        temp = vital_signs.get("temperature_f")
        spo2 = vital_signs.get("oxygen_saturation")
        systolic = vital_signs.get("blood_pressure_systolic")
        diastolic = vital_signs.get("blood_pressure_diastolic")
        if isinstance(temp, (int, float)) and temp >= 102:
            return True
        if isinstance(spo2, (int, float)) and spo2 < 92:
            return True
        if isinstance(systolic, (int, float)) and systolic >= 180:
            return True
        if isinstance(diastolic, (int, float)) and diastolic >= 120:
            return True
        return False

    async def list_assigned_patients(self, current_user: User) -> List[Dict[str, Any]]:
        assignment = await self._get_nurse_department(current_user)
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.hospital_id == current_user.hospital_id,
                    Admission.department_id == assignment.department_id,
                    Admission.is_active == True,
                )
            )
            .options(selectinload(Admission.patient).selectinload(PatientProfile.user), selectinload(Admission.doctor))
            .order_by(desc(Admission.created_at))
        )
        rows = []
        for a in res.scalars().all():
            d = _serialize_model(a)
            d["patient_name"] = (
                f"{a.patient.user.first_name} {a.patient.user.last_name}".strip()
                if a.patient and a.patient.user
                else None
            )
            d["patient_ref"] = a.patient.patient_id if a.patient else None
            d["doctor_name"] = f"{a.doctor.first_name} {a.doctor.last_name}".strip() if a.doctor else None
            rows.append(d)
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
        await self._mirror_upsert(MedicalRecord, rec_id, _model_values_raw(rec))
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
        await self._mirror_upsert(MedicalRecord, rec_id, _model_values_raw(rec))
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
        if ward_id:
            query = query.where(Bed.ward_id == uuid.UUID(ward_id))
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
            ward_id=uuid.UUID(payload["ward_id"]),
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
        assignment = await self._get_nurse_department(current_user)
        res = await self.tenant_db.execute(
            select(Admission)
            .where(
                and_(
                    Admission.hospital_id == current_user.hospital_id,
                    Admission.department_id == assignment.department_id,
                    Admission.is_active == True,
                )
            )
            .options(selectinload(Admission.patient).selectinload(PatientProfile.user), selectinload(Admission.doctor))
            .order_by(desc(Admission.created_at))
        )
        rows: List[Dict[str, Any]] = []
        for a in res.scalars().all():
            d = _serialize_model(a)
            d["patient_ref"] = a.patient.patient_id if a.patient else None
            d["patient_name"] = (
                f"{a.patient.user.first_name} {a.patient.user.last_name}".strip()
                if a.patient and a.patient.user
                else None
            )
            d["doctor_name"] = f"{a.doctor.first_name} {a.doctor.last_name}".strip() if a.doctor else None
            rows.append(d)
        return rows

    async def get_discharge_summary(self, admission_number: str, current_user: User) -> Dict[str, Any]:
        admission = await self._resolve_admission(admission_number, current_user)
        if not admission.discharge_summary_id:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        s = await self.tenant_db.get(DischargeSummary, admission.discharge_summary_id)
        if not s:
            raise HTTPException(status_code=404, detail="Discharge summary not found")
        return _serialize_model(s)
