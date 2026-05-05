from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES_JSON = ROOT / "docs" / "ALL_ENDPOINTS.json"
OUT_MD = ROOT / "docs" / "DATA_STORAGE.md"


CONFIRMED_TABLES = {
    # Super admin + auth
    ("POST", "/api/v1/super-admin/plans"): "subscription_plans",
    ("GET", "/api/v1/super-admin/plans"): "subscription_plans",
    ("PUT", "/api/v1/super-admin/plans/{plan_id}"): "subscription_plans",
    ("DELETE", "/api/v1/super-admin/plans/{plan_id}"): "subscription_plans",
    ("POST", "/api/v1/super-admin/hospitals/{hospital_name}/assign-plan"): "hospital_subscriptions",
    ("GET", "/api/v1/super-admin/hospitals/{hospital_name}/subscription"): "hospital_subscriptions, subscription_plans, hospitals",
    ("GET", "/admin/verify"): "users",
    ("GET", "/api/v1/auth/hospitals"): "hospitals",
    ("POST", "/api/v1/auth/patient/register"): "users, patient_profiles",
    # Hospital admin core
    ("POST", "/api/v1/hospital-admin/departments"): "departments",
    ("GET", "/api/v1/hospital-admin/departments"): "departments",
    ("PUT", "/api/v1/hospital-admin/departments/{department_id}"): "departments",
    ("PATCH", "/api/v1/hospital-admin/departments/{department_id}/status"): "departments",
    ("POST", "/api/v1/hospital-admin/staff"): (
        "users, user_roles, and role profiles: "
        "doctor_profiles/nurse_profiles/receptionist_profiles + staff_profiles + staff_department_assignments"
    ),
    ("GET", "/api/v1/hospital-admin/staff"): "users + roles (via user_roles) + metadata/profile joins",
    ("GET", "/api/v1/hospital-admin/staff/{staff_id}"): "users, user_roles, role-specific profile table(s)",
    ("PATCH", "/api/v1/hospital-admin/staff/doctors/{staff_id}"): "users, doctor_profiles, staff_profiles, staff_department_assignments",
    ("PATCH", "/api/v1/hospital-admin/staff/nurses/{staff_id}"): "users, nurse_profiles, staff_profiles, staff_department_assignments",
    ("PATCH", "/api/v1/hospital-admin/staff/receptionists/{staff_id}"): "users, receptionist_profiles, staff_profiles, staff_department_assignments",
    ("PATCH", "/api/v1/hospital-admin/staff/lab-techs/{staff_id}"): "users, staff_profiles, staff_department_assignments",
    ("PATCH", "/api/v1/hospital-admin/staff/pharmacists/{staff_id}"): "users, staff_profiles, staff_department_assignments",
    ("PATCH", "/api/v1/hospital-admin/staff/{staff_id}/status"): "users",
    # Billing + pharmacy
    ("POST", "/api/v1/billing/tax-profiles"): "tax_profiles",
    ("GET", "/api/v1/billing/tax-profiles"): "tax_profiles",
    ("PUT", "/api/v1/billing/tax-profiles/{tax_id}"): "tax_profiles",
    ("PATCH", "/api/v1/billing/tax-profiles/{tax_id}/status"): "tax_profiles",
    ("POST", "/api/v1/billing/services"): "service_items",
    ("GET", "/api/v1/billing/services"): "service_items",
    ("GET", "/api/v1/billing/services/{service_id}"): "service_items",
    ("PUT", "/api/v1/billing/services/{service_id}"): "service_items",
    ("PATCH", "/api/v1/billing/services/{service_id}/status"): "service_items",
    ("DELETE", "/api/v1/billing/services/{service_id}"): "service_items (soft-delete via is_active=false)",
    ("POST", "/api/v1/pharmacy/suppliers"): "pharmacy_suppliers",
    ("GET", "/api/v1/pharmacy/suppliers"): "pharmacy_suppliers",
    ("GET", "/api/v1/pharmacy/suppliers/{supplier_id}"): "pharmacy_suppliers",
    ("PUT", "/api/v1/pharmacy/suppliers/{supplier_id}"): "pharmacy_suppliers",
    ("DELETE", "/api/v1/pharmacy/suppliers/{supplier_id}"): "pharmacy_suppliers",
    ("POST", "/api/v1/pharmacy/medicines"): "pharmacy_medicines",
    ("GET", "/api/v1/pharmacy/medicines"): "pharmacy_medicines",
    ("GET", "/api/v1/pharmacy/medicines/{medicine_id}"): "pharmacy_medicines",
    ("PUT", "/api/v1/pharmacy/medicines/{medicine_id}"): "pharmacy_medicines",
    ("DELETE", "/api/v1/pharmacy/medicines/{medicine_id}"): "pharmacy_medicines",
    # Public
    ("POST", "/demo/request"): "demo_requests",
    ("POST", "/contact/send"): "contact_messages",
}


TABLE_FIELDS = {
    "users": "hospital_id, email, phone, password_hash, first_name, last_name, middle_name, staff_id, status, email_verified, phone_verified, last_login, failed_login_attempts, locked_until, password_changed_at, avatar_url, timezone, language, user_metadata, id, created_at, updated_at, is_active",
    "user_roles": "user_id, role_id, assigned_at, assigned_by",
    "roles": "name, display_name, description, is_system_role, level, id, created_at, updated_at, is_active",
    "hospitals": "name, registration_number, email, phone, address, city, state, country, pincode, license_number, established_date, website, logo_url, is_active, status, tenant_database_name, settings, id, created_at, updated_at",
    "subscription_plans": "name, display_name, description, monthly_price, yearly_price, max_doctors, max_patients, max_appointments_per_month, max_storage_gb, features, id, created_at, updated_at, is_active",
    "hospital_subscriptions": "hospital_id, plan_id, status, start_date, end_date, is_trial, trial_end_date, auto_renew, current_usage, id, created_at, updated_at, is_active",
    "departments": "name, code, description, head_doctor_id, location, phone, email, is_emergency, is_icu, bed_capacity, opening_time, closing_time, is_24x7, settings, hospital_id, id, created_at, updated_at, is_active",
    "doctor_profiles": "user_id, department_id, doctor_id, medical_license_number, designation, specialization, sub_specialization, experience_years, qualifications, certifications, medical_associations, consultation_fee, follow_up_fee, consultation_type, availability_time, is_available_for_emergency, is_accepting_new_patients, bio, languages_spoken, hospital_id, id, created_at, updated_at, is_active",
    "nurse_profiles": "user_id, department_id, nurse_id, nursing_license_number, designation, specialization, experience_years, shift_type, certifications, ward_assignments, can_administer_medication, can_take_vitals, can_assist_procedures, bio, hospital_id, id, created_at, updated_at, is_active",
    "receptionist_profiles": "user_id, department_id, receptionist_id, employee_id, designation, work_area, experience_years, qualifications, shift_type, employment_type, computer_skills, languages_spoken, can_schedule_appointments, can_modify_appointments, can_register_patients, can_collect_payments, bio, is_active, hospital_id, id, created_at, updated_at",
    "staff_profiles": "user_id, department_id, employee_id, designation, joining_date, qualification, experience_years, specialization, emergency_contact_name, emergency_contact_phone, emergency_contact_relation, is_full_time, salary, skills, certifications, hospital_id, id, created_at, updated_at, is_active",
    "staff_department_assignments": "staff_id, department_id, is_primary, effective_from, effective_to, notes, unassignment_reason, hospital_id, id, created_at, updated_at, is_active",
    "tax_profiles": "name, gst_percentage, is_active, hospital_id, id, created_at, updated_at",
    "service_items": "department_id, code, name, category, base_price, tax_profile_id, is_active, hospital_id, id, created_at, updated_at",
    "pharmacy_suppliers": "name, contact_person, phone, email, address_line1, address_line2, city, state, pincode, country, gstin, drug_license_no, payment_terms, credit_limit, rating, status, notes, hospital_id, id, created_at, updated_at, is_active",
    "pharmacy_medicines": "code, name, generic_name, brand_name, category, schedule_type, dosage_form, strength, unit, manufacturer, hsn_code, gst_percent, min_stock, max_stock, reorder_level, requires_prescription, is_controlled_substance, storage_instructions, barcode, notes, status, hospital_id, id, created_at, updated_at, is_active",
    "patient_profiles": "user_id, patient_id, date_of_birth, gender, blood_group, marital_status, occupation, emergency_contact_name, emergency_contact_phone, emergency_contact_relation, address, city, state, country, pincode, allergies, chronic_conditions, current_medications, family_medical_history, insurance_provider, insurance_policy_number, insurance_expiry_date, preferred_language, communication_preferences, consent_data_sharing, profile_completed, registration_source, last_visit_date, total_visits, hospital_id, id, created_at, updated_at, is_active",
    "demo_requests": "full_name, email, phone, hospital_name, role, hospital_size, preferred_demo_date, preferred_demo_mode, modules, notes, id, created_at, updated_at, is_active",
    "contact_messages": "full_name, email, phone, hospital_name, message, id, created_at, updated_at, is_active",
}


def infer_tables(path: str, method: str) -> str:
    p = path.lower()
    inferred: list[str] = []

    # Strong path->table hints
    hints = [
        ("subscription", ["subscription_plans", "hospital_subscriptions"]),
        ("/plans", ["subscription_plans"]),
        ("hospital-admin/departments", ["departments"]),
        ("hospital-admin/staff", ["users", "user_roles", "staff_profiles"]),
        ("/doctors", ["users", "doctor_profiles"]),
        ("/nurses", ["users", "nurse_profiles"]),
        ("/receptionists", ["users", "receptionist_profiles"]),
        ("pharmacy/suppliers", ["pharmacy_suppliers"]),
        ("pharmacy/medicines", ["pharmacy_medicines"]),
        ("billing/tax-profiles", ["tax_profiles"]),
        ("billing/services", ["service_items"]),
        ("/patients", ["users", "patient_profiles"]),
        ("admissions", ["admissions"]),
        ("appointments", ["appointments"]),
        ("/beds", ["beds"]),
        ("/wards", ["wards"]),
        ("insurance/claims", ["insurance_claims"]),
        ("support/tickets", ["support_tickets"]),
        ("telemed/sessions", ["telemed_sessions"]),
        ("telemed/tele-appointments", ["tele_appointments"]),
        ("telemed/prescriptions", ["tele_prescriptions"]),
        ("lab/test-registration", ["lab_test_registrations"]),
        ("lab/sample-tracking", ["lab_sample_tracking"]),
        ("lab/report-generation", ["lab_report_records"]),
        ("lab/result-access", ["lab_result_access_grants", "lab_result_access_logs"]),
        ("lab/equipment", ["lab_equipment", "equipment_maintenance_logs"]),
        ("notifications", ["notification_jobs", "notification_preferences", "notification_providers"]),
        ("payments", ["gateway_payments", "refunds"]),
        ("demo/request", ["demo_requests"]),
        ("contact/send", ["contact_messages"]),
    ]
    for token, tables in hints:
        if token in p:
            for t in tables:
                if t not in inferred:
                    inferred.append(t)

    if method == "DELETE" and "billing/services" in p and "service_items" in inferred:
        return "service_items (soft-delete likely via is_active)"
    if not inferred:
        return "review_needed"
    return ", ".join(inferred)


def main() -> None:
    routes = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    by_method: dict[str, list[dict]] = defaultdict(list)
    for r in routes:
        for method in r["methods"]:
            by_method[method].append(r)

    lines: list[str] = []
    lines.append("# Complete Endpoint Mapping (All Endpoints Listed)")
    lines.append("")
    lines.append(f"Total endpoints: **{len(routes)}**")
    lines.append("")
    lines.append("This file lists all endpoints with visible status and table mapping.")
    lines.append("- `CONFIRMED`: verified mapping")
    lines.append("- `INFERRED`: best-effort mapping from route/service naming (review recommended)")
    lines.append("")

    section_order = [("POST", "1) All POST endpoints"), ("GET", "2) All GET endpoints"), ("PUT", "3) All PUT endpoints"), ("PATCH", "4) All PATCH endpoints"), ("DELETE", "5) All DELETE endpoints")]

    for method, title in section_order:
        rows = sorted(by_method.get(method, []), key=lambda x: x["path"])
        lines.append(f"## {title}")
        lines.append("")
        for row in rows:
            key = (method, row["path"])
            tables = CONFIRMED_TABLES.get(key)
            if tables:
                status = "CONFIRMED"
                table_text = tables
            else:
                status = "INFERRED"
                table_text = infer_tables(row["path"], method)
            lines.append(f"- `{method} {row['path']}`")
            lines.append(f"  - endpoint: `{row['endpoint']}`")
            lines.append(f"  - status: `{status}`")
            lines.append(f"  - tables: `{table_text}`")
        lines.append("")

    lines.append("## 6) Confirmed table fields reference")
    lines.append("")
    for table in sorted(TABLE_FIELDS):
        lines.append(f"- `{table}`")
        lines.append(f"  - fields: `{TABLE_FIELDS[table]}`")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
