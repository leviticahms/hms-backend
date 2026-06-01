# KT — Hospital Admin Module

Knowledge-transfer doc for the **Hospital Admin** endpoints shown in Swagger
(`Hospital Admin - Platform Settings`, `... Department Management`, `... Staff Management`,
`... Department Assignment`, `... Appointment Oversight`, `... Patient Management`,
`... Ward & Bed Management`, `... Admission Management`, `... Reports & Analytics`,
`... Dashboard`, `... Audit`) plus the **Support - Tickets** group.

For every endpoint it shows the **full file flow**: which router function receives it,
which service class/method runs the logic, which schema classes validate input/output,
which model classes are touched, and the step-by-step flow.

> Paths are dotted module names. `Class.method` means a method on that class.
> Line numbers are for the current branch and may shift over time.
> **Tenant scoping:** every `/hospital-admin/*` endpoint is locked to the `hospital_id`
> from the JWT and runs on that hospital's **tenant DB** (mirroring staff users back to the
> platform DB when a sub-database is provisioned).

---

## Endpoints covered

**Hospital Admin - Platform Settings** (read-only)
1. `GET /api/v1/hospital-admin/platform-settings/features` — Subscription feature flags
2. `GET /api/v1/hospital-admin/platform-settings/subscription` — Subscription detail
3. `GET /api/v1/hospital-admin/platform-settings/plan` — Plan quotas
4. `GET /api/v1/hospital-admin/platform-settings/hospital` — Registry row
5. `GET /api/v1/hospital-admin/platform-settings/modules` — Modules on/off
6. `GET /api/v1/hospital-admin/platform-settings/usage` — Usage vs limits
7. `GET /api/v1/hospital-admin/platform-settings` — Combined settings

**Hospital Admin - Department Management**
8. `POST /api/v1/hospital-admin/departments` — Create Department
9. `GET /api/v1/hospital-admin/departments` — List Departments
10. `GET /api/v1/hospital-admin/departments/{department_id}` — Get Department Details
11. `PUT /api/v1/hospital-admin/departments/{department_id}` — Update Department
12. `PATCH /api/v1/hospital-admin/departments/{department_id}/status` — Update Department Status

**Hospital Admin - Staff Management**
13. `POST /api/v1/hospital-admin/staff` — Create Staff User
14. `GET /api/v1/hospital-admin/staff` — List Staff Users
15. `GET /api/v1/hospital-admin/staff/{staff_id}` — Get Staff Details
16. `PATCH /api/v1/hospital-admin/staff/doctors/{staff_id}` — Update Doctor Staff Profile
17. `PATCH /api/v1/hospital-admin/staff/receptionists/{staff_id}` — Update Receptionist Staff Profile
18. `PATCH /api/v1/hospital-admin/staff/lab-techs/{staff_id}` — Update Lab Tech Staff Profile
19. `PATCH /api/v1/hospital-admin/staff/pharmacists/{staff_id}` — Update Pharmacist Staff Profile
20. `PATCH /api/v1/hospital-admin/staff/{staff_id}/status` — Update Staff Status
21. `POST /api/v1/hospital-admin/staff/{staff_id}/reset-password` — Reset Staff Password

**Hospital Admin - Department Assignment**
22. `POST /api/v1/hospital-admin/departments/assign-staff` — Assign Staff To Department
23. `POST /api/v1/hospital-admin/departments/unassign-staff` — Unassign Staff From Department
24. `GET /api/v1/hospital-admin/departments/{department_name}/staff` — Get Department Staff
25. `GET /api/v1/hospital-admin/staff/{staff_name}/departments` — Get Staff Departments

**Hospital Admin - Appointment Oversight**
26. `GET /api/v1/hospital-admin/appointments` — List Appointments
27. `GET /api/v1/hospital-admin/appointments/{appointment_id}` — Get Appointment Details
28. `PATCH /api/v1/hospital-admin/appointments/{appointment_id}/status` — Update Appointment Status

**Hospital Admin - Patient Management** (non-medical)
29. `GET /api/v1/hospital-admin/patients` — List Patients
30. `PATCH /api/v1/hospital-admin/patients/{patient_id}/status` — Update Patient Status

**Hospital Admin - Ward & Bed Management**
31. `POST /api/v1/hospital-admin/wards` — Create Ward
32. `GET /api/v1/hospital-admin/wards` — List Wards
33. `PUT /api/v1/hospital-admin/wards/{ward_id}` — Update Ward
34. `PATCH /api/v1/hospital-admin/wards/{ward_id}/status` — Update Ward Status
35. `POST /api/v1/hospital-admin/beds` — Create Bed
36. `GET /api/v1/hospital-admin/beds` — List Beds
37. `GET /api/v1/hospital-admin/beds/{bed_id}` — Get Bed Details
38. `PATCH /api/v1/hospital-admin/beds/{bed_id}/status` — Update Bed Status

**Hospital Admin - Admission Management**
39. `POST /api/v1/hospital-admin/admissions` — Create Admission
40. `GET /api/v1/hospital-admin/admissions` — List Admissions
41. `POST` & `PATCH /api/v1/hospital-admin/admissions/{admission_id}/assign-bed` — Assign Bed
42. `POST` & `PATCH /api/v1/hospital-admin/admissions/{admission_id}/discharge` — Discharge Patient

**Hospital Admin - Reports & Analytics**
43. `GET /api/v1/hospital-admin/reports/bed-occupancy` — Bed Occupancy Report
44. `GET /api/v1/hospital-admin/reports/department-performance` — Department Performance Report
45. `GET /api/v1/hospital-admin/reports/revenue-summary` — Revenue Summary Report

**Hospital Admin - Dashboard**
46. `GET /api/v1/hospital-admin/dashboard/overview` — Dashboard Overview
47. `GET /api/v1/hospital-admin/dashboard/staff-stats` — Staff Statistics
48. `GET /api/v1/hospital-admin/dashboard/appointment-stats` — Appointment Statistics

**Hospital Admin - Audit**
49. `GET /api/v1/hospital-admin/audit-logs` — List Hospital Admin Audit Logs

**Support - Tickets**
50. `POST /api/v1/support/staff/tickets` — Create Ticket As Staff
51. `GET /api/v1/support/staff/tickets` — List My Tickets As Staff
52. `POST /api/v1/support/hospital-admin/tickets` — Create Ticket As Hospital Admin
53. `GET /api/v1/support/hospital-admin/tickets` — List Tickets For Hospital Admin
54. `GET /api/v1/support/hospital-admin/tickets/completed` — List Completed Tickets
55. `PATCH /api/v1/support/hospital-admin/tickets/{ticket_id}/status` — Update Ticket Status

---

## Module file map (what each file is for)

| Layer | Module | File | Role in this module |
| --- | --- | --- | --- |
| Router | `app.api.v1.routers.admin.hospital_admin` | `app/api/v1/routers/admin/hospital_admin.py` | Endpoints 1–49 (`/hospital-admin/*`) |
| Router | `app.api.v1.routers.support.tickets` | `app/api/v1/routers/support/tickets.py` | Endpoints 50–55 (`/support/*`) |
| Service | `app.services.hospital_admin_service` | `app/services/hospital_admin_service.py` | `HospitalAdminService` — departments, staff, appts, patients, wards/beds, admissions, reports, dashboard |
| Service | `app.services.subscription_feature_service` | `app/services/subscription_feature_service.py` | Platform-settings bundles (endpoints 1–7) |
| Service | `app.services.super_admin_service` | `app/services/super_admin_service.py` | `SuperAdminService.create_support_ticket` / `update_support_ticket_status` (tickets) |
| Service | `app.services.email_service` | `app/services/email_service.py` | `EmailService` — ticket notification emails |
| Schema | `app.schemas.admin` | `app/schemas/admin.py` | Request/response models for endpoints 8–49 |
| Schema | `app.schemas.plan_features` | `app/schemas/plan_features.py` | Response models for endpoints 1–7 |
| RBAC | `app.api.deps` | `app/api/deps.py` | `require_hospital_admin`, `require_hospital_admin_context`, `get_db_session` |
| RBAC | `app.dependencies.auth` | `app/dependencies/auth.py` | `require_hospital_context` |
| Model | `app.models.user` | `app/models/user.py` | `User`, `Role`, `AuditLog` |
| Model | `app.models.hospital` | `app/models/hospital.py` | `Department`, `Ward`, `Bed`, `StaffDepartmentAssignment` |
| Model | `app.models.patient` | `app/models/patient.py` | `PatientProfile`, `Appointment`, `Admission` |
| Model | `app.models.doctor` | `app/models/doctor.py` | `DoctorProfile` |
| Model | `app.models.support` | `app/models/support.py` | `SupportTicket` |
| Model | `app.models.tenant` | `app/models/tenant.py` | `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel` |

---

## High-level module flow

```
HTTP request (Hospital Admin JWT)
   │
   ▼
Router (hospital_admin.py / support/tickets.py)
   │  - require_hospital_admin() / require_hospital_admin_context()  → RBAC + hospital scope
   │  - validates body with app.schemas.admin.* / plan_features.*
   │  - parses path UUIDs (department_id / staff_id / ward_id / bed_id / admission_id / ...)
   ▼
get_hospital_admin_service()  (hospital_admin.py:65)
   │  - resolves tenant DB name, builds HospitalAdminService(db, hospital_id, platform_db=...)
   ▼
HospitalAdminService  (runs on TENANT DB; mirrors staff users to PLATFORM DB)
   │  - reads/writes Department, Ward, Bed, Admission, Appointment, PatientProfile,
   │    DoctorProfile, User, Role, StaffDepartmentAssignment
   │  - platform-settings endpoints instead call subscription_feature_service bundles
   │    against the PLATFORM DB (hospitals / hospital_subscriptions / subscription_plans)
   ▼
Response (raw dict or typed *Out schema)
```

**Two DB targets:**
- Endpoints **1–7** (Platform Settings) and **49** (Audit) read the **platform DB** directly
  (`get_platform_db_session`).
- Everything else flows through `HospitalAdminService` on the **tenant DB**, with staff-user
  writes mirrored back to the platform DB so auth/login keeps working.

---

## Endpoint-by-endpoint flow

### Platform Settings (read-only, platform DB)

#### 1. `GET /hospital-admin/platform-settings/features`
- Router: `hospital_admin.get_hospital_subscription_features` (`hospital_admin.py:97`)
- RBAC: `require_hospital_admin()` + `require_hospital_context` · DB: `get_platform_db_session`
- Schema out: `app.schemas.plan_features.HospitalFeatureFlagsOut`
- Service: `subscription_feature_service.get_plan_info_for_hospital`
- Flow: resolve the hospital's plan and return effective flags for `lab_tests`, `video_consultation`, `pharmacy`.
- Models: `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel` (read-only).

#### 2. `GET /hospital-admin/platform-settings/subscription`
- Router: `hospital_admin.get_hospital_platform_subscription_detail` (`hospital_admin.py:120`)
- Schema out: `HospitalSubscriptionDetailOut`
- Service: `subscription_feature_service.get_hospital_subscription_detail_bundle` (`:114`)
- Flow: return the subscription lifecycle row (status, dates, trial, usage JSON).
- Models: `HospitalSubscription`, `SubscriptionPlanModel`.

#### 3. `GET /hospital-admin/platform-settings/plan`
- Router: `hospital_admin.get_hospital_platform_plan_quotas` (`hospital_admin.py:136`)
- Schema out: `HospitalPlanQuotasOut`
- Service: `subscription_feature_service.get_hospital_plan_quotas_bundle` (`:147`)
- Flow: return current plan tier limits + pricing joined via subscription.
- Models: `HospitalSubscription`, `SubscriptionPlanModel`.

#### 4. `GET /hospital-admin/platform-settings/hospital`
- Router: `hospital_admin.get_hospital_platform_registry_row` (`hospital_admin.py:152`)
- Schema out: `HospitalRegistryPlatformOut`
- Service: `subscription_feature_service.get_hospital_registry_platform_bundle` (`:185`)
- Flow: return the platform `hospitals` registry profile for this tenant.
- Models: `Hospital`.

#### 5. `GET /hospital-admin/platform-settings/modules`
- Router: `hospital_admin.get_hospital_platform_modules` (`hospital_admin.py:168`)
- Schema out: `HospitalModulesOut`
- Service: `subscription_feature_service.get_hospital_modules_bundle` (`:232`)
- Flow: module list with human labels + effective on/off state (same keys as `/features`).
- Models: `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel`.

#### 6. `GET /hospital-admin/platform-settings/usage`
- Router: `hospital_admin.get_hospital_platform_usage_vs_limits` (`hospital_admin.py:184`)
- Schema out: `HospitalUsageVsLimitsOut`
- Service: `subscription_feature_service.get_hospital_usage_vs_limits_bundle` (`:243`)
- Flow: `current_usage` from subscription JSON plus resolved plan quota caps.
- Models: `HospitalSubscription`, `SubscriptionPlanModel`.

#### 7. `GET /hospital-admin/platform-settings`
- Router: `hospital_admin.get_hospital_platform_settings` (`hospital_admin.py:200`)
- Schema out: `HospitalPlatformSettingsOut`
- Service: `subscription_feature_service.get_hospital_platform_settings_bundle` (`:60`)
- Flow: combined view — registry row + subscription status + effective plan features.
- Models: `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel`.

### Department Management

#### 8. `POST /hospital-admin/departments`
- Router: `hospital_admin.create_department` (`hospital_admin.py:218`)
- Schema in: `app.schemas.admin.DepartmentCreate`
- Service: `HospitalAdminService.create_department` (`hospital_admin_service.py:412`)
- Flow: validate unique department code in the hospital, optional head-doctor, insert `Department`.
- Models: `Department`, `User` (head doctor).

#### 9. `GET /hospital-admin/departments`
- Router: `hospital_admin.list_departments` (`hospital_admin.py:235`) — `page`, `limit`, `active_only`
- Schema out: `DepartmentListOut`
- Service: `HospitalAdminService.get_departments` (`hospital_admin_service.py:505`)
- Models: `Department`, `StaffDepartmentAssignment`.

#### 10. `GET /hospital-admin/departments/{department_id}`
- Router: `hospital_admin.get_department_details` (`hospital_admin.py:259`)
- Schema out: `DepartmentDetailsOut`
- Service: `HospitalAdminService.get_department_details` (`hospital_admin_service.py:591`)
- Models: `Department`, `User`.

#### 11. `PUT /hospital-admin/departments/{department_id}`
- Router: `hospital_admin.update_department` (`hospital_admin.py:285`)
- Schema in: `DepartmentUpdate`
- Service: `HospitalAdminService.update_department` (`hospital_admin_service.py:673`)
- Flow: drop `None` fields → uniqueness check → update `Department`.
- Models: `Department`.

#### 12. `PATCH /hospital-admin/departments/{department_id}/status`
- Router: `hospital_admin.update_department_status` (`hospital_admin.py:321`)
- Schema in: `DepartmentStatusUpdate`
- Service: `HospitalAdminService.update_department_status` (`hospital_admin_service.py:781`)
- Flow: enable/disable department (`is_active`).
- Models: `Department`.

### Staff Management

#### 13. `POST /hospital-admin/staff`
- Router: `hospital_admin.create_staff_user` (`hospital_admin.py:352`)
- Schema in: `StaffCreate`
- Service: `HospitalAdminService.create_staff_user` (`hospital_admin_service.py:820`)
- Flow: create `User`, assign `Role` (DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST), create
  role-specific profile (e.g. `DoctorProfile`), generate temp password (`SecurityManager`), then
  **mirror user+role to the platform DB** (`_mirror_staff_auth_to_platform`, `:336`).
- Models: `User`, `Role`, `DoctorProfile`.

#### 14. `GET /hospital-admin/staff`
- Router: `hospital_admin.list_staff_users` (`hospital_admin.py:372`) — `role`, `active_only`, paging
- Schema out: `StaffListOut`
- Service: `HospitalAdminService.get_staff_users` (`hospital_admin_service.py:1354`)
- Models: `User`, `Role`.

#### 15. `GET /hospital-admin/staff/{staff_id}`
- Router: `hospital_admin.get_staff_details` (`hospital_admin.py:399`)
- Schema out: `StaffDetailsOut`
- Service: `HospitalAdminService.get_staff_details` (`hospital_admin_service.py:1480`)
- Models: `User`, `Role`, `DoctorProfile`.

#### 16. `PATCH /hospital-admin/staff/doctors/{staff_id}`
- Router: `hospital_admin.update_doctor_staff_profile` (`hospital_admin.py:426`)
- Schema in: `DoctorStaffUpdate` · Schema out: `StaffUpdateResponse`
- Service: `HospitalAdminService.update_doctor_staff` (`hospital_admin_service.py:1738`)
- Flow: common user-field updates (`_apply_common_staff_updates`, `:1672`) + doctor profile fields.
- Models: `User`, `DoctorProfile`.

#### 17. `PATCH /hospital-admin/staff/receptionists/{staff_id}`
- Router: `hospital_admin.update_receptionist_staff_profile` (`hospital_admin.py:445`)
- Schema in: `ReceptionistStaffUpdate` · Schema out: `StaffUpdateResponse`
- Service: `HospitalAdminService.update_receptionist_staff` (`hospital_admin_service.py:1971`)
- Models: `User`.

#### 18. `PATCH /hospital-admin/staff/lab-techs/{staff_id}`
- Router: `hospital_admin.update_lab_tech_staff_profile` (`hospital_admin.py:464`)
- Schema in: `LabTechStaffUpdate` · Schema out: `StaffUpdateResponse`
- Service: `HospitalAdminService.update_lab_tech_staff` (`hospital_admin_service.py:2077`)
- Models: `User`.

#### 19. `PATCH /hospital-admin/staff/pharmacists/{staff_id}`
- Router: `hospital_admin.update_pharmacist_staff_profile` (`hospital_admin.py:483`)
- Schema in: `PharmacistStaffUpdate` · Schema out: `StaffUpdateResponse`
- Service: `HospitalAdminService.update_pharmacist_staff` (`hospital_admin_service.py:2109`)
- Models: `User`.

#### 20. `PATCH /hospital-admin/staff/{staff_id}/status`
- Router: `hospital_admin.update_staff_status` (`hospital_admin.py:502`)
- Schema in: `StaffStatusUpdate`
- Service: `HospitalAdminService.update_staff_status` (`hospital_admin_service.py:2141`)
- Flow: activate/deactivate the staff `User`; mirror status to platform DB.
- Models: `User`.

#### 21. `POST /hospital-admin/staff/{staff_id}/reset-password`
- Router: `hospital_admin.reset_staff_password` (`hospital_admin.py:529`)
- Service: `HospitalAdminService.reset_staff_password` (`hospital_admin_service.py:2202`)
- Flow: generate new temp password, clear failed-login/lock, force change on next login.
- Models: `User`.

### Department Assignment

#### 22. `POST /hospital-admin/departments/assign-staff`
- Router: `hospital_admin.assign_staff_to_department` (`hospital_admin.py:560`)
- Schema in: `DepartmentAssignmentCreate`
- Service: `HospitalAdminService.assign_staff_to_department` (`hospital_admin_service.py:5882`)
- Flow: link a staff user to a department (mandatory for them to operate).
- Models: `StaffDepartmentAssignment`, `Department`, `User`.

#### 23. `POST /hospital-admin/departments/unassign-staff`
- Router: `hospital_admin.unassign_staff_from_department` (`hospital_admin.py:576`)
- Schema in: `DepartmentUnassignmentCreate`
- Service: `HospitalAdminService.unassign_staff_from_department` (`hospital_admin_service.py:6187`)
- Models: `StaffDepartmentAssignment`.

#### 24. `GET /hospital-admin/departments/{department_name}/staff`
- Router: `hospital_admin.get_department_staff` (`hospital_admin.py:591`)
- Service: `HospitalAdminService.get_department_staff` (`hospital_admin_service.py:6246`)
- Flow: all staff assigned to the named department.
- Models: `StaffDepartmentAssignment`, `Department`, `User`.

#### 25. `GET /hospital-admin/staff/{staff_name}/departments`
- Router: `hospital_admin.get_staff_departments` (`hospital_admin.py:606`)
- Service: `HospitalAdminService.get_staff_departments` (`hospital_admin_service.py:6298`)
- Flow: all departments the named staff member belongs to.
- Models: `StaffDepartmentAssignment`, `Department`, `User`.

### Appointment Oversight

#### 26. `GET /hospital-admin/appointments`
- Router: `hospital_admin.list_appointments` (`hospital_admin.py:625`) — status/doctor/department/date filters
- Schema out: `AppointmentListOut`
- Service: `HospitalAdminService.get_appointments` (`hospital_admin_service.py:2733`)
- Models: `Appointment`, `PatientProfile`, `DoctorProfile`, `Department`.

#### 27. `GET /hospital-admin/appointments/{appointment_id}`
- Router: `hospital_admin.get_appointment_details` (`hospital_admin.py:658`)
- Schema out: `AppointmentDetailsOut`
- Service: `HospitalAdminService.get_appointment_details` (`hospital_admin_service.py:2841`)
- Models: `Appointment`, `PatientProfile`, `DoctorProfile`.

#### 28. `PATCH /hospital-admin/appointments/{appointment_id}/status`
- Router: `hospital_admin.update_appointment_status` (`hospital_admin.py:685`)
- Schema in: `AppointmentStatusUpdate`
- Service: `HospitalAdminService.update_appointment_status` (`hospital_admin_service.py:2937`)
- Flow: cancel/reschedule/complete, optional doctor reassignment + admin notes.
- Models: `Appointment`, `DoctorProfile`.

### Patient Management (non-medical)

#### 29. `GET /hospital-admin/patients`
- Router: `hospital_admin.list_patients` (`hospital_admin.py:726`) — `search`, `active_only`, paging
- Schema out: `PatientListOut`
- Service: `HospitalAdminService.get_patients` (`hospital_admin_service.py:3059`)
- Flow: demographic/contact/account data only — **no** medical history.
- Models: `PatientProfile`, `User`.

#### 30. `PATCH /hospital-admin/patients/{patient_id}/status`
- Router: `hospital_admin.update_patient_status` (`hospital_admin.py:755`)
- Schema in: `PatientStatusUpdate`
- Service: `HospitalAdminService.update_patient_status` (`hospital_admin_service.py:3185`)
- Flow: activate/deactivate patient login; medical records untouched.
- Models: `PatientProfile`, `User`.

### Ward & Bed Management

#### 31. `POST /hospital-admin/wards`
- Router: `hospital_admin.create_ward` (`hospital_admin.py:789`)
- Schema in: `WardCreate`
- Service: `HospitalAdminService.create_ward` (`hospital_admin_service.py:3262`)
- Models: `Ward`.

#### 32. `GET /hospital-admin/wards`
- Router: `hospital_admin.list_wards` (`hospital_admin.py:808`) — `ward_type`, `active_only`, paging
- Schema out: `WardListOut`
- Service: `HospitalAdminService.get_wards` (`hospital_admin_service.py:3432`)
- Models: `Ward`, `Bed` (occupancy stats).

#### 33. `PUT /hospital-admin/wards/{ward_id}`
- Router: `hospital_admin.update_ward` (`hospital_admin.py:835`)
- Schema in: `WardUpdate`
- Service: `HospitalAdminService.update_ward` (`hospital_admin_service.py:3572`)
- Models: `Ward`.

#### 34. `PATCH /hospital-admin/wards/{ward_id}/status`
- Router: `hospital_admin.update_ward_status` (`hospital_admin.py:871`)
- Schema in: `WardStatusUpdate`
- Service: `HospitalAdminService.update_ward_status` (`hospital_admin_service.py:3773`)
- Models: `Ward`.

#### 35. `POST /hospital-admin/beds`
- Router: `hospital_admin.create_bed` (`hospital_admin.py:898`)
- Schema in: `BedCreate`
- Service: `HospitalAdminService.create_bed` (`hospital_admin_service.py:3811`)
- Flow: identify ward by name, insert `Bed` (code, equipment, pricing for private beds).
- Models: `Bed`, `Ward`.

#### 36. `GET /hospital-admin/beds`
- Router: `hospital_admin.list_beds` (`hospital_admin.py:918`) — `ward_id`, `status`, `bed_type`, paging
- Schema out: `BedListOut`
- Service: `HospitalAdminService.get_beds` (`hospital_admin_service.py:3907`)
- Models: `Bed`, `Ward`.

#### 37. `GET /hospital-admin/beds/{bed_id}`
- Router: `hospital_admin.get_bed_details` (`hospital_admin.py:947`)
- Schema out: `BedDetailsOut`
- Service: `HospitalAdminService.get_bed_details` (`hospital_admin_service.py:4009`)
- Models: `Bed`, `Ward`, `PatientProfile`.

#### 38. `PATCH /hospital-admin/beds/{bed_id}/status`
- Router: `hospital_admin.update_bed_status` (`hospital_admin.py:974`)
- Schema in: `BedStatusUpdate`
- Service: `HospitalAdminService.update_bed_status` (`hospital_admin_service.py:4084`)
- Flow: available/occupied/maintenance/reserved + optional patient + maintenance notes.
- Models: `Bed`, `PatientProfile`.

### Admission Management

#### 39. `POST /hospital-admin/admissions`
- Router: `hospital_admin.create_admission` (`hospital_admin.py:1011`)
- Schema in: `AdmissionCreate`
- Service: `HospitalAdminService.create_admission` (`hospital_admin_service.py:4184`)
- Flow: create admission with patient/doctor/department + initial diagnosis.
- Models: `Admission`, `PatientProfile`, `Department`.

#### 40. `GET /hospital-admin/admissions`
- Router: `hospital_admin.list_admissions` (`hospital_admin.py:1030`) — status/date filters, paging
- Schema out: `AdmissionListOut`
- Service: `HospitalAdminService.get_admissions` (`hospital_admin_service.py:4535`)
- Models: `Admission`, `Bed`, `PatientProfile`.

#### 41. `POST` & `PATCH /hospital-admin/admissions/{admission_id}/assign-bed`
- Router: `hospital_admin.assign_bed_to_admission` (`hospital_admin.py:1061`) — same handler for both verbs
- Schema in: `BedAssignmentCreate`
- Service: `HospitalAdminService.assign_bed_to_admission` (`hospital_admin_service.py:4288`)
- Flow: validate bed free → admission → ADMITTED, bed → OCCUPIED (prevents double assignment).
- Models: `Admission`, `Bed`.

#### 42. `POST` & `PATCH /hospital-admin/admissions/{admission_id}/discharge`
- Router: `hospital_admin.discharge_patient` (`hospital_admin.py:1096`) — same handler for both verbs
- Schema in: `DischargeCreate`
- Service: `HospitalAdminService.discharge_patient` (`hospital_admin_service.py:4400`)
- Flow: admission → DISCHARGED, bed → AVAILABLE, compute length of stay + discharge notes.
- Models: `Admission`, `Bed`.

### Reports & Analytics

#### 43. `GET /hospital-admin/reports/bed-occupancy`
- Router: `hospital_admin.get_bed_occupancy_report` (`hospital_admin.py:1131`) — date range, `ward_id`
- Schema out: `BedOccupancyReportOut`
- Service: `HospitalAdminService.get_bed_occupancy_report` (`hospital_admin_service.py:4648`)
- Models: `Bed`, `Ward`, `Admission`.

#### 44. `GET /hospital-admin/reports/department-performance`
- Router: `hospital_admin.get_department_performance_report` (`hospital_admin.py:1157`) — date range
- Schema out: `DepartmentPerformanceReportOut`
- Service: `HospitalAdminService.get_department_performance_report` (`hospital_admin_service.py:4806`)
- Models: `Department`, `Appointment`, `DoctorProfile`.

#### 45. `GET /hospital-admin/reports/revenue-summary`
- Router: `hospital_admin.get_revenue_summary_report` (`hospital_admin.py:1181`) — date range
- Schema out: `RevenueSummaryReportOut`
- Service: `HospitalAdminService.get_revenue_summary_report` (`hospital_admin_service.py:4964`)
- Flow: revenue from completed-appointment consultation fees + department breakdown + 7-day trend.
- Models: `Appointment`, `Department`.

### Dashboard

#### 46. `GET /hospital-admin/dashboard/overview`
- Router: `hospital_admin.get_dashboard_overview` (`hospital_admin.py:1205`)
- Schema out: `DashboardOverviewOut`
- Service: `HospitalAdminService.get_dashboard_overview` (`hospital_admin_service.py:5053`)
- Flow: patient/staff/appointment/bed/revenue KPIs + recent activity.
- Models: `User`, `PatientProfile`, `Appointment`, `Bed`, `Ward`.

#### 47. `GET /hospital-admin/dashboard/staff-stats`
- Router: `hospital_admin.get_staff_statistics` (`hospital_admin.py:1224`)
- Schema out: `StaffStatisticsOut`
- Service: `HospitalAdminService.get_staff_statistics` (`hospital_admin_service.py:5335`)
- Models: `User`, `Role`, `Department`, `DoctorProfile`.

#### 48. `GET /hospital-admin/dashboard/appointment-stats`
- Router: `hospital_admin.get_appointment_statistics` (`hospital_admin.py:1243`)
- Schema out: `AppointmentStatisticsOut`
- Service: `HospitalAdminService.get_appointment_statistics` (`hospital_admin_service.py:5481`)
- Models: `Appointment`, `Department`.

### Audit

#### 49. `GET /hospital-admin/audit-logs`
- Router: `hospital_admin.list_hospital_admin_audit_logs` (`hospital_admin.py:1272`) — runs on **platform DB**
- Schema out: `HospitalAdminAuditLogListOut` (`admin.py:922`)
- Flow: query `AuditLog` where `resource_type == "HospitalAdmin"` + `hospital_id == JWT hospital`;
  build summary counts (VIEW/UPDATE/CREATE/DELETE) + paginated items joined to `User` for names.
  Rows are written automatically by middleware on every `/hospital-admin/*` request.
- Helpers: `app.utils.hospital_admin_audit_labels.action_display_from_code`, `resource_from_row`.
- Models: `AuditLog`, `User`.

### Support - Tickets (router `support/tickets.py`)

`SupportTicket` lives in the **tenant DB** (`get_db_session`). Create/update logic is delegated to
`SuperAdminService`; emails go out via `EmailService`.

#### 50. `POST /support/staff/tickets`
- Router: `tickets.create_ticket_as_staff` (`tickets.py:62`) — `get_current_user` + `require_hospital_context`
- Schema in: `SupportTicketCreateIn` (`tickets.py:28`)
- Service: `SuperAdminService.create_support_ticket` (`super_admin_service.py:1697`)
- Flow: any hospital-scoped staff raises a ticket → email **all hospital admins** (`_get_hospital_admin_emails`).
- Models: `SupportTicket`, `User`, `Role`.

#### 51. `GET /support/staff/tickets`
- Router: `tickets.list_my_support_tickets_as_staff` (`tickets.py:120`) — `status`, `completed_only`, paging
- Flow: list tickets where `raised_by_user_id == current_user`.
- Models: `SupportTicket`.

#### 52. `POST /support/hospital-admin/tickets`
- Router: `tickets.create_ticket_as_hospital_admin` (`tickets.py:162`) — `require_hospital_admin_context()`
- Schema in: `SupportTicketCreateIn`
- Service: `SuperAdminService.create_support_ticket` (`super_admin_service.py:1697`)
- Flow: hospital admin raises a ticket → email the **Super Admin** (`settings.SUPERADMIN_EMAIL`).
- Models: `SupportTicket`.

#### 53. `GET /support/hospital-admin/tickets`
- Router: `tickets.list_tickets_for_hospital_admin` (`tickets.py:199`) — optional `status`, paging
- Flow: list all tickets for the hospital.
- Models: `SupportTicket`.

#### 54. `GET /support/hospital-admin/tickets/completed`
- Router: `tickets.list_completed_tickets_for_hospital_admin` (`tickets.py:227`)
- Flow: list tickets with status RESOLVED or CLOSED.
- Models: `SupportTicket`.

#### 55. `PATCH /support/hospital-admin/tickets/{ticket_id}/status`
- Router: `tickets.update_ticket_status_as_hospital_admin` (`tickets.py:254`)
- Schema in: `SupportTicketStatusUpdateIn` (`tickets.py:34`)
- Service: `SuperAdminService.update_support_ticket_status` (`super_admin_service.py:1802`)
- Flow: verify ticket belongs to the hospital → update status/notes; on RESOLVED/CLOSED email the raiser.
- Models: `SupportTicket`, `User`.

---

## Shared helpers used across endpoints

- `require_hospital_admin()` / `require_hospital_admin_context()` / `require_hospital_context`
  — RBAC + hospital-scope gates on every endpoint.
- `get_hospital_admin_service` (`hospital_admin.py:65`) — resolves tenant DB + builds the service
  with an optional platform-DB handle for mirroring.
- `HospitalAdminService._mirror_staff_auth_to_platform` (`:336`),
  `_sync_user_row_to_platform_after_mutation` (`:363`) — keep platform auth rows in sync after
  tenant-side staff writes.
- `HospitalAdminService._apply_common_staff_updates` (`:1672`) — shared field updates for the
  per-role staff PATCH endpoints (16–19).
- `HospitalAdminService._get_hospital_*` lookups (`:5652`–`:6446`) — hospital-scoped fetch helpers.
- `SecurityManager` (`app/core/security.py`) — temp password hashing for staff create/reset.
- `EmailService` (`app/services/email_service.py`) — support-ticket notification emails.

## Notes

- The Swagger duplicates on `assign-bed` and `discharge` (POST + PATCH) are intentional — both verbs
  map to the same handler for client compatibility.
- Patient Management is **non-medical only**: no diagnoses/medications/allergies are returned.
- `HospitalAdminService` also contains doctor-profile/schedule methods (`create_doctor_schedule`,
  `get_doctor_schedules`, etc.) that are **not** exposed by these endpoints and are therefore not listed.
