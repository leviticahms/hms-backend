"""
Patient Appointment Booking API
Authenticated appointment booking system for registered patients.
PATIENT AUTHENTICATION REQUIRED: Patients must login to book appointments.
"""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from datetime import datetime

from app.core.database import get_platform_db_session
from app.models.hospital import Department
from app.models.patient import PatientProfile, Appointment
from app.models.user import User
from app.core.enums import AppointmentStatus, UserRole, UserStatus
from app.core.utils import generate_appointment_ref
from app.core.security import get_current_user
from app.dependencies.auth import get_current_patient
from app.schemas.patient_care import (
    AppointmentBookingCreate,
    AppointmentCancellationCreate,
    PatientAppointmentUpdate,
)
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/patient-appointment-booking", tags=["Patient Portal - Appointment Booking"])


def _normalize_patient_booking_time(raw: str) -> tuple[str, str]:
    """Return (HH:MM for schedule matching, HH:MM:SS for DB)."""
    raw = (raw or "").strip()
    parts = [p for p in raw.split(":") if p != ""]
    if len(parts) >= 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) >= 2:
        h, m, s = int(parts[0]), int(parts[1]), 0
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid appointment_time; use HH:MM or HH:MM:SS",
        )
    return f"{h:02d}:{m:02d}", f"{h:02d}:{m:02d}:{s:02d}"


# ============================================================================
# AUTHENTICATED ENDPOINTS (Patient Authentication Required)
# Patient's hospital_id is used (assigned at registration); no need to ask for hospital.
# ============================================================================

async def _get_patient_hospital(
    current_patient: PatientProfile, db: AsyncSession
):
    """Get hospital for current patient. Raises 400 if patient has no hospital_id (must register to a hospital first)."""
    from app.models.tenant import Hospital
    if not current_patient.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient must be registered to a hospital to book appointments. Please complete registration with a hospital."
        )
    result = await db.execute(
        select(Hospital).where(
            and_(
                Hospital.id == current_patient.hospital_id,
                Hospital.is_active == True
            )
        )
    )
    hospital = result.scalar_one_or_none()
    if not hospital:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Your registered hospital is not found or inactive"
        )
    return hospital


@router.get("/departments")
async def get_departments(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get list of departments in the patient's hospital.
    
    Access Control:
    - **Who can access:** Patients only (own hospital from JWT token)
    """
    hospital = await _get_patient_hospital(current_patient, db)
    query = select(Department).where(
        and_(
            Department.hospital_id == hospital.id,
            Department.is_active == True
        )
    ).order_by(Department.name)
    result = await db.execute(query)
    departments = result.scalars().all()
    return [
        {
            "name": dept.name,
            "description": dept.description,
            "code": dept.code,
            "location": dept.location,
            "is_emergency": dept.is_emergency,
            "is_24x7": dept.is_24x7,
            "hospital_name": hospital.name,
        }
        for dept in departments
    ]


@router.get("/departments/{department_name}/doctors")
async def get_department_doctors(
    department_name: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get doctors by department name in the patient's hospital.
    
    Access Control:
    - **Who can access:** Patients only (own hospital from JWT token)
    """
    hospital = await _get_patient_hospital(current_patient, db)
    dept_query = select(Department).where(
        and_(
            Department.hospital_id == hospital.id,
            Department.name.ilike(f"%{department_name}%"),
            Department.is_active == True
        )
    )
    dept_result = await db.execute(dept_query)
    department = dept_result.scalar_one_or_none()
    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Department '{department_name}' not found in your hospital"
        )
    
    # Get doctors in the department (from staff assignments)
    from app.models.hospital import StaffDepartmentAssignment
    from app.models.user import Role
    
    result = await db.execute(
        select(StaffDepartmentAssignment)
        .join(User, StaffDepartmentAssignment.staff_id == User.id)
        .join(User.roles)
        .where(
            and_(
                StaffDepartmentAssignment.department_id == department.id,
                StaffDepartmentAssignment.is_active == True,
                User.status == UserStatus.ACTIVE,
                Role.name == UserRole.DOCTOR  # Only get doctors
            )
        )
        .options(selectinload(StaffDepartmentAssignment.staff))
        .order_by(User.first_name)
    )
    
    assignments = result.scalars().all()
    
    # Convert staff assignments to doctor list
    doctors = []
    for assignment in assignments:
        staff = assignment.staff
        doctors.append({
            "name": f"Dr. {staff.first_name} {staff.last_name}",
            "specialization": "General Medicine",  # Default since we don't have DoctorProfile
            "designation": "Doctor",  # Default since we don't have DoctorProfile
            "consultation_fee": 500.0,  # Default consultation fee
            "experience_years": 5  # Default experience
        })
    
    return {
        "department_name": department.name,
        "department_code": department.code,
        "hospital_name": hospital.name,
        "doctors": doctors
    }


@router.get("/doctors/{doctor_name}/available-slots")
async def get_doctor_available_slots(
    doctor_name: str,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get available time slots by doctor name in the patient's hospital.
    
    Access Control:
    - **Who can access:** Patients only (own hospital from JWT token)
    """
    from app.models.hospital import StaffDepartmentAssignment
    from app.models.user import Role
    
    hospital = await _get_patient_hospital(current_patient, db)
    doctor_query = select(User).join(
        StaffDepartmentAssignment, User.id == StaffDepartmentAssignment.staff_id
    ).join(User.roles).where(
        and_(
            User.hospital_id == hospital.id,
            or_(
                func.concat('Dr. ', User.first_name, ' ', User.last_name).ilike(f"%{doctor_name}%"),
                func.concat(User.first_name, ' ', User.last_name).ilike(f"%{doctor_name}%")
            ),
            StaffDepartmentAssignment.is_active == True,
            User.status == UserStatus.ACTIVE,
            Role.name == UserRole.DOCTOR
        )
    )
    
    doctor_result = await db.execute(doctor_query)
    doctor = doctor_result.scalar_one_or_none()
    
    if not doctor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Doctor '{doctor_name}' not found in your hospital"
        )
    
    try:
        datetime.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    svc = AppointmentService(db)
    slots = await svc.get_available_time_slots_for_doctor_user(doctor.id, date)

    return {
        "doctor_name": f"Dr. {doctor.first_name} {doctor.last_name}",
        "hospital_name": hospital.name,
        "date": date,
        "available_slots": [
            {
                "time": s["time"],
                "is_available": s["is_available"],
                "duration_minutes": s["duration_minutes"],
            }
            for s in slots
        ],
    }


@router.post("/book-appointment")
async def book_appointment(
    booking_request: AppointmentBookingCreate,
    current_patient: PatientProfile = Depends(get_current_patient),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Book an appointment for authenticated patient.
    
    Access Control:
    - **Who can access:** Patients only (identity from JWT token)
    """
    from app.models.hospital import StaffDepartmentAssignment
    from app.models.user import Role
    
    data = booking_request.dict()
    
    # Validate appointment date/time
    try:
        appointment_datetime = datetime.fromisoformat(f"{data['appointment_date']}T{data['appointment_time']}")
        if appointment_datetime <= datetime.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot book appointments in the past"
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date/time format"
        )
    
    # Use patient's hospital (assigned at registration)
    hospital = await _get_patient_hospital(current_patient, db)
    
    # Find department by name in specified hospital
    dept_result = await db.execute(
        select(Department)
        .where(
            and_(
                Department.hospital_id == hospital.id,
                Department.name.ilike(f"%{data['department_name']}%"),
                Department.is_active == True
            )
        )
    )
    
    department = dept_result.scalar_one_or_none()
    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Department '{data['department_name']}' not found in {hospital.name}"
        )
    
    # Find doctor by name (from staff assignments in specified hospital)
    doctor_result = await db.execute(
        select(User)
        .join(StaffDepartmentAssignment, User.id == StaffDepartmentAssignment.staff_id)
        .join(User.roles)
        .where(
            and_(
                User.hospital_id == hospital.id,
                StaffDepartmentAssignment.department_id == department.id,
                StaffDepartmentAssignment.is_active == True,
                or_(
                    func.concat('Dr. ', User.first_name, ' ', User.last_name).ilike(f"%{data['doctor_name']}%"),
                    func.concat(User.first_name, ' ', User.last_name).ilike(f"%{data['doctor_name']}%")
                ),
                Role.name == UserRole.DOCTOR,
                User.status == UserStatus.ACTIVE
            )
        )
    )
    
    doctor = doctor_result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Doctor '{data['doctor_name']}' not found in {data['department_name']} department at {hospital.name}"
        )

    try:
        time_hhmm, time_hhmmss = _normalize_patient_booking_time(data["appointment_time"])
    except HTTPException:
        raise
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid appointment_time; use HH:MM or HH:MM:SS",
        )

    svc = AppointmentService(db)
    day_slots = await svc.get_available_time_slots_for_doctor_user(doctor.id, data["appointment_date"])
    if not day_slots:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This doctor has no published availability on that day. Pick another date or use available-slots.",
        )
    match = next((s for s in day_slots if s["time"] == time_hhmm), None)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected time is outside this doctor's schedule. Choose a time from GET .../available-slots.",
        )
    if not match["is_available"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Time slot is not available",
        )

    # Check if time slot is available (race safety)
    existing_appointment = await db.execute(
        select(Appointment)
        .where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.appointment_date == data['appointment_date'],
                Appointment.appointment_time == time_hhmmss,
                Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED])
            )
        )
    )
    
    if existing_appointment.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Time slot is not available"
        )
    
    # Generate appointment reference
    appointment_ref = generate_appointment_ref()
    
    # Ensure appointment_ref is unique
    while True:
        existing_ref = await db.execute(
            select(Appointment).where(Appointment.appointment_ref == appointment_ref)
        )
        if not existing_ref.scalar_one_or_none():
            break
        appointment_ref = generate_appointment_ref()
    
    # Automatically assign hospital_id to patient and user if it's null
    # This links the patient to the hospital when they book their first appointment
    if current_patient.hospital_id is None:
        current_patient.hospital_id = hospital.id
    
    # Also update the user's hospital_id if it's null
    if current_user.hospital_id is None:
        current_user.hospital_id = hospital.id
    
    # Create appointment
    appointment = Appointment(
        appointment_ref=appointment_ref,
        patient_id=current_patient.id,
        doctor_id=doctor.id,
        department_id=department.id,
        hospital_id=hospital.id,  # Use the specified hospital
        appointment_date=data['appointment_date'],
        appointment_time=time_hhmmss,
        duration_minutes=int(match["duration_minutes"]),
        status=AppointmentStatus.REQUESTED,
        chief_complaint=data['chief_complaint'],
        consultation_fee=500.0,  # Default consultation fee
        created_by_role=UserRole.PATIENT,
        created_by_user=current_patient.user_id
    )
    
    db.add(appointment)
    await db.commit()
    
    return {
        "success": True,
        "message": "Appointment booked successfully!",
        "patient_ref": current_patient.patient_id,
        "patient_name": f"{current_patient.user.first_name} {current_patient.user.last_name}",
        "appointment_ref": appointment_ref,
        "doctor_name": f"Dr. {doctor.first_name} {doctor.last_name}",
        "department_name": department.name,
        "hospital_name": hospital.name,
        "appointment_date": data['appointment_date'],
        "appointment_time": data['appointment_time'],
        "status": AppointmentStatus.REQUESTED,
        "consultation_fee": 500.0
    }


@router.get("/appointment/{appointment_ref}")
async def get_appointment_details(
    appointment_ref: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get appointment details by appointment reference.
    
    Access Control:
    - **Who can access:** Patients only (own appointments from JWT token)
    """
    result = await db.execute(
        select(Appointment)
        .where(
            and_(
                Appointment.appointment_ref == appointment_ref,
                Appointment.patient_id == current_patient.id  # Ensure patient can only see their own appointments
            )
        )
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.doctor),
            selectinload(Appointment.department)
        )
    )
    
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found or you don't have permission to view it"
        )
    
    return {
        "appointment_ref": appointment.appointment_ref,
        "patient_ref": appointment.patient.patient_id,
        "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
        "patient_phone": appointment.patient.user.phone,
        "patient_email": appointment.patient.user.email,
        "doctor_name": f"Dr. {appointment.doctor.first_name} {appointment.doctor.last_name}",
        "department_name": appointment.department.name,
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time,
        "status": appointment.status,
        "chief_complaint": appointment.chief_complaint,
        "consultation_fee": float(appointment.consultation_fee),
        "created_at": appointment.created_at.isoformat(),
        "notes": appointment.notes
    }


@router.patch("/appointment/{appointment_ref}")
async def update_my_appointment(
    appointment_ref: str,
    body: PatientAppointmentUpdate,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Reschedule or update appointment details (patient portal).
    Uses the same doctor schedule and slot rules as POST /book-appointment.
    """
    from app.models.hospital import StaffDepartmentAssignment
    from app.models.user import Role
    from app.models.doctor import DoctorProfile

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    result = await db.execute(
        select(Appointment).where(
            and_(
                Appointment.appointment_ref == appointment_ref,
                Appointment.patient_id == current_patient.id,
            )
        )
    )
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found or you don't have permission to update it",
        )
    if appointment.status in [AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update appointment with status: {appointment.status}",
        )

    hospital = await _get_patient_hospital(current_patient, db)
    schedule_affecting = any(
        k in payload
        for k in ("department_name", "doctor_name", "appointment_date", "appointment_time")
    )

    if payload.get("department_name"):
        dn = payload["department_name"].strip()
        dept_result = await db.execute(
            select(Department).where(
                and_(
                    Department.hospital_id == hospital.id,
                    Department.name.ilike(f"%{dn}%"),
                    Department.is_active == True,
                )
            )
        )
        department = dept_result.scalar_one_or_none()
        if not department:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Department '{dn}' not found in your hospital",
            )
        appointment.department_id = department.id

    if payload.get("doctor_name"):
        doc_q = (
            select(User)
            .join(StaffDepartmentAssignment, User.id == StaffDepartmentAssignment.staff_id)
            .join(User.roles)
            .where(
                and_(
                    User.hospital_id == hospital.id,
                    StaffDepartmentAssignment.department_id == appointment.department_id,
                    StaffDepartmentAssignment.is_active == True,
                    or_(
                        func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(
                            f"%{payload['doctor_name']}%"
                        ),
                        func.concat(User.first_name, " ", User.last_name).ilike(
                            f"%{payload['doctor_name']}%"
                        ),
                    ),
                    Role.name == UserRole.DOCTOR,
                    User.status == UserStatus.ACTIVE,
                )
            )
        )
        doctor_row = await db.execute(doc_q)
        doctor = doctor_row.scalar_one_or_none()
        if not doctor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not found in the selected department",
            )
        appointment.doctor_id = doctor.id
        dp_res = await db.execute(
            select(DoctorProfile).where(
                and_(
                    DoctorProfile.user_id == doctor.id,
                    DoctorProfile.hospital_id == hospital.id,
                )
            )
        )
        dp = dp_res.scalar_one_or_none()
        if dp and dp.consultation_fee is not None:
            appointment.consultation_fee = dp.consultation_fee

    if payload.get("appointment_date"):
        appointment.appointment_date = str(payload["appointment_date"]).strip()[:10]

    if payload.get("appointment_time"):
        try:
            time_hhmm, time_hhmmss = _normalize_patient_booking_time(payload["appointment_time"])
        except HTTPException:
            raise
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid appointment_time; use HH:MM or HH:MM:SS",
            )
        appointment.appointment_time = time_hhmmss

    if "chief_complaint" in payload:
        appointment.chief_complaint = payload.get("chief_complaint")

    if schedule_affecting:
        try:
            ad = appointment.appointment_date
            ats = appointment.appointment_time
            appointment_datetime = datetime.fromisoformat(f"{ad}T{ats}")
            if appointment_datetime <= datetime.now():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot reschedule to a past date or time",
                )
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid appointment date/time",
            )

        in_dept = await db.execute(
            select(StaffDepartmentAssignment.id).where(
                and_(
                    StaffDepartmentAssignment.staff_id == appointment.doctor_id,
                    StaffDepartmentAssignment.department_id == appointment.department_id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
        )
        if not in_dept.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected doctor is not assigned to this department",
            )

        svc = AppointmentService(db)
        day_slots = await svc.get_available_time_slots_for_doctor_user(
            appointment.doctor_id,
            appointment.appointment_date,
            exclude_appointment_id=appointment.id,
        )
        if not day_slots:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This doctor has no published availability on that day",
            )
        pt_parts = appointment.appointment_time.split(":")
        th = f"{int(pt_parts[0]):02d}:{int(pt_parts[1]):02d}"
        match = next((s for s in day_slots if s["time"] == th), None)
        if not match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected time is outside this doctor's schedule",
            )
        if not match["is_available"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Time slot is not available",
            )
        appointment.duration_minutes = int(match["duration_minutes"])

    await db.commit()

    return {
        "success": True,
        "message": "Appointment updated successfully",
        "appointment_ref": appointment.appointment_ref,
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time[:5]
        if appointment.appointment_time and len(appointment.appointment_time) >= 5
        else appointment.appointment_time,
        "status": appointment.status,
    }


@router.get("/my-appointments")
async def get_my_appointments(
    current_patient: PatientProfile = Depends(get_current_patient),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=50, description="Items per page"),
    status_filter: Optional[str] = Query(None, description="Filter by appointment status"),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Get all appointments for the authenticated patient.
    
    Access Control:
    - **Who can access:** Patients only (own appointments from JWT token)
    """
    offset = (page - 1) * limit
    
    # Build query for patient's appointments only
    query = select(Appointment).where(
        Appointment.patient_id == current_patient.id
    ).options(
        selectinload(Appointment.doctor),
        selectinload(Appointment.department)
    )
    
    # Apply status filter if provided
    if status_filter:
        query = query.where(Appointment.status == status_filter)
    
    # Get total count
    count_query = select(func.count(Appointment.id)).where(
        Appointment.patient_id == current_patient.id
    )
    if status_filter:
        count_query = count_query.where(Appointment.status == status_filter)
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(offset).limit(limit).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc())
    result = await db.execute(query)
    appointments = result.scalars().all()
    
    return {
        "appointments": [
            {
                "appointment_ref": apt.appointment_ref,
                "doctor_name": f"Dr. {apt.doctor.first_name} {apt.doctor.last_name}",
                "department_name": apt.department.name,
                "appointment_date": apt.appointment_date,
                "appointment_time": apt.appointment_time,
                "status": apt.status,
                "chief_complaint": apt.chief_complaint,
                "consultation_fee": float(apt.consultation_fee),
                "created_at": apt.created_at.isoformat()
            }
            for apt in appointments
        ],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit
        }
    }


@router.patch("/appointment/{appointment_ref}/cancel")
async def cancel_appointment(
    appointment_ref: str,
    cancellation_request: AppointmentCancellationCreate,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session)
):
    """
    Cancel an appointment by appointment reference.
    
    Access Control:
    - **Who can access:** Patients only (own appointments from JWT token)
    """
    result = await db.execute(
        select(Appointment)
        .where(
            and_(
                Appointment.appointment_ref == appointment_ref,
                Appointment.patient_id == current_patient.id  # Ensure patient can only cancel their own appointments
            )
        )
    )
    
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found or you don't have permission to cancel it"
        )
    
    if appointment.status in [AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel appointment with status: {appointment.status}"
        )
    
    # Update appointment
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_at = datetime.utcnow()
    appointment.cancellation_reason = cancellation_request.cancellation_reason
    
    await db.commit()
    
    return {
        "success": True,
        "message": f"Appointment {appointment_ref} has been cancelled successfully",
        "appointment_ref": appointment_ref,
        "status": AppointmentStatus.CANCELLED,
        "cancelled_at": appointment.cancelled_at.isoformat()
    }