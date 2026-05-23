"""
Receptionist-facing directory and appointment lookup APIs.

These endpoints power front-desk dropdowns and management tables.

Doctor and department listings use ``get_db_session`` (tenant DB when the hospital
has ``tenant_database_name``), matching where staff and profiles are stored.
Appointment queue / slot helpers that read platform OPD rows still use
``get_platform_db_session``.
"""
from __future__ import annotations

import uuid
from datetime import date as date_type
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_receptionist
from app.core.database import get_db_session, get_platform_db_session
from app.core.enums import AppointmentStatus, UserRole
from app.core.response_utils import success_response
from app.models.doctor import DoctorProfile
from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.nurse import NurseProfile
from app.models.patient import Admission, Appointment, PatientProfile
from app.models.user import Role, User, user_roles
from app.services.appointment_service import AppointmentService
from app.utils.hospital_id_resolve import resolve_effective_hospital_id
from app.utils.doctor_department_resolve import (
    doctor_department_display,
    resolve_doctor_departments_multi_session,
)
from app.utils.receptionist_serializers import serialize_opd_appointment_full

directory_router = APIRouter()
appointments_router = APIRouter(prefix="/receptionist")

DOCTOR_TAG = "Receptionist - Doctors Management"
DEPARTMENT_TAG = "Receptionist - Departments"
APPOINTMENT_TAG = "Receptionist - Appointment Scheduling"


def _status_label(raw: Optional[str]) -> str:
    value = (raw or "").strip().upper()
    return "Active" if value == "ACTIVE" else "Inactive"


def _availability_label(doctor: DoctorProfile, active_statuses: set[str]) -> str:
    meta = getattr(doctor.user, "user_metadata", None) or {}
    explicit = (meta.get("availability") or meta.get("doctor_availability") or "").strip()
    if explicit:
        return explicit
    if str(doctor.user_id) in active_statuses:
        return "In Consultation"
    return "Available" if doctor.is_accepting_new_patients else "Unavailable"


def _qualification_text(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(str(v) for v in values if str(v).strip())
    return str(values or "")


def _department_hours(department: Department) -> str:
    if department.is_24x7:
        return "24x7"
    if department.opening_time and department.closing_time:
        return f"{department.opening_time.strftime('%H:%M')} - {department.closing_time.strftime('%H:%M')}"
    return ""


async def _hospital_id(db: AsyncSession, current_user: User) -> uuid.UUID:
    hospital_id = current_user.hospital_id or await resolve_effective_hospital_id(db, current_user)
    if not hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Receptionist must be associated with a hospital",
        )
    return hospital_id


async def _department_ids_for_filter(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    department: Optional[str],
) -> Optional[list[uuid.UUID]]:
    raw = (department or "").strip()
    if not raw:
        return None
    conditions = [Department.hospital_id == hospital_id, Department.is_active == True]
    try:
        dept_uuid = uuid.UUID(raw)
        conditions.append(Department.id == dept_uuid)
    except ValueError:
        like = f"%{raw}%"
        conditions.append(or_(Department.name.ilike(like), Department.code.ilike(like)))
    result = await db.execute(select(Department.id).where(and_(*conditions)))
    return list(result.scalars().all())


async def _active_doctor_user_ids_for_today(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> set[str]:
    today = date_type.today().isoformat()
    result = await db.execute(
        select(Appointment.doctor_id)
        .where(
            and_(
                Appointment.hospital_id == hospital_id,
                Appointment.appointment_date == today,
                Appointment.status.in_(["CHECKED_IN", "IN_PROGRESS", AppointmentStatus.CONFIRMED.value]),
            )
        )
        .distinct()
    )
    return {str(row) for row in result.scalars().all()}


def _doctor_payload(
    doctor: DoctorProfile,
    active_statuses: set[str],
    department_override: Optional[Department] = None,
) -> dict[str, Any]:
    user = doctor.user
    department = department_override or doctor.department
    dept_name, dept_id = doctor_department_display(
        department,
        user,
        specialization=doctor.specialization,
    )
    availability = _availability_label(doctor, active_statuses)
    return {
        "id": str(user.id),
        "doctorId": str(user.id),
        "doctorProfileId": str(doctor.id),
        "doctorCode": doctor.doctor_id,
        "name": f"Dr. {user.first_name} {user.last_name}".strip(),
        "gender": (user.user_metadata or {}).get("gender") or "",
        "qualification": _qualification_text(doctor.qualifications),
        "specialization": doctor.specialization,
        "experience": doctor.experience_years,
        "experienceUnit": "Years",
        "department": dept_name,
        "departmentId": str(dept_id or doctor.department_id or ""),
        "consultationFee": float(doctor.consultation_fee or 0),
        "email": user.email,
        "phone": user.phone,
        "emergencyContact": (user.user_metadata or {}).get("emergency_contact")
        or (user.user_metadata or {}).get("emergencyContact")
        or "",
        "licenseNumber": doctor.medical_license_number,
        "status": _status_label(user.status),
        "availability": availability,
        "isAvailable": availability.lower() == "available",
        "designation": doctor.designation,
        "subSpecialization": doctor.sub_specialization,
        "contact": {"email": user.email, "phone": user.phone},
    }


async def _doctor_query(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    keyword: Optional[str] = None,
    department: Optional[str] = None,
    status_filter: Optional[str] = None,
    availability: Optional[str] = None,
    platform_db: Optional[AsyncSession] = None,
):
    query = (
        select(DoctorProfile)
        .join(User, DoctorProfile.user_id == User.id)
        .join(user_roles, User.id == user_roles.c.user_id)
        .join(Role, user_roles.c.role_id == Role.id)
        .where(
            and_(
                DoctorProfile.hospital_id == hospital_id,
                User.hospital_id == hospital_id,
                Role.name == UserRole.DOCTOR.value,
            )
        )
        .options(selectinload(DoctorProfile.user), selectinload(DoctorProfile.department))
    )
    dept_ids = await _department_ids_for_filter(db, hospital_id, department)
    if dept_ids is not None:
        if not dept_ids:
            return []
        assigned_staff = (
            select(StaffDepartmentAssignment.staff_id)
            .where(
                and_(
                    StaffDepartmentAssignment.hospital_id == hospital_id,
                    StaffDepartmentAssignment.department_id.in_(dept_ids),
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .distinct()
        )
        query = query.where(
            or_(
                DoctorProfile.department_id.in_(dept_ids),
                DoctorProfile.user_id.in_(assigned_staff),
            )
        )
    if keyword:
        term = f"%{keyword.strip()}%"
        full_name = func.concat(User.first_name, " ", User.last_name)
        query = query.where(
            or_(
                full_name.ilike(term),
                User.first_name.ilike(term),
                User.last_name.ilike(term),
                User.email.ilike(term),
                User.phone.ilike(term),
                User.staff_id.ilike(term),
                DoctorProfile.doctor_id.ilike(term),
                DoctorProfile.specialization.ilike(term),
                DoctorProfile.medical_license_number.ilike(term),
            )
        )
    if status_filter:
        status_upper = status_filter.strip().upper()
        query = query.where(User.status == status_upper)
    result = await db.execute(query.order_by(User.first_name, User.last_name))
    doctors = result.scalars().all()
    active_statuses = await _active_doctor_user_ids_for_today(db, hospital_id)

    doctor_user_ids = [d.user_id for d in doctors if d.user_id]
    sessions = [db]
    if platform_db is not None and id(platform_db) != id(db):
        sessions.append(platform_db)
    dept_by_user = await resolve_doctor_departments_multi_session(
        sessions, hospital_id, doctor_user_ids
    )

    rows = [
        _doctor_payload(
            doctor,
            active_statuses,
            department_override=dept_by_user.get(doctor.user_id),
        )
        for doctor in doctors
    ]
    if availability:
        wanted = availability.strip().lower()
        rows = [row for row in rows if row["availability"].strip().lower() == wanted]
    return rows


@directory_router.get("/doctors", tags=[DOCTOR_TAG])
async def get_all_doctors(
    department: Optional[str] = Query(None, description="Department name, code, or UUID"),
    status: Optional[str] = Query(None, description="Active / Inactive"),
    availability: Optional[str] = Query(None, description="Available / In Consultation / Unavailable"),
    keyword: Optional[str] = Query(None, description="Optional search keyword"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    doctors = await _doctor_query(
        db,
        hospital_id,
        keyword=keyword,
        department=department,
        status_filter=status,
        availability=availability,
        platform_db=platform_db,
    )
    return success_response(message="Doctors retrieved successfully", data={"doctors": doctors, "total": len(doctors)})


@directory_router.get("/doctors/search", tags=[DOCTOR_TAG])
async def search_doctors(
    keyword: str = Query(..., min_length=1),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    doctors = await _doctor_query(
        db, hospital_id, keyword=keyword, platform_db=platform_db
    )
    return success_response(message="Doctor search completed successfully", data={"doctors": doctors, "total": len(doctors)})


@directory_router.get("/doctors/dropdown", tags=[DOCTOR_TAG])
async def get_doctor_dropdown(
    department: Optional[str] = Query(None, description="Optional department name/code/UUID"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    doctors = await _doctor_query(
        db,
        hospital_id,
        department=department,
        status_filter="ACTIVE",
        platform_db=platform_db,
    )
    items = [
        {
            "id": row["doctorId"],
            "doctorId": row["doctorId"],
            "doctorProfileId": row["doctorProfileId"],
            "name": row["name"],
            "department": row["department"],
            "specialization": row["specialization"],
            "availability": row["availability"],
        }
        for row in doctors
    ]
    return success_response(message="Doctor dropdown retrieved successfully", data={"doctors": items, "total": len(items)})


@directory_router.get("/doctors/statistics", tags=[DOCTOR_TAG])
async def get_doctor_statistics(
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    doctors = await _doctor_query(db, hospital_id, platform_db=platform_db)
    return success_response(
        message="Doctor statistics retrieved successfully",
        data={
            "totalDoctors": len(doctors),
            "available": sum(1 for row in doctors if row["availability"].lower() == "available"),
            "inConsultation": sum(1 for row in doctors if row["availability"].lower() == "in consultation"),
            "onLeave": sum(1 for row in doctors if row["availability"].lower() == "on leave"),
            "active": sum(1 for row in doctors if row["status"].lower() == "active"),
            "inactive": sum(1 for row in doctors if row["status"].lower() == "inactive"),
        },
    )


@directory_router.get("/doctors/{doctor_id}", tags=[DOCTOR_TAG])
async def get_doctor_by_id(
    doctor_id: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    try:
        doc_uuid = uuid.UUID(doctor_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="doctorId must be a valid UUID")
    result = await db.execute(
        select(DoctorProfile)
        .join(User, DoctorProfile.user_id == User.id)
        .where(
            and_(
                DoctorProfile.hospital_id == hospital_id,
                or_(DoctorProfile.id == doc_uuid, DoctorProfile.user_id == doc_uuid),
            )
        )
        .options(selectinload(DoctorProfile.user), selectinload(DoctorProfile.department))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    active_statuses = await _active_doctor_user_ids_for_today(db, hospital_id)
    sessions = [db]
    if id(platform_db) != id(db):
        sessions.append(platform_db)
    dept_map = await resolve_doctor_departments_multi_session(
        sessions, hospital_id, [doctor.user_id]
    )
    return success_response(
        message="Doctor retrieved successfully",
        data=_doctor_payload(
            doctor,
            active_statuses,
            department_override=dept_map.get(doctor.user_id),
        ),
    )


def _department_payload(
    department: Department,
    doctor_count: int,
    nurse_count: int,
    active_admissions: int,
) -> dict[str, Any]:
    settings = department.settings if isinstance(department.settings, dict) else {}
    available_beds = max((department.bed_capacity or 0) - active_admissions, 0)
    head = department.head_doctor
    return {
        "id": str(department.id),
        "departmentId": str(department.id),
        "name": department.name,
        "code": department.code,
        "head": f"Dr. {head.first_name} {head.last_name}".strip() if head else "",
        "headDoctorId": str(department.head_doctor_id) if department.head_doctor_id else None,
        "location": department.location or "",
        "phone": department.phone or "",
        "email": department.email or "",
        "operatingHours": _department_hours(department),
        "bedCapacity": department.bed_capacity or 0,
        "description": department.description or "",
        "specializations": settings.get("specializations") or settings.get("specialization") or "",
        "equipmentList": settings.get("equipmentList") or settings.get("equipment_list") or settings.get("equipment") or "",
        "emergencyAvailable": bool(department.is_emergency),
        "status": "Active" if department.is_active else "Inactive",
        "availableBeds": available_beds,
        "occupiedBeds": active_admissions,
        "doctorCount": doctor_count,
        "nurseCount": nurse_count,
        "isIcu": bool(department.is_icu),
        "is24x7": bool(department.is_24x7),
    }


async def _department_counts(db: AsyncSession, hospital_id: uuid.UUID) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    doctor_rows = await db.execute(
        select(DoctorProfile.department_id, func.count(DoctorProfile.id))
        .where(DoctorProfile.hospital_id == hospital_id)
        .group_by(DoctorProfile.department_id)
    )
    nurse_rows = await db.execute(
        select(NurseProfile.department_id, func.count(NurseProfile.id))
        .where(NurseProfile.hospital_id == hospital_id)
        .group_by(NurseProfile.department_id)
    )
    admission_rows = await db.execute(
        select(Admission.department_id, func.count(Admission.id))
        .where(and_(Admission.hospital_id == hospital_id, Admission.is_active == True))
        .group_by(Admission.department_id)
    )
    return (
        {row[0]: row[1] for row in doctor_rows.all()},
        {row[0]: row[1] for row in nurse_rows.all()},
        {row[0]: row[1] for row in admission_rows.all()},
    )


async def _department_query(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    keyword: Optional[str] = None,
    status_filter: Optional[str] = None,
    emergency_available: Optional[bool] = None,
) -> list[dict[str, Any]]:
    query = (
        select(Department)
        .where(Department.hospital_id == hospital_id)
        .options(selectinload(Department.head_doctor))
        .order_by(Department.name)
    )
    if keyword:
        term = f"%{keyword.strip()}%"
        query = query.where(or_(Department.name.ilike(term), Department.code.ilike(term), Department.description.ilike(term)))
    if status_filter:
        query = query.where(Department.is_active == (status_filter.strip().lower() == "active"))
    if emergency_available is not None:
        query = query.where(Department.is_emergency == emergency_available)
    result = await db.execute(query)
    departments = result.scalars().all()
    doctor_counts, nurse_counts, admission_counts = await _department_counts(db, hospital_id)
    return [
        _department_payload(
            department,
            doctor_counts.get(department.id, 0),
            nurse_counts.get(department.id, 0),
            admission_counts.get(department.id, 0),
        )
        for department in departments
    ]


@directory_router.get("/departments", tags=[DEPARTMENT_TAG])
async def get_all_departments(
    status: Optional[str] = Query(None, description="Active / Inactive"),
    emergencyAvailable: Optional[bool] = Query(None, description="Filter emergency-ready departments"),
    keyword: Optional[str] = Query(None),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    departments = await _department_query(
        db,
        hospital_id,
        keyword=keyword,
        status_filter=status,
        emergency_available=emergencyAvailable,
    )
    return success_response(
        message="Departments retrieved successfully",
        data={"departments": departments, "total": len(departments)},
    )


@directory_router.get("/departments/search", tags=[DEPARTMENT_TAG])
async def search_departments(
    keyword: str = Query(..., min_length=1),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    departments = await _department_query(db, hospital_id, keyword=keyword)
    return success_response(
        message="Department search completed successfully",
        data={"departments": departments, "total": len(departments)},
    )


@directory_router.get("/departments/dropdown", tags=[DEPARTMENT_TAG])
async def get_department_dropdown(
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    departments = await _department_query(db, hospital_id, status_filter="Active")
    items = [
        {
            "id": row["departmentId"],
            "departmentId": row["departmentId"],
            "name": row["name"],
            "code": row["code"],
            "emergencyAvailable": row["emergencyAvailable"],
        }
        for row in departments
    ]
    return success_response(message="Department dropdown retrieved successfully", data={"departments": items, "total": len(items)})


@directory_router.get("/departments/statistics", tags=[DEPARTMENT_TAG])
async def get_department_statistics(
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    departments = await _department_query(db, hospital_id)
    return success_response(
        message="Department statistics retrieved successfully",
        data={
            "totalDepartments": len(departments),
            "activeDepartments": sum(1 for row in departments if row["status"].lower() == "active"),
            "emergencyReady": sum(1 for row in departments if row["emergencyAvailable"]),
            "totalBeds": sum(int(row["bedCapacity"] or 0) for row in departments),
            "availableBeds": sum(int(row["availableBeds"] or 0) for row in departments),
            "doctorCount": sum(int(row["doctorCount"] or 0) for row in departments),
            "nurseCount": sum(int(row["nurseCount"] or 0) for row in departments),
        },
    )


@directory_router.get("/departments/{department_id}", tags=[DEPARTMENT_TAG])
async def get_department_by_id(
    department_id: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="departmentId must be a valid UUID")
    rows = await _department_query(db, hospital_id)
    department = next((row for row in rows if row["departmentId"] == str(dept_uuid)), None)
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return success_response(message="Department retrieved successfully", data=department)


@directory_router.get("/departments/{department_id}/doctors", tags=[DEPARTMENT_TAG])
async def get_department_doctors(
    department_id: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    doctors = await _doctor_query(db, hospital_id, department=department_id)
    return success_response(message="Department doctors retrieved successfully", data={"doctors": doctors, "total": len(doctors)})


@directory_router.get("/departments/{department_id}/nurses", tags=[DEPARTMENT_TAG])
async def get_department_nurses(
    department_id: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="departmentId must be a valid UUID")
    result = await db.execute(
        select(NurseProfile)
        .join(User, NurseProfile.user_id == User.id)
        .where(and_(NurseProfile.hospital_id == hospital_id, NurseProfile.department_id == dept_uuid))
        .options(selectinload(NurseProfile.user))
        .order_by(User.first_name, User.last_name)
    )
    nurses = result.scalars().all()
    data = [
        {
            "id": str(nurse.user_id),
            "nurseProfileId": str(nurse.id),
            "nurseId": nurse.nurse_id,
            "name": f"{nurse.user.first_name} {nurse.user.last_name}".strip(),
            "designation": nurse.designation,
            "specialization": nurse.specialization,
            "experience": nurse.experience_years,
            "status": "Active" if nurse.is_active else "Inactive",
        }
        for nurse in nurses
    ]
    return success_response(message="Department nurses retrieved successfully", data={"nurses": data, "total": len(data)})


@directory_router.get("/departments/{department_id}/beds", tags=[DEPARTMENT_TAG])
async def get_department_beds(
    department_id: str,
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    try:
        dept_uuid = uuid.UUID(department_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="departmentId must be a valid UUID")
    dept_result = await db.execute(
        select(Department).where(and_(Department.id == dept_uuid, Department.hospital_id == hospital_id))
    )
    department = dept_result.scalar_one_or_none()
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    active_admissions = (
        await db.execute(
            select(func.count(Admission.id)).where(
                and_(Admission.department_id == department.id, Admission.hospital_id == hospital_id, Admission.is_active == True)
            )
        )
    ).scalar() or 0
    return success_response(
        message="Bed availability retrieved successfully",
        data={
            "departmentId": str(department.id),
            "department": department.name,
            "bedCapacity": department.bed_capacity or 0,
            "occupiedBeds": active_admissions,
            "availableBeds": max((department.bed_capacity or 0) - active_admissions, 0),
        },
    )


@directory_router.get("/appointments/available-slots", tags=[APPOINTMENT_TAG])
async def get_available_slots(
    date: str = Query(..., description="Appointment date YYYY-MM-DD"),
    doctorId: Optional[str] = Query(None, description="Doctor user/profile UUID"),
    doctor_id: Optional[str] = Query(None, description="Doctor user/profile UUID"),
    doctorName: Optional[str] = Query(None, description="Doctor display name"),
    doctor_name: Optional[str] = Query(None, description="Doctor display name"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    raw_id = (doctorId or doctor_id or "").strip()
    raw_name = (doctorName or doctor_name or "").strip()
    doctor_user_id: Optional[uuid.UUID] = None
    if raw_id:
        try:
            doc_uuid = uuid.UUID(raw_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="doctorId must be a valid UUID")
        result = await db.execute(
            select(DoctorProfile).where(
                and_(DoctorProfile.hospital_id == hospital_id, or_(DoctorProfile.id == doc_uuid, DoctorProfile.user_id == doc_uuid))
            )
        )
        doctor = result.scalar_one_or_none()
        if not doctor:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
        doctor_user_id = doctor.user_id
    elif raw_name:
        doctors = await _doctor_query(db, hospital_id, keyword=raw_name)
        if len(doctors) != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT if doctors else status.HTTP_404_NOT_FOUND,
                detail="Doctor name must match exactly one doctor; pass doctorId to avoid ambiguity",
            )
        doctor_user_id = uuid.UUID(doctors[0]["doctorId"])
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="doctorId or doctorName is required")
    service = AppointmentService(db)
    slots = await service.get_available_time_slots_for_doctor_user(doctor_user_id, date)
    return success_response(message="Available slots retrieved successfully", data={"doctorId": str(doctor_user_id), "date": date, "slots": slots})


async def _appointments_for_date(db: AsyncSession, hospital_id: uuid.UUID, appointment_date: str):
    result = await db.execute(
        select(Appointment)
        .where(and_(Appointment.hospital_id == hospital_id, Appointment.appointment_date == appointment_date))
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.doctor),
            selectinload(Appointment.department),
        )
        .order_by(Appointment.appointment_time)
    )
    return result.scalars().all()


@appointments_router.get("/appointments/queue", tags=[APPOINTMENT_TAG])
async def get_appointment_queue(
    date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    target_date = date or date_type.today().isoformat()
    appointments = await _appointments_for_date(db, hospital_id, target_date)
    queue_statuses = {"CHECKED_IN", "IN_PROGRESS", AppointmentStatus.CONFIRMED.value, AppointmentStatus.REQUESTED.value}
    data = [serialize_opd_appointment_full(a) for a in appointments if (a.status or "").upper() in queue_statuses]
    return success_response(message="Appointment queue retrieved successfully", data={"date": target_date, "queue": data, "total": len(data)})


@appointments_router.get("/appointments/status-summary", tags=[APPOINTMENT_TAG])
async def get_appointment_status_summary(
    date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    current_user: User = Depends(require_receptionist()),
    db: AsyncSession = Depends(get_db_session),
):
    hospital_id = await _hospital_id(db, current_user)
    target_date = date or date_type.today().isoformat()
    result = await db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(and_(Appointment.hospital_id == hospital_id, Appointment.appointment_date == target_date))
        .group_by(Appointment.status)
    )
    by_status = {str(status or "UNKNOWN"): int(count or 0) for status, count in result.all()}
    waiting = by_status.get("CHECKED_IN", 0) + by_status.get(AppointmentStatus.CONFIRMED.value, 0) + by_status.get(AppointmentStatus.REQUESTED.value, 0)
    completed = by_status.get(AppointmentStatus.COMPLETED.value, 0)
    cancelled = by_status.get(AppointmentStatus.CANCELLED.value, 0)
    return success_response(
        message="Appointment status summary retrieved successfully",
        data={
            "date": target_date,
            "totalAppointments": sum(by_status.values()),
            "waiting": waiting,
            "completed": completed,
            "cancelled": cancelled,
            "byStatus": by_status,
        },
    )
