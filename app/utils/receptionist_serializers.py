"""
Full payloads for receptionist GET endpoints (patient registration & appointments).
Never exposes password_hash or plaintext passwords.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, Optional

from app.models.hospital import Department
from app.models.patient import Appointment, PatientProfile
from app.models.user import User


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _time_str(t: Optional[time]) -> Optional[str]:
    return t.isoformat() if t else None


def serialize_user_for_receptionist(u: User) -> Dict[str, Any]:
    """All User columns safe for staff UI — password_hash excluded."""
    ph = getattr(u, "password_hash", None) or ""
    return {
        "id": str(u.id),
        "email": u.email,
        "phone": u.phone,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "middle_name": getattr(u, "middle_name", None),
        "staff_id": getattr(u, "staff_id", None),
        "status": getattr(u, "status", None),
        "email_verified": getattr(u, "email_verified", None),
        "phone_verified": getattr(u, "phone_verified", None),
        "last_login": _iso(getattr(u, "last_login", None)),
        "avatar_url": getattr(u, "avatar_url", None),
        "timezone": getattr(u, "timezone", None),
        "language": getattr(u, "language", None),
        "hospital_id": str(u.hospital_id) if getattr(u, "hospital_id", None) else None,
        "user_metadata": getattr(u, "user_metadata", None) or {},
        "failed_login_attempts": getattr(u, "failed_login_attempts", None),
        "locked_until": _iso(getattr(u, "locked_until", None)),
        "password_changed_at": _iso(getattr(u, "password_changed_at", None)),
        "created_at": _iso(getattr(u, "created_at", None)),
        "updated_at": _iso(getattr(u, "updated_at", None)),
        "is_active": getattr(u, "is_active", None),
        "password": None,
        "password_hash": None,
        "has_portal_password": bool(str(ph).strip()),
    }


def serialize_patient_profile_for_receptionist(patient: PatientProfile) -> Dict[str, Any]:
    """All PatientProfile ORM fields as JSON-friendly dict."""
    ec_name = patient.emergency_contact_name
    ec_phone = patient.emergency_contact_phone
    ec_rel = patient.emergency_contact_relation
    return {
        "id": str(patient.id),
        "patient_profile_id": str(patient.id),
        "patient_id": patient.patient_id,
        "patient_ref": patient.patient_id,
        "mrn": patient.mrn,
        "user_id": str(patient.user_id),
        "hospital_id": str(patient.hospital_id),
        "date_of_birth": patient.date_of_birth,
        "gender": patient.gender,
        "blood_group": patient.blood_group,
        "blood_group_value": patient.blood_group_value,
        "id_type": patient.id_type,
        "id_number": patient.id_number,
        "id_name": patient.id_name,
        "address": patient.address,
        "city": patient.city,
        "district": patient.district,
        "state": patient.state,
        "country": patient.country,
        "pincode": patient.pincode,
        "emergency_contact_name": ec_name,
        "emergency_contact_phone": ec_phone,
        "emergency_contact_relation": ec_rel,
        # Names often used by receptionist edit forms (same columns as above)
        "relationship": ec_rel,
        "emergency_contact_number": ec_phone,
        "emergencyContactName": ec_name,
        "emergencyContactNumber": ec_phone,
        "emergencyContactRelationship": ec_rel,
        "emergency_contact_details": {
            "name": ec_name,
            "phone": ec_phone,
            "number": ec_phone,
            "relationship": ec_rel,
            "relation": ec_rel,
        },
        "medical_history": patient.medical_history,
        "allergies": patient.allergies or [],
        "chronic_conditions": patient.chronic_conditions or [],
        "current_medications": patient.current_medications or [],
        "insurance_provider": patient.insurance_provider,
        "insurance_policy_number": patient.insurance_policy_number,
        "insurance_expiry": patient.insurance_expiry,
        "created_at": _iso(getattr(patient, "created_at", None)),
        "updated_at": _iso(getattr(patient, "updated_at", None)),
        "is_active": getattr(patient, "is_active", None),
    }


def build_receptionist_patient_full_payload(patient: PatientProfile) -> Dict[str, Any]:
    """
    Flat + structured patient record for receptionist GET responses.
    Includes nested ``patient_profile`` and ``user`` with every stored field.
    """
    u = patient.user
    ec_phone = patient.emergency_contact_phone
    ec_rel = patient.emergency_contact_relation
    ec_name = patient.emergency_contact_name
    em_verified = bool(getattr(u, "email_verified", False))
    has_email = bool((u.email or "").strip())

    profile = serialize_patient_profile_for_receptionist(patient)
    user_obj = serialize_user_for_receptionist(u)

    flat = {
        **profile,
        "patient_name": f"{u.first_name} {u.last_name}",
        "name": f"{u.first_name} {u.last_name}",
        "first_name": u.first_name,
        "last_name": u.last_name,
        "phone": u.phone,
        "email": u.email,
        "emergency_contact_relationship": ec_rel,
        # Legacy: same value as emergency_contact_phone (some older clients read this key)
        "emergency_contact": ec_phone,
        "password": None,
        "portal_login_enabled": has_email and em_verified,
        "has_portal_password": user_obj.get("has_portal_password", False),
        "user": user_obj,
        "patient_profile": profile,
    }
    return flat


def serialize_department_for_receptionist(dept: Department) -> Dict[str, Any]:
    return {
        "id": str(dept.id),
        "hospital_id": str(dept.hospital_id),
        "name": dept.name,
        "code": dept.code,
        "description": dept.description,
        "head_doctor_id": str(dept.head_doctor_id) if dept.head_doctor_id else None,
        "location": dept.location,
        "phone": dept.phone,
        "email": dept.email,
        "is_emergency": dept.is_emergency,
        "is_icu": dept.is_icu,
        "bed_capacity": dept.bed_capacity,
        "opening_time": _time_str(dept.opening_time),
        "closing_time": _time_str(dept.closing_time),
        "is_24x7": dept.is_24x7,
        "settings": dept.settings if isinstance(dept.settings, dict) else {},
        "created_at": _iso(getattr(dept, "created_at", None)),
        "updated_at": _iso(getattr(dept, "updated_at", None)),
        "is_active": getattr(dept, "is_active", None),
    }


def serialize_opd_appointment_table_row(a: Appointment) -> Dict[str, Any]:
    """Lightweight row for receptionist appointment scheduling table."""
    pt = a.appointment_time or ""
    time_disp = pt[:5] if len(pt) >= 5 else pt
    ref = a.appointment_ref or str(a.id)
    patient = a.patient
    doctor = a.doctor
    dept = a.department
    patient_name = ""
    patient_ref = ""
    if patient and getattr(patient, "user", None):
        patient_name = f"{patient.user.first_name or ''} {patient.user.last_name or ''}".strip()
        patient_ref = patient.patient_id or ""
    doctor_name = ""
    if doctor:
        doctor_name = f"Dr. {doctor.first_name or ''} {doctor.last_name or ''}".strip()
    return {
        "id": ref,
        "appointment_ref": ref,
        "patient_name": patient_name,
        "patient_ref": patient_ref,
        "doctor_name": doctor_name,
        "department_name": dept.name if dept else "",
        "appointment_date": a.appointment_date,
        "appointment_time": time_disp,
        "appointment_type": a.appointment_type,
        "chief_complaint": a.chief_complaint,
        "status": a.status,
        "is_checked_in": a.checked_in_at is not None,
    }


def serialize_opd_appointment_full(a: Appointment) -> Dict[str, Any]:
    """Every Appointment column plus nested patient (full), doctor user, department."""
    pt = a.appointment_time or ""
    time_disp = pt[:5] if len(pt) >= 5 else pt
    patient_block = build_receptionist_patient_full_payload(a.patient)
    return {
        "id": str(a.id),
        "appointment_ref": a.appointment_ref,
        "hospital_id": str(a.hospital_id),
        "patient_id": str(a.patient_id),
        "doctor_id": str(a.doctor_id),
        "department_id": str(a.department_id),
        "appointment_date": a.appointment_date,
        "appointment_time": a.appointment_time,
        "appointment_time_display": time_disp,
        "duration_minutes": a.duration_minutes,
        "status": a.status,
        "appointment_type": a.appointment_type,
        "chief_complaint": a.chief_complaint,
        "notes": a.notes,
        "checked_in_at": _iso(getattr(a, "checked_in_at", None)),
        "completed_at": _iso(getattr(a, "completed_at", None)),
        "cancelled_at": _iso(getattr(a, "cancelled_at", None)),
        "cancellation_reason": a.cancellation_reason,
        "consultation_fee": float(a.consultation_fee) if a.consultation_fee is not None else None,
        "is_paid": getattr(a, "is_paid", None),
        "created_by_role": a.created_by_role,
        "created_by_user": str(a.created_by_user) if a.created_by_user else None,
        "created_at": _iso(getattr(a, "created_at", None)),
        "updated_at": _iso(getattr(a, "updated_at", None)),
        "is_active": getattr(a, "is_active", None),
        "is_checked_in": a.checked_in_at is not None,
        "patient_ref": a.patient.patient_id,
        "patient_name": f"{a.patient.user.first_name} {a.patient.user.last_name}",
        "patient_phone": a.patient.user.phone,
        "patient_email": a.patient.user.email,
        "doctor_name": f"Dr. {a.doctor.first_name} {a.doctor.last_name}",
        "department_name": a.department.name,
        "patient": patient_block,
        "doctor": serialize_user_for_receptionist(a.doctor),
        "department": serialize_department_for_receptionist(a.department),
    }
